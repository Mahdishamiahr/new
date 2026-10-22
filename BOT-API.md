# API ربات‌های Windroida

با **BotFather** (منوی کناری ← «ربات‌ها») ربات می‌سازی، توکن می‌گیری، و بعد با این API هر کاری که بخوای باهاش می‌کنی.
شکل API شبیه Bot API تلگرامه؛ برای همین کتابخونه‌ها و کدهای تلگرامی با کمی تغییر کار می‌کنن.

> نمونه‌ی آماده‌ی پایتون (بدون نصب هیچ چیز): `python examples/echo_bot.py https://SERVER:8443 TOKEN` (برای گواهی خودامضا `--insecure` هم بزن).

## ۱. ساخت ربات
۱. تو برنامه `BotFather` رو باز کن و `/newbot` بفرست.
۲. اسم ربات رو بده (مثلاً `فروشگاه من`).
۳. نام کاربری بده؛ **باید با `bot` تموم بشه** (مثلاً `my_shop_bot`؛ حرف انگلیسی/عدد/`_`، ۳ تا ۳۲ کاراکتر).
۴. توکن رو می‌گیری، شکلش اینه: `123:AbCdEf...` . **فقط همون یک بار نشون داده می‌شه** (روی سرور فقط هشِ توکن ذخیره می‌شه). گم شد؟ از BotFather «توکن جدید» بگیر؛ توکن قبلی باطل می‌شه.

دستورهای BotFather: `/newbot` `/mybots` `/token` `/setname` `/setdescription` `/setcommands` `/setuserpic` `/deletebot` `/cancel` `/help` (همه با دکمه هم در دسترسن). هر نفر تا ۱۰ ربات می‌تونه داشته باشه.

## ۲. درخواست‌ها
```
https://آدرس-سرور:پورت/bot<TOKEN>/<متد>
```
پارامترها رو می‌تونی به یکی از این شکل‌ها بفرستی (GET یا POST): **query string**، **بدنه JSON** (`Content-Type: application/json`) یا **فرم** (`application/x-www-form-urlencoded`).
برای ارسال فایل: بدنه‌ی خام (هر Content-Type دیگه‌ای) و بقیه پارامترها تو query، یا `file_base64` تو JSON (حداکثر ۲۰ مگابایت).

پاسخ همیشه JSON است:
```json
{"ok": true,  "result": ...}
{"ok": false, "error_code": 400, "description": "متن خطا"}
```
کدها: `400` پارامتر نامعتبر · `401` توکن اشتباه · `403` ربات عضو اون چت نیست · `404` متد/چیز پیدا نشد · `429` خیلی سریع · `507` دیسک سرور پره.

## ۳. گرفتن پیام‌ها: `getUpdates` (long polling)
ربات پیام‌ها رو **خودش می‌گیره**؛ سرور هیچ‌جا درخواست نمی‌فرسته (وب‌هوک نداریم، برای همین پشت NAT و شبکه‌ی داخلی هم کار می‌کنه).

| پارامتر | توضیح |
|---|---|
| `offset` | `update_id` آخرین آپدیتی که پردازش کردی **+ ۱**؛ آپدیت‌های قبلش پاک می‌شن |
| `limit` | ۱ تا ۱۰۰ (پیش‌فرض ۱۰۰) |
| `timeout` | ثانیه‌های صبر تا آپدیت تازه برسه، ۰ تا ۲۵ |

حلقه‌ی معمول:
```python
offset = 0
while True:
    for u in call("getUpdates", offset=offset, timeout=25):
        offset = u["update_id"] + 1
        ...  # u["message"] یا u["callback_query"]
```
آپدیت‌ها ۲۴ ساعت (و حداکثر ۱۰۰۰ تا) نگه داشته می‌شن.

### شکل آپدیت
```jsonc
{ "update_id": 42,
  "message": {
    "message_id": 17, "date": 1760000000,
    "chat": {"id": 5, "type": "private", "username": "ali", "first_name": "علی"},   // type: private | group
    "from": {"id": 9, "is_bot": false, "username": "ali", "first_name": "علی"},
    "text": "/start",
    "reply_to_message_id": 12,                               // اگه جواب یه پیام بود
    "file": {"name": "a.jpg", "size": 1234, "kind": "image"} // اگه فایل بود (voice: true, duration)
  } }

{ "update_id": 43,
  "callback_query": { "id": "cq1a2b…", "from": {…}, "data": "yes", "message": {…پیامی که دکمه زیرش بود…} } }
```
**چه پیام‌هایی می‌رسه؟** تو چت خصوصی همه‌ی پیام‌ها. تو **گروه** فقط: دستورها (`/x` یا `/x@نام_ربات`)، پیام‌هایی که ربات رو `@منشن` کردن، و پاسخ به پیام‌های خود ربات. (ربات‌ها پیام ربات‌های دیگه رو نمی‌بینن.) برای اضافه کردن ربات به گروه، مثل یه کاربر معمولی با نام کاربری‌اش اضافه‌ش کن.

## ۴. متدها

| متد | پارامترها | نتیجه |
|---|---|---|
| `getMe` | — | مشخصات ربات |
| `getUpdates` | `offset` `limit` `timeout` | لیست آپدیت‌ها |
| `sendMessage` | `chat_id` `text` [`reply_to_message_id`] [`reply_markup`] | پیام فرستاده‌شده |
| `sendDocument` / `sendPhoto` / `sendVideo` / `sendAudio` | `chat_id` + فایل (بدنه‌ی خام یا `file_base64`) + `file_name` [`caption`] [`reply_markup`] | پیام |
| `sendVoice` | مثل بالا + [`duration`] | پیام صوتی |
| `editMessageText` | `chat_id` `message_id` `text` [`reply_markup`] | پیام ویرایش‌شده |
| `editMessageReplyMarkup` | `chat_id` `message_id` `reply_markup` (خالی = برداشتن دکمه‌ها) | پیام |
| `deleteMessage` | `chat_id` `message_id` (فقط پیام‌های خود ربات) | `true` |
| `answerCallbackQuery` | `callback_query_id` [`text`] [`show_alert`] | `true` |
| `sendChatAction` | `chat_id` `action` (`typing` / `record_voice`) | `true` |
| `getChat` | `chat_id` | مشخصات چت |
| `getFile` | `message_id` | `file_name`, `file_size`, `download_path` |
| `setMyCommands` / `getMyCommands` | `commands`: `[{"command":"start","description":"شروع"}]` | منوی «/» ربات |
| `setMyDescription` | `description` (تا ۲۰۰ حرف) | `true` |
| `deleteWebhook` / `getWebhookInfo` | — | (وب‌هوک پشتیبانی نمی‌شه) |

نکته‌ها: متن پیام تا ۴۰۰۰ کاراکتر (قالب‌بندی همون قالب‌بندی چت‌ها: `**درشت**`، `` `کد` ``…). هیچ `parse_mode`ای لازم نیست. ربات فقط تو چت‌هایی که عضوشه می‌تونه بنویسه (چت خصوصی با کاربری که بهش پیام داده/بازش کرده، یا گروهی که اضافه‌ش کردن). حدود ۴۰ پیام در ۱۰ ثانیه.

### دانلود فایلی که کاربر فرستاده
```
GET https://سرور/file/bot<TOKEN>/<message_id>
```
(فقط برای فایل‌های چت‌هایی که ربات عضوشه.)

## ۵. دکمه‌های شیشه‌ای (inline keyboard)
```json
{"chat_id": 5, "text": "انتخاب کن",
 "reply_markup": {"inline_keyboard": [
    [{"text": "✅ آره", "callback_data": "yes"}, {"text": "❌ نه", "callback_data": "no"}],
    [{"text": "🌐 سایت", "url": "https://example.com"}]]}}
```
* هر دکمه یا `callback_data` (۱ تا ۶۴ بایت) داره یا `url` (فقط `http://` و `https://`).
* حداکثر ۸ ردیف، ۸ دکمه تو هر ردیف، ۴۰ دکمه کل.
* وقتی کاربر دکمه‌ی `callback_data`دار رو بزنه، یه آپدیت `callback_query` می‌رسه. با `answerCallbackQuery` جواب بده (متن به‌صورت پیام کوچیک یا با `show_alert` به‌صورت پنجره نشون داده می‌شه) و اگه خواستی پیام رو با `editMessageText` عوض کن.
* کاربر فقط دکمه‌هایی رو می‌تونه بزنه که ربات واقعاً گذاشته (سرور `callback_data` جعلی رو رد می‌کنه). `callback_query_id` تا ۲ دقیقه معتبره و فقط یک‌بار جواب می‌گیره.

## ۶. حداقل ربات پایتون
```python
import json, urllib.request
BASE = "https://سرور:8443/bot123:TOKEN/"
def call(method, **p):
    req = urllib.request.Request(BASE + method, json.dumps(p).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=60))["result"]

offset = 0
while True:
    for u in call("getUpdates", offset=offset, timeout=25):
        offset = u["update_id"] + 1
        if "message" in u:
            call("sendMessage", chat_id=u["message"]["chat"]["id"], text="سلام!")
```
و با curl:
```bash
curl "https://سرور:8443/bot$TOKEN/getMe"
curl -X POST "https://سرور:8443/bot$TOKEN/sendMessage" -H "Content-Type: application/json" -d '{"chat_id":5,"text":"سلام"}'
curl -X POST "https://سرور:8443/bot$TOKEN/sendDocument?chat_id=5&file_name=a.pdf" --data-binary @a.pdf
```

## ۷. امنیت و حریم خصوصی
* توکن مثل رمزه. تو کد عمومی (گیت‌هاب…) نذارش؛ لو رفت → BotFather → «توکن جدید».
* پیام توکن تو چت BotFather «محرمانه» علامت می‌خوره: تو نوتیفیکیشن‌ها نشون داده نمی‌شه.
* ربات‌ها **نمی‌تونن لاگین کنن** (رمز تصادفی ناشناخته دارن)، به پیام‌های بقیه‌ی چت‌ها دسترسی ندارن و فقط چیزهایی رو می‌بینن که تو بخش ۳ گفته شد. ولی یادت باشه: **هر چیزی که تو چت با یه ربات می‌نویسی رو صاحب اون ربات می‌خونه** (و مدیر سرور هم مثل بقیه‌ی پیام‌ها).
* مدیر سرور تو دیتاشیت (تب «ربات‌ها») می‌تونه هر رباتی رو مسدود یا حذف کنه.
* ربات «حذف‌شده» فقط تاریخچه‌ی چت‌ها رو باقی می‌ذاره؛ توکنش از کار می‌افته.
