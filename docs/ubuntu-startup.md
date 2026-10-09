# راه‌اندازی Ramon هنگام روشن شدن اوبونتو

سرویس `ramon-ai-trader.service` پس از Docker و شبکه، شش کانتینر مدل، داشبورد اصلی، داشبورد بازار، داشبورد آموزش، outbox و Kronos را از ایمیج‌های موجود اجرا می‌کند. Compose سلامت مدل و صفحات داشبورد را بررسی می‌کند؛ شکست راه‌اندازی پس از ۳۰ ثانیه دوباره تلاش می‌شود. سیاست `unless-stopped` برای بازیابی کانتینرهای متوقف‌شده بر اثر خطای پردازش باقی است. unhealthy شدن کانتینر به‌تنهایی باعث restart آن نمی‌شود.

فقط یک بار پس از دریافت کد:

```bash
git pull --ff-only origin main
docker compose build model
docker compose --profile advisors build kronos-advisor
python3 scripts/install_boot_service.py
```

اسکریپت را با کاربر معمولی دارای دسترسی Docker اجرا کن؛ برای نصب واحد systemd خودش `sudo` درخواست می‌کند. مسیر پروژه و HOME همین کاربر ثبت می‌شوند تا mount فایل‌های MT5 به مسیر root تغییر نکند. پیش از نصب، موجود بودن هر دو ایمیج و اعتبار Compose و واحد systemd بررسی می‌شود. اگر مسیر پروژه جابه‌جا شد، نصب‌کننده را دوباره اجرا کن.

هنگام بوت build، git pull، آموزش مدل یا کامپایل EA انجام نمی‌شود. TimesFM در این سرویس صریحاً خاموش است؛ بارگذاری همزمان آن فعلاً راه‌اندازی سرویس اصلی را معطل کرده بود و علت دقیق هنوز معلوم نیست. Chronos و Kronos از کش مشترک وزن‌ها استفاده می‌کنند. اجرای EA، باز شدن MT5 روی دسکتاپ و آمادگی بروکر مستقل از آماده بودن کانتینرها هستند. Kronos به درخواست تازهٔ EA و حداقل ۲۵۶ کندل بسته‌شده نیاز دارد؛ بدون آن، خروجی «در دسترس نیست» منتشر می‌کند.

وضعیت و گزارش:

```bash
systemctl status ramon-ai-trader.service --no-pager
journalctl -u ramon-ai-trader.service -n 50 --no-pager
docker compose --profile advisors ps
```

برای توقف همراه با غیرفعال کردن بوت:

```bash
sudo systemctl disable --now ramon-ai-trader.service
```

این توقف فقط کانتینرهای همین پروژه را متوقف می‌کند؛ volumeها و پایگاه داده حذف نمی‌شوند. برای فعال‌سازی دوباره:

```bash
sudo systemctl enable --now ramon-ai-trader.service
```

در این محیط Docker daemon و بوت اوبونتو در دسترس نبود؛ ساختار Compose و واحد systemd با وابستگی آزمایشی بررسی شده‌اند. تأیید نهایی بوت روی کامپیوتر کاربر انجام می‌شود.
