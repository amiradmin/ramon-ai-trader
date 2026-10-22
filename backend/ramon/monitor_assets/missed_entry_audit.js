
const E=v=>String(v??"—").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function run(since){
 const until=Math.floor(Date.now()/1000),status=document.getElementById("status");status.textContent="در حال بررسی…";
 try{
  const response=await fetch("/api/missed-entry-audit?since="+since+"&until="+until,{cache:"no-store"});if(!response.ok)throw Error("HTTP "+response.status);
  const x=await response.json();if(!x.available)throw Error(x.reason);
  document.getElementById("summary").textContent="داده تصمیم: "+x.sampled_decision_samples+"/"+x.total_decision_samples+" | WAIT: "+(x.decisions.WAIT||0)+" | BUY: "+(x.decisions.BUY||0)+" | SELL: "+(x.decisions.SELL||0)+" | تعارض جهت: "+x.direction_conflicts+" | نتایج معاملات ثبت‌شده در بازه: "+x.recorded_trade_outcomes_opened+(x.truncated?" | هشدار: نمونه‌ها محدود شده‌اند":"");
  document.getElementById("reasons").innerHTML=Object.entries(x.reasons).map(([k,v])=>"<tr><td>"+E(k)+"</td><td>"+E(v)+"</td></tr>").join("");
  document.getElementById("cases").innerHTML=x.cases.map(r=>"<tr><td>"+E(new Date(r.captured*1000).toLocaleString("fa-IR"))+"</td><td>"+E(r.decision)+"</td><td>"+E(r.reason)+"</td><td>"+E(r.market_state)+"</td><td>"+E(r.candidate_direction)+"</td><td>"+E(r.market_direction)+"</td><td>"+E(r.direction_conflict?"بله":"خیر")+"</td></tr>").join("");
  status.textContent="بررسی داده‌های ثبت‌شده پایان یافت؛ نتیجه فرصت سود فرضی ارزیابی نشده است.";
 }catch(e){status.textContent="خطا: "+e.message;}
}
document.getElementById("today").onclick=()=>run(Math.floor(new Date(new Date().setHours(0,0,0,0)).getTime()/1000));
document.getElementById("eight").onclick=()=>run(Math.floor(Date.now()/1000)-8*3600);
