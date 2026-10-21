# Worker مستقل تلگرام WatchBot

این Worker مستقل از telegram-bale-bridge است. هیچ تنظیم، توکن یا webhook پروژهٔ قبلی را تغییر ندهید. برای WatchBot ربات تلگرام جداگانه بسازید؛ getUpdates همزمان با webhook روی همان ربات کار نمی‌کند.

## ساخت از داشبورد

Workers & Pages → Create application → Create Worker / Hello World. نام: watchbot-telegram-relay. پس از ایجاد، Edit code و جایگزینی کل کد با worker.mjs، سپس Deploy.

Settings → Variables and Secrets → Add، نوع Secret:

- TELEGRAM_BOT_TOKEN: توکن ربات جدید کریپتو.
- RELAY_KEY: رمز تصادفی حداقل ۳۲ کاراکتر؛ در برنامه هم عین همین مقدار لازم است.

تولید کلید روی سیستم خودتان:

```powershell
.\.venv315-final\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

کلید را اینجا یا در گیت منتشر نکنید. در صورت نیاز تغییر Secret را Deploy کنید. آدرس Worker با مسیر /health باید پاسخ service=watchbot-telegram-relay بدهد. این صرفاً دسترسی Worker است، نه تأیید تلگرام. دسترسی بدون فیلترشکن از اینترنت کاربر باید جداگانه بررسی شود.

## اتصال ترمینال ربات

```powershell
$env:TELEGRAM_RELAY_URL = 'https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev'
$secret = Read-Host 'Relay key' -AsSecureString
$env:TELEGRAM_RELAY_KEY = [System.Net.NetworkCredential]::new('', $secret).Password.Trim()
```

در چت ربات جدید Start بزنید. سپس:

```powershell
.\.venv315-final\Scripts\python.exe -u -c "from watchbot.io import Telegram; t=Telegram(); print(t.call('getMe',{})['username']); print([{'chat_id':u['message']['chat']['id'],'type':u['message']['chat']['type']} for u in t.call('getUpdates',{'timeout':0}) if 'message' in u])"
```

اگر هیچ آپدیتی نیامد پیام تازه بفرستید. اگر webhook فعال است ابتدا مالک باید آن را بررسی کند؛ relay عمداً اجازهٔ تغییر webhook ندارد.

```powershell
$env:TELEGRAM_CHAT_ID = 'YOUR_PRIVATE_CHAT_ID'
```

با دستور زیر پیام آزمایشی به مقصد انتخاب‌شده ارسال می‌شود:

```powershell
.\.venv315-final\Scripts\python.exe -u -c "from watchbot.io import Telegram; print(Telegram().text('WatchBot: Telegram relay connected')['message_id'])"
```

بعد اسکنر را در همین ترمینال اجرا کنید:

```powershell
.\.venv315-final\Scripts\python.exe -u -m watchbot --config config.toobit.json run
```

متغیرها فقط در این ترمینال و فرزندان آن هستند؛ با بستن ترمینال باید دوباره تنظیم شوند. در حالت relay نیازی به TELEGRAM_BOT_TOKEN محلی نیست. مقصد گروهی به TELEGRAM_OPERATOR_USER_ID هم نیاز دارد. کلید relay اجازهٔ خواندن آپدیت‌ها و ارسال پیام/عکس با ربات را می‌دهد؛ مانند توکن محرمانه نگه دارید.

## محدوده

فقط getMe/getUpdates/sendMessage/sendPhoto با POST و Bearer key مجاز است؛ مقصد ثابت Telegram است. توکن در آدرس عمومی Worker قرار نمی‌گیرد. Worker به‌تنهایی موتور تحلیل پایتون را اجرا نمی‌کند؛ کامپیوتر برای اسکن باید روشن بماند. دریافت Toobit مستقیم می‌ماند. دریافت واقعی پیام، نمودار و فرمان‌ها فقط پس از استقرار و تست کاربر تأیید می‌شود.
