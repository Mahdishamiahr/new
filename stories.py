"""Stories (like Telegram / Instagram): a photo, a short video or a coloured text post that disappears after 24 hours.

* Who sees a story?  `contacts`  = people you share a private chat with (at least one message in it)
                     `shared`    = contacts + people who are in a group with you
  Blocked people (either direction) never see it. Nobody can browse strangers' stories.
* Seen-by list: only the owner sees it. A viewer who turned "read receipts" off is not listed (and not counted),
  exactly like the blue ticks in chats.
* Files live in data/stories/<user id>/ and are deleted when the story expires (WINDROIDA_STORY_HOURS, default 24).
"""
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path

REACTIONS = ("❤️", "🔥", "👍", "😂", "😮", "😢", "👏")
AUDIENCES = ("contacts", "shared")
MAX_ACTIVE = 30                 # live stories per user
TEXT_BACKGROUNDS = 8            # the client has 8 gradients (index 0..7)
MAX_TEXT = 300
MAX_CAPTION = 200
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def make_stories(Fail, common, log):
    now = common.now

    class StoriesMixin:
        # ------------------------------------------------------------------ who may see what
        def _story_shared_group(self, db, a, b):
            return db.execute(
                "SELECT 1 FROM members x JOIN members y ON x.chat_id=y.chat_id JOIN chats c ON c.id=x.chat_id "
                "WHERE x.user_id=? AND y.user_id=? AND c.kind='group' AND c.channel=0 AND c.chan=0 LIMIT 1",
                (a, b)).fetchone() is not None

        def _story_can_see(self, db, owner, viewer, audience):
            if owner == viewer:
                return True
            if self._is_blocked(db, owner, viewer) or self._is_blocked(db, viewer, owner):
                return False
            if self._is_contact(db, owner, viewer):
                return True
            return audience == "shared" and self._story_shared_group(db, owner, viewer)

        def _story_audience_ids(self, db, uid, audience):
            """Everybody who might see uid's story (used for the live 'new story' ping; the feed re-checks)."""
            ids = set(self._contacts_of(db, uid))
            if audience == "shared":
                ids |= {r[0] for r in db.execute(
                    "SELECT DISTINCT y.user_id FROM members x JOIN members y ON x.chat_id=y.chat_id "
                    "JOIN chats c ON c.id=x.chat_id WHERE x.user_id=? AND y.user_id!=? AND c.kind='group' "
                    "AND c.channel=0 AND c.chan=0", (uid, uid))}
            ids = {i for i in ids if not self._is_blocked(db, uid, i) and not self._is_blocked(db, i, uid)}
            ids.add(uid)                                   # my other devices
            return ids

        def _story_row(self, db, sid, viewer):
            """The live story `sid` if `viewer` may see it, else a generic Fail (no hint whether it exists)."""
            try:
                sid = int(sid)
            except (TypeError, ValueError):
                raise Fail("استوری پیدا نشد")
            row = db.execute("SELECT s.*, u.banned FROM stories s JOIN users u ON u.id=s.user_id WHERE s.id=?",
                             (sid,)).fetchone()
            if (not row or row["expires_at"] <= now() or row["banned"]
                    or not self._story_can_see(db, row["user_id"], viewer, row["audience"])):
                raise Fail("استوری پیدا نشد یا منقضی شده")
            return row

        # ------------------------------------------------------------------ views
        @staticmethod
        def _story_json(row, seen, mine_reaction, counts):
            d = {"id": row["id"], "kind": row["kind"], "text": row["text"], "bg": row["bg"],
                 "created_at": row["created_at"], "expires_at": row["expires_at"], "w": row["w"], "h": row["h"],
                 "dur": row["dur"], "audience": row["audience"], "seen": seen, "my_reaction": mine_reaction}
            if counts is not None:
                d["views"], d["likes"] = counts
            return d

        def op_stories_feed(self, conn, req):
            db, uid, t = conn.db, conn.uid, now()
            rows = db.execute(
                "SELECT s.*, u.username, u.display_name, u.avatar, u.avatar_at AS uav FROM stories s "
                "JOIN users u ON u.id=s.user_id WHERE s.expires_at>? AND u.banned=0 AND u.is_bot=0 "
                "ORDER BY s.created_at ASC LIMIT 800", (t,)).fetchall()
            vis_cache, keep = {}, []
            for r in rows:
                key = (r["user_id"], r["audience"])
                if key not in vis_cache:
                    vis_cache[key] = self._story_can_see(db, r["user_id"], uid, r["audience"])
                if vis_cache[key]:
                    keep.append(r)
            ids = [r["id"] for r in keep]
            seen, counts = {}, {}
            if ids:
                q = ",".join("?" * len(ids))
                for x in db.execute(f"SELECT story_id, reaction FROM story_views WHERE user_id=? AND story_id IN ({q})",
                                    (uid, *ids)):
                    seen[x["story_id"]] = x["reaction"]
                for x in db.execute(
                        f"SELECT story_id, COUNT(*) AS n, SUM(CASE WHEN reaction IS NOT NULL THEN 1 ELSE 0 END) AS l "
                        f"FROM story_views WHERE hidden=0 AND story_id IN ({q}) GROUP BY story_id", tuple(ids)):
                    counts[x["story_id"]] = (x["n"], x["l"] or 0)
            groups = {}
            for r in keep:
                mine = r["user_id"] == uid
                g = groups.setdefault(r["user_id"], {
                    "user": {"id": r["user_id"], "username": r["username"], "display_name": r["display_name"],
                             "avatar_at": r["uav"] if r["avatar"] else 0}, "stories": []})
                g["stories"].append(self._story_json(
                    r, mine or r["id"] in seen, seen.get(r["id"]), counts.get(r["id"], (0, 0)) if mine else None))
            out = list(groups.values())
            for g in out:
                g["all_seen"] = all(s["seen"] for s in g["stories"])
                g["last_at"] = g["stories"][-1]["created_at"]
            out.sort(key=lambda g: (g["user"]["id"] != uid, g["all_seen"], -g["last_at"]))
            return {"groups": out, "ttl": common.STORY_SECONDS}

        # ------------------------------------------------------------------ posting
        def _story_post(self, db, uid, kind, text="", bg=0, audience="contacts", file_rel=None, size=None,
                        w=0, h=0, dur=0.0):
            if audience not in AUDIENCES:
                audience = "contacts"
            if not self._room_hits(("story", uid), 12, 3600):
                raise Fail("امروز خیلی استوری گذاشتی؛ کمی بعد دوباره امتحان کن")
            live = db.execute("SELECT COUNT(*) FROM stories WHERE user_id=? AND expires_at>?", (uid, now())).fetchone()[0]
            if live >= MAX_ACTIVE:
                raise Fail(f"حداکثر {MAX_ACTIVE} استوری فعال می‌تونی داشته باشی؛ یکی رو پاک کن")
            with db:
                cur = db.execute(
                    "INSERT INTO stories(user_id, kind, file_path, file_size, text, bg, audience, w, h, dur, created_at, expires_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (uid, kind, file_rel, size, text, bg, audience, w, h, dur, now(), now() + common.STORY_SECONDS))
            row = db.execute("SELECT * FROM stories WHERE id=?", (cur.lastrowid,)).fetchone()
            self.push(self._story_audience_ids(db, uid, audience), {"event": "story_new", "user_id": uid})
            return self._story_json(row, True, None, (0, 0))

        @staticmethod
        def _clean_story_text(text, limit):
            return _CTRL.sub("", str(text or "").replace("\r\n", "\n")).strip()[:limit]

        def op_story_text(self, conn, req):
            text = self._clean_story_text(req.get("text"), MAX_TEXT)
            if not text:
                raise Fail("متن استوری خالیه")
            try:
                bg = max(0, min(TEXT_BACKGROUNDS - 1, int(req.get("bg") or 0)))
            except (TypeError, ValueError):
                bg = 0
            return {"story": self._story_post(conn.db, conn.uid, "text", text=text, bg=bg,
                                              audience=str(req.get("audience") or "contacts"))}

        def story_upload(self, db, uid, kind, tmp_path, caption, audience, dur):
            """Called by the HTTP endpoint with the finished temp file. -> story json (raises Fail)."""
            user = db.execute("SELECT banned, is_bot FROM users WHERE id=?", (uid,)).fetchone()
            if user is None or user["banned"] or user["is_bot"]:
                raise Fail("این حساب نمی‌تونه استوری بذاره")
            if kind not in ("image", "video"):
                raise Fail("نوع استوری نامعتبره")
            caption = self._clean_story_text(caption, MAX_CAPTION)
            try:
                dur = max(0.0, min(float(dur or 0), 600.0))
            except (TypeError, ValueError):
                dur = 0.0
            folder = common.STORIES_DIR / str(uid)
            folder.mkdir(parents=True, exist_ok=True)
            token = secrets.token_hex(10)
            size = Path(tmp_path).stat().st_size
            w = h = 0
            if kind == "image":
                raw = Path(tmp_path).read_bytes()
                try:
                    data, w, h, ext = common.process_story_image(raw)
                except ValueError as e:
                    raise Fail(str(e))
                dest = folder / (token + ext)
                dest.write_bytes(data)
                size = len(data)
            else:
                if size > common.MAX_STORY_VIDEO:
                    raise Fail(f"حجم ویدیو بیشتر از {common.MAX_STORY_VIDEO // (1024 * 1024)} مگابایته")
                if dur > common.STORY_MAX_VIDEO_SECONDS:
                    raise Fail("ویدیوی استوری حداکثر ۳۰ ثانیه است")
                with open(tmp_path, "rb") as f:
                    ext = common.sniff_video(f.read(16))
                if not ext:
                    raise Fail("فقط ویدیوی MP4 یا WebM قبوله")
                dest = folder / (token + ext)
                shutil.move(str(tmp_path), str(dest))
            rel = f"{uid}/{dest.name}"
            try:
                return self._story_post(db, uid, kind, text=caption, audience=str(audience or "contacts"),
                                        file_rel=rel, size=size, w=w, h=h, dur=dur)
            except Exception:
                common.remove_story_file(rel)
                raise

        def story_file(self, db, uid, sid):
            """-> (path, mime) of a live story's file if `uid` may see it, else None."""
            try:
                row = self._story_row(db, sid, uid)
            except Fail:
                return None
            if row["kind"] == "text":
                return None
            p = common.resolve_story(row["file_path"])
            if p is None:
                return None
            return p, ("image/jpeg" if p.suffix == ".jpg" else "image/png" if p.suffix == ".png"
                       else "video/webm" if p.suffix == ".webm" else "video/mp4")

        # ------------------------------------------------------------------ viewing / reacting / replying
        def op_story_seen(self, conn, req):
            row = self._story_row(conn.db, req.get("story_id"), conn.uid)
            if row["user_id"] == conn.uid:
                return {}
            rr = conn.db.execute("SELECT read_receipts FROM users WHERE id=?", (conn.uid,)).fetchone()
            hidden = 0 if (rr is None or rr[0]) else 1
            with conn.db:
                conn.db.execute("INSERT OR IGNORE INTO story_views(story_id, user_id, viewed_at, hidden) VALUES (?,?,?,?)",
                                (row["id"], conn.uid, now(), hidden))
            return {}

        def op_story_viewers(self, conn, req):
            row = self._story_row(conn.db, req.get("story_id"), conn.uid)
            if row["user_id"] != conn.uid:
                raise Fail("فقط صاحب استوری می‌تونه بیننده‌ها رو ببینه")
            rows = conn.db.execute(
                "SELECT v.viewed_at, v.reaction, u.id, u.username, u.display_name, u.avatar, u.avatar_at FROM story_views v "
                "JOIN users u ON u.id=v.user_id WHERE v.story_id=? AND v.hidden=0 ORDER BY v.viewed_at DESC LIMIT 300",
                (row["id"],)).fetchall()
            return {"viewers": [{"user": {"id": r["id"], "username": r["username"], "display_name": r["display_name"],
                                          "avatar_at": r["avatar_at"] if r["avatar"] else 0},
                                 "at": r["viewed_at"], "reaction": r["reaction"]} for r in rows]}

        def op_story_react(self, conn, req):
            row = self._story_row(conn.db, req.get("story_id"), conn.uid)
            if row["user_id"] == conn.uid:
                raise Fail("به استوری خودت واکنش نمی‌شه داد")
            emoji = str(req.get("emoji") or "")
            if emoji and emoji not in REACTIONS:
                raise Fail("واکنش نامعتبر")
            if not self._room_hits(("storyreact", conn.uid), 20, 30):
                raise Fail("آروم‌تر")
            with conn.db:
                conn.db.execute(
                    "INSERT INTO story_views(story_id, user_id, viewed_at, reaction, hidden) VALUES (?,?,?,?,0) "
                    "ON CONFLICT(story_id, user_id) DO UPDATE SET reaction=excluded.reaction, "
                    "hidden=CASE WHEN excluded.reaction IS NULL THEN story_views.hidden ELSE 0 END",
                    (row["id"], conn.uid, now(), emoji or None))
            if emoji:
                me = conn.db.execute("SELECT id, display_name FROM users WHERE id=?", (conn.uid,)).fetchone()
                self.push([row["user_id"]], {"event": "story_react", "story_id": row["id"], "emoji": emoji,
                                             "user": {"id": me["id"], "display_name": me["display_name"]}})
            return {"my_reaction": emoji or None}

        def _private_chat(self, db, a, b):
            """-> (chat id, created_now) of the private chat between a and b (made if it does not exist)."""
            lo, hi = sorted((a, b))
            key = f"{lo}:{hi}"
            row = db.execute("SELECT id FROM chats WHERE pair_key=?", (key,)).fetchone()
            if row:
                return row["id"], False
            with db:
                cur = db.execute("INSERT INTO chats(kind, pair_key, created_at) VALUES ('private',?,?)", (key, now()))
                for u in {lo, hi}:
                    db.execute("INSERT INTO members(chat_id, user_id, joined_at) VALUES (?,?,?)", (cur.lastrowid, u, now()))
            return cur.lastrowid, True

        def op_story_reply(self, conn, req):
            row = self._story_row(conn.db, req.get("story_id"), conn.uid)
            if row["user_id"] == conn.uid:
                raise Fail("به استوری خودت پاسخ نمی‌شه داد")
            text = self._clean_story_text(req.get("text"), common.MAX_TEXT)
            if not text:
                raise Fail("پیام خالیه")
            self._check_send_rate(conn.uid)
            chat_id, created = self._private_chat(conn.db, conn.uid, row["user_id"])
            msg = self._store_message(conn.db, conn.uid, chat_id, text, None,
                                      meta={"story": {"id": row["id"], "kind": row["kind"]}})
            if created:
                v = self._chat_view(conn.db, chat_id, row["user_id"])
                if v:
                    self.push([row["user_id"]], {"event": "chat_added", "chat": v})
            self._announce(conn.db, chat_id, msg, conn.uid, exclude=conn)
            return {"message": msg, "chat_id": chat_id}

        # ------------------------------------------------------------------ deleting / expiry
        def _story_remove(self, db, row):
            with db:
                db.execute("DELETE FROM stories WHERE id=?", (row["id"],))
            common.remove_story_file(row["file_path"])

        def op_story_delete(self, conn, req):
            try:
                sid = int(req.get("story_id"))
            except (TypeError, ValueError):
                raise Fail("استوری پیدا نشد")
            row = conn.db.execute("SELECT * FROM stories WHERE id=? AND user_id=?", (sid, conn.uid)).fetchone()
            if not row:
                raise Fail("استوری پیدا نشد")
            self._story_remove(conn.db, row)
            self.push(self._story_audience_ids(conn.db, conn.uid, row["audience"]),
                      {"event": "story_gone", "story_id": sid, "user_id": conn.uid})
            return {}

        def sweep_stories(self):
            db = common.connect_db(self.db_path)
            try:
                rows = db.execute("SELECT * FROM stories WHERE expires_at<=?", (now(),)).fetchall()
                for r in rows:
                    self._story_remove(db, r)
                if rows:
                    log(f"stories: {len(rows)} expired story(ies) removed")
                # files left behind by a crash (no row points at them) and older than a day
                live = {r[0] for r in db.execute("SELECT file_path FROM stories WHERE file_path IS NOT NULL")}
                base = common.STORIES_DIR
                if base.is_dir():
                    for folder in base.iterdir():
                        if not folder.is_dir():
                            continue
                        for f in folder.iterdir():
                            rel = f"{folder.name}/{f.name}"
                            try:
                                if rel not in live and time.time() - f.stat().st_mtime > 86400:
                                    f.unlink()
                            except OSError:
                                pass
            finally:
                db.close()

    return StoriesMixin
