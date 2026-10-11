# ممیزی مسیر WAIT → ورود داشبوردی (فقط تحقیقاتی)

دامنه: کد موجود در `main` در زمان تهیه این گزارش و گزارش‌های تاریخی ثبت‌شده؛
**بدون** تغییر ربات زنده، مدل، محدودیت‌های ریسک یا داده‌های SQLite.

## داده‌ای که واقعاً ثبت شده

خروجی محلی `trade_execution_provenance_audit` برای ۴۴۰ معامله:
- `RECORDED_DIRECTION_MATCH`: 316
- `FINAL_FIELD_MATCHES_TRADE`: 50
- `FINAL_FIELD_WAIT`: 68
- `NO_RECORDED_DIRECTION_MATCH`: 2
- `UNMATCHED_DECISION`: 4

از ۷۰ تعارض حل‌نشده، ۶۴ مورد `WAIT / DASHBOARD_OPPORTUNITY`،
چهار مورد `WAIT / HUMAN_ASSISTED` و دو مورد `NO_RECORDED_DIRECTION_MATCH /
DASHBOARD_OPPORTUNITY` هستند. همه در گزارش نقش `MAIN` و
`entry_magic=26092212` دارند. این اعداد **بر اساس گزارش ارائه‌شده از
دیتابیس محلی** هستند؛ این گزارش منبع تاریخی را به‌طور مستقل بازاجرای نکرده است.

## مسیر فعلی در کد — شواهد مستقیم

1. `src/ramon/monitor.py`: درخواست `POST /api/manual-entry` به
   `queue_manual_entry` می‌رود (تقریباً خط 2220). تابع، جهت `BUY/SELL`
   و فرصت انتخاب‌شده را اعتبارسنجی می‌کند (تقریباً 1902–1930).
   `RECHECK` از نمونه تازه استفاده می‌کند؛ `DISCRETIONARY` با
   `manual_unlocked` جهت انتخابی انسان را مجاز می‌داند و فیلترهای
   تحلیلی را advisory می‌کند (تقریباً 1937–1998).
   فرمان در `Ramon_ManualEntries.txt` نوشته می‌شود؛ ثبت
   `human_assisted_event` به معنای **queued** است، نه fill قطعی.
2. `mt5/Ramon.mq5`: در `OnTimer`، وجود فایل فرمان موجب درخواست
   مدل تازه و سپس مصرف فرمان با `ReadDashboardManualEntry` می‌شود
   (تقریباً 5356–5380 و 5682–5690). `ReadDashboardManualEntry`
   جهت، `sample_key`، هندسه SL/TP، TTL نود یا صدوبیست ثانیه و
   کندل انتخابی را اعتبارسنجی می‌کند (تقریباً 4436–4545).
3. پس از پذیرش فرمان، EA مقدار محلی `decision` را برابر
   `dashboard_direction`، `sample_key` را برابر نمونه فرمان،
   `manual_execution_override` را ۱ و `dashboard_manual_entry`
   را true قرار می‌دهد (تقریباً 5698–5720).
   **هیچ الزام مستقلی در این قطعه نیست که `final_decision` ذخیره‌شده
   در دیتابیس مساوی جهت معامله باشد**. لذا اختلاف `WAIT` و معامله
   با مسیر فعلی **سازگار است**، اما منشأ تاریخی را اثبات نمی‌کند.
4. کد عمداً برای فرمان صریح داشبورد فیلترهای news timing،
   loss cooldown، سقف روزانه و بعضی confirmationهای تحلیلی را
   کنار می‌گذارد (تقریباً 5725–5819؛
   `tests/test_entry_routes.py` نیز بخشی از این قرارداد را آزمایش می‌کند).
   در مقابل، `EnableLiveTrading`، محدودیت تعداد معاملات داشبوردی،
   الزامات حساب hedging، قیمت تازه، اسپرد، سقف زیان اجرایی،
   مارجین، `V2ExecBegin`، `V2Preflight`، `V2ExecReserve`
   و بررسی مجدد پیش از `Trade.Buy/Sell` وجود دارند
   (تقریباً 5733–5760 و 5900–5990).
   **حضور guard در کد به معنی اثبات اجرای موفق آن در تاریخ نیست.**
5. `mt5/Ramon.mq5` در کد فعلی برای کامنت ورودی حاوی `:M`
   برچسب `HUMAN_ASSISTED` تولید می‌کند (حدود 2873)، نه
   `DASHBOARD_OPPORTUNITY`. برچسب‌های تاریخی قدیمی‌تر
   نباید با تعریف فعلی بازنویسی شوند.

## محدودیت شواهد تاریخی

`docs/V2_DASHBOARD_PROVENANCE_AUDIT_FA.md` از قبل نشان داده است که
برای بازسازی معاملات قدیمی، نسخه‌های تاریخی EA (از جمله 0.59.6/0.59.8)،
Journal و فایل فرمان لازم‌اند. در DB آن ممیزی، جدول
`human_assisted_events` موجود نبوده است. پس `entry_source`،
`magic`، `sample_key` یا وجود `inference_audit` به تنهایی
هویت کلیک‌کننده، مجوز ورود یا broker fill را اثبات نمی‌کنند.

## جمع‌بندی و تصمیم تحقیقاتی

- **مسیر فعلی** امکان ورود داشبوردی پس از `WAIT` را با
  override صریح و با کنترل‌های مستقل حساب/اجرا فراهم می‌کند؛
  بنابراین `WAIT` در دیتابیس و `BUY/SELL` اجرایی لزوماً تناقض منطقی نیست.
- **۶۸ معامله `WAIT` و دو معامله بدون تطابق هنوز تاریخی و حل‌نشده‌اند**؛
  هیچ‌کدام خودکار به داده آموزشیِ ورود خودکار Ramon اضافه نمی‌شوند.
- برای انتساب قطعی، زنجیره `manual-entry request → queue command
  → EA command-consumed → execution_id → order/deal/position → outcome`
  همراه با زمان، نسخه و شناسه ثابت لازم است.
- پیشنهاد بعدی: ممیزی فقط‌خواندنی لاگ‌های تاریخی EA/MT5 و artifactهای
  مرتبط در صورت دسترسی؛ تغییر guardهای لایو **جزو این کار نیست**.
