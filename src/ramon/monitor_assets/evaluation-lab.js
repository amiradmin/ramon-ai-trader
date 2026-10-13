(() => {
"use strict";
const target=document.getElementById("evaluation-lab-grid");
const note=document.getElementById("evaluation-lab-note");
if(!target||!note)return;
function escapeText(x){return String(x??"—").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}
function card(title,value,detail){
return '<div class="training-health-card"><div class="muted">'+escapeText(title)+'</div><strong>'+escapeText(value)+'</strong><p>'+escapeText(detail)+'</p></div>';
}
async function load(){
try{
const response=await fetch("/api/evaluation-lab",{cache:"no-store"});
if(!response.ok)throw new Error("HTTP "+response.status);
const data=await response.json();if(!data.available)throw new Error(data.reason);
const audit=data.data_audit,auto=data.auto_trades;
target.innerHTML=[
card("تصمیم‌های بررسی‌شده",audit.decisions_scanned,"WAIT: "+audit.wait_decisions),
card("کاندیداهای خودکار",auto.candidate_count,"فقط AUTO_RAMON و RANGE_AUTO، بدون مداخله ثبت‌شده"),
card("سود خالص، واحد حساب",auto.net_units,"ارزیابی مقدماتی؛ نه دلار"),
card("Profit Factor",auto.profit_factor===null?"نامشخص":auto.profit_factor.toFixed(3),"داده‌های منتخب"),
card("موارد بدون تطبیق",audit.unmatched_auto_samples,"مغایرت شناسه معامله و تصمیم"),
card("WAIT Replay",data.wait_replay.status,"سود فرضی هنوز قابل استناد نیست"),
card("زیان‌بارترین نوع خروج",(()=>{
  const rows=Object.entries(data.loss_attribution?.by_exit_detail||{}).filter(x=>x[1].net_units<0).sort((a,b)=>a[1].net_units-b[1].net_units);
  return rows.length?rows[0][0]:"نامشخص";
})(),"گروه‌بندی مقدماتی؛ رابطه علّی اثبات نشده"),
card("پوشش ریسک اولیه",(data.loss_attribution?.initial_risk_units?.coverage??0)+"/"+(data.loss_attribution?.initial_risk_units?.total??0),"برحسب واحد حساب"),
card("خروج دستی مشکوک",data.manual_provenance_audit?.suspected_count??0,"از آمار معاملات خودکار کنار گذاشته شد"),
card("Early Exit Replay",data.early_exit_replay?.candidate_trades??0,"سناریوهای قابل بررسی؛ سود فرضی هنوز محاسبه نشده")
].join("");
note.textContent="وضعیت آموزش: تأیید نشده. نبود مداخله ثبت‌شده اثبات خودکاربودن کامل معامله نیست. داده‌ها فقط خوانده می‌شوند.";
}catch(error){target.textContent="گزارش Evaluation Lab در دسترس نیست: "+error.message;}
}
load();setInterval(load,60000);
})();
