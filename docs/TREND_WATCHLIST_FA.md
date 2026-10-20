# فهرست آزمایشی بازارهای پرمعامله

این فایل برای دیدبان ده‌نمادی پروژه آماده شده است. فهرست بر پایهٔ بخش «Most Traded» صفحهٔ رتبه‌بندی Binance در ۲۰ اکتبر ۲۰۲۶ تهیه شده و فقط نمادهایی در آن مانده‌اند که برای فید USDⓈ-M perpetual پروژه قابل استفاده‌اند. قرارداد PEPE در این بازار با نماد 1000PEPEUSDT نمایش داده می‌شود.

| ردیف | نماد در پروژه | دارایی |
|---:|---|---|
| ۱ | BNBUSDT | BNB |
| ۲ | BTCUSDT | Bitcoin |
| ۳ | ETHUSDT | Ethereum |
| ۴ | SOLUSDT | Solana |
| ۵ | 1000PEPEUSDT | Pepe |
| ۶ | WLDUSDT | Worldcoin |
| ۷ | NEARUSDT | NEAR Protocol |
| ۸ | SUIUSDT | Sui |
| ۹ | PHAUSDT | Phala |
| ۱۰ | XRPUSDT | XRP |

این رتبه‌بندی ثابت نیست و برچسب «پرمعامله» به معنی تأیید ستاپ یا پیشنهاد معامله نیست. بازارهای پرحجم و رتبه‌ها می‌توانند در طول روز جابه‌جا شوند. در شروع اسکن، ربات موجودبودن قراردادهای تنظیم‌شده را با فهرست قراردادهای عمومی Binance بررسی می‌کند؛ اگر نمادی در دسترس نباشد، اسکن را با خطای روشن متوقف می‌کند.

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

سپس دکمهٔ «فعال‌سازی نمودار زنده» را در داشبورد بزن. تنظیم `paper_enabled` خاموش است؛ هیچ سفارش واقعی یا معاملهٔ فرضی جدیدی با این فایل اجرا نمی‌شود.

منابع:
- [رتبه‌بندی معاملاتی Binance](https://www.binance.com/en/markets/trading_data/rankings)
- [بازارهای فیوچرز Binance](https://www.binance.com/en/futures/markets/overview-um)
- [مستندات رسمی Binance USDⓈ-M Futures](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)
