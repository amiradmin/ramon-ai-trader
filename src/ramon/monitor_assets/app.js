"use strict";
const $ = id => document.getElementById(id);
const labels = {pass:"شرط برقرار",blocked:"شرط ردشده",active:"پوزیشن باز",shadow:"فقط ناظر",stale:"دادهٔ قدیمی",unknown:"نامشخص",observed:"مشاهده‌شده",idle:"انتظار"};
const coords = {market:[35,28],service:[280,28],forecast:[525,28],shadow:[280,218],timing:[770,28],extension:[770,218],edge:[770,408],strength:[770,408],confirmation:[525,408],base:[280,598],range:[35,598],decision:[525,598],news:[770,598],account:[770,798],limits:[525,798],risk:[280,798],order:[35,798],position:[35,1018]};

const marketStates={TREND_UP:"روند صعودی",TREND_DOWN:"روند نزولی",PULLBACK_UP:"پولبک در روند صعودی",PULLBACK_DOWN:"پولبک در روند نزولی",RANGE_LOW:"لبهٔ پایین رنج",RANGE_HIGH:"لبهٔ بالای رنج",RANGE_MIDDLE:"وسط رنج",BREAKOUT_UP:"شکست صعودی",BREAKOUT_DOWN:"شکست نزولی",BREAKOUT_RETEST_UP:"آزمون مجدد شکست صعودی",BREAKOUT_RETEST_DOWN:"آزمون مجدد شکست نزولی",FALSE_BREAKOUT_UP:"شکست کاذب سقف",FALSE_BREAKOUT_DOWN:"شکست کاذب کف",REGIME_TRANSITION:"تغییر رژیم",PRICE_GAP:"جهش قیمت",VOLATILITY_SHOCK:"شوک نوسان",LOW_LIQUIDITY:"اسپرد زیاد نسبت به نوسان",FLAT_MARKET:"بازار تخت",DISORDERLY_MARKET:"بازار نامنظم",VOLATILITY_COMPRESSION:"فشردگی نوسان",CONFLICTING_STRUCTURE:"ساختارهای متعارض",UNCERTAIN:"نامشخص"};
let snapshot = null, selected = "decision", lastKey = null, busy = false, timer = null, zoomed = false;
const timeFormat = new Intl.DateTimeFormat("fa-IR", {timeZone:"Asia/Tehran", hour:"2-digit",minute:"2-digit",second:"2-digit"});
const dateFormat = new Intl.DateTimeFormat("fa-IR", {timeZone:"Asia/Tehran", year:"numeric",month:"short",day:"numeric",hour:"2-digit",minute:"2-digit",second:"2-digit"});
const brokerDateFormat = new Intl.DateTimeFormat("fa-IR", {timeZone:"UTC",year:"numeric",month:"short",day:"numeric",hour:"2-digit",minute:"2-digit",second:"2-digit"});
function tradeTime(row) {return row.at?formattedTime(row.at,true):row.broker_at?`${brokerDateFormat.format(new Date(row.broker_at))} (وقت بروکر)`:"زمان نامشخص";}
function formattedTime(value, full=false) {if(!value) return "زمان نامشخص";const date=new Date(value);return Number.isNaN(date.getTime())?"زمان نامعتبر":(full?dateFormat:timeFormat).format(date);}
function age(f) {if(!f || f.age_seconds === null) return "نامشخص";if(f.state === "clock_error") return "خطای ساعت";const n=f.age_seconds;return n<60?`${Math.round(n)} ثانیه قبل`:n<3600?`${Math.floor(n/60)} دقیقه قبل`:`${Math.floor(n/3600)} ساعت قبل`;}
function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;}
function valueText(v){if(v===null||v===undefined||v==="")return "ثبت نشده";if(typeof v==="boolean")return v?"بله":"خیر";if(typeof v==="number")return Number.isInteger(v)?String(v):v.toFixed(4);if(typeof v==="object")return JSON.stringify(v,null,2);return String(v);}
function detail(){const n=snapshot?.nodes.find(n=>n.id===selected);if(!n)return;$("detail-title").textContent=n.title;$("detail-state").textContent=labels[n.state];$("detail-state").className=`state-badge ${n.state}`;$("detail-description").textContent=n.detail;$("detail-values").replaceChildren();if(n.state==="stale")addValue("نتیجهٔ آخرین مشاهده، با دادهٔ قدیمی",labels[n.observed_state]);for(const [key,v] of Object.entries(n.values))addValue(key,valueText(v));const source={model:"تاریخچهٔ تصمیم مدل",ea:"فایل وضعیت اکسپرت MAIN",health:"پاسخ سلامت سرویس"}[n.source];$("detail-source").textContent=`منبع: ${source} · ${n.observed_at?formattedTime(n.observed_at,true):"زمان نامشخص"} · زمان‌ها به وقت تهران`;document.querySelectorAll(".node").forEach(e=>{e.classList.toggle("selected",e.id===`node-${selected}`);e.setAttribute("aria-pressed",String(e.id===`node-${selected}`));});}
function addValue(k,v){const d=el("div");const dd=el("dd",null,v);if(/[0-9]/.test(String(v))&&!/[\u0600-\u06FF]/.test(String(v)))dd.dir="ltr";d.append(el("dt",null,k),dd);$("detail-values").append(d);}
const nodeNumbers={market:"01",service:"02",forecast:"03",timing:"04",extension:"05",edge:"06",strength:"07",confirmation:"08",base:"09",range:"R1",shadow:"S1",decision:"10",news:"11",account:"12",limits:"13",risk:"14",order:"15",position:"16"};
function renderNodes(){for(const n of snapshot.nodes){let b=$(`node-${n.id}`);if(!b){b=el("button",`node ${n.state}`);b.id=`node-${n.id}`;b.type="button";b.addEventListener("click",()=>{selected=n.id;detail();});const top=el("span","node-top");top.append(el("span","state-badge"),el("span","node-number",nodeNumbers[n.id]||"—"));b.append(top,el("span","node-title"),el("span","node-detail"));$("nodes").append(b);}b.style.left=`${coords[n.id][0]}px`;b.style.top=`${coords[n.id][1]}px`;b.className=`node ${n.state}`;b.querySelector(".state-badge").className=`state-badge ${n.state}`;b.querySelector(".state-badge").textContent=labels[n.state];b.querySelector(".node-title").textContent=n.title;b.querySelector(".node-detail").textContent=n.detail;b.setAttribute("aria-label",`${n.title}، ${labels[n.state]}، ${n.detail}`);}}
const ns="http://www.w3.org/2000/svg";
function svgEl(name,attrs){const e=document.createElementNS(ns,name);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,v);return e;}
function route(a,b){const [ax,ay]=coords[a],[bx,by]=coords[b],w=206,h=127;
 if(a==="confirmation"&&b==="base")return {d:`M ${ax} ${ay+h/2} H 503 V ${by+h/2} H ${bx+w}`,x:503,y:by-15};
 if(a==="range"&&b==="decision")return {d:`M ${ax+w/2} ${ay+h} V ${by+h+30} H ${bx+w/2} V ${by+h}`,x:398,y:by+h+25};
 if(ay===by)return {d:`M ${ax+(bx>ax?w:0)} ${ay+h/2} H ${bx+(bx>ax?0:w)}`,x:(ax+bx+w)/2,y:ay+h/2-10};
 if(ax===bx)return {d:`M ${ax+w/2} ${ay+(by>ay?h:0)} V ${by+(by>ay?0:h)}`,x:ax+w/2+8,y:(ay+by+h)/2};
 return {d:`M ${ax+w/2} ${ay+h} V ${by-20} H ${bx+w/2} V ${by}`,x:(ax+bx+w)/2,y:by-28};}
function renderEdges(changed){
 const svg=$("edges");svg.replaceChildren();
 const defs=svgEl("defs",{});
 const markerColors={pass:"#66d8b1",blocked:"#f18586",active:"#72b5f6",shadow:"#b09ae9",stale:"#dfbd74",unknown:"#71829a",observed:"#72b5f6",idle:"#71829a"};
 for(const [state,color] of Object.entries(markerColors)){const marker=svgEl("marker",{id:`arrow-${state}`,viewBox:"0 0 10 10",refX:"9",refY:"5",markerWidth:"6",markerHeight:"6",orient:"auto-start-reverse"});marker.append(svgEl("path",{d:"M 0 0 L 10 5 L 0 10 z",fill:color}));defs.append(marker);}
 svg.append(defs);
 const fresh=snapshot.model_freshness.state==="fresh";
 for(const edge of snapshot.edges){
   const from=snapshot.nodes.find(n=>n.id===edge.from),to=snapshot.nodes.find(n=>n.id===edge.to);
   let state=to?.state||"unknown";
   const branchOff=(edge.from==="base"&&edge.to==="range"&&snapshot.decision!=="WAIT"&&snapshot.nodes.find(n=>n.id==="range")?.observed_state!=="pass")||(edge.from==="base"&&edge.to==="decision"&&snapshot.nodes.find(n=>n.id==="range")?.observed_state==="pass");
   if(branchOff)state="idle";
   else if(["pass","observed"].includes(from?.state)&&["pass","observed"].includes(to?.state))state="pass";
   const r=route(edge.from,edge.to);
   const pulse=changed&&fresh&&to?.source==="model"&&state==="pass"&&!branchOff?" pulse":"";
   svg.append(svgEl("path",{d:r.d,class:`flow ${state}${pulse}`,"marker-end":`url(#arrow-${state})`}));
   const t=svgEl("text",{x:r.x,y:r.y,"text-anchor":"middle"});t.textContent=edge.label;svg.append(t);
 }
}
function modelMapTooltipText(row){
  const lines=[`${row.name} · ${row.handler}`,`Status: ${row.status}`,`شرط: ${row.condition_label||"ثبت نشده"} · ${labels[row.condition_state]||"نامشخص"}`,`Decision ID: ${row.sample_key||"ثبت نشده"}`];
  if(row.observed_at)lines.push(`Observed: ${formattedTime(row.observed_at,true)}`);
  lines.push("");
  for(const [k,v] of Object.entries(row.values||{}))lines.push(`${k}: ${valueText(v)}`);
  return lines.join("\n");
}
function renderModelMap(){
  const body=$("model-map-body"),tip=$("model-map-tooltip");
  if(!body||!tip)return;
  body.replaceChildren();
  for(const row of snapshot.model_handler_map||[]){
    const tr=el("tr",`model-map-row ${row.condition_state||"unknown"}`);
    const tdName=el("td","model-map-name",row.name);
    const tdHandler=el("td","model-map-handler",row.handler||"ثبت نشده");
    const tdStatus=el("td","model-map-status",row.status||"ثبت نشده");
    const tdSample=el("td","model-map-sample",row.sample_key||"—");
    tr.append(tdName,tdHandler,tdStatus,tdSample);
    const show=e=>{
      tip.textContent=modelMapTooltipText(row);
      tip.hidden=false;
      const x=Math.min(window.innerWidth-430,e.clientX+16);
      const y=Math.min(window.innerHeight-260,e.clientY+16);
      tip.style.left=`${Math.max(8,x)}px`;
      tip.style.top=`${Math.max(8,y)}px`;
    };
    tr.addEventListener("mouseenter",show);
    tr.addEventListener("mousemove",show);
    tr.addEventListener("mouseleave",()=>{tip.hidden=true;});
    tr.addEventListener("focusin",e=>show(e));
    tr.addEventListener("focusout",()=>{tip.hidden=true;});
    tr.tabIndex=0;
    body.append(tr);
  }
  if(!(snapshot.model_handler_map||[]).length){
    const tr=el("tr");const td=el("td","empty","هنوز دادهٔ Model / Handler ثبت نشده است");
    td.colSpan=4;tr.append(td);body.append(tr);
  }
}
function renderRoadmap(){
  const r=snapshot?.income_roadmap;if(!r)return;
  $("roadmap-stage").textContent=`${r.current_stage_number} / ${r.stage_count}`;
  $("roadmap-summary").textContent=r.disclaimer||"";
  $("roadmap-account").textContent=`حساب: ${r.account_type||"UNKNOWN"} · موجودی تقریبی: ${typeof r.balance_usd==="number"?"$"+r.balance_usd.toFixed(2):"ثبت نشده"}`;
  const box=$("roadmap-steps");box.replaceChildren();
  for(const s of r.stages||[]){
    const card=el("article",`roadmap-step ${s.state}`);
    const top=el("div","roadmap-step-top");
    top.append(el("span","roadmap-number",String(s.number).padStart(2,"0")),el("span","roadmap-state",s.state==="done"?"انجام شده":s.state==="current"?"مرحله فعلی":"قفل"));
    card.append(top,el("h3",null,s.title),el("strong","roadmap-capital",s.capital),el("span","roadmap-eta",`زمان تقریبی: ${s.eta||"نامشخص"}`),el("p",null,s.goal),el("small",null,s.note));
    box.append(card);
  }
}
function renderReadiness(){
  const r=snapshot?.dollar_readiness;if(!r)return;
  $("readiness-score").textContent=String(r.score??"—");
  $("readiness-status").textContent=r.status||"—";
  $("readiness-status").className=`readiness-badge ${String(r.status||"").toLowerCase().replace(" ","-")}`;
  $("readiness-bar").style.width=`${Math.max(0,Math.min(100,Number(r.score)||0))}%`;
  const box=$("readiness-criteria");box.replaceChildren();
  for(const c of r.criteria||[]){
    const item=el("div",`readiness-item ${c.pass?"pass":"fail"}`);
    const top=el("div","readiness-item-top");top.append(el("strong",null,c.label),el("span",null,c.pass?"✓":"×"));
    item.append(top,el("b",null,c.value),el("small",null,c.target));box.append(item);
  }
  $("readiness-version").textContent=r.current_ea_version?`نسخهٔ فعلی ثبت‌شده: ${r.current_ea_version} · ${r.current_version_trades} معاملهٔ پیاپی روی همین نسخه`:"نسخهٔ EA در معاملات ثبت نشده؛ امتیاز پایداری نسخه صفر است.";
}
function renderHistory(){const list=$("timeline");list.replaceChildren();for(const x of snapshot.timeline){const r=el("div","history-row");r.append(el("time",null,formattedTime(x.at,true)),el("strong",null,x.decision));const d=el("div","row-detail",x.detail);d.append(el("small",null,`${x.strategy} · ${x.sample_key||"شناسه نامشخص"}`));r.append(d);list.append(r);}if(!snapshot.timeline.length)list.append(el("p","empty","هنوز تصمیمی ثبت نشده است"));const trades=$("trades");if($("trade-dialog").open)return;const focusedTrade=document.activeElement?.dataset.tradeKey;trades.replaceChildren();for(const x of snapshot.trades){const r=el("button","history-row trade-row");r.type="button";r.dataset.tradeKey=x.trade_key||"";r.disabled=!x.trade_key;r.addEventListener("click",()=>openTrade(x.trade_key));r.append(el("time",null,tradeTime(x)),el("strong",null,x.direction||"—"));const d=el("span","row-detail",x.exit||"علت خروج ثبت نشده");d.append(el("small",null,`${x.role||"نقش نامشخص"} · ${x.strategy||"مسیر نامشخص"}`));const n=typeof x.net_units==="number"?x.net_units:null;r.append(d,el("span",`profit ${n>=0?"positive":"negative"}`,n===null?"—":`${n>=0?"+":""}${n.toFixed(2)}`));trades.append(r);if(focusedTrade&&focusedTrade===x.trade_key)r.focus({preventScroll:true});}if(!snapshot.trades.length)trades.append(el("p","empty","هنوز معاملهٔ بسته‌شده‌ای ثبت نشده است"));}
let tradeController = null;
let tradeResizeObserver = null;
function traceValue(value){return typeof value==="number"?new Intl.NumberFormat("fa-IR",{maximumFractionDigits:4}).format(value):valueText(value);}
function renderTradeTrace(data){
  const content=$("trade-dialog-content");content.replaceChildren();
  $("trade-dialog-title").textContent=`مسیر معاملهٔ ${data.direction||"—"}`;
  const summary=el("div","trace-summary");
  const net=data.net_units;
  summary.append(el("strong",`trace-result ${net>=0?"positive":"negative"}`,`نتیجه: ${traceValue(net)} واحد حساب`));
  summary.append(el("span",null,`ورود: ${tradeTime(data.opened)}`),el("span",null,`خروج: ${tradeTime(data.closed)}`));
  summary.append(el("small",null,`زمان تصمیم: ${formattedTime(data.decision_at,true)}`));
  const identity=el("small","trace-identity",`Decision ID: ${data.sample_key||"—"} · ${data.trade_key}`);identity.dir="ltr";summary.append(identity);content.append(summary);
  for(const warning of data.warnings)content.append(el("p","warning",warning));
  content.append(el("p","trace-note","فلوچارت تاریخی همین معامله؛ روی هر باکس کلیک کن تا مقادیر و دلیل آن را ببینی. فلش‌ها ترتیب منطقی مراحل را نشان می‌دهند."));
  const toolbar=el("div","trace-flow-toolbar");toolbar.append(el("span",null,"مسیر تصمیم → ورود → خروج"));
  const zoom=el("button","trace-zoom","بزرگ‌نمایی ＋");zoom.type="button";toolbar.append(zoom);content.append(toolbar);
  const viewport=el("div","trace-flow-viewport");viewport.setAttribute("aria-label","فلوچارت مسیر تصمیم معامله");
  const space=el("div","trace-flow-space");const canvas=el("div","trace-flow-canvas");canvas.dir="rtl";
  const svg=document.createElementNS(ns,"svg");svg.setAttribute("viewBox","0 0 920 640");svg.setAttribute("aria-hidden","true");
  const defs=document.createElementNS(ns,"defs");const marker=document.createElementNS(ns,"marker");
  for(const [key,value] of Object.entries({id:"trace-arrow",viewBox:"0 0 10 10",refX:"9",refY:"5",markerWidth:"8",markerHeight:"8",orient:"auto"}))marker.setAttribute(key,value);
  const tip=document.createElementNS(ns,"path");tip.setAttribute("d","M 0 0 L 10 5 L 0 10 z");tip.setAttribute("fill","#8ea9ca");marker.append(tip);defs.append(marker);svg.append(defs);canvas.append(svg);
  const positions=[[650,24],[370,24],[90,24],[90,166],[370,166],[650,166],[650,308],[370,308],[90,308],[90,450]];
  for(let i=1;i<data.stages.length;i++){
    const [ax,ay]=positions[i-1],[bx,by]=positions[i];let d;
    if(ay===by){const left=bx<ax;d=`M ${left?ax-3:ax+243} ${ay+52} H ${left?bx+251:bx-11}`;}
    else d=`M ${ax+120} ${ay+108} V ${by-11}`;
    const connector=document.createElementNS(ns,"path");connector.setAttribute("d",d);connector.setAttribute("class","trace-connector");connector.setAttribute("marker-end","url(#trace-arrow)");svg.append(connector);
  }
  const detail=el("section","trace-selected-detail");detail.setAttribute("aria-label","جزئیات مرحلهٔ انتخاب‌شده");
  const buttons=[];
  function select(stage,index){
    buttons.forEach((button,i)=>{button.classList.toggle("selected",i===index);button.setAttribute("aria-pressed",String(i===index));});
    detail.replaceChildren();const header=el("div","trace-selected-heading");header.append(el("h3",null,stage.title),el("span",`state-badge ${stage.state}`,labels[stage.state]||"نامشخص"));
    detail.append(header,el("p","trace-step-detail",stage.detail));const values=el("dl","trace-values");
    for(const [label,value] of Object.entries(stage.values)){const pair=el("div");pair.append(el("dt",null,label),el("dd",null,traceValue(value)));values.append(pair);}detail.append(values);
  }
  data.stages.forEach((stage,index)=>{
    const [x,y]=positions[index];const box=el("button",`trace-flow-box ${stage.state}`);box.type="button";box.style.left=`${x}px`;box.style.top=`${y}px`;
    box.setAttribute("aria-label",`${index+1}. ${stage.title}، ${labels[stage.state]||"نامشخص"}`);
    const top=el("span","trace-box-top");top.append(el("span","trace-box-number",new Intl.NumberFormat("fa-IR").format(index+1)),el("span",`state-badge ${stage.state}`,labels[stage.state]||"نامشخص"));
    box.append(top,el("strong",null,stage.title),el("span","trace-box-caption",stage.id==="route"?stage.values["تصمیم نهایی"]||"نامشخص":stage.id==="exit"?`نتیجه: ${traceValue(data.net_units)} واحد حساب`:stage.detail));
    box.addEventListener("click",()=>select(stage,index));buttons.push(box);canvas.append(box);
  });
  const finalArrow=document.createElementNS(ns,"path");finalArrow.setAttribute("d","M 210 558 V 578");finalArrow.setAttribute("class","trace-connector");finalArrow.setAttribute("marker-end","url(#trace-arrow)");svg.append(finalArrow);
  canvas.append(el("div","trace-flow-end","پایان معامله"));
  const caption=el("div","trace-flow-caption");caption.append(el("strong",null,"هر باکس، یک مرحله از تصمیم"),el("p",null,"سبز: شرط ثبت‌شده برقرار است\nآبی: مقدار تاریخی مشاهده شده است\nخاکستری: اطلاعات کافی ثبت نشده است"));canvas.append(caption);
  space.append(canvas);viewport.append(space);content.append(viewport,detail);
  let zoomed=false;
  function fit(){const scale=zoomed?1:Math.min(1,viewport.clientWidth/920);canvas.style.transform=`scale(${scale})`;space.style.width=`${920*scale}px`;space.style.height=`${640*scale}px`;}
  zoom.addEventListener("click",()=>{zoomed=!zoomed;zoom.textContent=zoomed?"نمای کلی −":"بزرگ‌نمایی ＋";fit();});
  tradeResizeObserver?.disconnect();tradeResizeObserver=new ResizeObserver(fit);tradeResizeObserver.observe(viewport);fit();select(data.stages.find(s=>s.id==="route")||data.stages[0],Math.max(0,data.stages.findIndex(s=>s.id==="route")));

}
async function openTrade(key){
  tradeController?.abort();const controller=new AbortController();tradeController=controller;
  const dialog=$("trade-dialog");$("trade-dialog-title").textContent="مسیر ثبت‌شدهٔ معامله";$("trade-dialog-content").replaceChildren(el("p","trace-loading","در حال دریافت مسیر این معامله…"));
  if(!dialog.open)dialog.showModal();dialog.scrollTop=0;
  const timeout=setTimeout(()=>controller.abort(),8000);
  try{const response=await fetch(`/api/trade-trace?trade_key=${encodeURIComponent(key)}`,{cache:"no-store",signal:controller.signal});if(!response.ok)throw Error("trace unavailable");const data=await response.json();if(tradeController===controller&&dialog.open)renderTradeTrace(data);}
  catch{if(tradeController===controller&&dialog.open){const retry=el("button","trace-retry","تلاش مجدد");retry.type="button";retry.addEventListener("click",()=>openTrade(key));$("trade-dialog-content").replaceChildren(el("p","warning","مسیر معامله دریافت نشد. دوباره تلاش کن."),retry);}}
  finally{clearTimeout(timeout);}
}
$("trade-dialog-close").addEventListener("click",()=>$("trade-dialog").close());
$("trade-dialog").addEventListener("close",()=>{tradeController?.abort();tradeController=null;tradeResizeObserver?.disconnect();tradeResizeObserver=null;});
$("trade-dialog").addEventListener("click",event=>{if(event.target!==$("trade-dialog"))return;const bounds=event.target.getBoundingClientRect();if(event.clientX<bounds.left||event.clientX>bounds.right||event.clientY<bounds.top||event.clientY>bounds.bottom)event.target.close();});
function resize(){const width=$("viewport").clientWidth;const scale=zoomed?1:Math.min(1,Math.max(.3,width/1020));$("canvas").style.setProperty("--zoom",scale);$("canvas-space").style.width=`${1020*scale}px`;$("canvas-space").style.height=`${1190*scale}px`;}
function render(){const changed=lastKey!==snapshot.sample_key;$("decision").textContent=snapshot.decision;$("reason").textContent=snapshot.reason_fa;const state=snapshot.nodes.find(n=>n.id==="decision")?.values["حالت بازار"];$("market-state").textContent=`حالت بازار: ${marketStates[state]||state||"ثبت نشده"}`;$("strategy").textContent=snapshot.nodes.find(n=>n.id==="decision")?.values["مسیر"]||"—";$("model-age").textContent=age(snapshot.model_freshness);$("model-time").textContent=formattedTime(snapshot.model_freshness.at,true);$("ea-age").textContent=age(snapshot.ea_freshness);$("ea-status").textContent=snapshot.ea_status;$("service-status").textContent=snapshot.nodes.find(n=>n.id==="service")?.state==="pass"?"پاسخ‌گو":"در دسترس نیست";$("source-link").textContent=snapshot.joined?`شناسهٔ مدل و اکسپرت یکسان · نسخه ${snapshot.ea_version||"—"}`:"شناسهٔ مدل و اکسپرت قابل تطبیق نیست";$("warning").hidden=snapshot.warnings.length===0;$("warning").textContent=snapshot.warnings.join(" · ");$("updated").textContent=`بازخوانی ${formattedTime(snapshot.generated_at)}`;$("sample-id").textContent=`DECISION ID ${snapshot.sample_key||"—"}`;renderNodes();renderEdges(changed);detail();renderReadiness();renderRoadmap();renderModelMap();renderHistory();resize();lastKey=snapshot.sample_key;}
async function refresh(){if(busy)return;busy=true;clearTimeout(timer);const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),5000);try{const response=await fetch("/api/snapshot",{cache:"no-store",signal:controller.signal});if(!response.ok)throw Error("monitor unavailable");snapshot=await response.json();render();$("connection").textContent="داشبورد متصل";$("connection").className="connection connected";}catch{ $("connection").textContent="اتصال قطع است؛ تلاش مجدد";$("connection").className="connection error";$("warning").hidden=false;$("warning").textContent="ارتباط با داشبورد قطع شده است؛ داده‌های روی صفحه مربوط به آخرین دریافت هستند.";document.querySelectorAll(".node").forEach(e=>{e.className="node stale";const b=e.querySelector(".state-badge");b.className="state-badge stale";b.textContent="دادهٔ قدیمی";});if(snapshot){snapshot.nodes.forEach(n=>n.state="stale");detail();renderEdges(false);}}finally{clearTimeout(timeout);busy=false;timer=setTimeout(refresh,3000);}}
$("zoom").addEventListener("click",()=>{zoomed=!zoomed;$("zoom").textContent=zoomed?"−":"＋";$("zoom").setAttribute("aria-label",zoomed?"نمای کلی فلوچارت":"بزرگ‌نمایی فلوچارت");resize();});$("refresh").addEventListener("click",refresh);new ResizeObserver(resize).observe($("viewport"));document.addEventListener("visibilitychange",()=>{if(!document.hidden)void refresh();});void refresh();
