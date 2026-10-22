'use strict';
const el = id => document.getElementById(id);
let state = null;
let dirty = false;
const dollars = value => value == null ? 'نامشخص' : `$${Number(value).toFixed(2)}`;
function preview() {
  const value = Number(el('risk').value);
  const valid = el('risk').value !== '' && Number.isFinite(value) && value >= .01 && value <= .50;
  el('save').disabled = !valid || !state?.writable;
  const base = state?.requested ?? state?.observed ?? .35;
  el('impact').textContent = !valid ? 'مقدار باید بین ۰٫۰۱ و ۰٫۵۰ دلار باشد.' :
    value === base ? 'سقف انتخاب‌شده با مقدار فعلی برابر است.' :
    `${dollars(base)} ← ${dollars(value)}: ${value > base ? 'سقف بالاتر می‌رود؛ ورودهای بیشتری با لات حداقل ممکن است مجاز شوند و ریسک مجاز هر ورود افزایش می‌یابد.' : 'سقف پایین‌تر می‌آید؛ بعضی ورودها رد می‌شوند یا حجم کمتری می‌گیرند.'} این تغییر تضمین نمی‌کند معامله‌ای انجام شود.`;
}
async function refresh() {
  try {
    const response = await fetch('/api/control');
    if (!response.ok) throw new Error();
    state = await response.json();
    el('observed').textContent = dollars(state.observed);
    el('requested').textContent = dollars(state.requested);
    el('freshness').textContent = !state.supported ? 'نسخهٔ سازگار اکسپرت هنوز در گزارش تأیید نشده است.' : !state.fresh ? 'گزارش تازه نیست؛ اعمال تنظیم تأیید نشده است.' :
      state.requested != null && Math.abs(state.requested - state.observed) < .005 ? 'اعمال تنظیم در گزارش تازه تأیید شد.' : 'آخرین مقدار گزارش‌شده؛ تغییر ممکن است هنوز اعمال نشده باشد.';
    if (!dirty) el('risk').value = state.requested ?? state.observed ?? state.default;
    if (!state.writable) el('status').textContent = 'ذخیره در دسترس نیست؛ مسیر فایل مشترک MT5 باید قابل نوشتن باشد.';
    preview();
  } catch (_) {
    el('status').textContent = 'دریافت وضعیت ناموفق بود؛ دوباره تلاش می‌شود.';
    el('save').disabled = true;
  }
}
el('risk').addEventListener('input', () => { dirty = true; preview(); });
el('control-form').addEventListener('submit', async event => {
  event.preventDefault();
  el('save').disabled = true;
  try {
    const response = await fetch('/api/control', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({max_executable_risk_usd: Number(el('risk').value)})});
    if (!response.ok) throw new Error();
    el('status').textContent = 'ذخیره شد؛ منتظر تأیید مقدار در گزارش تازهٔ اکسپرت هستیم.';
    dirty = false;
    await refresh();
  } catch (_) { el('status').textContent = 'ذخیره انجام نشد. دسترسی مسیر و مقدار را بررسی کنید.'; preview(); }
});
refresh();
setInterval(refresh, 3000);
