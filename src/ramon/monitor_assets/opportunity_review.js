
const $=x=>document.getElementById(x),fmt=x=>x===null||x===undefined?"نامشخص":Number(x).toLocaleString("fa-IR",{maximumFractionDigits:3});
function cell(tr,v){const td=document.createElement("td");td.textContent=String(v??"—");tr.append(td)}
function rows(id,records,keys){$(id).replaceChildren();for(const r of records){const tr=document.createElement("tr");keys.forEach(k=>cell(tr,typeof k==="function"?k(r):r[k]));$(id).append(tr)}}
async function load(since){$("status").textContent="در حال بارگذاری…";try{const until=Math.floor(Date.now()/1000);const res=await fetch("/api/opportunity-review?since="+since+"&until="+until,{cache:"no-store"});if(!res.ok)throw Error("HTTP "+res.status);const d=await res.json();if(!d.available)throw Error(d.reason||"ناموجود");$("metrics").replaceChildren();for(const [k,v] of [["تصمیم‌های ثبت‌شده",fmt(d.decision_samples_total)],["تصمیم WAIT",fmt(d.wait_decisions)],["معاملات زیان‌ده ثبت‌شده",fmt(d.losing_outcomes)],["خالص معاملات دریافت‌شده",fmt(d.realized_net_units)],["فرصت سودآور ازدست‌رفته","تأیید نشده"]]){let box=document.createElement("div");box.className="metric";let label=document.createElement("span");label.textContent=k;let value=document.createElement("strong");value.textContent=v;box.append(label,value);$("metrics").append(box)}
$("disclaimer").textContent=d.profitable_missed_positions.explanation+(d.decision_samples_truncated?" | تعداد تصمیم‌ها محدود به نمونه است.":"");
rows("wait",Object.entries(d.wait_reasons).map(([reason,count])=>({reason,count})),["reason","count"]);
rows("losses",d.worst_losses,["trade_key","direction",r=>fmt(r.net_units),r=>fmt(r.net_r),"exit_reason","exit_detail",r=>fmt(r.initial_risk_units),"entry_source"]);
$("actions").replaceChildren();for(const action of d.improvement_actions){const li=document.createElement("li");li.textContent=action.action+" — فقط پژوهشی";$("actions").append(li)}$("status").textContent="به‌روز شد"}catch(e){$("status").textContent="خطا: "+e.message}}
$("day").onclick=()=>load(Math.floor(Date.now()/1000)-86400);
$("yesterday").onclick=()=>{const d=new Date();d.setDate(d.getDate()-1);d.setHours(0,0,0,0);load(Math.floor(d.getTime()/1000))};
$("day").click();
