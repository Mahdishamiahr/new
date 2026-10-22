"""Bots: BotFather (the bot that makes bots) + the Bot API (HTTP, long polling).

How it fits together
* A bot is an ordinary row in `users` with is_bot=1 (nobody can log in as it: random password) plus a row in `bots`
  (owner, SHA-256 of the token, description, commands). Users find it by its exact @username (ends with "bot").
* BotFather is the built-in official bot (username BotFather). It runs inside the server: /newbot, /mybots, token,
  rename, description, commands, photo, delete - with inline buttons.
* The token looks like  <bot id>:<random>  . Only its hash is stored, so a lost token cannot be shown again:
  "new token" revokes the old one and shows the new one once.
* Bots talk to the server with HTTP:  https://HOST/bot<TOKEN>/<method>   (see BOT-API.md).
  Messages reach a bot as "updates" it fetches with getUpdates (long polling - no outgoing connections, no webhooks).
* In groups a bot only receives commands (/x), @mentions of it and replies to its own messages (like Telegram's
  privacy mode), never the whole conversation.
"""
import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time

BOT_USER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,31}$")
TOKEN_RE = re.compile(r"^(\d{1,9}):([A-Za-z0-9_-]{20,64})$")
CMD_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
URL_RE = re.compile(r"^https?://[^\s]{1,500}$", re.I)
MAX_BOTS_PER_USER = 10
UPDATE_KEEP_SECONDS = 24 * 3600
UPDATE_QUEUE_MAX = 1000
MAX_KB_ROWS, MAX_KB_COLS, MAX_KB_TOTAL = 8, 8, 40
BOT_MAX_FILE = 20 * 1024 * 1024         # file sent by a bot (raw body / base64)
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

BF_COMMANDS = [("newbot", "ساخت ربات جدید"), ("mybots", "ربات‌های من"), ("token", "توکن جدید"),
               ("setname", "تغییر نام"), ("setdescription", "تغییر توضیح"), ("setcommands", "تنظیم دستورها"),
               ("setuserpic", "تغییر عکس"), ("deletebot", "حذف ربات"), ("cancel", "لغو"), ("help", "راهنما")]

WELCOME = ("سلام! من BotFather‌ام 🤖\nبا من ربات می‌سازی و مدیریت می‌کنی؛ بعدش می‌تونی با API (فایل BOT-API.md) "
           "هر کاری دلت خواست باهاشون بکنی.\n\nیکی از گزینه‌ها رو بزن:")

API_HELP = ("📖 راهنمای سریع API\n\n"
            "۱) با /newbot یه ربات بساز و توکن بگیر.\n"
            "۲) آدرس API:  https://آدرس-سرور/bot<TOKEN>/<متد>\n\n"
            "متدها: getMe · getUpdates · sendMessage · sendDocument · sendVoice · editMessageText · "
            "editMessageReplyMarkup · deleteMessage · answerCallbackQuery · sendChatAction · getChat · getFile · "
            "setMyCommands · setMyDescription\n\n"
            "مثال:\n`curl \"https://آدرس-سرور/bot<TOKEN>/getUpdates?timeout=20\"`\n"
            "`curl -X POST \"https://آدرس-سرور/bot<TOKEN>/sendMessage\" -H \"Content-Type: application/json\" "
            "-d '{\"chat_id\":1,\"text\":\"سلام\"}'`\n\n"
            "• پیام‌ها فقط با getUpdates (long polling) می‌رسن؛ وب‌هوک نداریم.\n"
            "• تو گروه‌ها ربات فقط دستورها (/...)، منشن و پاسخ به پیام‌های خودش رو می‌بینه.\n"
            "• دکمه‌های شیشه‌ای: reply_markup با inline_keyboard؛ وقتی کاربر بزنه، یه callback_query می‌رسه.\n"
            "• مستندات کامل و نمونه کد پایتون: BOT-API.md و examples/echo_bot.py کنار سرور.")


class ApiError(Exception):
    def __init__(self, code, desc):
        super().__init__(desc)
        self.code, self.desc = code, desc


def _line(text, limit):
    return " ".join(_CTRL.sub("", str(text or "")).split())[:limit]


def make_bots(Fail, common, log):
    now = common.now
    NOTSET = object()

    class BotsMixin:
        # ================================================================== setup
        def _bots_init(self):
            self.bot_cv = threading.Condition()
            self._cq = {}                       # callback_query id -> who pressed what (answer must come within 2 min)
            self._cq_lock = threading.Lock()
            self._bot_touch = {}
            self.botfather_id = None

        def _ensure_botfather(self, db):
            row = db.execute("SELECT id, is_bot FROM users WHERE username='BotFather' COLLATE NOCASE").fetchone()
            if row is not None and not row["is_bot"]:      # a normal user took the name before bots existed
                with db:
                    db.execute("UPDATE users SET username=? WHERE id=?", (f"BotFather_user{row['id']}", row["id"]))
                log("renamed an old normal account called BotFather")
                row = None
            if row is None:
                ps, ph = common.hash_secret(secrets.token_urlsafe(24))
                ss, sh = common.hash_secret(secrets.token_urlsafe(24))
                with db:
                    cur = db.execute(
                        "INSERT INTO users(username, display_name, bio, pw_salt, pw_hash, sec_question, sec_salt, sec_hash,"
                        " created_at, is_bot) VALUES ('BotFather','BotFather','ساخت و مدیریت ربات',?,?,'-',?,?,?,1)",
                        (ps, ph, ss, sh, now()))
                uid = cur.lastrowid
            else:
                uid = row["id"]
            with db:
                db.execute("INSERT OR IGNORE INTO bots(user_id, owner_id, official, description, commands, created_at) "
                           "VALUES (?,NULL,1,?,?,?)",
                           (uid, "ربات رسمی برای ساخت و مدیریت ربات‌ها", json.dumps(
                               [{"command": c, "description": d} for c, d in BF_COMMANDS], ensure_ascii=False), now()))
            self.botfather_id = uid
            return uid

        # ================================================================== helpers
        def _bot_info(self, db, uid):
            b = db.execute("SELECT description, commands, official FROM bots WHERE user_id=?", (uid,)).fetchone()
            if not b:
                return {"description": "", "commands": [], "official": False}
            try:
                cmds = json.loads(b["commands"] or "[]")
            except ValueError:
                cmds = []
            return {"description": b["description"], "commands": cmds, "official": bool(b["official"])}

        @staticmethod
        def _bot_user_json(u):
            return {"id": u["id"], "is_bot": bool(u["is_bot"]), "username": u["username"], "first_name": u["display_name"]}

        def _bot_chat_json(self, db, chat_id, for_bot=None):
            c = db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
            if not c:
                return {"id": chat_id, "type": "private"}
            d = {"id": c["id"], "type": "private" if c["kind"] == "private" else "group", "title": c["title"]}
            if c["kind"] == "private" and for_bot:
                o = db.execute("SELECT u.username, u.display_name FROM members m JOIN users u ON u.id=m.user_id "
                               "WHERE m.chat_id=? AND m.user_id!=?", (chat_id, for_bot)).fetchone()
                if o:
                    d["username"], d["first_name"] = o["username"], o["display_name"]
            elif c["username"]:
                d["username"] = c["username"]
            return d

        def _bot_msg_json(self, db, msg, for_bot=None):
            """A message as a bot sees it (Telegram-like field names)."""
            u = db.execute("SELECT id, username, display_name, is_bot FROM users WHERE id=?", (msg["sender_id"],)).fetchone()
            d = {"message_id": msg["id"], "date": msg["created_at"], "chat": self._bot_chat_json(db, msg["chat_id"], for_bot),
                 "from": self._bot_user_json(u) if u else None, "text": msg.get("body") or ""}
            if msg.get("reply"):
                d["reply_to_message_id"] = msg["reply"]["id"]
            if msg.get("file"):
                f = msg["file"]
                d["file"] = {"name": f.get("name"), "size": f.get("size"), "kind": f.get("kind")}
                if f.get("voice"):
                    d["file"].update(voice=True, duration=f.get("dur"))
            if msg.get("markup"):
                d["reply_markup"] = {"inline_keyboard": msg["markup"]}
            return d

        @staticmethod
        def _bot_markup(raw):
            """Validate/normalise reply_markup -> list of rows, or None (= no buttons)."""
            if raw in (None, "", "null"):
                return None
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except ValueError:
                    raise Fail("reply_markup باید JSON معتبر باشه")
            if not isinstance(raw, dict):
                raise Fail("reply_markup نامعتبره")
            kb = raw.get("inline_keyboard")
            if not kb:
                return None
            if not isinstance(kb, list) or len(kb) > MAX_KB_ROWS:
                raise Fail(f"حداکثر {MAX_KB_ROWS} ردیف دکمه")
            out, total = [], 0
            for row in kb:
                if not isinstance(row, list) or not row or len(row) > MAX_KB_COLS:
                    raise Fail(f"هر ردیف ۱ تا {MAX_KB_COLS} دکمه داشته باشه")
                r = []
                for b in row:
                    if not isinstance(b, dict):
                        raise Fail("دکمه نامعتبره")
                    text = _line(b.get("text"), 64)
                    if not text:
                        raise Fail("متن دکمه خالیه")
                    cb, url = b.get("callback_data"), b.get("url")
                    if cb is not None:
                        cb = str(cb)
                        if not 1 <= len(cb.encode("utf-8")) <= 64:
                            raise Fail("callback_data باید ۱ تا ۶۴ بایت باشه")
                        r.append({"text": text, "callback_data": cb})
                    elif url:
                        url = str(url).strip()
                        if not URL_RE.match(url):
                            raise Fail("آدرس دکمه باید با http:// یا https:// شروع بشه")
                        r.append({"text": text, "url": url})
                    else:
                        raise Fail("هر دکمه باید callback_data یا url داشته باشه")
                    total += 1
                out.append(r)
            if total > MAX_KB_TOTAL:
                raise Fail(f"حداکثر {MAX_KB_TOTAL} دکمه")
            return out

        def _bot_member(self, db, bot_uid, chat_id):
            try:
                chat_id = int(chat_id)
            except (TypeError, ValueError):
                raise Fail("chat_id نامعتبره")
            if not db.execute("SELECT 1 FROM members WHERE chat_id=? AND user_id=?", (chat_id, bot_uid)).fetchone():
                raise ApiError(403, "ربات عضو این چت نیست")
            return chat_id

        # ================================================================== sending as a bot
        def _bot_send(self, db, bot_uid, chat_id, text, reply_to=None, markup=None, secret=False,
                      fname=None, raw=None, extra_meta=None):
            chat_id = self._bot_member(db, bot_uid, chat_id)
            if bot_uid != self.botfather_id and not self._room_hits(("botsend", bot_uid), 40, 10):
                raise ApiError(429, "خیلی سریع پیام می‌فرستی؛ کمی صبر کن")
            text = _CTRL.sub("", str(text or "").replace("\r\n", "\n")).strip()
            if len(text) > common.MAX_TEXT:
                raise Fail(f"متن خیلی طولانیه (حداکثر {common.MAX_TEXT} کاراکتر)")
            meta = dict(extra_meta or {})
            if markup:
                meta["markup"] = markup
            if secret:
                meta["secret"] = True
            msg = self._store_message(db, bot_uid, chat_id, text, reply_to, fname, raw=raw, meta=meta or None)
            self._announce(db, chat_id, msg, bot_uid)
            return msg

        def _bot_edit(self, db, bot_uid, mid, text=NOTSET, markup=NOTSET):
            row = db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
            if not row or row["deleted"] or row["sender_id"] != bot_uid or row["kind"] == "system":
                raise Fail("پیام پیدا نشد یا مال این ربات نیست")
            meta = {}
            if row["meta"]:
                try:
                    meta = json.loads(row["meta"])
                except ValueError:
                    meta = {}
            sets, vals = [], []
            if text is not NOTSET:
                text = _CTRL.sub("", str(text or "").replace("\r\n", "\n")).strip()
                if not text and row["kind"] == "text":
                    raise Fail("متن خالیه")
                if len(text) > common.MAX_TEXT:
                    raise Fail("متن خیلی طولانیه")
                sets.append("body=?")
                vals.append(text)
            if markup is not NOTSET:
                if markup:
                    meta["markup"] = markup
                else:
                    meta.pop("markup", None)
                sets.append("meta=?")
                vals.append(json.dumps(meta, ensure_ascii=False) if meta else None)
            if sets:
                with db:
                    db.execute(f"UPDATE messages SET {', '.join(sets)} WHERE id=?", (*vals, mid))
            msg = self._msg_by_id(db, mid)
            self.push(self._member_ids(db, row["chat_id"]), {"event": "message_updated", "message": msg})
            return msg

        def _bot_delete_message(self, db, bot_uid, mid):
            row = db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
            if not row or row["deleted"] or row["sender_id"] != bot_uid:
                raise Fail("پیام پیدا نشد یا مال این ربات نیست")
            common.remove_upload(row["file_path"], db, row["id"])
            with db:
                db.execute("UPDATE messages SET deleted=1, body='', file_path=NULL, meta=NULL WHERE id=?", (mid,))
                db.execute("UPDATE chats SET pinned_id=NULL WHERE id=? AND pinned_id=?", (row["chat_id"], mid))
                db.execute("DELETE FROM reactions WHERE message_id=?", (mid,))
            msg = self._msg_by_id(db, mid)
            self.push(self._member_ids(db, row["chat_id"]), {"event": "message_updated", "message": msg})
            self._push_chat_update(db, row["chat_id"])

        # ================================================================== updates (getUpdates queue)
        def _bot_enqueue(self, db, bot_uid, payload):
            with db:
                db.execute("INSERT INTO bot_updates(bot_id, payload, created_at) VALUES (?,?,?)",
                           (bot_uid, json.dumps(payload, ensure_ascii=False), now()))
                db.execute("DELETE FROM bot_updates WHERE bot_id=? AND id <= "
                           "(SELECT id FROM bot_updates WHERE bot_id=? ORDER BY id DESC LIMIT 1 OFFSET ?)",
                           (bot_uid, bot_uid, UPDATE_QUEUE_MAX))
            with self.bot_cv:
                self.bot_cv.notify_all()

        def _bot_wants(self, db, bot, chat, msg):
            """Private chat: everything. Group: commands, @mentions and replies to the bot's own messages."""
            if chat["kind"] == "private":
                return True
            body = (msg.get("body") or "").strip()
            if body.startswith("/"):
                tag = body.split()[0]
                return "@" not in tag or tag.lower().endswith("@" + bot["username"].lower())
            if "@" + bot["username"].lower() in body.lower():
                return True
            if msg.get("reply") and msg.get("reply_to"):
                r = db.execute("SELECT sender_id FROM messages WHERE id=?", (msg["reply_to"],)).fetchone()
                return bool(r and r["sender_id"] == bot["user_id"])
            return False

        def _bot_on_message(self, db, chat_id, msg):
            """Called from _announce for every new message: BotFather answers in-process, other bots get an update."""
            if not msg or msg.get("kind") == "system" or msg.get("sender_id") is None:
                return
            sender = db.execute("SELECT is_bot FROM users WHERE id=?", (msg["sender_id"],)).fetchone()
            if not sender or sender["is_bot"]:                  # bots never talk to bots (no loops)
                return
            bots = db.execute(
                "SELECT b.*, u.username FROM members m JOIN bots b ON b.user_id=m.user_id JOIN users u ON u.id=b.user_id "
                "WHERE m.chat_id=? AND m.user_id!=? AND u.banned=0", (chat_id, msg["sender_id"])).fetchall()
            if not bots:
                return
            chat = db.execute("SELECT kind FROM chats WHERE id=?", (chat_id,)).fetchone()
            for b in bots:
                if not self._bot_wants(db, b, chat, msg):
                    continue
                if b["official"]:
                    threading.Thread(target=self._botfather_thread, args=(chat_id, msg), daemon=True).start()
                else:
                    self._bot_enqueue(db, b["user_id"], {"message": self._bot_msg_json(db, msg, b["user_id"])})

        # ================================================================== button presses (from the web/Android app)
        def op_bot_callback(self, conn, req):
            try:
                mid = int(req.get("message_id"))
            except (TypeError, ValueError):
                raise Fail("پیام نامعتبره")
            data = str(req.get("data") or "")
            if not 1 <= len(data.encode("utf-8")) <= 64:
                raise Fail("دکمه نامعتبره")
            if not self._room_hits(("botcb", conn.uid), 12, 5):
                raise Fail("آروم‌تر دکمه بزن")
            row = conn.db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
            if not row or row["deleted"]:
                raise Fail("پیام پیدا نشد")
            self._require_member(conn.db, row["chat_id"], conn.uid)
            bot = conn.db.execute("SELECT b.*, u.username FROM bots b JOIN users u ON u.id=b.user_id "
                                  "WHERE b.user_id=? AND u.banned=0", (row["sender_id"],)).fetchone()
            if not bot:
                raise Fail("این دکمه دیگه کار نمی‌کنه")
            try:
                meta = json.loads(row["meta"] or "{}")
            except ValueError:
                meta = {}
            if not any(b.get("callback_data") == data for r in (meta.get("markup") or []) for b in r):
                raise Fail("این دکمه دیگه کار نمی‌کنه")        # only buttons the bot really put there can be pressed
            if bot["official"]:
                self._botfather_callback(conn.db, conn.uid, row, data)
                return {}
            cqid = "cq" + secrets.token_hex(8)
            with self._cq_lock:
                t = time.time()
                for k in [k for k, v in self._cq.items() if v["exp"] < t]:
                    del self._cq[k]
                self._cq[cqid] = {"bot": bot["user_id"], "user": conn.uid, "exp": t + 120}
            u = conn.db.execute("SELECT id, username, display_name, is_bot FROM users WHERE id=?", (conn.uid,)).fetchone()
            msg = self._msg_by_id(conn.db, mid)
            self._bot_enqueue(conn.db, bot["user_id"], {"callback_query": {
                "id": cqid, "from": self._bot_user_json(u), "data": data,
                "message": self._bot_msg_json(conn.db, msg, bot["user_id"])}})
            return {}

        # ================================================================== Bot API (HTTP)
        def _bot_by_token(self, db, token):
            m = TOKEN_RE.match(str(token or ""))
            if not m:
                return None
            row = db.execute("SELECT b.*, u.username, u.display_name, u.banned, u.is_bot FROM bots b "
                             "JOIN users u ON u.id=b.user_id WHERE b.user_id=? AND b.official=0", (int(m.group(1)),)).fetchone()
            if not row or not row["token_hash"] or row["banned"]:
                return None
            good = hashlib.sha256(m.group(2).encode()).digest()
            return row if hmac.compare_digest(good, bytes(row["token_hash"])) else None

        def _bot_auth(self, db, ip, token):
            bad = self.ip_hits[("botbad", ip)]
            t = time.time()
            while bad and bad[0] < t - 60:
                bad.popleft()
            if len(bad) >= 20:
                raise ApiError(429, "توکن اشتباه زیاد فرستادی؛ یه دقیقه صبر کن")
            bot = self._bot_by_token(db, token)
            if bot is None:
                bad.append(t)
                raise ApiError(401, "توکن نامعتبره")
            if not self._room_hits(("botapi", bot["user_id"]), 150, 10):
                raise ApiError(429, "درخواست‌های خیلی زیاد؛ کمی صبر کن")
            if t - self._bot_touch.get(bot["user_id"], 0) > 60:
                self._bot_touch[bot["user_id"]] = t
                with db:
                    db.execute("UPDATE bots SET last_api_at=? WHERE user_id=?", (now(), bot["user_id"]))
            return bot

        def bot_api(self, ip, token, method, p, raw=None):
            """-> (http status, json). `p` = parameters (query + JSON/form body), `raw` = raw request body if any."""
            db = None
            try:
                db = common.connect_db(self.db_path)
                bot = self._bot_auth(db, ip, token)
                handler = getattr(self, "_bapi_" + method, None) if re.fullmatch(r"[A-Za-z]{3,40}", method or "") else None
                if handler is None:
                    raise ApiError(404, f"متد {method} وجود نداره")
                p = dict(p or {})
                p["_token"] = str(token)
                return 200, {"ok": True, "result": handler(db, bot, p, raw)}
            except ApiError as e:
                return e.code, {"ok": False, "error_code": e.code, "description": e.desc}
            except Fail as e:
                return 400, {"ok": False, "error_code": 400, "description": str(e)}
            except Exception as e:
                log("bot api error:", method, repr(e))
                return 500, {"ok": False, "error_code": 500, "description": "خطای داخلی سرور"}
            finally:
                if db is not None:
                    db.close()

        @staticmethod
        def _pint(p, key, required=True):
            v = p.get(key)
            if v in (None, ""):
                if required:
                    raise Fail(f"پارامتر {key} لازمه")
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                raise Fail(f"پارامتر {key} باید عدد باشه")

        def _bapi_getMe(self, db, bot, p, raw):
            info = self._bot_info(db, bot["user_id"])
            return {"id": bot["user_id"], "is_bot": True, "username": bot["username"], "first_name": bot["display_name"],
                    "description": info["description"], "can_join_groups": True, "supports_inline_queries": False}

        def _bapi_getUpdates(self, db, bot, p, raw):
            offset = self._pint(p, "offset", False) or 0
            limit = max(1, min(100, self._pint(p, "limit", False) or 100))
            timeout = max(0, min(25, self._pint(p, "timeout", False) or 0))
            uid = bot["user_id"]
            if offset > 0:                                   # confirms everything before `offset`
                with db:
                    db.execute("DELETE FROM bot_updates WHERE bot_id=? AND id<?", (uid, offset))
            deadline = time.time() + timeout
            while True:
                rows = db.execute("SELECT id, payload FROM bot_updates WHERE bot_id=? AND id>=? ORDER BY id LIMIT ?",
                                  (uid, offset, limit)).fetchall()
                left = deadline - time.time()
                if rows or left <= 0 or self._stop.is_set():
                    break
                with self.bot_cv:
                    self.bot_cv.wait(timeout=min(1.0, left))
            out = []
            for r in rows:
                try:
                    d = json.loads(r["payload"])
                except ValueError:
                    continue
                d["update_id"] = r["id"]
                out.append(d)
            return out

        def _bapi_sendMessage(self, db, bot, p, raw):
            reply = self._pint(p, "reply_to_message_id", False)
            msg = self._bot_send(db, bot["user_id"], p.get("chat_id"), p.get("text"), reply_to=reply,
                                 markup=self._bot_markup(p.get("reply_markup")))
            return self._bot_msg_json(db, msg, bot["user_id"])

        def _bot_send_file(self, db, bot, p, raw, voice=False):
            data = raw
            if data is None:
                b64 = p.get("file_base64") or p.get("file")
                if not b64:
                    raise Fail("فایل داده نشده؛ بدنه‌ی خام یا file_base64 بفرست")
                try:
                    data = base64.b64decode(str(b64), validate=True)
                except (binascii.Error, ValueError):
                    raise Fail("file_base64 معتبر نیست")
            if not data:
                raise Fail("فایل خالیه")
            if len(data) > BOT_MAX_FILE:
                raise Fail(f"حجم فایل بیشتر از {BOT_MAX_FILE // (1024 * 1024)} مگابایته")
            if not common.disk_ok(len(data)):
                raise ApiError(507, "فضای دیسک سرور تقریباً پره")
            fname = self._safe_filename(p.get("file_name") or p.get("filename") or ("voice.ogg" if voice else "file"))
            meta = None
            if voice:
                try:
                    dur = float(p.get("duration") or 0)
                except (TypeError, ValueError):
                    dur = 0.0
                meta = self._voice_meta(fname, {"dur": dur, "wave": ""})
            msg = self._bot_send(db, bot["user_id"], p.get("chat_id"), p.get("caption"),
                                 reply_to=self._pint(p, "reply_to_message_id", False),
                                 markup=self._bot_markup(p.get("reply_markup")),
                                 fname=fname, raw=data, extra_meta=meta)
            return self._bot_msg_json(db, msg, bot["user_id"])

        def _bapi_sendDocument(self, db, bot, p, raw):
            return self._bot_send_file(db, bot, p, raw)

        _bapi_sendPhoto = _bapi_sendVideo = _bapi_sendAudio = _bapi_sendDocument

        def _bapi_sendVoice(self, db, bot, p, raw):
            return self._bot_send_file(db, bot, p, raw, voice=True)

        def _own_msg(self, db, bot, p):
            chat_id = self._bot_member(db, bot["user_id"], p.get("chat_id"))
            mid = self._pint(p, "message_id")
            r = db.execute("SELECT chat_id FROM messages WHERE id=?", (mid,)).fetchone()
            if not r or r["chat_id"] != chat_id:
                raise ApiError(404, "پیام پیدا نشد")
            return mid

        def _bapi_editMessageText(self, db, bot, p, raw):
            mid = self._own_msg(db, bot, p)
            markup = self._bot_markup(p["reply_markup"]) if "reply_markup" in p else NOTSET
            msg = self._bot_edit(db, bot["user_id"], mid, text=p.get("text"), markup=markup)
            return self._bot_msg_json(db, msg, bot["user_id"])

        def _bapi_editMessageReplyMarkup(self, db, bot, p, raw):
            mid = self._own_msg(db, bot, p)
            msg = self._bot_edit(db, bot["user_id"], mid, markup=self._bot_markup(p.get("reply_markup")))
            return self._bot_msg_json(db, msg, bot["user_id"])

        def _bapi_deleteMessage(self, db, bot, p, raw):
            self._bot_delete_message(db, bot["user_id"], self._own_msg(db, bot, p))
            return True

        def _bapi_answerCallbackQuery(self, db, bot, p, raw):
            cqid = str(p.get("callback_query_id") or "")
            with self._cq_lock:
                cq = self._cq.get(cqid)
                if not cq or cq["bot"] != bot["user_id"] or cq["exp"] < time.time():
                    raise Fail("این callback_query منقضی شده یا مال این ربات نیست")
                del self._cq[cqid]
            text = _line(p.get("text"), 200)
            alert = str(p.get("show_alert")).lower() in ("1", "true")
            if text:
                self.push([cq["user"]], {"event": "bot_answer", "text": text, "alert": alert, "bot": bot["display_name"]})
            return True

        def _bapi_sendChatAction(self, db, bot, p, raw):
            chat_id = self._bot_member(db, bot["user_id"], p.get("chat_id"))
            action = "voice" if str(p.get("action")) in ("record_voice", "voice") else "text"
            self.push(self._member_ids(db, chat_id), {"event": "typing", "chat_id": chat_id,
                                                      "user_id": bot["user_id"], "action": action})
            return True

        def _bapi_getChat(self, db, bot, p, raw):
            chat_id = self._bot_member(db, bot["user_id"], p.get("chat_id"))
            d = self._bot_chat_json(db, chat_id, bot["user_id"])
            d["members_count"] = db.execute("SELECT COUNT(*) FROM members WHERE chat_id=?", (chat_id,)).fetchone()[0]
            return d

        def _bapi_getFile(self, db, bot, p, raw):
            mid = self._pint(p, "message_id")
            row = db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
            if not row or row["deleted"] or row["kind"] != "file":
                raise ApiError(404, "فایل پیدا نشد")
            self._bot_member(db, bot["user_id"], row["chat_id"])
            return {"file_name": row["file_name"], "file_size": row["file_size"],
                    "download_path": f"/file/bot{p.get('_token', '<TOKEN>')}/{mid}"}

        def bot_file(self, ip, token, mid):
            """-> (path, file name) of a file in a chat the bot belongs to, else None. (GET /file/bot<TOKEN>/<message id>)"""
            db = common.connect_db(self.db_path)
            try:
                bot = self._bot_auth(db, ip, token)
                row = db.execute("SELECT * FROM messages WHERE id=?", (int(mid),)).fetchone()
                if not row or row["deleted"] or row["kind"] != "file":
                    return None
                if not db.execute("SELECT 1 FROM members WHERE chat_id=? AND user_id=?",
                                  (row["chat_id"], bot["user_id"])).fetchone():
                    return None
                path = common.resolve_upload(row["file_path"])
                return (path, row["file_name"]) if path else None
            except (ApiError, ValueError):
                return None
            finally:
                db.close()

        @staticmethod
        def _parse_commands(raw):
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except ValueError:
                    raise Fail("commands باید JSON معتبر باشه")
            if not isinstance(raw, list) or len(raw) > 50:
                raise Fail("commands باید لیست حداکثر ۵۰ تایی باشه")
            out = []
            for c in raw:
                if not isinstance(c, dict):
                    raise Fail("هر دستور باید یک شیء باشه")
                name = str(c.get("command") or "").lstrip("/").lower()
                if not CMD_RE.match(name):
                    raise Fail(f"دستور «{name}» نامعتبره (حرف کوچک انگلیسی، عدد و _ ؛ حداکثر ۳۲)")
                out.append({"command": name, "description": _line(c.get("description"), 64)})
            return out

        def _bot_save_commands(self, db, bot_uid, cmds):
            with db:
                db.execute("UPDATE bots SET commands=? WHERE user_id=?", (json.dumps(cmds, ensure_ascii=False), bot_uid))
            self._broadcast_profile(db, bot_uid)

        def _bapi_setMyCommands(self, db, bot, p, raw):
            self._bot_save_commands(db, bot["user_id"], self._parse_commands(p.get("commands")))
            return True

        def _bapi_getMyCommands(self, db, bot, p, raw):
            return self._bot_info(db, bot["user_id"])["commands"]

        def _bapi_setMyDescription(self, db, bot, p, raw):
            desc = _CTRL.sub("", str(p.get("description") or "")).strip()[:200]
            with db:
                db.execute("UPDATE bots SET description=? WHERE user_id=?", (desc, bot["user_id"]))
            self._broadcast_profile(db, bot["user_id"])
            return True

        def _bapi_setWebhook(self, db, bot, p, raw):
            raise Fail("وب‌هوک پشتیبانی نمی‌شه؛ از getUpdates (long polling) استفاده کن")

        def _bapi_deleteWebhook(self, db, bot, p, raw):
            return True

        def _bapi_getWebhookInfo(self, db, bot, p, raw):
            n = db.execute("SELECT COUNT(*) FROM bot_updates WHERE bot_id=?", (bot["user_id"],)).fetchone()[0]
            return {"url": "", "pending_update_count": n}

        def sweep_bots(self):
            db = common.connect_db(self.db_path)
            try:
                with db:
                    db.execute("DELETE FROM bot_updates WHERE created_at<?", (now() - UPDATE_KEEP_SECONDS,))
                    db.execute("DELETE FROM botfather_state WHERE updated_at<?", (now() - 3600,))
            finally:
                db.close()

        # ================================================================== creating / changing bots
        def _bot_new_token(self, db, bot_uid):
            token = f"{bot_uid}:{secrets.token_urlsafe(32)}"
            with db:
                db.execute("UPDATE bots SET token_hash=? WHERE user_id=?",
                           (hashlib.sha256(token.split(":", 1)[1].encode()).digest(), bot_uid))
            return token

        def _bot_create(self, db, owner_id, name, username):
            ps, ph = common.hash_secret(secrets.token_urlsafe(24))          # random password nobody knows: no login
            ss, sh = common.hash_secret(secrets.token_urlsafe(24))
            try:
                with db:
                    cur = db.execute(
                        "INSERT INTO users(username, display_name, bio, pw_salt, pw_hash, sec_question, sec_salt, sec_hash,"
                        " created_at, is_bot) VALUES (?,?,'',?,?,'-',?,?,?,1)", (username, name, ps, ph, ss, sh, now()))
                    uid = cur.lastrowid
                    db.execute("INSERT INTO bots(user_id, owner_id, description, commands, created_at) VALUES (?,?,'','[]',?)",
                               (uid, owner_id, now()))
            except sqlite3.IntegrityError:
                raise Fail("این نام کاربری قبلاً گرفته شده")
            return uid, self._bot_new_token(db, uid)

        def _bot_delete(self, db, bot_uid):
            with db:
                db.execute("DELETE FROM bots WHERE user_id=?", (bot_uid,))
                db.execute("UPDATE users SET banned=1, username=?, display_name='ربات حذف‌شده', bio='', avatar=NULL, avatar_at=0 "
                           "WHERE id=?", (f"deleted_bot_{bot_uid}", bot_uid))
                db.execute("DELETE FROM members WHERE user_id=? AND chat_id IN (SELECT id FROM chats WHERE kind='group')",
                           (bot_uid,))
            self._broadcast_profile(db, bot_uid)

        # ================================================================== BotFather
        def _bf_state(self, db, uid):
            r = db.execute("SELECT state, data FROM botfather_state WHERE user_id=?", (uid,)).fetchone()
            if not r:
                return None, {}
            try:
                return r["state"], json.loads(r["data"] or "{}")
            except ValueError:
                return r["state"], {}

        def _bf_set(self, db, uid, state, data=None):
            with db:
                db.execute("INSERT INTO botfather_state(user_id, state, data, updated_at) VALUES (?,?,?,?) "
                           "ON CONFLICT(user_id) DO UPDATE SET state=excluded.state, data=excluded.data, updated_at=excluded.updated_at",
                           (uid, state, json.dumps(data or {}, ensure_ascii=False), now()))

        def _bf_clear(self, db, uid):
            with db:
                db.execute("DELETE FROM botfather_state WHERE user_id=?", (uid,))

        def _bf_reply(self, db, chat_id, text, markup=None, secret=False):
            try:
                msg = self._bot_send(db, self.botfather_id, chat_id, text, markup=markup, secret=secret)
                return msg
            except (Fail, ApiError) as e:                  # e.g. the user blocked BotFather
                log("botfather reply failed:", str(e))
                return None

        def _bf_toast(self, uid, text):
            self.push([uid], {"event": "bot_answer", "text": text, "alert": False, "bot": "BotFather"})

        def _bf_own_bot(self, db, uid, bot_id):
            try:
                bot_id = int(bot_id)
            except (TypeError, ValueError):
                raise Fail("ربات نامعتبره")
            r = db.execute("SELECT b.*, u.username, u.display_name FROM bots b JOIN users u ON u.id=b.user_id "
                           "WHERE b.user_id=? AND b.owner_id=? AND b.official=0 AND u.banned=0", (bot_id, uid)).fetchone()
            if not r:
                raise Fail("این ربات پیدا نشد (شاید حذف شده)")
            return r

        def _bf_my_bots(self, db, uid):
            return db.execute("SELECT b.*, u.username, u.display_name FROM bots b JOIN users u ON u.id=b.user_id "
                              "WHERE b.owner_id=? AND b.official=0 AND u.banned=0 ORDER BY b.created_at", (uid,)).fetchall()

        @staticmethod
        def _btn(text, data):
            return {"text": text, "callback_data": data}

        def _bf_main_menu(self):
            return [[self._btn("➕ ساخت ربات جدید", "new")], [self._btn("📋 ربات‌های من", "list")],
                    [self._btn("📖 راهنمای API", "api")]]

        def _bf_list_view(self, db, uid, action=None, prompt=None):
            bots = self._bf_my_bots(db, uid)
            if not bots:
                return "هنوز رباتی نساختی.", [[self._btn("➕ ساخت ربات جدید", "new")]]
            rows = [[self._btn(f"🤖 {b['display_name']} (@{b['username']})", f"{action or 'bot'}:{b['user_id']}")] for b in bots]
            rows.append([self._btn("⬅️ برگشت", "home")])
            return prompt or "ربات‌های تو:", rows

        def _bf_bot_view(self, db, bot):
            info = self._bot_info(db, bot["user_id"])
            text = (f"🤖 {bot['display_name']}\n@{bot['username']}\n\nتوضیح: {info['description'] or '—'}\n"
                    f"دستورها: {len(info['commands'])} تا")
            rows = [[self._btn("🔑 توکن جدید", f"tok:{bot['user_id']}"), self._btn("✏️ تغییر نام", f"nm:{bot['user_id']}")],
                    [self._btn("📝 توضیح", f"ds:{bot['user_id']}"), self._btn("📋 دستورها", f"cm:{bot['user_id']}")],
                    [self._btn("🖼 عکس", f"pic:{bot['user_id']}"), self._btn("🗑 حذف", f"del:{bot['user_id']}")],
                    [self._btn("⬅️ برگشت", "list")]]
            return text, rows

        def _bf_token_text(self, bot, token):
            return (f"🔑 توکن ربات «{bot['display_name']}» (@{bot['username']}):\n\n`{token}`\n\n"
                    "این توکن فقط همین یک بار نشون داده می‌شه و مثل رمز ربات توئه؛ به کسی نده. "
                    "اگه گم شد یا لو رفت، از «توکن جدید» توکن قبلی رو باطل کن.\nراهنمای API: /help")

        def _botfather_thread(self, chat_id, msg):
            db = None
            try:
                time.sleep(0.15)
                self.push([msg["sender_id"]], {"event": "typing", "chat_id": chat_id,
                                               "user_id": self.botfather_id, "action": "text"})
                time.sleep(0.35)             # also keeps the answer behind the sender's own message in the chat
                db = common.connect_db(self.db_path)
                if self._room_hits(("bf", msg["sender_id"]), 30, 60):
                    self._botfather_message(db, chat_id, msg)
            except Exception as e:
                log("botfather error:", repr(e))
            finally:
                if db is not None:
                    db.close()

        def _botfather_message(self, db, chat_id, msg):
            uid = msg["sender_id"]
            state, data = self._bf_state(db, uid)
            text = (msg.get("body") or "").strip()
            if msg.get("kind") == "file":
                if state == "set_pic":
                    return self._bf_take_pic(db, uid, chat_id, msg, data)
                return self._bf_reply(db, chat_id, "الان فایلی نمی‌خوام؛ /help رو بزن.")
            cmd = text.split()[0].lower().split("@")[0] if text.startswith("/") else ""
            if cmd == "/cancel":
                self._bf_clear(db, uid)
                return self._bf_reply(db, chat_id, "لغو شد.", self._bf_main_menu())
            if cmd in ("/start", "/help"):
                self._bf_clear(db, uid)
                if cmd == "/help":
                    return self._bf_reply(db, chat_id, API_HELP, self._bf_main_menu())
                return self._bf_reply(db, chat_id, WELCOME, self._bf_main_menu())
            if cmd == "/newbot":
                return self._bf_new_start(db, uid, chat_id)
            if cmd == "/mybots":
                self._bf_clear(db, uid)
                t, rows = self._bf_list_view(db, uid)
                return self._bf_reply(db, chat_id, t, rows)
            picks = {"/token": ("tok", "توکن کدوم ربات عوض بشه؟"), "/setname": ("nm", "اسم کدوم ربات عوض بشه؟"),
                     "/setdescription": ("ds", "توضیح کدوم ربات؟"), "/setcommands": ("cm", "دستورهای کدوم ربات؟"),
                     "/setuserpic": ("pic", "عکس کدوم ربات؟"), "/deletebot": ("del", "کدوم ربات حذف بشه؟")}
            if cmd in picks:
                self._bf_clear(db, uid)
                act, prompt = picks[cmd]
                t, rows = self._bf_list_view(db, uid, act, prompt)
                return self._bf_reply(db, chat_id, t, rows)
            if cmd:
                return self._bf_reply(db, chat_id, "این دستور رو نمی‌شناسم؛ /help", self._bf_main_menu())
            if state:
                return self._bf_state_text(db, uid, chat_id, state, data, text)
            return self._bf_reply(db, chat_id, "متوجه نشدم 🤔 یکی از گزینه‌ها رو بزن:", self._bf_main_menu())

        # ---- /newbot
        def _bf_new_start(self, db, uid, chat_id):
            if len(self._bf_my_bots(db, uid)) >= MAX_BOTS_PER_USER:
                return self._bf_reply(db, chat_id, f"حداکثر {MAX_BOTS_PER_USER} ربات می‌تونی داشته باشی؛ یکی رو با /deletebot حذف کن.")
            self._bf_set(db, uid, "new_name")
            return self._bf_reply(db, chat_id, "خب، ربات جدید! اسمش چی باشه؟\n"
                                  "(اسمیه که تو چت نشون داده می‌شه؛ ۱ تا ۴۰ حرف.) برای انصراف: /cancel")

        def _bf_state_text(self, db, uid, chat_id, state, data, text):
            if state == "new_name":
                name = _line(text, 40)
                if not name:
                    return self._bf_reply(db, chat_id, "اسم نمی‌تونه خالی باشه؛ دوباره بفرست.")
                self._bf_set(db, uid, "new_username", {"name": name})
                return self._bf_reply(db, chat_id, "حالا یه نام کاربری برای ربات انتخاب کن.\n"
                                      "باید با `bot` تموم بشه (مثلاً `my_shop_bot`)، فقط حرف انگلیسی، عدد و _ ، "
                                      "با حرف شروع بشه و ۳ تا ۳۲ کاراکتر باشه.")
            if state == "new_username":
                username = text.strip().lstrip("@")
                err = None
                if not BOT_USER_RE.match(username) or not username.lower().endswith("bot"):
                    err = "نام کاربری معتبر نیست: باید با حرف شروع بشه، ۳ تا ۳۲ کاراکتر (حرف انگلیسی/عدد/_) باشه و با `bot` تموم بشه."
                elif db.execute("SELECT 1 FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone() or \
                        db.execute("SELECT 1 FROM chats WHERE username=? COLLATE NOCASE", (username,)).fetchone():
                    err = "این نام کاربری قبلاً گرفته شده؛ یکی دیگه امتحان کن."
                if err:
                    return self._bf_reply(db, chat_id, err)
                if not self._room_hits(("newbot", uid), 5, 3600):
                    self._bf_clear(db, uid)
                    return self._bf_reply(db, chat_id, "تو یه ساعت خیلی ربات ساختی؛ بعداً دوباره بیا.")
                name = data.get("name") or username
                try:
                    self._check_display_name(name, username)
                except Fail as e:
                    self._bf_set(db, uid, "new_name")
                    return self._bf_reply(db, chat_id, f"{e}\nیه اسم دیگه بفرست.")
                bot_uid, token = self._bot_create(db, uid, name, username)
                self._bf_clear(db, uid)
                bot = self._bf_own_bot(db, uid, bot_uid)
                log("bot created:", username)
                return self._bf_reply(db, chat_id, "✅ ربات ساخته شد!\n\n" + self._bf_token_text(bot, token), secret=True,
                                      markup=[[self._btn("⚙️ تنظیمات ربات", f"bot:{bot_uid}")]])
            bot = self._bf_own_bot(db, uid, data.get("bot"))
            if state == "set_name":
                name = _line(text, 40)
                if not name:
                    return self._bf_reply(db, chat_id, "اسم نمی‌تونه خالی باشه؛ دوباره بفرست.")
                try:
                    self._check_display_name(name, bot["username"])
                except Fail as e:
                    return self._bf_reply(db, chat_id, f"{e}\nیه اسم دیگه بفرست.")
                with db:
                    db.execute("UPDATE users SET display_name=? WHERE id=?", (name, bot["user_id"]))
                self._bf_clear(db, uid)
                self._broadcast_profile(db, bot["user_id"])
                return self._bf_reply(db, chat_id, f"✅ اسم ربات شد «{name}».", [[self._btn("⬅️ برگشت", f"bot:{bot['user_id']}")]])
            if state == "set_desc":
                desc = "" if text == "." else _CTRL.sub("", text).strip()[:200]
                with db:
                    db.execute("UPDATE bots SET description=? WHERE user_id=?", (desc, bot["user_id"]))
                self._bf_clear(db, uid)
                self._broadcast_profile(db, bot["user_id"])
                return self._bf_reply(db, chat_id, "✅ توضیح ربات ذخیره شد." if desc else "✅ توضیح ربات پاک شد.",
                                      [[self._btn("⬅️ برگشت", f"bot:{bot['user_id']}")]])
            if state == "set_cmds":
                cmds = []
                try:
                    if text != ".":
                        for line in text.splitlines():
                            line = line.strip()
                            if not line:
                                continue
                            m = re.match(r"^/?([A-Za-z0-9_]+)\s*(?:[-–—:]\s*(.*))?$", line)
                            if not m:
                                raise Fail(f"خط «{line[:40]}» نامعتبره")
                            cmds.append({"command": m.group(1), "description": (m.group(2) or "").strip()})
                    cmds = self._parse_commands(cmds)
                except Fail as e:
                    return self._bf_reply(db, chat_id, f"{e}\nدوباره بفرست (هر خط: `دستور - توضیح`).")
                self._bf_clear(db, uid)
                self._bot_save_commands(db, bot["user_id"], cmds)
                return self._bf_reply(db, chat_id, f"✅ {len(cmds)} دستور ذخیره شد." if cmds else "✅ دستورها پاک شدن.",
                                      [[self._btn("⬅️ برگشت", f"bot:{bot['user_id']}")]])
            if state == "set_pic":
                return self._bf_reply(db, chat_id, "یه عکس بفرست (نه متن). برای انصراف: /cancel")
            self._bf_clear(db, uid)
            return self._bf_reply(db, chat_id, "متوجه نشدم؛ /help", self._bf_main_menu())

        def _bf_take_pic(self, db, uid, chat_id, msg, data):
            try:
                bot = self._bf_own_bot(db, uid, data.get("bot"))
                row = db.execute("SELECT file_path, file_name FROM messages WHERE id=?", (msg["id"],)).fetchone()
                path = common.resolve_upload(row["file_path"]) if row else None
                if path is None or common.media_info(row["file_name"])[1] != "image":
                    return self._bf_reply(db, chat_id, "این عکس نیست؛ یه عکس بفرست.")
                if path.stat().st_size > common.MAX_AVATAR:
                    return self._bf_reply(db, chat_id, "حجم عکس زیاده.")
                self.set_user_avatar(db, bot["user_id"], path.read_bytes())
            except Fail as e:
                return self._bf_reply(db, chat_id, str(e))
            self._bf_clear(db, uid)
            return self._bf_reply(db, chat_id, "✅ عکس ربات عوض شد.", [[self._btn("⬅️ برگشت", f"bot:{bot['user_id']}")]])

        # ---- buttons pressed in a BotFather message
        def _botfather_callback(self, db, uid, row, data):
            chat_id, mid = row["chat_id"], row["id"]
            act, _, arg = data.partition(":")

            def show(text, markup):
                self._bot_edit(db, self.botfather_id, mid, text=text, markup=markup)
            try:
                if act == "home":
                    self._bf_clear(db, uid)
                    show(WELCOME, self._bf_main_menu())
                elif act == "api":
                    show(API_HELP, [[self._btn("⬅️ برگشت", "home")]])
                elif act == "list":
                    self._bf_clear(db, uid)
                    t, rows = self._bf_list_view(db, uid)
                    show(t, rows)
                elif act == "new":
                    self._bf_new_start(db, uid, chat_id)
                else:
                    bot = self._bf_own_bot(db, uid, arg)
                    back = [[self._btn("⬅️ برگشت", f"bot:{bot['user_id']}")]]
                    if act == "bot":
                        self._bf_clear(db, uid)
                        show(*self._bf_bot_view(db, bot))
                    elif act == "tok":
                        show("توکن فعلی باطل می‌شه و باید توکن جدید رو تو برنامه‌ات بذاری. مطمئنی؟",
                             [[self._btn("✅ بله، توکن جدید", f"tokok:{bot['user_id']}"), self._btn("لغو", f"bot:{bot['user_id']}")]])
                    elif act == "tokok":
                        token = self._bot_new_token(db, bot["user_id"])
                        show(f"✅ توکن قبلی «{bot['display_name']}» باطل شد.", back)
                        self._bf_reply(db, chat_id, self._bf_token_text(bot, token), secret=True)
                    elif act == "nm":
                        self._bf_set(db, uid, "set_name", {"bot": bot["user_id"]})
                        show(f"اسم جدید برای «{bot['display_name']}» رو بفرست. (انصراف: /cancel)", back)
                    elif act == "ds":
                        self._bf_set(db, uid, "set_desc", {"bot": bot["user_id"]})
                        show("توضیح جدید ربات رو بفرست (تا ۲۰۰ حرف؛ برای پاک کردن فقط یه نقطه `.` بفرست). انصراف: /cancel", back)
                    elif act == "cm":
                        self._bf_set(db, uid, "set_cmds", {"bot": bot["user_id"]})
                        show("دستورها رو خط به خط بفرست، هر خط `دستور - توضیح`، مثلاً:\nstart - شروع\nhelp - راهنما\n"
                             "برای پاک کردن همه یه نقطه `.` بفرست. انصراف: /cancel", back)
                    elif act == "pic":
                        self._bf_set(db, uid, "set_pic", {"bot": bot["user_id"]})
                        show("یه عکس برای ربات بفرست. انصراف: /cancel", back)
                    elif act == "del":
                        show(f"ربات @{bot['username']} برای همیشه حذف می‌شه و توکنش از کار می‌افته. مطمئنی؟",
                             [[self._btn("🗑 بله، حذف کن", f"delok:{bot['user_id']}"), self._btn("لغو", f"bot:{bot['user_id']}")]])
                    elif act == "delok":
                        self._bot_delete(db, bot["user_id"])
                        t, rows = self._bf_list_view(db, uid)
                        show("🗑 ربات حذف شد.\n\n" + t, rows)
            except Fail as e:
                self._bf_toast(uid, str(e))

    return BotsMixin
