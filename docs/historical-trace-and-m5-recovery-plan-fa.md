# برنامهٔ ممیزی historical execution trace و بازیابی M5

دامنه: فقط تحقیق آفلاین. این سند هیچ تغییری در `Ramon.mq5`، تنظیمات
`EnableLiveTrading`، سفارش‌ها، ریسک یا SQLite اصلی ایجاد نمی‌کند.

## وضعیت اثبات‌شده در بررسی 2026-10-11

- `main` محلی و `origin/main` روی `8970418888281e786473de2df8be1e3fac490ca9`
  هستند. PRهای #79 تا #83 همگی merged هستند و PR #83 همین commit را دارد.
- `data/research/trade_execution_provenance.csv` برای 70 معاملهٔ هدف فقط
  `trade_key`، `sample_key`، جهت/زمان‌های ذخیره‌شده و telemetry سطح DB دارد.
  این فایل `execution_id`، broker order/deal identifier، دستور واقعی داشبورد یا
  رویداد «EA command consumed» ندارد.
- در مخزن، Journal تاریخی MT5 یا آرشیو `Ramon_ManualEntries.txt` برای دورهٔ
  معاملات هدف یافت نشد. لاگ‌های موجود در `build/` مربوط به تست/کامپایل هستند و
  نباید به معاملات واقعی تعمیم داده شوند.
- بنابراین طبقه‌بندی فعلی هر 70 هدف `NO_TRACE_EVIDENCE` است. این نتیجه فقط
  «نبودِ artifact قابل بررسی در workspace» را می‌گوید؛ نه اینکه ورود داشبوردی
  یا دستی رخ نداده است.

## قرارداد شواهد برای هر trade

برای هر `trade_key`، artifactها باید حداقل یکی از شناسه‌های ثابت
`sample_key`، `execution_id`، `position_id`، `order ticket` یا `deal ticket` را
داشته باشند. زمان‌ها باید صریحاً با timezone یا offset همراه باشند.

| وضعیت | حداقل شواهد لازم |
|---|---|
| `TRACE_CONFIRMED` | زنجیرهٔ هم‌ساز `decision_sample → command → EA consumed → execution_id/order request → broker order/deal → trade_outcome` با شناسه‌های قابل اتصال |
| `PARTIAL_TRACE` | حداقل یک artifact مستقیم وجود دارد، اما زنجیره کامل یا شناسهٔ اتصال ناقص است |
| `NO_TRACE_EVIDENCE` | برای trade هیچ artifact مستقیم و قابل اتصال موجود نیست |
| `CONFLICTING_EVIDENCE` | artifactهای قابل اتصال، جهت/شناسه/ترتیب زمانی ناسازگار دارند |

`entry_source`، `entry_magic`، `trade_role` و تطابق صرفِ `sample_key`، به تنهایی
برای `TRACE_CONFIRMED` کافی نیستند.

## برداشت امن artifactهای MT5 در HP-Mini

این دستورات فقط فایل‌ها را کپی و checksum تولید می‌کنند؛ هیچ دستور MT5، سفارش
یا SQLite اصلی را تغییر نمی‌دهند. ابتدا MT5 را متوقف نکنید و فایل‌های فعال را
حذف یا جابه‌جا نکنید.

```bash
WORK="$HOME/Documents/Presentation/ramon-ai-trader"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$WORK/data/research/mt5-trace-intake/$STAMP"
mkdir -p "$DEST"

# مسیرهای واقعی نصب Wine/MT5 را فقط فهرست می‌کند.
find "$HOME/.mt5" "$HOME/.wine" -type f \
  \( -iname '*.log' -o -iname 'Ramon_ManualEntries.txt' -o -iname '*journal*' \
     -o -iname '*execution*' -o -iname '*deal*' -o -iname '*order*' \) \
  -print 2>/dev/null | sort > "$DEST/candidates.txt"

# پیش از کپی، فایل‌ها را بازبینی کنید؛ اطلاعات حساب یا توکن را عمومی نکنید.
sed -n '1,240p' "$DEST/candidates.txt"
```

پس از بازبینی انسانی و انتخاب فقط artifactهای دورهٔ هدف، آن‌ها را در همان
`$DEST` کپی کنید، سپس:

```bash
find "$DEST" -type f -print0 | sort -z | xargs -0 sha256sum > "$DEST/SHA256SUMS"
find "$DEST" -type f -printf '%p\t%s bytes\n' | sort > "$DEST/manifest.tsv"
```

فایل‌های مطلوب عبارت‌اند از Journalهای EA، Experts/Journal terminal، نسخهٔ EA
و فایل فرمان مربوط به همان بازه، و MT5 Account History export شامل ticketهای
order/deal/position. پیش از اشتراک‌گذاری، شمارهٔ حساب، نام کارگزار، مسیرهای
شخصی و هر credential را redact کنید؛ شناسه‌های لازم برای join را حفظ کنید.

## وضعیت و برنامهٔ M5

خروجی `trade_rejection_quality.json` شامل 362 timestamp یکتای M5 با اثر روی
452 رخداد-معامله در 14 روز است. نمونه‌های محلی M5 از `2026-08-07 07:05 UTC`
تا `2026-09-28 17:45 UTC` هستند؛ بنابراین آن‌ها برای پوشش بعد از این انتها
منبع بازیابی محسوب نمی‌شوند. شکاف‌های شبانهٔ منظم نیز باید با ساعات و calendar
خود LiteFinance سنجیده شوند، نه با interpolation.

### export معتبر از MT5

در History Center همان terminal/account، فقط `XAUUSD_l / M5` را برای
`2026-09-23T00:00:00Z` تا `2026-10-10T00:00:00Z` export کنید. export باید
زمان open، OHLC و spread (اگر terminal آن را می‌دهد) داشته باشد. فایل را با
نامی مانند زیر، بدون overwrite کردن import قبلی، نگه دارید:

```text
data/research/mt5-trace-intake/<STAMP>/XAUUSD_l_M5_20260923_20261010.csv
```

پیش از هر merge پژوهشی، کنترل‌ها الزامی‌اند:

1. هر timestamp بر 300 بخش‌پذیر باشد و timezone آن معلوم باشد.
2. `low <= min(open, close) <= max(open, close) <= high` و همهٔ OHLC مثبت باشند.
3. هیچ duplicate متناقض وجود نداشته باشد؛ دو منبع متفاوت برای یک candle باید
   به `CONFLICTING_EVIDENCE` بروند، نه overwrite.
4. فقط candleهایی وارد sidecar پژوهشی شوند که source، checksum و بازهٔ export
   آن‌ها ثبت شده است.
5. SQLite اصلی `data/ramon_history.sqlite3` immutable بماند. بعد از ساخت
   sidecar، `trade_rejection_audit` و `executed_trade_dataset_builder` دوباره
   فقط روی آن sidecar اجرا شوند.

هیچ candle ساختگی، forward fill، interpolation یا انتقال دادهٔ آینده مجاز نیست.
تا رسیدن export معتبر، تعداد 82 `COMPLETE_CONTEXT` تغییر نمی‌کند و 70 تعارض
برای ارزیابی مدل واجد شرایط نیستند.
