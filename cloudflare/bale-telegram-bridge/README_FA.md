# پل یک‌طرفه بله به تلگرام

کامپیوتر با توکن ربات فرستنده به کانال بله پیام می‌فرستد. بله آپدیت ربات گیرنده را به Worker می‌فرستد؛ Worker متن یا عکس را به کانال تلگرام می‌فرستد. دسترسی مستقیم کامپیوتر به workers.dev و تلگرام لازم نیست. دسترسی بله به Worker باید هنگام نصب آزمایش شود. این راهکار میزبانی موتور تحلیل یا داشبورد نیست.

## نصب Worker

Worker جدید بسازید یا Worker آزمایشی `watchbot-telegram-relay` را استفاده کنید. Worker قدیمی InnoMaterial را تغییر ندهید.

1. محتوای `worker.mjs` همین پوشه را در Edit code قرار دهید و Deploy کنید.
2. در Settings → Variables and Secrets این مقادیر را تنظیم کنید:

| نام | نوع | مقدار |
|---|---|---|
| `BALE_RECEIVER_TOKEN` | Secret | توکن ربات گیرنده بله |
| `TELEGRAM_BOT_TOKEN` | Secret | توکن ربات مقصد تلگرام |
| `WEBHOOK_KEY` | Secret | رشته تصادفی جدید و محرمانه حداقل ۳۲ کاراکتر |
| `BALE_SOURCE_CHAT_ID` | Variable | `4869628883` |
| `TELEGRAM_CHAT_ID` | Variable | شناسه کانال تلگرام، مانند `@channel_username` |

3. ربات تلگرام را مدیر کانال مقصد کنید و مجوز ارسال پیام بدهید.
4. یک KV namespace در Storage & databases → KV بسازید. در Worker → Bindings → Add binding → KV namespace نام متغیر را `BRIDGE_STATE` قرار دهید و همان namespace را انتخاب کنید.
5. دوباره Deploy کنید. لاگ درخواست‌ها و Observability را خاموش نگه دارید؛ آدرس webhook شامل کلید محرمانه است.

## ثبت webhook از کامپیوتر

در همان PowerShell دارای `BALE_RECEIVER_TOKEN`:

```powershell
$env:BALE_BRIDGE_URL = "https://YOUR-WORKER.workers.dev"
$secret = Read-Host "Webhook key" -AsSecureString
$env:BALE_WEBHOOK_KEY = [System.Net.NetworkCredential]::new("", $secret).Password.Trim()
.\.venv315-final\Scripts\python.exe cloudflare\bale-telegram-bridge\register_webhook.py
```

اسکریپت فقط از طریق API بله ثبت می‌کند و نیازی به اتصال مستقیم به Worker ندارد. کلید باید با `WEBHOOK_KEY` در Worker یکسان باشد. پس از ثبت، getUpdates ربات گیرنده را اجرا نکنید. پیام `TEST-BRIDGE-1` را در کانال بله بگذارید و رسیدنش به تلگرام را بررسی کنید. در صورت نیاز به برگشت به polling، متد deleteWebhook بله را فراخوانی کنید.

## اتصال موتور تحلیل

در ترمینال اجرای موتور، توکن فرستنده موجود را نگه دارید و بنویسید:

```powershell
$env:BALE_CHANNEL_ID = "4869628883"
.\.venv315-final\Scripts\python.exe -u -m watchbot --config config.toobit.json run
```

وقتی هر دو متغیر BALE_SENDER_TOKEN و BALE_CHANNEL_ID وجود دارند، متن و نمودار به بله ارسال می‌شوند و تنظیمات مستقیم/relay تلگرام نادیده گرفته می‌شوند. این مسیر یک‌طرفه است؛ فرمان‌های تلگرام دریافت نمی‌شوند. برای توقف و ادامه از داشبورد استفاده کنید. تأیید ارسال محلی فقط دریافت بله را ثابت می‌کند؛ رسیدن به تلگرام را جداگانه آزمایش کنید.

KV ارسال‌های تکراری بعدی همان update را تا ۴۸ ساعت حذف می‌کند. در درخواست‌های همزمان یا قطع ارتباط پس از پذیرش تلگرام، تکرار هنوز ممکن است. شکست مقصد با HTTP 502 پاسخ داده می‌شود؛ روی سیاست نامشخص تلاش مجدد بله برای تحویل قطعی حساب نکنید. کلید، توکن، URL کامل webhook و خروجی خام getWebhookInfo را منتشر نکنید.
