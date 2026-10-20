# فهرست آزمایشی دارایی‌های جاافتاده‌تر

این فایل برای دیدبان ده‌نمادی پروژه آماده شده است. فهرست، انتخابی محافظه‌کارانه‌تر از نظر سابقه و شناخته‌شدگی بازار است و میم‌کوین‌ها و توکن‌های کم‌سابقهٔ فهرست قبلی را کنار می‌گذارد. این فهرست رتبه‌بندی زندهٔ حجم معاملات نیست و ادعای کم‌نوسان‌بودن یا مصونیت از پامپ‌ودامپ ندارد.

| ردیف | نماد در پروژه | دارایی |
|---:|---|---|
| ۱ | BTCUSDT | Bitcoin |
| ۲ | ETHUSDT | Ethereum |
| ۳ | BNBUSDT | BNB |
| ۴ | SOLUSDT | Solana |
| ۵ | XRPUSDT | XRP |
| ۶ | LINKUSDT | Chainlink |
| ۷ | ADAUSDT | Cardano |
| ۸ | LTCUSDT | Litecoin |
| ۹ | AVAXUSDT | Avalanche |
| ۱۰ | TRXUSDT | TRON |

نمادها در پیکربندی قرارداد دائمی Binance USDⓈ-M استفاده می‌شوند. موجودبودن قرارداد به معنی پیشنهاد معامله یا تأیید ستاپ نیست. حجم نسبی، عمق دفتر سفارش و نوسان با زمان تغییر می‌کنند؛ هیچ‌کدام از این دارایی‌ها از افت شدید یا دست‌کاری بازار مصون نیستند. برای مقایسهٔ روزانه باید حجم و نقدشوندگی همان قرارداد در Binance بررسی شود. اگر قراردادی در دسترس نباشد، اسکن باید با خطای روشن متوقف شود و نماد مشابه بی‌اطلاع جایگزین نشود.

## اجرای آزمایش جدا از ژورنال قبلی

پوشهٔ دیتابیس و نمودار این آزمایش مستقل است: `runtime/trending/`. دیتابیس قبلی پایش دست‌نخورده می‌ماند.

در پنجرهٔ اول PowerShell:

```powershell
.\.venv-stable\Scripts\python.exe -m watchbot --config config.trending.json run
```

در پنجرهٔ دوم:

```powershell
.\.venv-stable\Scripts\python.exe -m watchbot --config config.trending.json dashboard
```

سپس دکمهٔ «فعال‌سازی نمودار زنده» را در داشبورد بزن. تنظیم `paper_enabled` خاموش است؛ این فایل سفارش واقعی نمی‌فرستد و معاملهٔ فرضی جدید ثبت نمی‌کند.

منابع:
- [پارامترهای رسمی قراردادهای دائمی Binance USDⓈ-M](https://www.binance.com/en/futures/trading-parameters/perpetual)
- [بازارهای فیوچرز Binance](https://www.binance.com/en/futures/markets/overview-um)
- [مستندات رسمی Binance USDⓈ-M Futures](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)
