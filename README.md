Warning: truncated output (original token count: 22309)
Total output lines: 794

# Ramon — ربات معاملاتی مبتنی بر Chronos‑2

**Ramon** یک پروژهٔ مستقل برای معاملهٔ طلا در MetaTrader 5 است. هدف آن ساخت رباتی است که با یک مدل واقعی سری زمانی تصمیم بگیرد، نتیجهٔ معاملات واقعی حساب سنتی را اندازه‌گیری کند و در نسخه‌های بعدی از داده‌های خودش یاد بگیرد؛ در عین حال، اندازهٔ ریسک و شرایط اجرای سفارش قابل بررسی و کنترل باشند.

**وضعیت پروژه: نسخهٔ اولیهٔ قابل آزمایش.** سرویس محلی Chronos‑2، اکسپرت MT5، ثبت تاریخچهٔ بسته‌شدهٔ M15 و چرخهٔ آموزش روزانهٔ challenger پیاده‌سازی شده‌اند. مدل جدید فقط وقتی معیارهای holdout تعریف‌شده را بهتر کند می‌تواند جایگزین مدل فعال شود. از نسخهٔ 0.24، آموزش مدل‌های Entry و Meta از نتیجهٔ خالص معاملات واقعیِ بسته‌شده و قابل اتصال به شناسهٔ تصمیم انجام می‌شود؛ فعال‌شدن آن‌ها نیازمند دادهٔ کافی و عبور از ارزیابی زمانی است. هیچ بازده یا سود تضمین‌شده‌ای وجود ندارد.

> دامنهٔ فعلی: LiteFinance، نماد دقیق XAUUSD_l، تایم‌فریم M15، حساب واقعی سنتی با تأیید دستی تبدیل واحدهای حساب. Ramon از Eskandar و قوانین شش‌مرحله‌ای آن مستقل است.

## هدف نهایی

می‌خواهیم Ramon این چرخه را با دادهٔ قابل ردیابی طی کند:

1. آخرین کندل‌های بسته‌شده و هزینهٔ واقعی ورود را از MT5 دریافت کند.
2. با نسخهٔ مشخصی از مدل، مسیر احتمالی قیمت و عدم‌قطعیت آن را پیش‌بینی کند و BUY، SELL یا WAIT را همراه دلیل تولید کند.
3. تنها در چارچوب بودجهٔ ریسک، محدودیت حساب و شرایط سالم بازار سفارش بدهد.
4. برای هر تصمیم، ورودی‌ها، خروجی مدل، سفارش و نتیجهٔ واقعی معامله را ثبت کند؛ از جمله تصمیم‌های WAIT و سیگنال‌هایی که به علت حداقل حجم کارگزار اجرا نشده‌اند.
5. مدل جدید را با تاریخچهٔ بدون نشت اطلاعات آموزش دهد، روی دوره‌های بعدی و هزینه‌های واقعی بسنجد، و تنها پس از بهتر شدن معیارهای از پیش تعیین‌شده به‌صورت نسخه‌بندی‌شده جایگزین کند.
6. بتواند در صورت خرابی مدل، تغییر رفتار بازار یا افت عملکرد، ورود جدید را متوقف کند و به نسخهٔ قبلی برگردد.

«هوشمند» در این پروژه یعنی مدل قابل آموزش، پیش‌بینی همراه عدم‌قطعیت و تصمیم قابل ارزیابی. صرفِ تعداد بیشتر معاملات یا حذف WAIT نشان‌دهندهٔ هوشمندی نیست. ربات باید بتواند فرصت‌های خوب را بگیرد و در موقعیت نامناسب علت صبر کردن را نشان دهد.

## امروز چه چیزی کار می‌کند؟

| بخش | وضعیت فعلی |
| --- | --- |
| پیش‌بینی | کد بارگذاری checkpoint **autogluon/chronos-2-small** با API پایتون Chronos‑2؛ استفاده از حداکثر ۲۵۶ قیمت بسته‌شدن برای پیش‌بینی چهار کندل آینده و کوانتیل‌های ۱۰٪، ۵۰٪ و ۹۰٪؛ اجرای وزن واقعی روی دستگاه کاربر هنوز تأیید نشده است |
| تصمیم | BUY/SELL/WAIT از میانهٔ پیش‌بینی کندل چهارم، اسپرد، ATR و پهنای بازهٔ پیش‌بینی؛ حداقل مزیت و قدرت سیگنال فعلاً پارامترهای ثابت‌اند |
| ارتباط | سرویس HTTP روی 127.0.0.1:8012 در اجرای محلی؛ در Docker فقط همین نشانی از میزبان منتشر می‌شود؛ خطای مدل یا پاسخ نامعتبر به سفارش منجر نمی‌شود |
| اجرای MT5 | بررسی نماد و حساب، مجوز معامله، تازگی قیمت، اسپرد، پوزیشن‌های موجود، مارجین، سقف دفعات ورود و محاسبهٔ حجم با OrderCalcProfit |
| ارزیابی | بازپخش آفلاین دادهٔ M15 از پایگاه SQLite با ورود در کندل بعدی، حداکثر چهار کندل نگهداری، اسپرد و فرض محافظه‌کارانه برای برخورد همزمان SL/TP |
| آموزش | LoRA نسخه‌بندی‌شده + job روزانهٔ challenger؛ ۷۰٪ train، ۱۰٪ validation و ۲۰٪ holdout؛ promotion فقط پس از عبور از گیت net-R، تعداد معامله و drawdown |
| هنوز در برنامه | ارزیابی فرصت‌های اجرا‌نشده، تعویض هماهنگ checkpoint Chronos و نقش‌ها، بازگشت خودکار بر اساس افت عملکرد و تأیید عملی MT5 |

Chronos‑2 در نسخهٔ فعلی **پیش‌بینی‌کنندهٔ سری زمانی** است، نه عاملی که مستقیماً از پاداش معامله یاد گرفته باشد. فیلتر مزیت خالص، حد سود/ضرر و محدودیت‌های اجرا هنوز کد مشخص دارند. در صورت در دسترس نبودن مدل، استراتژی EMA/RSI جای آن قرار نمی‌گیرد. Ollama در این معماری لازم نیست.

## معماری

~~~mermaid
flowchart TD
    A["MT5: Ramon EA روی XAUUSD_l M15"] -->|"کندل بسته‌شده، Bid/Ask"| B["سرویس محلی 127.0.0.1:8012"]
    B -->|"تا ۲۵۶ قیمت بسته‌شدن"| C["Chronos-2: پیش‌بینی ۴ کندل"]
    C -->|"بازه و میانهٔ پیش‌بینی"| B
    B -->|"BUY/SELL/WAIT + دلیل"| A
    A -->|"کنترل ریسک و ارسال سفارش"| D["حساب سنتی LiteFinance"]
~~~

ورودی Chronos از کندل‌های کامل‌شدهٔ M15 است؛ اکسپرت هر ۳۰ ثانیه با Bid/Ask تازه و دادهٔ M1 مجدداً شرایط ورود را بررسی می‌کند. ثبت snapshot در زمان داشتن پوزیشن نیز ادامه دارد. سرویس یک endpoint سلامت به نشانی /health و یک endpoint تصمیم به نشانی /decision دارد. سرویس روی loopback گوش می‌دهد و برای استفاده روی همان ماشینی طراحی شده که MT5 زیر Wine اجرا می‌شود.

## ساختار ریپو

| مسیر | کار |
| --- | --- |
| mt5/Ramon.mq5 | اکسپرت مستقل MT5 و کنترل‌های قبل از سفارش |
| src/ramon/model.py | بارگذاری checkpoint و خواندن خروجی واقعی Chronos‑2 |
| src/ramon/core.py | اعتبارسنجی داده، ATR، اسپرد و تصمیم BUY/SELL/WAIT |
| src/ramon/server.py | سرویس محلی HTTP و پاسخ سلامت |
| src/ramon/preflight.py | آزمایش بارگذاری مدل و یک پیش‌بینی واقعی، بدون سفارش |
| src/ramon/history.py و replay.py | خواندن تاریخچه و ارزیابی ترتیبی آفلاین |
| src/ramon/train.py | ساخت checkpoint جدید با LoRA و ثبت تقسیم داده |
| Dockerfile و compose.yaml | اجرای مدل و ابزارهای پیش‌بینی/بازپخش/آموزش در کانتینر CPU |
| Dockerfile.mt5 و scripts/mt5_container.sh | محیط اختیاری MT5 با Wine و صفحهٔ نصب در مرورگر محلی |
| scripts/setup_local.sh | نصب محلی جایگزین، آزمایش مدل و کامپایل اکسپرت در Ubuntu/Wine |
| tests/ | تست تصمیم، پاسخ HTTP، سازگاری آداپتور مدل و ترتیب بازپخش |

## اجرا با Docker Compose

Docker Compose سرویس **Chronos‑2** و فرمان‌های پایتونی Ramon را داخل کانتینر اجرا می‌کند؛ بسته‌های Python و PyTorch داخل image نصب می‌شوند و به APT سیستم میزبان وابسته نیستند. برای MT5 دو مسیر دارید: استفاده از نصب Wine فعلی روی میزبان، یا ساخت محیط Wine جداگانه در پروفایل اختیاری `mt5`. پروفایل اختیاری برنامهٔ MT5 را نصب نمی‌کند؛ برای آن باید فایل نصب ویندوزی MT5 را تهیه و از محیط گرافیکی داخل کانتینر اجرا کنید. اجرای زنده به‌صورت پیش‌فرض در اکسپرت خاموش است.

پیش‌نیاز: Docker Engine و افزونهٔ Docker Compose **از قبل نصب و قابل اجرا باشند**. اگر Docker روی Ubuntu فعلی نصب نیست، با مخزن‌های ناسازگار noble/questing برای نصب آن سراغ `apt install` نروید؛ ابتدا مخزن‌های سیستم باید ترمیم شوند. فضای کافی برای image پایتون/PyTorch و cache مدل لازم است.

~~~bash
cd ~/Documents/Presentation/ramon-ai-trader
git pull --ff-only
mkdir -p data checkpoints
docker compose build
docker compose up -d model
docker compose ps
curl http://127.0.0.1:8012/health
~~~

اولین اجرا باید وزن مدل را در volume پایدار `chronos_cache` دانلود و بارگذاری کند؛ تا آماده‌شدن مدل، `curl` ممکن است موقتاً خطا بدهد. برای دیدن پیشرفت و خطاها: `docker compose logs -f model`. در پاسخ موفق، `ready` برابر `true` و نام مدل برابر checkpoint بارگذاری‌شده است. اگر UID/GID کاربرتان 1000 نیست، هنگام ساخت image مقدارهای خودتان را بفرستید: `RAMON_UID=$(id -u) RAMON_GID=$(id -g) docker compose build`؛ همان مقدارها را در فایل `.env` برای اجرای بعدی Compose ثبت کنید.

مدل داخل کانتینر روی همهٔ رابط‌های **خود کانتینر** گوش می‌دهد، اما Compose پورت را فقط به `127.0.0.1:8012` میزبان وصل می‌کند. اگر MT5 روی همین میزبان اجرا می‌شود، نشانی `ModelUrl` اکسپرت و URL مجاز WebRequest همان `http://127.0.0.1:8012` هستند. این سرویس HTTP احراز هویت ندارد؛ پورت آن را روی شبکهٔ عمومی منتشر نکنید. اگر پورت 8012 میزبان اشغال است، سرویس قدیمی Ramon را متوقف کنید و سپس Compose را اجرا کنید.

برای آزمون پیش‌بینی واقعی و کارهای آفلاین، سرویس ابزارها را با همان image اجرا کنید:

~~~bash
docker compose --profile tools run --rm tools -m ramon.preflight --device cpu

# فایل تاریخچهٔ SQLite را ابتدا در پوشهٔ data/ قرار دهید.
docker compose --profile tools run --rm tools -m ramon.replay \
  --db /data/history.sqlite3 --symbol XAUUSD_l --device cpu

# آموزش دستی؛ قبل از جایگزینی مدل زنده خروجی را جداگانه ارزیابی کنید.
docker compose --profile tools run --rm tools -m ramon.train \
  --db /data/history.sqlite3 --device cpu --out /checkpoints/checkpoint-001
~~~

`data/` فقط خواندنی است و checkpointها در `checkpoints/` روی میزبان باقی می‌مانند. ابزارهای آموزش و بازپخش با پروفایل جدا اجرا می‌شوند و خودکار در زمان راه‌اندازی مدل فعال نمی‌شوند. برای سرو کردن checkpoint تأییدشده، مقدار `CHRONOS_MODEL=/checkpoints/checkpoint-001/model` را در `.env` بگذارید و `docker compose up -d --force-recreate model` را اجرا کنید. آموزش CPU می‌تواند بسیار کند باشد؛ Compose فعلی شتاب‌دهندهٔ GPU را درخواست نمی‌کند.

برای خاموش کردن سرویس: `docker compose down`. این فرمان volume مدل را حذف نمی‌کند؛ از `down -v` استفاده نکنید مگر اینکه بخواهید cache مدل را پاک کنید. برای کامپایل EA در MT5 موجود روی میزبان، بخش بعدی و دستور `bash scripts/setup_local.sh --mt5-only` را ببینید؛ اگر MetaEditor وجود ندارد، نصب MT5 فعلی را بررسی کنید یا از محیط جداگانهٔ زیر استفاده کنید.

### MT5 در کانتینر جداگانه (اختیاری)

این مسیر مشکل بسته‌های Wine میزبان را دور می‌زند: image اختیاری از Ubuntu 24.04 ساخته می‌شود و Wine64/Wine32 را از همان مخزن داخل کانتینر می‌گیرد. به Docker فعال، فضای دیسک اضافی و فایل نصب **ویندوزی** MT5 از منبع مورداعتماد خودتان نیاز دارد. برنامه و اطلاعات ورود MT5 در volume جداگانهٔ `mt5_prefix` نگهداری می‌شوند؛ پیشوند `~/.mt5` میزبان به کانتینر وصل نمی‌شود. این محیط هنوز روی دستگاه شما ساخته و آزموده نشده است.

~~~bash
cd ~/Documents/Presentation/ramon-ai-trader
mkdir -p installer checkpoints
# فایل نصب ویندوزی MT5 را با نام installer/mt5setup.exe در این مسیر بگذارید.
docker compose --profile mt5 up -d --build mt5
docker compose --profile mt5 ps
docker compose --profile mt5 exec -d mt5 wine /installer/mt5setup.exe
~~~

آدرس `http://127.0.0.1:6080/vnc.html` را در مرورگر **همان دستگاه** باز کنید و مراحل نصب MT5 را در پنجرهٔ گرافیکی انجام دهید. مرورگر این محیط رمز عبور ندارد و Compose آن را فقط روی loopback میزبان منتشر می‌کند؛ پورت 6080 را به شبکهٔ عمومی باز نکنید. پس از تکمیل نصب، بدون وارد کردن اطلاعات حساب به خط فرمان، کامپایل را انجام دهید و ترمینال را باز کنید:

~~~bash
docker compose --profile mt5 exec mt5 bash scripts/setup_local.sh --mt5-only
docker compose --profile mt5 exec -d mt5 run-mt5
~~~

در MT5 داخل کانتینر، URL مجاز WebRequest را **`http://model:8012`** قرار دهید و ورودی `ModelUrl` اکسپرت را `http://model:8012/decision` تنظیم کنید؛ `127.0.0.1` در این حالت به خود کانتینر MT5 اشاره می‌کند. سپس از رابط MT5 با حساب درست وارد شوید، اکسپرت کامپایل‌شده را به نمودار `XAUUSD_l` در `M15` وصل کنید و ابتدا فقط وضعیت غیرفعال آن را ببینید. بعد از راه‌اندازی دوبارهٔ کانتینر، فرمان `run-mt5` را دوباره اجرا کنید؛ خودکار اجرا شدن ترمینال هنوز تنظیم نشده است. اگر نصب یا کامپایل شکست خورد، `docker compose --profile mt5 logs mt5` و فایل `Ramon.log` داخل پوشهٔ Experts را بررسی کنید. اعتبارسنجیِ کامپایل، اتصال واقعی و تصمیم مدل روی رایانهٔ شما همچنان لازم است.

## نصب محلی روی Ubuntu و MT5 (جایگزین Compose)

پیش‌نیازها: Python 3.11 یا بالاتر، uv، Wine، MetaTrader 5 و MetaEditor سالم، دسترسی به اینترنت برای دریافت بسته‌ها و وزن مدل، و RAM/فضای دیسک کافی برای PyTorch. دانلود بستهٔ PyTorch می‌تواند بسیار بزرگ‌تر از فایل وزن مدل باشد. این راهنما فرض می‌کند MT5 روی همان دستگاه Ubuntu اجرا می‌شود.

~~~bash
git clone https://github.com/amiradmin/ramon-ai-trader.git
cd ramon-ai-trader
bash scripts/setup_local.sh
~~~

این دستور وابستگی‌ها را نصب می‌کند، مدل را دانلود و بارگذاری می‌کند، یک پیش‌بینی چهارمرحله‌ای واقعی را می‌سنجد، سپس Ramon.mq5 را در Experts کپی و با MetaEditor کامپایل می‌کند. **این دستور معاملهٔ زنده را فعال نمی‌کند.**

اسکریپت مسیر MetaEditor و پوشهٔ Experts را در پیشوندهای رایج Wine مانند ~/.mt5 و ~/.wine جست‌وجو می‌کند. برای نمایش نتیجهٔ جست‌وجو **بدون نصب و کپی فایل** از حالت تشخیص استفاده کنید. اگر ریپو از قبل روی دستگاهتان است، ابتدا تغییرات را بگیرید:

~~~bash
git pull --ff-only
bash scripts/setup_local.sh --diagnose
~~~

اگر MetaEditor پیدا نشد، با دستور زیر محل نصبش را ببینید. اگر چند پوشهٔ داده پیدا شد، مسیر ترمینال درست را از **File → Open Data Folder** در MT5 بردارید. RAMON_MT5_DIR باید به پوشهٔ حاوی metaeditor64.exe اشاره کند و RAMON_MT5_DATA_DIR باید پوشه‌ای باشد که MQL5/Experts در آن وجود دارد:

~~~bash
find "$HOME/.mt5" "$HOME/.wine" -type f \
  \( -iname 'metaeditor64.exe' -o -iname 'metaeditor.exe' \) -print 2>/dev/null

RAMON_MT5_DIR="/path/to/MetaTrader 5" \
RAMON_MT5_DATA_DIR="/path/to/terminal-data" \
bash scripts/setup_local.sh
~~~

برای نصب و آزمون **فقط مدل** در زمانی که مسیر MT5 مشخص نیست، از حالت جداگانه استفاده کنید؛ پس از یافتن مسیر MT5، نصب کامل را دوباره اجرا کنید:

~~~bash
bash scripts/setup_local.sh --model-only
bash scripts/setup_local.sh
~~~

اگر پیش‌بینی مدل موفق بود ولی اجرای MetaEditor زیر Wine شکست خورد، برای تکرار **فقط کپی و کامپایل اکسپرت** از فرمان زیر استفاده کنید؛ وزن مدل دوباره آزمایش نمی‌شود:

~~~bash
bash scripts/setup_local.sh --mt5-only
~~~

خطای «wine32 is missing» همراه با خطای باز شدن metaeditor.exe معمولاً به وابستگی ۳۲ بیتی Wine یا DLLهای آن مربوط است. ابتدا نوع فایل و وضعیت بسته را بررسی کنید:

~~~bash
file "$HOME/.mt5/drive_c/Program Files/MetaTrader 5/metaeditor.exe"
dpkg --print-foreign-architectures
dpkg-query -W wine32:i386 2>/dev/null || true
~~~

اگر خروجی file برابر PE32 بود، wine32:i386 ممکن است لازم باشد؛ **تا وقتی نسخه‌های مخزن‌های APT یکسان نیستند آن را نصب نکنید**. برای نمونه، انتخاب libgcc-s1:amd64 نسخهٔ questing و libgcc-s1:i386 نسخهٔ noble باعث شکست حل وابستگی می‌شود و با نصب اجباری یا پایین‌آوردن نسخهٔ کتابخانه‌های اصلی نباید حل شود. پیش از هر نصب، نسخهٔ سیستم، منبع بسته‌ها و فایل مخزن‌ها را بخوانید:

~~~bash
. /etc/os-release; printf 'OS=%s CODENAME=%s\n' "$PRETTY_NAME" "$VERSION_CODENAME"
apt-cache policy libgcc-s1:amd64 libgcc-s1:i386 wine32:i386
grep -RniE 'noble|questing|cloudflareclient|Architectures:' \
  /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null
~~~

APT باید نسخه‌های سازگار از **همان نسخهٔ Ubuntu** را برای amd64 و i386 ببیند. خطای «pkg.cloudflareclient.com questing Release» مشکل دیگری است: آن مخزن نسخهٔ questing را منتشر نمی‌کند؛ ابتدا فایل مخزن مربوطه را شناسایی کنید و برای رفع خطای APT فقط همان مخزن ناسازگار را اصلاح یا غیرفعال کنید. این کار مشکل مخزن‌های مخلوط Ubuntu را به‌تنهایی برطرف نمی‌کند. پس از اصلاح منابع، تغییرات پیشنهادی **apt -s install wine32:i386** را بررسی کنید؛ اگر حذف Wine یا تنزل کتابخانه‌های اصلی پیشنهاد شد، نصب را انجام ندهید. خطای c0000135 می‌تواند از DLL دیگری هم باشد؛ اگر wine32 از قبل نصب بود یا MetaEditor فایل PE32+ بود، خروجی Wine و فایل Ramon.log را بررسی کنید. پس از رفع Wine فرمان --mt5-only کافی است.

راه جایگزین بدون تغییر بسته‌های Ubuntu: اگر MetaEditor از داخل MT5 باز می‌شود، فایل کپی‌شدهٔ MQL5/Experts/Ramon/Ramon.mq5 را در آن باز کنید و F7 بزنید. تنها پس از کامپایل موفق و نمایش صفر خطا، اکسپرت را به نمودار وصل کنید؛ EnableLiveTrading همچنان false بماند.

اگر پردازشگر گرافیکی CUDA و نصب سازگار PyTorch دارید، RAMON_DEVICE=cuda را برای آزمون اولیه بگذارید؛ حالت پیش‌فرض CPU است. اگر اسکریپت MetaEditor را پیدا نکرد، می‌توان فایل mt5/Ramon.mq5 را دستی به MQL5/Experts/Ramon/ کپی کرد و در MetaEditor با F7 کامپایل کرد.

پس از نصب، سرویس را در یک ترمینال جداگانه اجرا کنید و تا زمانی که Ramon روی نمودار است باز نگه دارید:

~~~bash
cd ramon-ai-trader
uv run --extra model python -m ramon.server --device cpu
~~~

بررسی سرویس:

~~~bash
curl http://127.0.0.1:8012/health
~~~

باید ready برابر true و نام checkpoint را نشان دهد. بارگذاری checkpoint آموزش‌دیدهٔ محلی با گزینهٔ --model و مسیر پوشهٔ مدل ممکن است؛ مراحل ارزیابی قبل از جایگزینی را در بخش آموزش ببینید.

### اتصال اکسپرت

1. در MT5 به **Tools → Options → Expert Advisors** بروید؛ Allow WebRequest را فعال و http://127.0.0.1:8012 را به فهرست URLها اضافه کنید.
2. اکسپرت Ramon را به **نمودار جداگانهٔ XAUUSD_l در M15** وصل کنید. ورودی EnableLiveTrading در شروع **false** باشد.
3. /health، پنل نمودار و تب Experts را بررسی کنید. باید وضعیت مدل، دلیل BUY/SELL/WAIT و خطای احتمالی ارتباط دیده شود. پنل حالت DISARMED را نشان می‌دهد و در این حالت سفارش نمی‌فرستد.
4. پیش از فعال‌سازی حساب سنتی، مقدار MoneyUnitsPerUSD را با مشخصات همان حساب بررسی کنید. حالت پیش‌فرض AutoLockCurrentAccount=true است: Ramon در OnInit به همان login و server فعال قفل می‌شود و اگر بعداً حساب یا سرور تغییر کند ورود جدید را متوقف می‌کند؛ بنابراین شمارهٔ حساب در ریپو هاردکد نمی‌شود. اگر قفل دستی می‌خواهید، AutoLockCurrentAccount=false و AllowedAccountLogin را محلی تنظیم کنید. پس از بررسی ریسک، EnableLiveTrading را فقط روی همان نشست MT5 فعال کنید.


### خروجی تشخیصی Ramon با یک دستور

Ramon v0.17 در هر به‌روزرسانی پنل، فایل `Ramon_Diagnostic.txt` را در پوشهٔ مشترک MT5 (`FILE_COMMON`) می‌نویسد. این فایل شامل آخرین تصمیم مدل، دلیل BUY/SELL/WAIT، زمان کندل سیگنال، low/median/high پیش‌بینی، ATR، edge، spread، فاصلهٔ SL/TP، وضعیت account lock، مجوزهای معامله، پوزیشن مدیریت‌شده، تعداد معاملات روز و وضعیت فعلی اجرا است. شمارهٔ login حساب در این خروجی چاپ نمی‌شود.

پس از اینکه Ramon روی نمودار `XAUUSD_l / M15` اجرا شد، برای گرفتن وضعیت کامل مدل و MT5 فقط این فرمان را اجرا کنید:

~~~bash
cd ~/Documents/Presentation/ramon-ai-trader
bash scripts/ramon_diagnostic.sh
~~~

خروجی با دو بخش شروع می‌شود: `RAMON MODEL SERVICE` برای سلامت کانتینر Chronos-2 و `RAMON MT5 DIAGNOSTIC` برای وضعیت EA. اگر چند Wine prefix وجود داشته باشد، اسکریپت جدیدترین فایل تشخیصی را انتخاب می‌کند. برای اجبار مسیر خاص می‌توان `RAMON_DIAGNOSTIC_FILE=/absolute/path/Ramon_Diagnostic.txt` را قبل از فرمان تنظیم کرد.

WebRequest در Strategy Tester متاتریدر اجرا نمی‌شود؛ بازپخش تاریخی مستقل از تستر انجام می‌شود. اگر سرویس خاموش شود یا پاسخ معتبر برای کندل جاری ندهد، ورود جدید انجام نمی‌شود. حد ضرر و حد سود سفارش‌های باز نزد کارگزار ثبت می‌شوند؛ خروج زمانی چهارکندلی به وصل بودن اکسپرت نیاز دارد.

## منطق تصمیم و محدودیت ریسک نسخهٔ اولیه

| پارامتر | مقدار پیش‌فرض / رفتار |
| --- | --- |
| افق پیش‌بینی | چهار کندل M15 آینده؛ ورودی حداکثر ۲۵۶ کندل بسته‌شده |
| حداقل مزیت | بیشینهٔ ۰٫۱۲ × ATR14 و ۱٫۵ × اسپرد؛ نسبت مزیت به پهنای پیش‌بینی دست‌کم ۰٫۲۰ |
| حد ضرر / هدف | فاصلهٔ اولیه ۱٫۵ × ATR14 و ۳ × ATR14؛ هدف اسمی حدود ۲ برابر فاصلهٔ حد ضرر است |
| ریسک هر معامله | بودجهٔ **برنامه‌ریزی‌شدهٔ ۰٫۰۶ دلار** با فرض دستی MoneyUnitsPerUSD=100 برای حساب سنتی |
| اسپرد / دفعات معامله | حداکثر ۵۰ پوینت؛ حداکثر چهار ورود برای این اکسپرت در روزِ سرور کارگزار |
| حجم و موقعیت | بررسی حداقل حجم با OrderCalcProfit؛ اگر کوچک‌ترین حجم مجاز از بودجه بگذرد، ورود رد می‌شود؛ همزمان یک موقعیت برای Ramon |
| نگهداری موقعیت | حداکثر چهار کندل M15 تا تلاش برای خروج زمانی؛ SL/TP طبق سفارش نزد کارگزار ثبت می‌شوند |
| شروع | EnableLiveTrading=false؛ AutoLockCurrentAccount=true نشست را به login/server فعلی MT5 قفل می‌کند. AllowedAccountLogin فقط برای قفل دستی استفاده می‌شود |

۰٫۰۶ دلار **سقف قطعی زیان واقعی نیست**: لغزش، گپ، کمیسیون، تغییر قیمت و رفتار کارگزار می‌توانند زیان را بیشتر کنند. محدودیت چهار ورود در روز، محدودیت زیان تجمعی روزانه نیست. فیلتر مستقل اخبار، قطع‌کنندهٔ افت سرمایهٔ روزانه و ثبت کامل کارمزد هنوز پیاده‌سازی نشده‌اند. بنابراین وضعیت خبری UNKNOWN در نسخهٔ فعلی به معنی سنجش ایمنی خبر نیست.

## ارزیابی با دادهٔ کارگزار

برای دادهٔ تاریخی از جدول history_bars در SQLite ابزار تاریخچهٔ ریپوی جداگانهٔ MetaTrader assistant استفاده می‌شود. نماد باید **دقیقاً XAUUSD_l** و timeframe برابر M15 باشد. مسیر زیر را با فایل واقعی روی کامپیوتر خودتان عوض کنید:

~~~bash
uv run --extra model python -m ramon.replay \
  --db /absolute/path/to/history.sqlite3 \
  --symbol XAUUSD_l --point 0.01 --fallback-spread 42 --stride 4
~~~

بازپخش روی ۲۰٪ آخر تاریخچه به ترتیب زمانی است؛ در اولین کندل **بعد از سیگنال** وارد می‌شود و در هر زمان فقط یک معامله فرض می‌کند. اگر اسپرد تاریخی ذخیره نشده باشد، fallback-spread ورودی استفاده می‌شود. وقتی حد سود و ضرر داخل یک کندل لمس شوند، زیان ابتدا شمرده می‌شود. خروجی شامل تعداد تصمیم‌ها، خرید، فروش، برد، باخت، خروج زمانی و مجموع R است.

### مقایسهٔ Chronos با خط مبنای ثابت

برای مقایسهٔ دو مدل روی همان ۲۰٪ آخر تاریخچه، با اسپرد **ثبت‌شده در هر کندل**، پس از دریافت شاخه ابتدا image ابزارها را از کد جدید بسازید. صرف `git switch` فایل‌های برنامهٔ نصب‌شده در کانتینر در حال اجرا را به‌روز نمی‌کند. این مسیر سرویس زندهٔ `model` را راه‌اندازی مجدد نمی‌کند:

~~~bash
docker compose --profile tools build tools
docker compose --profile tools run --rm --no-deps tools -m ramon.compare \
  --db /data/ramon_history.sqlite3 \
  --symbol XAUUSD_l --model autogluon/chronos-2-small --stride 4
~~~

خط مبنا فقط میانگین تغییر چهار قیمت بسته‌شدن اخیر را تا افق چهار کندل ادامه می‌دهد و هیچ پارامتری را روی دورهٔ آزمون یاد نمی‌گیرد. هر دو از تابع تصمیم و بازپخش یکسان استفاده می‌کنند؛ موقعیت‌هایشان جداگانه پیش می‌رود و ممکن است تعداد یا زمان معاملات یکسان نباشد. گزارش شامل دوره، پوشش اسپرد، تعداد معاملات، R خالص، افت سرمایه و محدودیت‌های مقایسه است. `start_mt5_time` و `end_mt5_time` ساعت خام سرور MT5 هستند. اگر اختلاف ساعت UTC از دادهٔ معاملات همان نماد در فاصلهٔ یک روزِ مرز دوره ثبت شده و نمونه‌ها با هم سازگار باشند، `start_utc` یا `end_utc` به‌همراه اختلاف ساعت و تعداد نمونه‌ها اضافه می‌شود؛ در غیر این صورت UTC حدس زده نمی‌شود. هیچ فایل یا مدل زنده‌ای را عوض نمی‌کند.

برای بررسی سریع تایم‌استمپ‌ها بدون بارگذاری Chronos یا اجرای دوبارهٔ مقایسه، پس از ساخت image ابزارها اجرا کنید:

~~~bash
docker compose --profile tools run --rm --no-deps tools -m ramon.compare \
  --db /data/ramon_history.sqlite3 --symbol XAUUSD_l --time-only
~~~

فرمان در صورت نبود اسپرد تاریخی در هر یک از کندل‌های دورهٔ آزمون متوقف می‌شود؛ باید دادهٔ اسپرد واقعی MT5 را وارد کنید. کمیسیون، سوآپ و کارمزد به R تبدیل و از معاملات بستهٔ ثبت‌شده تخمین زده می‌شوند. اگر دادهٔ کامل این اقلام موجود نباشد، خروجی صریحاً **فقط پس از اسپرد** است و دربارهٔ سود خالص پس از همهٔ هزینه‌ها نتیجه نمی‌دهد. برای فرض هزینهٔ معلوم می‌توانید `--cost-r 0.05` را وارد کنید؛ این مقدار نمونهٔ فرضی است و باید از حساب خودتان به‌دست آید. لغزش، تیک، گپ، اخبار، حجم و خروج دقیق EA با کندل M15 بازسازی نمی‌شوند. تعداد معاملهٔ کمتر از ۲۰ در هر یک از دو روش برای مقایسهٔ عملکرد ناکافی گزارش می‌شود.

اگر خطای تعداد کندل یا اسپرد ناقص دیدید، در MT5 از **File → Open Data Folder** پوشهٔ همان ترمینال را باز کنید، فایل `mt5/ExportRamonHistory.mq5` را در `MQL5/Scripts` آن قرار دهید، در MetaEditor با F7 کامپایل کنید و اسکریپت را یک بار روی نمودار اجرا کنید (پیش‌فرض: ۱۰٬۰۰۰ کندل بستهٔ `XAUUSD_l/M15`). تا زمانی که در Experts پیام `Ramon history export complete` نیامده، فایل CSV ساخته نشده است. برای پیدا کردن فایل در Wine:

~~~bash
find "$HOME/.mt5" "$HOME/.wine" -type f -iname 'Ramon*History*.csv' -print 2>/dev/null
~~~

مسیر CSV پیدا‌شده را در متغیر `CSV` بگذارید؛ اگر مسیر پیش‌فرض درست است، فرمان‌های زیر آماده‌اند. شرط `if` اجازه نمی‌دهد در صورت نبود CSV، import اجرا شود:

~~~bash
CSV="$HOME/.mt5/drive_c/users/$USER/AppData/Roaming/MetaQuotes/Terminal/Common/Files/Ramon_XAUUSD_l_M15_History.csv"
if [[ -f "$CSV" ]]; then
  mkdir -p data/imports
  cp -- "$CSV" data/imports/Ramon_XAUUSD_l_M15_History.csv
  docker compose --profile tools run --rm --no-deps tools -m ramon.import_history \
    /data/…5309 tokens truncated… وضعیت کامل Ramon تا v0.22 روی شاخهٔ `stable/v0.22` ثابت شده است. این شاخه شامل Chronos-2، snapshot سی‌ثانیه‌ای، intrabar confirmation، AI-led trend continuation، یادگیری روزانه و minimum-lot risk override است و نقطهٔ rollback قبل از ensemble محسوب می‌شود.

### Multi-model role architecture — v0.24

Chronos جهت و ویژگی‌های پیش‌بینی را تولید می‌کند. Regime و Entry دو مدل لجستیک آموزش‌پذیر هستند و احتمال‌های آن‌ها به Meta داده می‌شود. وقتی بستهٔ معتبر فعال باشد، **Meta داور نهایی است**: احتمال حداقل `0.65` همراه مزیت مثبت Chronos → BUY/SELL؛ در غیر این صورت WAIT. تصمیم قوانین پایه در ناحیهٔ میانی جای Meta را نمی‌گیرد و `base_trade` نیز ورودی Meta نیست. کنترل‌های قطعی ریسک، اسپرد، مارجین و تک‌پوزیشن همچنان در MT5 اجرا می‌شوند.

**حالت شروع:** تا زمان ارتقای اولین بسته، `/health` مقدار `ensemble_mode=bootstrap_chronos` و `ensemble_ready=false` می‌دهد؛ همان Chronos پایه برای جمع‌آوری تجربه فعال است. فایل‌های مستقل `regime.json/entry.json/meta.json` نسخهٔ 0.23 خودکار بارگذاری نمی‌شوند، چون ارزیابی آن‌ها با روش قدیمی بوده است. اگر اشاره‌گر بسته موجود ولی خراب یا ناسازگار باشد، ورود جدید WAIT می‌شود و دلیل در `ensemble_error` ثبت می‌شود.

#### یادگیری از نتیجهٔ واقعی

- هر تصمیم شناسهٔ ۱۶کاراکتری دارد. سفارش با comment شامل `Ramon:<sample_key>` ارسال می‌شود.
- اکسپرت هنگام بازبودن پوزیشن هم snapshot می‌فرستد؛ مدیریت خروج پیش از درخواست شبکه اجرا می‌شود. ثبت داده اجازهٔ ورود دوم ایجاد نمی‌کند.
- اکسپرت تاریخچهٔ ۳۰ روز اخیر **حساب REAL** را برای پوزیشن‌های بسته‌شدهٔ خودش بررسی می‌کند و نتیجه را به `/trades` می‌فرستد. در هر نوبت حداکثر یک درخواست با timeout یک‌ثانیه‌ای ارسال می‌شود؛ ارسال ناموفق در نوبت بعد تکرار و پس از restart از تاریخچه بازیابی می‌شود.
- سود خالص برابر مجموع profit، commission، swap و fee ثبت‌شده روی dealهای پوزیشن است. SL، TP، خروج زمانی و خروج دستی از نتیجهٔ واقعی کارگزار منعکس می‌شوند؛ برگشت قیمت بعد از برخورد SL برچسب ضرر را به سود تبدیل نمی‌کند.
- معیار `net_r` سود خالص تقسیم بر ریسک اولیه است. ریسک از قیمت پرشدن deal و SL سفارش اولیه با `OrderCalcProfit` بازسازی می‌شود. واحد سود و ریسک یکی است؛ حساب سنتی به اشتباه ۱۰۰ برابر شمرده نمی‌شود. هزینه‌هایی که کارگزار فقط به‌شکل عملیات جداگانهٔ حساب و بدون اتصال به پوزیشن ثبت کند در این مجموع قابل انتساب نیستند.
- پوزیشن نیمه‌بسته، reversal یا ترکیب چند شناسهٔ تصمیم، comment حذف‌شده توسط کارگزار، SL اولیهٔ نامعلوم، و نمونهٔ فاقد اتصال معتبر وارد آموزش نمی‌شود. معاملات قبل از v0.24 قابل اتصال خودکار نیستند.
- Entry و Meta از **همان label سود خالص مثبتِ معاملهٔ واقعی** استفاده می‌کنند، ولی ویژگی‌ها و پنجره‌های آموزش آن‌ها متفاوت است. برای WAIT اجرا‌نشده سود فرضی ساخته نمی‌شود؛ این روش یادگیری تقویتی یا اثبات کیفیت فرصت‌های اجرا‌نشده نیست.

#### جداسازی زمانی و ارزیابی

1. ۵۰٪ اول معاملات برای آموزش Entry؛ Regime فقط برچسب‌هایی را می‌بیند که پیش از شروع پنجرهٔ Meta کامل شده باشند.
2. ۳۰٪ بعدی برای آموزش Meta با خروجی مدل‌های پایهٔ **ثابت و آموزش‌دیده فقط روی گذشته**. پس از آموزش Meta، مدل‌های پایه دوباره روی کل داده refit نمی‌شوند.
3. ۲۰٪ آخر برای ارزیابی نهایی. هر نمونه‌ای که زمان بسته‌شدن/کامل‌شدن برچسبش وارد پنجرهٔ بعد شود حذف می‌شود. timestamp داده و معامله هر دو زمان کارگزار هستند.
4. پس از هر ارزیابی، مرز آخرین نتیجهٔ مشاهده‌شده ذخیره می‌شود؛ اجرای بعدی باید حداقل ۲۰ نمونهٔ holdout تازه داشته باشد. آستانهٔ معامله روی holdout جست‌وجو نمی‌شود.

پیش‌فرض حداقل **۵۰۰ معاملهٔ واقعی بسته‌شده و قابل اتصال از یک checkpoint یکسان Chronos** است؛ ۵۰۰ snapshot کافی نیست. هر پنجرهٔ آموزش نیز به حداقل ۴۰ نمونه و ۱۰ نمونه از هر کلاس نیاز دارد. با سقف پیش‌فرض چهار ورود در روز، گردآوری ۵۰۰ معامله حداقل ۱۲۵ روزِ دارای چهار معامله طول می‌کشد؛ ارتقای فوری مدل‌ها انتظار نمی‌رود. مقدار حداقل از CLI `--minimum-samples` قابل تنظیم است، ولی کاهش آن جای شواهد معتبر را نمی‌گیرد.

ارتقا به حداقل ۲۰ معاملهٔ پذیرفته‌شده در holdout، balanced accuracy حداقل 0.52، net-R مثبت، بهبود حداقل 0.5R نسبت به مدل فعال **و** تصمیم پایهٔ Chronos، سقف drawdown برابر 8R و عدم افت کیفیت احتمال نسبت به مدل فعال نیاز دارد. برای ارزیابی ارزش مدل‌های اضافی، نتیجهٔ یک مدل ساده با فقط ویژگی‌های Chronos نیز در `chronos_features_only_ablation` گزارش می‌شود. این مقایسه فعلاً گزارش است، نه گیت ارتقای جداگانه.

ارزیابی روی **فرصت‌هایی است که واقعاً معامله شده‌اند**؛ کیفیت تصمیم‌های WAIT و فرصت‌های تازه‌ای که مدل بعدی ممکن است ایجاد کند از این داده اثبات نمی‌شود. این گزارش backtest کامل مسیر آیندهٔ استراتژی یا تضمین سود نیست.

#### نسخه‌بندی، سازگاری و بازگشت

هر تلاش آموزشی سه فایل مدل و manifest شامل هش‌ها، نسخهٔ schema، checkpoint Chronos، مرزهای زمانی و گزارش ارزیابی را در مسیر جدید `checkpoints/ensemble/versions/<bundle_id>/` می‌نویسد. فقط بستهٔ کاملِ پذیرفته‌شده با تعویض اتمیک `active.json` فعال می‌شود. بستهٔ قبلی حذف نمی‌شود. فایل خراب، بردار وزن نامعتبر و checkpoint ناسازگار رد می‌شوند.

job روزانه برای جلوگیری از دو نویسندهٔ همزمان قفل دارد؛ شکست آموزش Chronos مانع تلاش آموزش نقش‌ها نمی‌شود. Chronos challenger همچنان آموزش و ارزیابی می‌شود، اما در این مسیر **خودکار جایگزین نمی‌شود** تا checkpoint تولیدکنندهٔ ویژگی‌های معاملات ثابت بماند. تعویض Chronos به ارزیابی و دادهٔ سازگار با همان checkpoint نیاز دارد. سرویس تنها پس از تغییر واقعی اشاره‌گر مدل restart می‌شود.

بازگشت به بستهٔ نقشِ قبلیِ پذیرفته‌شده:

~~~bash
docker compose --profile tools run --rm tools -m ramon.bundles \
  --root /checkpoints/ensemble --bundle PREVIOUS_BUNDLE_ID \
  --chronos-model SAME_CHRONOS_CHECKPOINT
docker compose up -d --force-recreate model
~~~

شناسهٔ قبلی در `active.json` با نام `previous_bundle_id` نگهداری می‌شود. برای برگشت کامل کد به قبل از معماری چندمدلی، شاخهٔ `stable/v0.22` همچنان موجود است. دادهٔ فعلی را پیش از هر rollback نگه دارید.

#### نصب v0.24

~~~bash
git pull --ff-only
docker compose build model
docker compose up -d model
bash scripts/setup_local.sh --mt5-only
curl -fsS http://127.0.0.1:8012/health
~~~

اکسپرت کامپایل‌شدهٔ جدید را روی چارت بارگذاری کنید و نسخهٔ 0.24 را در diagnostic بررسی کنید. server و EA باید با هم به‌روز شوند تا شناسهٔ تصمیم و نتیجهٔ معامله وصل شوند. برنامهٔ زمان‌بندی روزانه، اگر قبلاً نصب نشده باشد، با `bash scripts/install_daily_learning_timer.sh` نصب می‌شود. خروجی diagnostic شامل `DecisionID`، `Saved`، `Bundle` و `TradeLearning` است. تست‌های Python اجرای زندهٔ Chronos یا کامپایل/اجرای MT5 را تأیید نمی‌کنند.

### گزارش عملکرد و اطلاعات هر معامله (EA 0.28)

```bash
bash scripts/analyze_ramon.sh --all
```

گزارش فقط دیتابیس را می‌خواند و اکنون مدل Chronos، revision در صورت دسترسی، bundle مدل‌های نقش‌محور و نسخهٔ EA هنگام ورود را تفکیک می‌کند. مشخصات bundle و انتهای بازه‌های آموزش/اعتبارسنجی در زمان تصمیم ذخیره می‌شوند؛ مدل فعال فعلی به سوابق قدیمی نسبت داده نمی‌شود. تفاوت عملکرد نسخه‌ها به‌تنهایی اثبات بهبود مدل نیست؛ شرایط بازار و تعداد نمونه‌ها نیز متفاوت‌اند.

بخش `ROLE BUNDLE PERFORMANCE AT ENTRY` نتیجهٔ Chronos bootstrap و معاملات هر bundle **فعال هنگام ورود** را جدا نشان می‌دهد. اگر هنوز هیچ معاملهٔ بسته‌شده‌ای با bundle فعال ثبت نشده باشد، این موضوع صریح نمایش داده می‌شود. دادهٔ مبهم یا ناسازگار در گروه جدا باقی می‌ماند. بخش `EXACT STORED ENTRY SIZING` برای معاملات دارای اطلاعات دقیق، تعداد دفعاتی را که ریسک محاسبه‌شده از قیمت پرشدن از بودجهٔ ترجیحی یا سقف برنامه‌ریزی‌شده بیشتر بوده است نشان می‌دهد. این بخش‌ها صرفاً گزارش هستند و رفتار معامله را تغییر نمی‌دهند.

`profit_units + commission_units + swap_units + fee_units = net_units`: اجزای مالی با علامت اصلی بروکر و مجموع تمام dealهای ورود و خروج گزارش می‌شوند. این هزینه‌ها قبلاً در net بودند و دوباره کسر نمی‌شوند. اسپرد و لغزش در قیمت اجرای معامله منعکس‌اند؛ هزینه‌های جداگانهٔ حساب که به پوزیشن متصل نشده‌اند قابل انتساب نیستند. ارسال دوبارهٔ معامله رکورد تکراری ایجاد نمی‌کند و اطلاعات قدیمیِ فاقد اجزا صفر فرض نمی‌شوند.

خروج زمان‌دار جدید با `maximum_hold_bars` و شناسهٔ deal خروج ثبت می‌شود. `DEAL_REASON_EXPERT` قدیمی بدون جزئیات، `UNKNOWN expert trigger` باقی می‌ماند. این برچسب علت آخرین deal خروج است؛ نتیجهٔ یک پوزیشن با چند خروج جزئی همچنان مجموع همهٔ خروج‌هاست.

زمان تولید گزارش UTC است. برای dealهای جدید، اختلاف ساعت بروکر با UTC در زمان وقوع ثبت و برای ورود و خروج جداگانه استفاده می‌شود. ساعت و منطقهٔ زمانی سیستم میزبان MT5 باید درست باشند. اختلاف روی مضرب ۱۵ دقیقه با حداکثر ۳۰ ثانیه خطای نمونه‌برداری پذیرفته می‌شود؛ دادهٔ نامعتبر ثبت نمی‌شود. اطلاعات در `Terminal/Common/Files/RamonTelemetry`، به تفکیک سرور، حساب و deal، نگهداری می‌شود تا با راه‌اندازی مجدد از بین نرود. پاک‌کردن این پوشه می‌تواند جزئیاتِ هنوز ارسال‌نشده را از دست بدهد. زمان معامله‌های قدیمی بدون offset با `BROKER[UTC offset unknown]` مشخص می‌شود؛ زمان دریافت سرور جای زمان اجرای معامله قرار نمی‌گیرد. زمان‌های آموزش تاریخی فعلاً با برچسب broker نمایش داده می‌شوند.

برای دریافت همهٔ فیلدهای جدید باید هم سرویس Python و هم EA به‌روز شوند:

```bash
git pull --ff-only
docker compose build model
docker compose up -d model
bash scripts/setup_local.sh --mt5-only
```

کامپایل موفق و نسخهٔ `0.28` روی نمودار را بررسی کنید. پس از بارگذاری مجدد EA، سوابق بسته‌شدهٔ ۳۰ روز اخیر به‌تدریج دوباره ارسال می‌شوند؛ ریز هزینهٔ آنها از تاریخچهٔ بروکر بازیابی می‌شود، اما نسخهٔ EA و علت دقیق خروج قدیمی حدس زده نمی‌شوند. تنظیمات ریسک و قواعد ورود/خروج تغییری نکرده‌اند.

برای خواندن کپی دیتابیس بدون Docker:

```bash
uv run python -m ramon.report --db /path/to/ramon_history.sqlite3 --all
```


### Clean learning labels after EA 0.28 baseline

The immutable pre-change baseline is kept on `stable/ramon-v0.28`. Account-performance
reporting still includes every closed position, but supervised role-model training now
uses only `LEARNABLE` outcomes. Desktop/mobile/web manual exits are
`CENSORED_MANUAL`, broker stop-outs are `CENSORED_STOP_OUT`, and legacy
`DEAL_REASON_EXPERT` rows without an exact recorded trigger are
`CENSORED_AMBIGUOUS_EXPERT`. New EA 0.28 maximum-hold exits already persist
`maximum_hold_bars`, so they remain learnable. No entry threshold, SL/TP distance,
risk sizing, BUY/SELL enablement, or live execution rule is changed by this patch.

`bash scripts/analyze_ramon.sh --all` now prints label-quality coverage and marks each
trade's label status. UTC event-time telemetry remains the source of truth for new
execution timestamps; legacy broker times are never guessed.

### بررسی دلیل ورود و وضعیت آموزش

پس از دریافت این تغییرات، سرویس را بازسازی کنید؛ EA همچنان با نسخهٔ ۰٫۲۸ سازگار است:

```bash
docker compose build model
docker compose up -d model
bash scripts/audit_ramon.sh --trade-number 29
```

شمارهٔ معامله مطابق ترتیب گزارش `--all` است و با ورود سوابق قدیمی ممکن است عوض شود.
برای بررسی مجدد، از `--trade-key` با شناسه‌ای که ابزار چاپ می‌کند استفاده کنید.
این ابزار دیتابیس و checkpointها را فقط می‌خواند؛ آموزش، فعال‌سازی مدل یا معامله اجرا نمی‌کند.
خروجی شامل سلامت سرویس، وضعیت timer، bundle روی دیسک، آخرین تلاش آموزش، تعداد دقیق
نمونه‌های قابل‌آموزش با همان query مربی و اطلاعات تصمیم متصل به معامله است.
مقدار `LEARNABLE` صرفاً کیفیت علت خروج را نشان می‌دهد؛ schema، مدل، جهت و فاصلهٔ زمانی
ورود تا تصمیم نیز برای ورود به مجموعهٔ آموزشی بررسی می‌شوند. حد پیش‌فرض اولین مرحله
۵۰۰ نمونه است؛ عبور از آن به‌تنهایی مدل را فعال نمی‌کند و شروط تفکیک زمانی، کلاس‌ها و
اعتبارسنجی همچنان اعمال می‌شوند. وضعیت آخرین تلاش آموزش، وضعیت همین لحظه فرض نمی‌شود.

از این نسخه، دلیل تصمیم پایه و نهایی و آستانه‌های زمان تصمیم در `model_metadata`
ذخیره می‌شوند. دلیل ورود قدیمی که در دیتابیس ثبت نشده `UNKNOWN_LEGACY` می‌ماند.
مسیر `ai_trend_continuation` در کد فعلی شرط حداقل `signal_strength` ندارد؛ بنابراین
قدرت پایین به‌تنهایی اثبات خطای اجرا نیست. ریسک حداقل حجم هم در EA از قدرت سیگنال
مشتق نمی‌شود؛ برای بررسی افزایش ریسک، دادهٔ حجم و minimum-lot override لازم است.

اسکریپت به‌صورت اختیاری `Ramon_Signals.csv` را در prefixهای رایج Wine پیدا می‌کند و مسیر
انتخاب‌شده را چاپ می‌کند. برای انتخاب دقیق فایل ترمینال موردنظر:

```bash
RAMON_SIGNAL_CSV='/path/to/Ramon_Signals.csv' bash scripts/audit_ramon.sh --trade-number 29
```

CSV قدیمی شناسهٔ تصمیم ندارد؛ ردیف‌های با فاصلهٔ حداکثر ۹۰ ثانیه از زمان تصمیم بروکر
صرفاً «نامزد تطبیق» هستند و اثبات اجرای معامله نیستند. دادهٔ اصلی تغییر نمی‌کند.
اگر فایل موجود نباشد، بررسی دیتابیس و وضعیت آموزش همچنان انجام می‌شود.

نسخه‌های EA ممکن است ردیف‌های جدید را زیر سرستون قدیمی CSV نوشته باشند. ابزار تعداد
ستون‌ها را بررسی می‌کند؛ قالب ۶۲ ستونی شناخته‌شده، پس از بررسی نوع فیلدها، با برچسب
`RECONSTRUCTED_KNOWN_LAYOUT` بازسازی می‌شود. این برچسب استنباط قالب را نشان می‌دهد،
نه تطبیق قطعی معامله. در قالب نامعتبر، اعداد با `UNKNOWN` نمایش داده می‌شوند و ردیف خام
برای بررسی باقی می‌ماند؛ قیمت‌ها دیگر به‌اشتباه به‌عنوان قدرت سیگنال یا ریسک گزارش نمی‌شوند.

اصلاح گزارش: `intrabar_move_atr` از قبل نسبت به جهت معامله محاسبه شده و برای SELL
دوباره منفی نمی‌شود. `ai_trend_score` نیز اندازهٔ غیرمنفی حرکت است؛ جهت روند قدیمی در
این ویژگی ذخیره نشده و گزارش آن را با ضرب در علامت معامله بازسازی نمی‌کند.
این اصلاح به مقایسهٔ ویژگی‌ها مربوط است؛ جمع سود و زیان و سیاست معامله تغییری ندارند.

برای بررسی کپی دیتابیس بدون Docker:

```bash
uv run python -m ramon.audit --db /path/to/ramon_history.sqlite3 \
  --ensemble-dir /path/to/checkpoints/ensemble \
  --chronos-model autogluon/chronos-2-small --trade-number 29
```

### Loss controls v0.34 (feature/loss-control-v1)

- The 12-completed-bar / 3-ATR trend-conflict veto now survives an active Meta
  bundle. Meta probabilities are still recorded; the final decision stays WAIT.
- The EA checks the latest two fully closed positions for its current account,
  symbol and opening magic directly in MT5 history before placing a new order.
  Two same-direction, net-negative positions whose final exit reason is SL block
  that direction for 1,800 seconds after the latest close. At exactly 1,800 seconds
  the cooldown expires. This is elapsed broker time, not candle-boundary counting.
- Manual closes, TP, non-negative SL outcomes and opposite-direction closes break
  the streak. Partial deals count once per fully closed position; open positions
  and other robots are excluded. Net includes deal profit, commission, swap and fee.
  Unreadable history blocks new entry, while existing-position management runs first.
- Learning upload order and training labels no longer control cooldown. The old
  server-side database gate is removed because its history can be incomplete.
  Deploy **both** server and EA v0.34 together; an older EA with this server does
  not implement the local cooldown.
- Default preferred risk remains $0.06 and the minimum-lot planned-risk cap $0.12.
  The current v0.41 hard cap is $0.20; existing MT5 Inputs may retain old values. Chronos weights, strength thresholds,
  SL/TP distances, training labels and observe-only profit protection are unchanged.

Validation includes Python regressions for both-direction Meta vetoes and a C++
API adapter executing the actual MQL cooldown functions against synthetic deal
history (manual/partial exits, fees, other robots, expiry, restart and history
errors). This does not replace MetaEditor compilation or terminal validation.

### پژوهش فقط‌خواندنی برای ریسک ورود و مدل رقیب

این ابزارها هیچ تغییری در فایل `Ramon.mq5`، سرویس زنده، حجم، آستانهٔ ورود یا
قواعد خروج ایجاد نمی‌کنند. ابتدا از پایگاه دادهٔ معامله‌ها یک **کپی** بگیرید؛
همهٔ دستورهای زیر روی کپی کار می‌کنند. کپی باید تاریخچهٔ کندل ۱۵ دقیقه‌ای با
اسپرد ثبت‌شده نیز داشته باشد.

۱. اسکریپت `mt5/InspectGoldContracts.mq5` را در متاتریدر، در بخش Scripts، کامپایل
و **یک بار اجرا** کنید. نماد طلا باید در Market Watch باشد. در تب Experts برای
هر نماد طلا، «زیان دلاری حداقل حجم
به‌ازای حرکت ۱٫۰۰ قیمت» و ریسک حد ضرر نمونه را می‌بینید. ورودی
`MoneyUnitsPerUSD=100` فقط برای حساب واقعی سنتی با همین تبدیل معتبر است؛
مقدار واقعی قرارداد و حجم باید از همان حسابِ متصل خوانده شود. این اسکریپت هیچ
سفارشی ارسال نمی‌کند.

۲. عدد «زیان دلاری به‌ازای حرکت ۱٫۰۰» نماد موردنظر را در دستور زیر بگذارید.
ورودی CSV تاریخچهٔ **M5 بسته‌شده** و صادرشده از متاتریدر است؛ فقط سه‌تایی‌های
کامل به کندل M15 تبدیل می‌شوند:

```bash
uv run python -m ramon.stop_feasibility \
  /path/to/Ramon_XAUUSD_l_M5_History.csv \
  --cap-usd 0.06 --min-lot-usd-per-price VALUE_FROM_MT5
```

این خروجی درصد زمان‌هایی را که حداقل حجم با بودجهٔ ۶ سنت جور می‌شود و یک
تقریب برخورد به حد ضررهای ۱٫۵، ۱٫۲۵ و ۱ برابر نوسان را نشان می‌دهد. **آزمون
سودآوری رامون نیست**: هر دو جهت فرضی، بدون تصمیم ورود واقعی، تست می‌شوند.
کم‌کردن فاصلهٔ حد ضرر صرفاً برای رسیدن به ۶ سنت می‌تواند دفعات برخورد را زیاد کند.

۳. برای بررسی یک شرط ورود ثابت در معاملات واقعی، دو دورهٔ اول و آخر را جدا
ببینید. این نمونه فقط برای تشخیص است؛ آستانه را با نگاه‌کردن به دورهٔ آخر
انتخاب نکنید:

```bash
uv run python -m ramon.entry_audit \
  --db /path/to/ramon_history_COPY.sqlite3 --symbol XAUUSD_l \
  --feature signal_strength --threshold 0.20 --keep above
```

این گزارش تنها معاملات **واقعاً اجراشده و دارای برچسب خروج تمیز** را می‌شناسد.
اگر معامله‌ای حذف می‌شد، فرصت‌های بعدی تغییر می‌کردند؛ بنابراین جمع معاملات
«نگه‌داشته‌شده» برآورد سود اجرای فیلتر نیست. با حدود ۹۰ معاملهٔ تمیز فعلی، بخش
آخر کمتر از ۲۰ معامله است و برای تصمیم دربارهٔ آستانه کافی نیست.

۴. کرونوس، خط مبنای ساده و مدل رقیب «تایمز‌اف‌ام ۲٫۵» را فقط روی بخش آخر
تاریخچهٔ **M15 با اسپرد واقعی** مقایسه کنید. وابستگی این مدل فقط در محیط
پژوهشی نصب می‌شود و به تصویر Docker زنده افزوده نشده است. دانلود اولیهٔ وزن
مدل نیاز به اینترنت و فضای دیسک دارد:

```bash
uv run --extra model --extra timesfm-shadow python -m ramon.compare \
  --db /path/to/ramon_history_COPY.sqlite3 --symbol XAUUSD_l \
  --time-only

uv run --extra model --extra timesfm-shadow python -m ramon.compare \
  --db /path/to/ramon_history_COPY.sqlite3 --symbol XAUUSD_l \
  --device cpu --timesfm-shadow --stride 4
```

بررسی زمان و وجود اسپرد قبل از بارگذاری مدل اجرا می‌شود. این بازپخش، خروج‌های
چندمرحله‌ای و پرشدن‌های واقعی اکسپرت را بازسازی نمی‌کند؛ نتیجهٔ بهتر یک مدل در
این آزمایش به‌تنهایی دلیل فعال‌کردن آن در حساب زنده نیست. وزن مدل ۳٫۰
تایمز‌اف‌ام مجوز جداگانهٔ غیرتجاری دارد؛ این مقایسه عمداً از نسخهٔ ۲٫۵ استفاده
می‌کند.

### آزمایش ورود دوم کوتاه‌مدت در کنار معاملهٔ اصلی

ابزار `ramon.addon_research` روی معامله‌های واقعیِ دارای زمان ورود و خروج UTC و
کندل‌های پنج‌دقیقه‌ای MT5 کار می‌کند. معاملهٔ اصلی را دست نمی‌زند و فقط نتیجهٔ
یک ورود دوم **فرضی** را حساب می‌کند. ورودی آن گزارش تولیدشده با
`bash scripts/analyze_ramon.sh --all`، CSV تاریخچهٔ M5 و مقدار واقعی زیان
دلاری حداقل حجم برای حرکت ۱٫۰۰ قیمت از `InspectGoldContracts.mq5` است. اسکریپت
متاتریدر نوع حساب (موقعیت‌های مستقل یا یک موقعیت تجمیع‌شده) را نیز نشان می‌دهد.

قواعد ثابت این آزمایش: معاملهٔ اصلی ابتدا دست‌کم ۰٫۲۵ برابر نوسان ۱۵ دقیقه‌ای
به سود برود؛ از سه کندل بسته‌شدهٔ M5، حداقل دو کندل هم‌جهت و میانگین دامنهٔ
آن‌ها حداکثر ۰٫۶ برابر نوسان باشد؛ اسپرد هنگام تصمیم و ورود حداکثر ۵۰ پوینت؛
و **مجموع ریسک اولیهٔ اصلی و ورود دوم حداکثر ۲۰ سنت**. ورود دوم در بازشدن
کندل بعدی با حد ضرر ۰٫۵ برابر نوسان، هدف سود ۳ سنت و حداکثر نگهداری سه کندل
پنج‌دقیقه‌ای فرض می‌شود. ورود دوم پس از بسته‌شدن معاملهٔ اصلی ادامه ندارد.

```bash
uv run python -m ramon.addon_research \
  --m5-csv /path/to/Ramon_XAUUSD_l_M5_History.csv \
  --report /path/to/report.txt \
  --broker-utc-offset-seconds 10800 \
  --min-lot-usd-per-price VALUE_FROM_MT5 \
  --summary-only
```

عدد اختلاف ساعت بروکر را از تشخیص همان حساب و بازهٔ مورد بررسی بگیرید؛ فرض
نکنید تاریخچهٔ همهٔ دوره‌ها همین اختلاف را دارد. سفارش دوم فقط وقتی بررسی
می‌شود که کندل‌های لازم پیش از خروج اصلی و در فایل CSV موجود باشند. اگر حد سود
و ضرر در یک کندل دیده شوند، حد ضرر مقدم است. قیمت ورود اصلی از نخستین کندل
کامل پس از زمان گزارش تخمین زده می‌شود؛ پرشدن واقعی، ترتیب تیک‌ها، لغزش،
کارمزد و اثر ورود دوم بر تصمیم‌های بعدی اکسپرت قابل بازسازی نیستند.

برای دادهٔ بارگذاری‌شدهٔ ۲۹ سپتامبر، ۸۴ معامله زمان UTC دوطرفه داشتند، که ۱۱
مورد خارج از پوشش CSV بودند. با **مقدار فرضی** ۰٫۰۱ دلار سود/زیان حداقل حجم
برای حرکت ۱٫۰۰ قیمت، از ۷۳ معاملهٔ قابل بررسی ۱۸ ورود دوم فرضی رخ داد:
۷ خروج در هدف، ۶ خروج در حد ضرر و ۵ خروج زمانی؛ جمع تقریبی **۰٫۰۵۰۵ دلار
زیان** بود. این مقدار ورودی باید با اسکریپت روی حساب واقعی تأیید شود. حتی با
تأیید آن نیز نمونه و دقت کندل پنج‌دقیقه‌ای برای فعال‌سازی زنده کافی نیستند.

### بررسی منشأ زیان و سقف ریسک پیشنهادی

گزارش عملکرد واقعی را با یک سقف ثابتِ **۱۰ سنت**، صرفاً برای بررسی، به دو بخش
قدیمی‌تر و ۲۰ معاملهٔ آخر تقسیم کنید:

```bash
uv run python -m ramon.loss_audit --report /path/to/report.txt \
  --cap-usd 0.10 --money-units-per-usd 100 --holdout-count 20
```

این ابزار نسخه‌های ۰٫۲۹ به بعد را که ریسک پرشدهٔ ورود در گزارش دارند بررسی
می‌کند، تعداد و نتیجهٔ معاملات بالاتر/پایین‌تر از سقف و سهم حد ضررها را نشان
می‌دهد. سقف ۱۰ سنت در این بررسی فقط یک **نامزد آزمایشی** است؛ ریسک پرشده با
ریسک قابل محاسبه پیش از سفارش ممکن است فرق کند. حذف یک معامله، معامله‌های
بعدی و وضعیت بازار را عوض می‌کند؛ جمع سود معاملات باقی‌مانده، سودی نیست که
ربات با این فیلتر به‌دست می‌آورد. زمان خروج نیز در گزارش دقت دقیقه‌ای دارد؛
بدون مسیر تیک نمی‌توان ادعا کرد خروج زودتر بهبود می‌دهد. برای ارزیابی آینده،
سقف را قبل از جمع‌آوری معاملات جدید ثابت نگه دارید.

### آزمون زمانیِ تشخیص برخورد به حد ضرر با دادهٔ ناقص

اگر خروجی M5 و گزارش معاملات را دارید، یک مدل سادهٔ پژوهشی را با کندل‌های
**کاملاً بسته‌شده پیش از دقیقهٔ ورود** آموزش دهید. این مدل نقش ریسک فعال
رامون نیست و به ویژگی‌های زمان ورود کرونوس دسترسی ندارد:

```bash
uv run python -m ramon.stop_shadow \
  --report /path/to/report.txt \
  --m5-csv /path/to/Ramon_XAUUSD_l_M5_History.csv \
  --broker-utc-offset-seconds 10800 --holdout-count 20
```

روی گزارش ۲۹ سپتامبر، ۸۴ معامله دارای زمان UTC بودند و ۱۱ مورد تاریخچهٔ
پیش از ورود در CSV نداشتند. از ۷۳ معاملهٔ قابل بررسی، ۵۳ مورد برای آموزش و
۲۰ مورد آخر برای ارزیابی استفاده شد. ۹ مورد از ۲۰ معاملهٔ آخر با حد ضرر
بسته شدند؛ مدل فقط ۲ معامله را پرخطر علامت زد که یکی به حد ضرر رسید.
مساحت زیر منحنی رتبه‌بندی ۰٫۴۳۴۳ و خطای احتمال ۰٫۳۲۷۰ بود، در حالی که
پیش‌بینی سادهٔ نرخ حد ضررِ بخش آموزش خطای ۰٫۲۶۹۴ داشت. این نتیجه **از
فعال‌سازی فیلتر ورود پشتیبانی نمی‌کند**. برچسب فقط برای معامله‌های اجراشده
وجود دارد و حذف آن‌ها فرصت‌های بعدی را عوض می‌کند.

تاریخچهٔ M15 ساخته‌شده از سه کندل کامل M5 تعداد ۳۳۲۷ کندل داشت و بازهٔ
مقایسهٔ مدل‌ها ۶۶۶ کندل با اسپرد ثبت‌شده بود. در محیط پژوهشی فعلی، وزن‌های
کرونوس و تایمزاف‌ام موجود نبودند و دانلود وزن‌ها تکمیل نشد؛ بنابراین
**هیچ عدد مقایسهٔ مستقیم این دو مدل تولید نشده است**. خروجی خط مبنای ساده
را نباید نتیجهٔ کرونوس یا تایمزاف‌ام شمرد.
