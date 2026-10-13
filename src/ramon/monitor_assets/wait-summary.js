(() => {
  "use strict";
  const root = document.getElementById("wait-summary-grid");
  const note = document.getElementById("wait-summary-note");
  if (!root || !note) return;
  const safe = value => String(value ?? "—").replace(/[&<>"']/g, c => ({
    "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"
  }[c]));
  const top = entries => Object.entries(entries || {}).sort((a,b) => b[1]-a[1]).slice(0,3);
  function metric(title, value, description) {
    return '<div class="training-health-card"><div class="muted">'+safe(title)+'</div>'+
      '<strong>'+safe(value)+'</strong><p>'+safe(description)+'</p></div>';
  }
  async function refresh() {
    try {
      const response = await fetch("/api/wait-analysis?limit=300", {cache:"no-store"});
      if (!response.ok) throw new Error("HTTP "+response.status);
      const data = await response.json();
      if (!data.available) throw new Error(data.reason || "UNAVAILABLE");
      const reasons = top(data.reasons);
      const states = top(data.market_states);
      const issues = top(data.quality_shadow_reasons);
      const count = Number(data.sampled_waits) || 0;
      root.innerHTML = [
        metric("نمونه‌های WAIT", count, "آخرین "+safe(data.limit)+" تصمیم WAIT بررسی‌شده"),
        metric("علت غالب", reasons.length ? reasons[0][1] : "—", reasons.length ? reasons[0][0] : "علتی ثبت نشده"),
        metric("تعارض مستقیم جهت", Number(data.direction_conflicts)||0,
          count ? (Math.round(100*(Number(data.direction_conflicts)||0)/count)+"٪ از نمونه‌ها") : "نمونه‌ای وجود ندارد"),
        metric("وضعیت غالب بازار", states.length ? states[0][1] : "—", states.length ? states[0][0] : "نامشخص"),
        metric("هشدارهای Shadow", issues.length ? issues[0][1] : "۰",
          issues.length ? issues.map(x=>x[0]+": "+x[1]).join(" | ") : "موردی ثبت نشده")
      ].join("");
      note.textContent = "نمونه محدود به "+count+" تصمیم WAIT اخیر؛ هشدارهای Shadow الزاماً مانع رسمی ورود نیستند. نتیجه فرضی معاملات: ارزیابی نشده.";
    } catch (error) {
      root.textContent = "دریافت آمار WAIT ناموفق بود: "+error.message;
      note.textContent = "برای بررسی جزئیات، گزارش کامل WAIT را باز کنید.";
    }
  }
  refresh();
  setInterval(refresh, 60000);
})();
