"use strict";
const $=id=>document.getElementById(id);
let snapshot=null,timeframe="m15",selectedOpinion="",busy=false,timer=null;
const timeFmt=new Intl.DateTimeFormat("fa-IR",{timeZone:"Asia/Tehran",hour:"2-digit",minute:"2-digit",second:"2-digit"});
const fullFmt=new Intl.DateTimeFormat("fa-IR",{timeZone:"Asia/Tehran",month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"});
const updateFmt=new Intl.DateTimeFormat("fa-IR-u-ca-persian",{timeZone:"Asia/Tehran",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false});
const num=v=>typeof v==="number"&&Number.isFinite(v)?v:null;
const saharFlowLabels={pass:"شرط برقرار",blocked:"شرط ردشده",active:"پوزیشن باز",shadow:"فقط ناظر",stale:"دادهٔ قدیمی",unknown:"نامشخص",observed:"مشاهده‌شده",idle:"انتظار"};
const saharFlowCoords={market:[35,28],service:[280,28],forecast:[525,28],shadow:[280,218],timing:[770,28],extension:[770,218],edge:[770,408],strength:[525,408],market_direction:[280,408],entry_timing:[35,408],base:[280,598],range:[35,598],decision:[525,598],news:[770,598],account:[770,798],limits:[525,798],risk:[280,798],order:[35,798],position:[35,1018]};
const saharFlowNames={market:"01 · آقای احمدی",service:"02 · آقای محمدی",forecast:"03 · خانم حسینی",timing:"04 · آقای رضایی",extension:"05 · آقای کریمی",edge:"06 · خانم مرادی",strength:"07 · آقای جعفری",market_direction:"08 · خانم محمودی",entry_timing:"09 · آقای کاظمی",base:"10 · آقای رحیمی",range:"R1",shadow:"S1",decision:"11 · خانم اکبری",news:"12 · آقای صادقی",account:"13 · خانم قاسمی",limits:"14 · آقای یوسفی",risk:"15 · خانم نادری",order:"16 · آقای شریفی",position:"17 · آقای حیدری"};
let saharFlowSelected="decision",saharFlowZoomed=false;
function node(id){return snapshot&&snapshot.nodes?snapshot.nodes.find(x=>x.id===id):null;}
function handler(name){return snapshot&&snapshot.model_handler_map?snapshot.model_handler_map.find(x=>x.name===name):null;}
function txt(v,fallback="—"){return v===null||v===undefined||v===""?fallback:String(v);}
function set(id,v){const e=$(id);if(e)e.textContent=txt(v);}
function value(n,key){return n&&n.values?n.values[key]:undefined;}
function saharFlowState(node){
  if(node&&node.state==="observed"&&["market","forecast"].includes(node.id))return "pass";
  return node&&node.state||"unknown";
}
function saharSvg(name,attrs){const e=document.createElementNS("http://www.w3.org/2000/svg",name);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,v);return e;}
function saharRoute(a,b){
  const [ax,ay]=saharFlowCoords[a],[bx,by]=saharFlowCoords[b],w=206,h=127;
  if(a==="range"&&b==="decision")return {d:`M ${ax+w/2} ${ay+h} V ${by+h+30} H ${bx+w/2} V ${by+h}`,x:398,y:by+h+25};
  if(ay===by)return {d:`M ${ax+(bx>ax?w:0)} ${ay+h/2} H ${bx+(bx>ax?0:w)}`,x:(ax+bx+w)/2,y:ay+h/2-10};
  if(ax===bx)return {d:`M ${ax+w/2} ${ay+(by>ay?h:0)} V ${by+(by>ay?0:h)}`,x:ax+w/2+8,y:(ay+by+h)/2};
  return {d:`M ${ax+w/2} ${ay+h} V ${by-20} H ${bx+w/2} V ${by}`,x:(ax+bx+w)/2,y:by-28};
}
function renderSaharFlowDetail(){
  const n=snapshot&&snapshot.nodes&&snapshot.nodes.find(x=>x.id===saharFlowSelected);
  if(!n)return;
  const state=saharFlowState(n);
  set("sahar-flow-detail-title",n.title);
  const badge=$("sahar-flow-detail-state");badge.textContent=saharFlowLabels[state]||state;badge.className="state-badge "+state;
  set("sahar-flow-detail-description",n.detail||"—");
  const dl=$("sahar-flow-detail-values");dl.replaceChildren();
  if(n.engine){const d=document.createElement("div"),dt=document.createElement("dt"),dd=document.createElement("dd");dt.textContent="نوع تصمیم‌گیری";dd.textContent=n.engine;d.append(dt,dd);dl.append(d);}
  for(const [k,v] of Object.entries(n.values||{})){const d=document.createElement("div"),dt=document.createElement("dt"),dd=document.createElement("dd");dt.textContent=k;dd.textContent=txt(v);d.append(dt,dd);dl.append(d);}
  document.querySelectorAll("#sahar-flow-nodes .node").forEach(e=>e.classList.toggle("selected",e.dataset.nodeId===saharFlowSelected));
}
function renderSaharFlow(){
  if(!snapshot||!snapshot.nodes)return;
  const nodes=$("sahar-flow-nodes");nodes.replaceChildren();
  let index=1;
  for(const n of snapshot.nodes){
    if(!saharFlowCoords[n.id])continue;
    const b=document.createElement("button");b.type="button";b.className="node "+saharFlowState(n);b.dataset.nodeId=n.id;
    b.style.left=saharFlowCoords[n.id][0]+"px";b.style.top=saharFlowCoords[n.id][1]+"px";
    const top=document.createElement("span");top.className="node-top";
    const badge=document.createElement("span");badge.className="state-badge "+saharFlowState(n);badge.textContent=saharFlowLabels[saharFlowState(n)]||saharFlowState(n);
    const eng=document.createElement("span");eng.className="node-engine";eng.textContent=n.engine||"";eng.hidden=!n.engine;
    const no=document.createElement("span");no.className="node-number";no.textContent=saharFlowNames[n.id]||String(index++).padStart(2,"0");
    const title=document.createElement("span");title.className="node-title";title.textContent=n.title;
    const detail=document.createElement("span");detail.className="node-detail";detail.textContent=n.detail||"";
    top.append(badge,eng,no);b.append(top,title,detail);
    b.addEventListener("click",()=>{saharFlowSelected=n.id;renderSaharFlowDetail();});
    nodes.append(b);
  }
  const svg=$("sahar-flow-edges");svg.replaceChildren();
  const defs=saharSvg("defs",{});
  const colors={pass:"#66d8b1",blocked:"#f18586",active:"#72b5f6",shadow:"#b09ae9",stale:"#dfbd74",unknown:"#71829a",observed:"#72b5f6",idle:"#71829a"};
  for(const [state,color] of Object.entries(colors)){const m=saharSvg("marker",{id:"sahar-arrow-"+state,viewBox:"0 0 10 10",refX:"9",refY:"5",markerWidth:"6",markerHeight:"6",orient:"auto-start-reverse"});m.append(saharSvg("path",{d:"M 0 0 L 10 5 L 0 10 z",fill:color}));defs.append(m);}svg.append(defs);
  for(const edge of snapshot.edges||[]){
    if(!saharFlowCoords[edge.from]||!saharFlowCoords[edge.to])continue;
    const from=snapshot.nodes.find(n=>n.id===edge.from),to=snapshot.nodes.find(n=>n.id===edge.to);
    let state=to?saharFlowState(to):"unknown";
    const branchOff=(edge.from==="base"&&edge.to==="range"&&snapshot.decision!=="WAIT"&&snapshot.nodes.find(n=>n.id==="range")?.observed_state!=="pass")||(edge.from==="base"&&edge.to==="decision"&&snapshot.nodes.find(n=>n.id==="range")?.observed_state==="pass");
    if(branchOff)state="idle";else if(from&&saharFlowState(from)==="pass"&&to&&saharFlowState(to)==="pass")state="pass";
    const r=saharRoute(edge.from,edge.to);
    svg.append(saharSvg("path",{d:r.d,class:"flow "+state,"marker-end":"url(#sahar-arrow-"+state+")"}));
    const t=saharSvg("text",{x:r.x,y:r.y,"text-anchor":"middle"});t.textContent=edge.label;svg.append(t);
  }
  renderSaharFlowDetail();
  resizeSaharFlow();
}
function resizeSaharFlow(){
  const viewport=$("sahar-flow-viewport");if(!viewport)return;
  const scale=saharFlowZoomed?1:Math.min(1,Math.max(.45,viewport.clientWidth/1020));
  $("sahar-flow-canvas").style.transform="scale("+scale+")";
  $("sahar-flow-space").style.width=(1020*scale)+"px";
  $("sahar-flow-space").style.height=(1190*scale)+"px";
}
function renderKpis(){
  const market=node("market"),forecast=node("forecast"),strength=node("strength"),edge=node("edge");
  const direction=node("market_direction"),timing=node("entry_timing"),news=node("news");
  const anomaly=handler("Anomaly Detection");
  set("mr-price",value(market,"قیمت خرید / فروش"));
  set("mr-spread","Spread "+txt(value(market,"اسپرد، point")));
  set("mr-decision",snapshot.decision);
  set("mr-reason",snapshot.reason_fa||snapshot.reason);
  set("mr-direction",value(direction,"جهت مستقل بازار")||direction&&direction.detail);
  set("mr-direction-score","Score "+txt(value(direction,"امتیاز جهت")));
  const s=num(value(strength,"قدرت")),sf=num(value(strength,"حداقل قدرت عادی"));
  set("mr-strength",s===null?"—":s.toFixed(4));
  set("mr-edge","حداقل "+txt(sf)+" · "+txt(edge&&edge.detail));
  set("mr-atr",txt(value(forecast,"ATR")));
  const state=handler("Market State");
  set("mr-state",state&&state.values&&(state.values.State||state.values["Market state"])||state&&state.status||"—");
  set("mr-timing",timing&&timing.state==="pass"?"READY":timing&&timing.state==="blocked"?"BLOCK":"WAIT");
  set("mr-timing-detail",timing&&timing.detail||"—");
  set("mr-anomaly",anomaly?anomaly.status+" · "+txt(anomaly.values&&anomaly.values.Ratio):"—");
  set("mr-news",news&&news.detail||"—");
  if(snapshot.generated_at){
    const updated=new Date(snapshot.generated_at);
    set("mr-updated","بروزرسانی "+timeFmt.format(updated));
    set("mr-last-update","آخرین بروزرسانی: "+updateFmt.format(updated));
  }else{
    set("mr-updated","—");
    set("mr-last-update","آخرین بروزرسانی: —");
  }
  const dl=$("market-signal-details");dl.replaceChildren();
  const rows=[
    ["Decision ID",snapshot.sample_key],["EA",snapshot.ea_version],["وضعیت EA",snapshot.ea_status],
    ["Manual Pass",(snapshot.manual_overrides_active||[]).join(", ")||"ندارد"],
    ["Chronos",handler("Forecast")&&handler("Forecast").handler],
    ["Entry model",handler("Entry")&&handler("Entry").handler],
    ["Entry probability",handler("Entry")&&handler("Entry").values&&handler("Entry").values["Entry probability"]],
    ["Anomaly",anomaly&&anomaly.status],["News",news&&news.state]
  ];
  for(const row of rows){const d=document.createElement("div"),dt=document.createElement("dt"),dd=document.createElement("dd");dt.textContent=row[0];dd.textContent=txt(row[1]);d.append(dt,dd);dl.append(d);}
}
function svg(name,attrs){const e=document.createElementNS("http://www.w3.org/2000/svg",name);for(const k in attrs)e.setAttribute(k,attrs[k]);return e;}
function renderCandles(){
  const rows=snapshot&&snapshot.recent_market?snapshot.recent_market[timeframe]||[]:[],root=$("market-candles");root.replaceChildren();
  if(!rows.length){const t=svg("text",{x:"450",y:"180","text-anchor":"middle",class:"market-chart-empty"});t.textContent="دادهٔ کندل ثبت نشده";root.append(t);return;}
  const hi=Math.max.apply(null,rows.map(x=>x.high)),lo=Math.min.apply(null,rows.map(x=>x.low)),pad=Math.max((hi-lo)*.08,.01),max=hi+pad,min=lo-pad;
  const W=900,H=360,left=58,right=18,top=18,bottom=42,plotW=W-left-right,plotH=H-top-bottom;
  const y=p=>top+(max-p)/(max-min)*plotH;
  for(let i=0;i<5;i++){const yy=top+i*plotH/4,price=max-i*(max-min)/4;root.append(svg("line",{x1:left,y1:yy,x2:W-right,y2:yy,class:"market-grid"}));const label=svg("text",{x:left-8,y:yy+4,"text-anchor":"end",class:"market-axis"});label.textContent=price.toFixed(2);root.append(label);}
  const slot=plotW/rows.length,bodyW=Math.max(5,Math.min(24,slot*.55));
  rows.forEach((b,i)=>{const x=left+slot*(i+.5),up=b.close>=b.open,cls=up?"up":"down";root.append(svg("line",{x1:x,y1:y(b.high),x2:x,y2:y(b.low),class:"candle-wick "+cls}));const y1=y(Math.max(b.open,b.close)),y2=y(Math.min(b.open,b.close));root.append(svg("rect",{x:x-bodyW/2,y:y1,width:bodyW,height:Math.max(2,y2-y1),rx:1,class:"candle-body "+cls}));if(i===0||i===rows.length-1||i%4===0){const label=svg("text",{x:x,y:H-15,"text-anchor":"middle",class:"market-axis"});label.textContent=fullFmt.format(new Date(b.time*1000));root.append(label);}});
  set("mr-chart-range",min.toFixed(2)+" – "+max.toFixed(2)+" · "+rows.length+" کندل "+timeframe.toUpperCase());
}
function renderOpinions(rows){
  const box=$("recent-opinions");box.replaceChildren();
  for(const r of rows||[]){const item=document.createElement("article");item.className="recent-opinion";const top=document.createElement("div"),strong=document.createElement("strong"),span=document.createElement("span");strong.textContent=r.opinion;span.textContent=r.confidence+"/5";top.append(strong,span);const tm=document.createElement("small");tm.textContent=r.created_utc?fullFmt.format(new Date(r.created_utc*1000)):"—";const p=document.createElement("p");p.textContent=r.note||"بدون توضیح";item.append(top,tm,p);box.append(item);}
  if(!(rows||[]).length){const p=document.createElement("p");p.className="empty";p.textContent="هنوز نظری ثبت نشده";box.append(p);}
}
async function saharManualOpportunity(row,button){
  if(button.disabled)return;
  if(!confirm("باز کردن "+row.direction+" برای این فرصت؟"))return;
  button.disabled=true;
  const old=button.textContent;
  button.textContent="در حال ارسال…";
  try{
    const response=await fetch("/api/manual-entry",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({direction:row.direction,signal_bar_time:row.signal_bar_time})
    });
    let data={};
    try{data=await response.json();}catch{}
    if(!response.ok||!data.queued)throw new Error(data.error||("HTTP "+response.status));
    button.textContent="در صف اجرا";
    set("sahar-opportunity-status","فرمان "+row.direction+" ارسال شد؛ EA قبل از ورود کنترل‌های اجرایی و ریسک را بررسی می‌کند.");
    setTimeout(()=>void refresh(),1200);
  }catch(e){
    button.textContent="رد شد";
    set("sahar-opportunity-status","ورود انجام نشد: "+e.message);
    setTimeout(()=>{button.disabled=!row.actionable;button.textContent=old;},1800);
  }
}
function renderSaharOpportunities(data){
  const body=$("sahar-opportunity-rows");body.replaceChildren();
  const labels={OPEN:"هنوز باز",TP_OBSERVED:"هدف دیده شد",SL_OBSERVED:"حد ضرر دیده شد",TIMEOUT_OBSERVED:"پایان ۴ ساعت",DATA_GAP:"شکاف داده"};
  const fmt=v=>num(v)===null?"—":Number(v).toFixed(3);
  const pct=v=>num(v)===null||v<0||v>1?"—":(v*100).toFixed(1)+"%";
  for(const row of data.opportunities||[]){
    const tr=document.createElement("tr"),p=num(row.success_probability);
    tr.className=p===null?"":p>=.70?"opportunity-confidence-high":p>=.55?"opportunity-confidence-medium":p>=.45?"opportunity-confidence-neutral":"opportunity-confidence-low";
    const disposition=row.position_open?"پوزیشن باز است"+(row.position_ticket?" · #"+row.position_ticket:""):row.entry_queued?"در صف اجرا":row.executed?"معامله ثبت شده":row.model_approved?"سیگنال مدل":"مسدود";
    const cells=[fullFmt.format(new Date(row.captured*1000)),row.strategy+" / "+row.direction,
      fmt(row.edge)+" / "+fmt(row.minimum_edge),fmt(row.strength),pct(row.success_probability),disposition+" · "+txt(row.last_reason),
      (labels[row.outcome]||row.outcome)+(row.net_r!==null&&row.outcome!=="DATA_GAP"?" · "+fmt(row.net_r)+"R":"")];
    cells.forEach((text,index)=>{const td=document.createElement("td");td.textContent=text;if([2,3,4].includes(index))td.dir="ltr";tr.append(td);});
    const action=document.createElement("td"),button=document.createElement("button");
    button.type="button";button.className="opportunity-entry "+(row.direction==="BUY"?"buy":"sell");
    if(row.position_open){
      button.textContent="پوزیشن باز است";
      button.disabled=true;
      button.title="این فرصت همین حالا پوزیشن باز دارد"+(row.position_ticket?" · Ticket "+row.position_ticket:"");
    }else if(row.entry_queued){
      button.textContent="در صف اجرا";
      button.disabled=true;
      button.title="فرمان این فرصت قبلاً برای EA ارسال شده است";
    }else{
      button.textContent=row.actionable?"باز کردن "+row.direction:"منقضی";
      button.disabled=!row.actionable;
      button.title=row.actionable?"ارسال ورود دستی کنترل‌شده به EA":"فقط فرصت‌های تازه قابل اجرا هستند";
    }
    button.addEventListener("click",()=>saharManualOpportunity(row,button));
    action.append(button);tr.append(action);body.append(tr);
  }
  set("sahar-opportunity-status",data.error?"دریافت جدول ناموفق: "+data.error:(data.opportunities||[]).length?"آخرین ۲۴ ساعت · "+data.opportunities.length+" فرصت · باز: "+(data.open_dashboard_positions||0)+" · در صف: "+(data.queued_dashboard_entries||0)+" · رنگ بر اساس احتمال موفقیت مدل":"هنوز فرصتی ثبت نشده");
}
async function refresh(){
  clearTimeout(timer);
  try{
    const res=await Promise.all([fetch("/api/snapshot",{cache:"no-store"}),fetch("/api/opinions",{cache:"no-store"}),fetch("/api/opportunities",{cache:"no-store"})]);
    if(!res[0].ok)throw new Error("snapshot unavailable");
    snapshot=await res[0].json();renderKpis();renderCandles();renderSaharFlow();
    if(res[1].ok)renderOpinions((await res[1].json()).opinions||[]);
    if(res[2].ok)renderSaharOpportunities(await res[2].json());
    $("market-connection").textContent="زنده";$("market-connection").className="connection connected";
  }catch(e){$("market-connection").textContent="اتصال قطع";$("market-connection").className="connection error";}
  timer=setTimeout(refresh,3000);
}
document.querySelectorAll("[data-tf]").forEach(btn=>btn.addEventListener("click",()=>{timeframe=btn.dataset.tf;document.querySelectorAll("[data-tf]").forEach(x=>x.classList.toggle("active",x===btn));renderCandles();}));
document.querySelectorAll("[data-opinion]").forEach(btn=>btn.addEventListener("click",()=>{selectedOpinion=btn.dataset.opinion;document.querySelectorAll("[data-opinion]").forEach(x=>x.classList.toggle("active",x===btn));$("submit-opinion").disabled=false;}));
$("submit-opinion").addEventListener("click",async()=>{
  if(busy||!selectedOpinion)return;busy=true;const btn=$("submit-opinion");btn.disabled=true;btn.textContent="در حال ثبت...";
  try{
    const response=await fetch("/api/opinion",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({opinion:selectedOpinion,confidence:Number($("opinion-confidence").value),note:$("opinion-note").value})});
    const data=await response.json();if(!response.ok||!data.saved)throw new Error(data.error||"ثبت نشد");
    $("opinion-status").textContent="نظر "+selectedOpinion+" ثبت شد · Decision "+(snapshot&&snapshot.sample_key||"—");
    $("opinion-note").value="";selectedOpinion="";document.querySelectorAll("[data-opinion]").forEach(x=>x.classList.remove("active"));await refresh();
  }catch(e){$("opinion-status").textContent="خطا در ثبت: "+e.message;}
  finally{busy=false;btn.textContent="ثبت نظر";btn.disabled=!selectedOpinion;}
});
$("sahar-flow-zoom").addEventListener("click",()=>{saharFlowZoomed=!saharFlowZoomed;$("sahar-flow-zoom").textContent=saharFlowZoomed?"نمای کلی −":"بزرگ‌نمایی ＋";resizeSaharFlow();});
new ResizeObserver(resizeSaharFlow).observe($("sahar-flow-viewport"));
void refresh();