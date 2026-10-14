"use strict";
let lastFullReview=null;
function reportForAnalysis(d){
 const report={
  report_type:"RAMON_OPPORTUNITY_REVIEW",
  exported_at_utc:new Date().toISOString(),
  symbol:d.symbol,
  period:{since_utc:d.since_utc,until_utc:d.until_utc},
  data_quality:{decision_samples:d.decision_samples,decision_samples_total:d.decision_samples_total,
   truncated:d.decision_samples_truncated},
  observed:{wait_decisions:d.wait_decisions,wait_reasons:d.wait_reasons,
   received_closed_outcomes:d.recorded_closed_outcomes_received,
   losing_outcomes:d.losing_outcomes,realized_net_units:d.realized_net_units,
   loss_by_exit_reason:d.loss_by_exit_reason,worst_losses:d.worst_losses,wait_cases:d.wait_cases},
  shadow_replay:d.shadow_replay,
  profitable_missed_positions:d.profitable_missed_positions,
  improvement_actions:d.improvement_actions,
  limitations:d.limitations,
  note:"Hypothetical TP does not establish a real executable missed trade. No automated strategy changes."
 };
 return "لطفاً گزارش Ramon را تحلیل کن: فرصت‌های مستقل احتمالی، علل WAIT، زیان‌های قابل بررسی، کیفیت داده و پیشنهادهای پژوهشی قابل بک‌تست را جداگانه توضیح بده. بدون شواهد، سود ازدست‌رفته یا علت قطعی ادعا نکن.\n\n"+JSON.stringify(report,null,2);
}
async function copyFullReview(){
 const btn=$("copy-report");if(!lastFullReview)return;
 const payload=reportForAnalysis(lastFullReview);
 try{
  await navigator.clipboard.writeText(payload);
  $("copy-status").textContent="گزارش کامل کپی شد ("+payload.length.toLocaleString("fa-IR")+" نویسه)";
 }catch(err){
  $("copy-status").textContent="کپی مستقیم مجاز نیست؛ متن را از پنجره انتخاب و کپی کن.";
  const overlay=document.createElement("dialog");
  const area=document.createElement("textarea");area.value=payload;area.dir="ltr";
  area.style.width="min(85vw,900px)";area.style.height="55vh";
  const close=document.createElement("button");close.textContent="بستن";close.onclick=()=>overlay.close();
  overlay.append(area,close);document.body.append(overlay);
  overlay.addEventListener("close",()=>overlay.remove(),{once:true});overlay.showModal();area.select();
 }
}

const $=x=>document.getElementById(x),fmt=x=>x===null||x===undefined?"نامشخص":Number(x).toLocaleString("fa-IR",{maximumFractionDigits:3});
function cell(tr,v){const td=document.createElement("td");td.textContent=String(v??"—");tr.append(td)}
function rows(id,records,keys){$(id).replaceChildren();for(const r of records){const tr=document.createElement("tr");keys.forEach(k=>cell(tr,typeof k==="function"?k(r):r[k]));$(id).append(tr)}}
async function load(since){lastFullReview=null;$("copy-report").disabled=true;$("status").textContent="در حال بارگذاری…";try{const until=Math.floor(Date.now()/1000);const res=await fetch("/api/opportunity-review?since="+since+"&until="+until,{cache:"no-store"});if(!res.ok)throw Error("HTTP "+res.status);const d=await res.json();if(!d.available)throw Error(d.reason||"ناموجود");lastFullReview=d;$("copy-report").disabled=false;$("metrics").replaceChildren();for(const [k,v] of [["تصمیم‌های ثبت‌شده",fmt(d.decision_samples_total)],["تصمیم WAIT",fmt(d.wait_decisions)],["معاملات زیان‌ده ثبت‌شده",fmt(d.losing_outcomes)],["خالص معاملات دریافت‌شده",fmt(d.realized_net_units)],["فرصت سودآور ازدست‌رفته","تأیید نشده"]]){let box=document.createElement("div");box.className="metric";let label=document.createElement("span");label.textContent=k;let value=document.createElement("strong");value.textContent=v;box.append(label,value);$("metrics").append(box)}
$("disclaimer").textContent=d.profitable_missed_positions.explanation+(d.decision_samples_truncated?" | تعداد تصمیم‌ها محدود به نمونه است.":"");
const shadow=d.shadow_replay||{};
$("replay-summary").replaceChildren();
for(const [label,value] of [["فرصت‌های مستقل آزمایش‌شده",fmt(shadow.independent_candidate_count||0)],
 ["برخورد فرضی TP",fmt((shadow.outcome_counts||{}).SIMULATED_TP||0)],
 ["برخورد فرضی SL",fmt((shadow.outcome_counts||{}).SIMULATED_SL||0)],
 ["مبهم در یک کندل",fmt((shadow.outcome_counts||{}).AMBIGUOUS_SAME_BAR||0)],
 ["نامشخص یا داده ناکافی",fmt(Object.entries(shadow.outcome_counts||{}).filter(([k])=>!["SIMULATED_TP","SIMULATED_SL","AMBIGUOUS_SAME_BAR"].includes(k)).reduce((a,[,v])=>a+v,0))]]) {
 const box=document.createElement("div");box.className="metric";
 const title=document.createElement("span");title.textContent=label;
 const val=document.createElement("strong");val.textContent=value;
 box.append(title,val);$("replay-summary").append(box);
}
rows("replay-cases",shadow.cases||[],[
 r=>new Date(r.captured*1000).toISOString().slice(0,16), "direction","reason",
 "status",r=>fmt(r.entry),r=>fmt(r.target),r=>fmt(r.stop)]);
rows("wait",Object.entries(d.wait_reasons).map(([reason,count])=>({reason,count})),["reason","count"]);
rows("losses",d.worst_losses,["trade_key","direction",r=>fmt(r.net_units),r=>fmt(r.net_r),"exit_reason","exit_detail",r=>fmt(r.initial_risk_units),"entry_source"]);
$("actions").replaceChildren();for(const action of d.improvement_actions){const li=document.createElement("li");li.textContent=action.action+" — فقط پژوهشی";$("actions").append(li)}$("status").textContent="به‌روز شد"}catch(e){$("status").textContent="خطا: "+e.message}}
$("copy-report").addEventListener("click",copyFullReview);
$("day").onclick=()=>load(Math.floor(Date.now()/1000)-86400);
$("yesterday").onclick=()=>{const d=new Date();d.setDate(d.getDate()-1);d.setHours(0,0,0,0);load(Math.floor(d.getTime()/1000))};
$("day").click();
