"use strict";
const $ = id => document.getElementById(id);
const labels = {pass:"شرط برقرار",blocked:"شرط ردشده",active:"پوزیشن باز",shadow:"فقط ناظر",stale:"دادهٔ قدیمی",unknown:"نامشخص",observed:"مشاهده‌شده",idle:"انتظار"};
const coords = {market:[35,28],service:[280,28],forecast:[525,28],shadow:[280,218],timing:[770,28],extension:[770,218],edge:[770,408],strength:[525,408],market_direction:[280,408],entry_timing:[35,408],base:[280,598],range:[35,598],decision:[525,598],news:[770,598],account:[770,798],limits:[525,798],risk:[280,798],order:[35,798],position:[35,1018]};

const marketStates={TREND_UP:"روند صعودی",TREND_DOWN:"روند نزولی",PULLBACK_UP:"پولبک در روند صعودی",PULLBACK_DOWN:"پولبک در روند نزولی",RANGE_LOW:"لبهٔ پایین رنج",RANGE_HIGH:"لبهٔ بالای رنج",RANGE_MIDDLE:"وسط رنج",BREAKOUT_UP:"شکست صعودی",BREAKOUT_DOWN:"شکست نزولی",BREAKOUT_RETEST_UP:"آزمون مجدد شکست صعودی",BREAKOUT_RETEST_DOWN:"آزمون مجدد شکست نزولی",FALSE_BREAKOUT_UP:"شکست کاذب سقف",FALSE_BREAKOUT_DOWN:"شکست کاذب کف",REGIME_TRANSITION:"تغییر رژیم",PRICE_GAP:"جهش قیمت",VOLATILITY_SHOCK:"شوک نوسان",LOW_LIQUIDITY:"اسپرد زیاد نسبت به نوسان",FLAT_MARKET:"بازار تخت",DISORDERLY_MARKET:"بازار نامنظم",VOLATILITY_COMPRESSION:"فشردگی نوسان",CONFLICTING_STRUCTURE:"ساختارهای متعارض",UNCERTAIN:"نامشخص"};
let snapshot = null, selected = "decision", lastKey = null, busy = false, timer = null, zoomed = false, overrideBusy = false;
const overrideStages = new Set(["timing","extension","edge","strength","market_direction","entry_timing","base","decision","range"]);
const timeFormat = new Intl.DateTimeFormat("fa-IR", {timeZone:"Asia/Tehran", hour:"2-digit",minute:"2-digit",second:"2-digit"});
const dateFormat = new Intl.DateTimeFormat("fa-IR", {timeZone:"Asia/Tehran", year:"numeric",month:"short",day:"numeric",hour:"2-digit",minute:"2-digit",second:"2-digit"});
const brokerDateFormat = new Intl.DateTimeFormat("fa-IR", {timeZone:"UTC",year:"numeric",month:"short",day:"numeric",hour:"2-digit",minute:"2-digit",second:"2-digit"});
function tradeTime(row) {return row.at?formattedTime(row.at,true):row.broker_at?`${brokerDateFormat.format(new Date(row.broker_at))} (وقت بروکر)`:"زمان نامشخص";}
function formattedTime(value, full=false) {if(!value) return "زمان نامشخص";const date=new Date(value);return Number.isNaN(date.getTime())?"زمان نامعتبر":(full?dateFormat:timeFormat).format(date);}
function age(f) {if(!f || f.age_seconds === null) return "نامشخص";if(f.state === "clock_error") return "خطای ساعت";const n=f.age_seconds;return n<60?`${Math.round(n)} ثانیه قبل`:n<3600?`${Math.floor(n/60)} دقیقه قبل`:`${Math.floor(n/3600)} ساعت قبل`;}
function el(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;}
function valueText(v){if(v===null||v===undefined||v==="")return "ثبت نشده";if(typeof v==="boolean")return v?"بله":"خیر";if(typeof v==="number")return Number.isInteger(v)?String(v):v.toFixed(4);if(typeof v==="object")return JSON.stringify(v,null,2);return String(v);}
function displayState(node){if(node?.state==="observed"&&["market","forecast"].includes(node.id))return "pass";return node?.state||"unknown";}
function detail(){const n=snapshot?.nodes.find(n=>n.id===selected);if(!n)return;const viewState=displayState(n);$("detail-title").textContent=n.title;$("detail-state").textContent=n.manual_override?"MANUAL PASS":labels[viewState];$("detail-state").className=`state-badge ${viewState}`;$("detail-description").textContent=n.detail;$("detail-values").replaceChildren();if(n.engine)addValue("نوع تصمیم‌گیری",n.engine);if(n.state==="stale")addValue("نتیجهٔ آخرین مشاهده، با دادهٔ قدیمی",labels[n.observed_state]);for(const [key,v] of Object.entries(n.values))addValue(key,valueText(v));if(n.manual_override_supported&&!n.manual_override){const wrap=el("div","override-action");const btn=el("button","override-button","عبور دستی از این شرط");btn.type="button";btn.disabled=overrideBusy;btn.addEventListener("click",()=>manualPass(n,btn));wrap.append(btn,el("div","override-help","این اقدام برای همین کندل FORCE PASS می‌شود؛ وضعیت واقعی و تمام داده‌های تصمیم نیز برای آموزش ذخیره می‌شوند."));$("detail-values").append(wrap);}else if(n.manual_review_supported){const wrap=el("div","override-action");const btn=el("button","override-button","ثبت تأیید دستی این مرحله");btn.type="button";btn.disabled=overrideBusy;btn.addEventListener("click",()=>manualReview(n,btn));wrap.append(btn,el("div","override-help","این اقدام برای آموزش ذخیره می‌شود اما قفل اجرایی/ایمنی این مرحله را دور نمی‌زند."));$("detail-values").append(wrap);}const source={model:"تاریخچهٔ تصمیم مدل",ea:"فایل وضعیت اکسپرت MAIN",health:"پاسخ سلامت سرویس"}[n.source];$("detail-source").textContent=`منبع: ${source} · ${n.observed_at?formattedTime(n.observed_at,true):"زمان نامشخص"} · زمان‌ها به وقت تهران`;document.querySelectorAll(".node").forEach(e=>{e.classList.toggle("selected",e.id===`node-${selected}`);e.setAttribute("aria-pressed",String(e.id===`node-${selected}`));});}
function addValue(k,v){const d=el("div");const dd=el("dd",null,v);if(/[0-9]/.test(String(v))&&!/[\u0600-\u06FF]/.test(String(v)))dd.dir="ltr";d.append(el("dt",null,k),dd);$("detail-values").append(d);}
async function manualPass(node,button){if(overrideBusy)return;overrideBusy=true;button.disabled=true;button.textContent="در حال ثبت...";try{const response=await fetch("/api/override",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({stage:node.id})});if(!response.ok){let msg=`HTTP ${response.status}`;try{const data=await response.json();msg=data.error||msg;}catch{}throw new Error(msg);}button.textContent="ثبت شد — MANUAL PASS";await refresh();}catch(err){button.disabled=false;button.textContent="خطا: "+err.message;}finally{overrideBusy=false;}}
async function manualReview(node,button){if(overrideBusy)return;overrideBusy=true;button.disabled=true;button.textContent="در حال ثبت...";try{const response=await fetch("/api/override",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({stage:node.id})});if(!response.ok){let msg=`HTTP ${response.status}`;try{const data=await response.json();msg=data.error||msg;}catch{}throw new Error(msg);}button.textContent="ثبت شد — REVIEW ONLY";}catch(err){button.disabled=false;button.textContent="خطا: "+err.message;}finally{overrideBusy=false;}}
const nodeNumbers={market:"01 · آقای احمدی",service:"02 · آقای محمدی",forecast:"03 · خانم حسینی",timing:"04 · آقای رضایی",extension:"05 · آقای کریمی",edge:"06 · خانم مرادی",strength:"07 · آقای جعفری",market_direction:"08 · خانم محمودی",entry_timing:"09 · آقای کاظمی",base:"10 · آقای رحیمی",range:"R1",shadow:"S1",decision:"11 · خانم اکبری",news:"12 · آقای صادقی",account:"13 · خانم قاسمی",limits:"14 · آقای یوسفی",risk:"15 · خانم نادری",order:"16 · آقای شریفی",position:"17 · آقای حیدری"};
function renderNodes(){for(const n of snapshot.nodes){let b=$(`node-${n.id}`);if(!b){b=el("button",`node ${n.state}`);b.id=`node-${n.id}`;b.type="button";b.addEventListener("click",()=>{selected=n.id;detail();});const top=el("span","node-top");top.append(el("span","state-badge"),el("span","node-engine"),el("span","node-number",nodeNumbers[n.id]||"—"));b.append(top,el("span","node-title"),el("span","node-metrics"),el("span","node-detail"));$("nodes").append(b);}b.style.left=`${coords[n.id][0]}px`;b.style.top=`${coords[n.id][1]}px`;const viewState=displayState(n);b.className=`node ${viewState}`;b.querySelector(".state-badge").className=`state-badge ${viewState}`;b.querySelector(".state-badge").textContent=labels[viewState];const engine=b.querySelector(".node-engine");engine.textContent=n.engine||"";engine.hidden=!n.engine;b.querySelector(".node-title").textContent=n.title;const metrics=b.querySelector(".node-metrics");metrics.textContent=n.metrics||n.score||"";metrics.hidden=!(n.metrics||n.score);b.querySelector(".node-detail").textContent=n.detail;b.setAttribute("aria-label",`${n.title}، ${n.engine||"Logic"}، ${labels[n.state]}، ${n.detail}`);}}
const ns="http://www.w3.org/2000/svg";
function svgEl(name,attrs){const e=document.createElementNS(ns,name);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,v);return e;}
function route(a,b){const [ax,ay]=coords[a],[bx,by]=coords[b],w=206,h=127;
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
   const fromState=from?displayState(from):"unknown";
   const toState=to?displayState(to):"unknown";
   let state=toState||"unknown";
   const branchOff=(edge.from==="base"&&edge.to==="range"&&snapshot.decision!=="WAIT"&&snapshot.nodes.find(n=>n.id==="range")?.observed_state!=="pass")||(edge.from==="base"&&edge.to==="decision"&&snapshot.nodes.find(n=>n.id==="range")?.observed_state==="pass");
   if(branchOff)state="idle";
   else if(fromState==="pass"&&toState==="pass")state="pass";
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
function renderHistory(){const list=$("timeline");list.replaceChildren();for(const x of snapshot.timeline){const r=el("div","history-row");r.append(el("time",null,formattedTime(x.at,true)),el("strong",null,x.decision));const d=el("div","row-detail",x.detail);d.append(el("small",null,`${x.strategy} · ${x.sample_key||"شناسه نامشخص"}`));r.append(d);list.append(r);}if(!snapshot.timeline.length)list.append(el("p","empty","هنوز تصمیمی ثبت نشده است"));const trades=$("trades");if($("trade-dialog").open)return;const focusedTrade=document.activeElement?.dataset.tradeKey;trades.replaceChildren();for(const x of snapshot.trades){const r=el("button","history-row trade-row");r.type="button";r.dataset.tradeKey=x.trade_key||"";r.disabled=!x.trade_key;r.addEventListener("click",()=>openTrade(x.trade_key));r.append(el("time",null,tradeTime(x)),el("strong",null,x.direction||"—"));const d=el("span","row-detail",x.exit||"علت خروج ثبت نشده");d.append(el("small",null,`${x.role||"نقش نامشخص"} · ${x.strategy||"مسیر نامشخص"} · ${x.entry_source==="DASHBOARD_OPPORTUNITY"?"ورود دستی از جدول":x.entry_source||"AUTO_RAMON"}`));const n=typeof x.net_units==="number"?x.net_units:null;r.append(d,el("span",`profit ${n>=0?"positive":"negative"}`,n===null?"—":`${n>=0?"+":""}${n.toFixed(2)}`));trades.append(r);if(focusedTrade&&focusedTrade===x.trade_key)r.focus({preventScroll:true});}if(!snapshot.trades.length)trades.append(el("p","empty","هنوز معاملهٔ بسته‌شده‌ای ثبت نشده است"));}
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
const opportunityDate=new Intl.DateTimeFormat("fa-IR",{timeZone:"Asia/Tehran",month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"});
const opportunitySet=(id,text)=>{$(id).textContent=text;};
function percent(v){return typeof v==="number"&&Number.isFinite(v)&&v>=0&&v<=1?(v*100).toFixed(1)+"%":"—";}
async function manualOpportunity(row,button,recheck=false){
  if(button.disabled)return;
  button.disabled=true;const old=button.textContent;button.textContent="در حال ارسال…";
  try{
    const response=await fetch("/api/manual-entry",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({direction:row.direction,signal_bar_time:row.signal_bar_time,recheck})});
    let data={};try{data=await response.json();}catch{}
    if(!response.ok||!data.queued)throw new Error(data.error||("HTTP "+response.status));
    button.textContent="ارسال شد";
    opportunitySet("opportunity-status",(recheck?"جهت انتخابی روی snapshot تازه بازبینی و ارسال شد؛ ":"فرمان "+row.direction+" ارسال شد؛ ")+"EA قبل از بازکردن پوزیشن همهٔ قفل‌های اجرایی و ریسک را دوباره بررسی می‌کند.");
    setTimeout(()=>void refreshOpportunities(),1500);
  }catch(err){button.textContent="رد شد";opportunitySet("opportunity-status","ورود دستی انجام نشد: "+err.message);setTimeout(()=>{button.disabled=!row.actionable;button.textContent=old;},1800);}
}
async function manualCloseOpportunity(row,button){
  if(button.disabled)return;
  button.disabled=true;const old=button.textContent;button.textContent="در حال بستن…";
  try{
    const response=await fetch("/api/manual-close",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({sample_key:row.sample_key,ticket:row.position_ticket})});
    let data={};try{data=await response.json();}catch{}
    if(!response.ok||!data.queued)throw new Error(data.error||("HTTP "+response.status));
    button.textContent="فرمان بستن ارسال شد";
    opportunitySet("opportunity-status","فرمان بستن پوزیشن #"+row.position_ticket+" ارسال شد؛ EA مالکیت پوزیشن و مجوز معامله را دوباره بررسی می‌کند.");
    setTimeout(()=>void refreshOpportunities(),1000);
  }catch(err){
    button.textContent="خطا";
    opportunitySet("opportunity-status","بستن معامله انجام نشد: "+err.message);
    setTimeout(()=>{button.disabled=false;button.textContent=old;},1800);
  }
}
let chronosSlope = null;
function renderTradingGuidance(){
  const title=$("trade-guidance-title"),reason=$("trade-guidance-reason"),container=title?.parentElement;
  if(!title||!reason||!container)return;
  const value=chronosSlope;
  const valid=value?.ready===true && value?.horizon_minutes===30 &&
    ["UP","DOWN","FLAT"].includes(value?.direction) &&
    typeof value.current_mid==="number" &&
    typeof value.forecast_step_1==="number" &&
    typeof value.forecast_step_2==="number";
  let mode="wait",heading="صبر — پیش‌بینی ۳۰ دقیقه‌ای Chronos در دسترس نیست";
  if(valid){
    if(value.direction==="UP"){mode="buy";heading="تمایل به BUY — پیش‌بینی صعودی Chronos (+۳۰ دقیقه)";}
    else if(value.direction==="DOWN"){mode="sell";heading="تمایل به SELL — پیش‌بینی نزولی Chronos (+۳۰ دقیقه)";}
    else heading="صبر — پیش‌بینی Chronos خنثی است";
  }
  title.textContent=heading;
  reason.textContent=valid
    ?"قیمت فعلی: "+value.current_mid.toFixed(2)+" · +۱۵ دقیقه: "+value.forecast_step_1.toFixed(2)+
     " · +۳۰ دقیقه: "+value.forecast_step_2.toFixed(2)+" · تازگی: "+(value.age_seconds??"—")+
     " ثانیه · فقط جهت احتمالی؛ برای ورود واقعی تأیید زمان‌بندی، اسپرد و ریسک لازم است."
    :"Bridge باید پیش‌بینی معتبر و تازه ارسال کند. هیچ سیگنال معامله‌ای از داده قدیمی صادر نمی‌شود.";
  container.className="trade-guidance "+mode;
}
async function refreshChronosSlope(){
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),2500);
  try{
    const response=await fetch("/api/chronos-slope",{cache:"no-store",signal:controller.signal});
    if(!response.ok)throw Error("HTTP "+response.status);
    chronosSlope=await response.json();
  }catch{chronosSlope=null;}
  finally{clearTimeout(timeout);renderTradingGuidance();}
}
function renderLiveMarketSync(){
  renderTradingGuidance();
  const target=$("live-market-sync");
  if(!target)return;
  if(!snapshot){
    target.textContent="وضعیت زنده Ramon هنوز دریافت نشده است.";
    return;
  }
  const fresh=snapshot.model_freshness?.state==="fresh";
  const state=snapshot.decision||"نامشخص";
  const reason=snapshot.reason_fa||snapshot.reason||"بدون دلیل";
  const sample=snapshot.model_freshness?.at;
  const timestamp=sample?formattedTime(sample,true):"نامشخص";
  target.textContent=(fresh?"آخرین تصمیم Ramon":"آخرین تصمیم ثبت‌شده (قدیمی)")+
    ": "+state+" · "+reason+" · زمان: "+timestamp+
    " · توجه: افق پیش‌بینی معاملاتی Ramon با مسیر ۳۰ دقیقه‌ای ChronosSlope متفاوت است.";
  target.classList.toggle("stale",!fresh);
}
function renderOpportunities(data){
  renderLiveMarketSync();
  const compatibility=data.manual_entry_compatibility||{supported:false,reason:"وضعیت EA قابل تأیید نیست"};
  const body=$("opportunity-rows");body.replaceChildren();
  const daily=data.today_profit||{};
  const dailyEl=$("today-profit-summary");
  if(dailyEl){
    const unit=daily.unit_label||"واحد حساب";
    const signed=v=>typeof v==="number"&&Number.isFinite(v)?((v>=0?"+":"")+v.toFixed(2)):"—";
    dailyEl.replaceChildren();
    [
      "بسته‌شده امروز: "+signed(daily.realized_units)+" "+unit+" · "+(daily.closed_trades||0)+" معامله",
      "P/L باز: "+signed(daily.floating_units)+" "+unit+" · "+(daily.open_positions||0)+" پوزیشن",
      "جمع امروز: "+signed(daily.total_units)+" "+unit
    ].forEach((label,index)=>{
      const span=document.createElement("span");span.textContent=label;
      const value=index===0?daily.realized_units:index===1?daily.floating_units:daily.total_units;
      span.className=typeof value==="number"?(value>0?"profit-positive":value<0?"profit-negative":"profit-flat"):"profit-flat";
      dailyEl.append(span);
    });
  }
  const labels={OPEN:"هنوز باز",TP_OBSERVED:"هدف در نمونه‌ها دیده شد",SL_OBSERVED:"حد ضرر در نمونه‌ها دیده شد",TIMEOUT_OBSERVED:"پایان ۴ ساعت",DATA_GAP:"نامشخص؛ شکاف داده"};
  const reasons={trend_conflict:"تعارض جهت",insufficient_model_edge:"مزیت ناکافی",insufficient_model_strength:"قدرت ناکافی",adverse_intrabar_timing:"حرکت کوتاه مخالف",late_entry_extension:"ورود دیرهنگام",direction_confirmation_required:"نبود تأیید جهت",market_direction_conflict:"تعارض جهت مستقل",market_direction_neutral:"جهت خنثی",confirmed_countertrend_reversal:"برگشت تأییدشده"};
  const format=v=>typeof v!=="number"||!Number.isFinite(v)?"—":v.toFixed(3);
  const estimatedPnlAt=(row,target)=>{
    const pnl=row.live_profit_units,entry=row.position_open_price,current=row.position_current_price;
    if(![pnl,entry,current,target].every(v=>typeof v==="number"&&Number.isFinite(v)))return null;
    const currentMove=current-entry,targetMove=target-entry;
    if(Math.abs(currentMove)<1e-9)return null;
    return pnl*(targetMove/currentMove);
  };
  const pnlText=v=>typeof v!=="number"||!Number.isFinite(v)?"—":((v>=0?"+":"")+Math.abs(v).toFixed(1)+" سنت "+(v>=0?"سود":"ضرر"));
  const sortedOpportunities=[...(data.opportunities||[])].sort((a,b)=>{
    const aOpen=a.position_open?1:0,bOpen=b.position_open?1:0;
    if(aOpen!==bOpen)return bOpen-aOpen;
    // Fresh decisions first; old high-scoring rows should not hide live updates.
    const freshness=(b.latest_captured||b.captured||0)-(a.latest_captured||a.captured||0);
    if(freshness!==0)return freshness;
    const ap=typeof a.success_probability==="number"&&Number.isFinite(a.success_probability)?a.success_probability:-1;
    const bp=typeof b.success_probability==="number"&&Number.isFinite(b.success_probability)?b.success_probability:-1;
    return bp-ap;
  });
  for(const row of sortedOpportunities){
    const tr=document.createElement("tr");
    const p=row.success_probability;
    tr.className=typeof p==="number"&&Number.isFinite(p)?(p>=0.70?"opportunity-confidence-high":p>=0.55?"opportunity-confidence-medium":p>=0.45?"opportunity-confidence-neutral":"opportunity-confidence-low"):"";
    const livePnl=typeof row.live_profit_units==="number"&&Number.isFinite(row.live_profit_units)
      ?(" · P/L "+(row.live_profit_units>=0?"+":"")+row.live_profit_units.toFixed(2))
      :"";
    const disposition=row.position_open?"پوزیشن باز است"+(row.position_ticket?" · #"+row.position_ticket:"")+livePnl+(row.position_volume?" · vol "+row.position_volume.toFixed(2):""):row.entry_queued?"در صف اجرا":row.executed?"معاملهٔ بسته‌شده ثبت شده":row.model_approved?"سیگنال صادر شده؛ اجرای سفارش تأیید نشده":"مسدود";
    const cells=[opportunityDate.format(new Date(row.captured*1000))+" · ثبت اولیه",row.strategy+" / "+row.direction,
      format(row.entry)+" / "+format(row.stop)+" / "+format(row.target),format(row.edge)+" / "+format(row.minimum_edge),
      format(row.strength),percent(row.success_probability),disposition+" · علت نخست: "+(reasons[row.first_reason]||row.first_reason||"—")+(row.last_reason!==row.first_reason?" · آخرین: "+(reasons[row.last_reason]||row.last_reason||"—"):""),
      (labels[row.outcome]||row.outcome)+(row.net_r!==null&&row.outcome!=="DATA_GAP"?" · "+format(row.net_r)+"R":""),
      row.position_open&&typeof row.live_profit_units==="number"&&Number.isFinite(row.live_profit_units)
        ?((row.live_profit_units>=0?"+":"")+row.live_profit_units.toFixed(2)+" units")
        :"—",
      !row.position_open&&typeof row.success_probability==="number"&&row.success_probability>=0.70
        ?(row.chronos_entry_action==="ENTER_NEAR"
          ?"ورود نزدیک/الان · "+format(row.chronos_entry_price)
          :row.chronos_entry_action==="WAIT_PULLBACK"&&typeof row.chronos_entry_price==="number"
            ?"حدود "+(row.chronos_entry_minutes||15)+" دقیقه دیگر · "+format(row.chronos_entry_price)
            :"در حال محاسبه…")
        :"—",
      row.position_open
        ?(row.chronos_exit_action==="EXIT_NOW"
          ?"خروج نزدیک · حدود "+pnlText(row.live_profit_units)
          :row.chronos_exit_action==="LOSS_EXIT_NOW"
            ?"خروج نزدیک از ضرر · حدود "+pnlText(row.live_profit_units)
            :row.chronos_exit_action==="RECOVERY_EXIT"&&typeof row.chronos_exit_price==="number"
              ?"خروج برای کاهش ضرر · "+format(row.chronos_exit_price)+" · حدود "+((row.chronos_exit_step||1)*15)+" دقیقه · "+pnlText(estimatedPnlAt(row,row.chronos_exit_price))
              :row.chronos_exit_action==="TARGET"&&typeof row.chronos_exit_price==="number"
                ?format(row.chronos_exit_price)+" · حدود "+((row.chronos_exit_step||1)*15)+" دقیقه · "+pnlText(estimatedPnlAt(row,row.chronos_exit_price))
                :row.chronos_exit_action==="NO_PROFIT_TARGET"
                  ?"فعلاً هدف سودی معتبر ندارد"
                  :"در حال محاسبه…")
        :"—"];
    cells.forEach((text,index)=>{const td=document.createElement("td");td.textContent=text;if([2,3,4,5,8,9,10].includes(index))td.dir="ltr";tr.append(td);});
    const action=document.createElement("td"),button=document.createElement("button");
    button.type="button";button.className="opportunity-entry "+(row.direction==="BUY"?"buy":"sell");
    if(row.position_open){
      button.textContent="بستن پوزیشن";
      button.disabled=false;
      button.className="opportunity-entry close";
      button.title="بستن فقط همین پوزیشن جدول · Ticket "+row.position_ticket;
      button.addEventListener("click",()=>manualCloseOpportunity(row,button));
    }else if(row.entry_queued){
      button.textContent="در صف اجرا";
      button.disabled=true;
      button.title="فرمان این فرصت قبلاً برای EA ارسال شده است";
    }else if(row.executed){
      button.textContent="قبلاً اجرا شده";
      button.disabled=true;
      button.title="برای حفظ attribution آموزشی، همان فرصت دوباره اجرا نمی‌شود";
    }else{
      const recheck=!row.actionable;
      button.textContent=!compatibility.supported?"EA نیاز به به‌روزرسانی دارد":recheck?"بازبینی و ورود "+row.direction:"باز کردن "+row.direction;
      button.disabled=!compatibility.supported;
      if(!compatibility.supported){
        button.title=compatibility.reason;
        action.append(button);tr.append(action);body.append(tr);
        continue;
      }
      button.title=recheck
        ?"جهت این ردیف روی snapshot تازه بررسی می‌شود؛ فقط Safety Gateهای اجرایی می‌توانند مانع سفارش شوند"
        :"فرمان ورود به EA ارسال می‌شود؛ EA دوباره ریسک و قفل‌های اجرایی را بررسی می‌کند";
      button.addEventListener("click",()=>manualOpportunity(row,button,recheck));
    }
    action.append(button);tr.append(action);body.append(tr);
  }
  const perf=snapshot&&snapshot.dashboard_opportunity_performance;
  const perfText=perf&&perf.closed?(" · دستی جدول: "+perf.closed+" بسته · برد "+(perf.win_rate*100).toFixed(1)+"% · خالص "+perf.net_units.toFixed(2)):"";
  const lastDecision=Math.max(0,...(data.opportunities||[]).map(r=>Number(r.latest_captured||r.captured||0)));
  const stale=lastDecision>0 && Date.now()/1000-lastDecision>120;
  const refreshedAt=new Intl.DateTimeFormat("fa-IR",{timeZone:"Asia/Tehran",hour:"2-digit",minute:"2-digit",second:"2-digit"}).format(new Date());
  const compatibilityWarning=compatibility.supported?"":" · ⚠ "+compatibility.reason;
  opportunitySet("opportunity-status",data.error?"دریافت جدول ناموفق: "+data.error:(data.opportunities||[]).length?
    "آخرین ۲۴ ساعت · "+data.opportunities.length+" کاندید · آخرین نمونه: "+(lastDecision?opportunityDate.format(new Date(lastDecision*1000)):"—")+
    " · دریافت موفق: "+refreshedAt+(stale?" · هشدار: نمونه جدید ثبت نشده":"")+compatibilityWarning+perfText:
    "هنوز کاندیدی با مزیت مثبت ثبت نشده · دریافت موفق: "+refreshedAt+compatibilityWarning+perfText);
}
let opportunityInFlight=false;
async function refreshOpportunities(){
  if(opportunityInFlight)return;
  opportunityInFlight=true;
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),4500);
  try{
    const r=await fetch("/api/opportunities",{cache:"no-store",signal:controller.signal});
    if(!r.ok)throw Error("HTTP "+r.status);
    renderOpportunities(await r.json());
  }catch(err){
    opportunitySet("opportunity-status","دریافت جدول ناموفق ("+(err?.message||"خطای شبکه")+")؛ ردیف‌های قبلی ممکن است قدیمی باشند");
  }finally{
    clearTimeout(timeout);
    opportunityInFlight=false;
  }
}
async function refresh(){if(busy)return;busy=true;clearTimeout(timer);const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),5000);try{const response=await fetch("/api/snapshot",{cache:"no-store",signal:controller.signal});if(!response.ok)throw Error("monitor unavailable");snapshot=await response.json();render();renderLiveMarketSync();void refreshOpportunities();$("connection").textContent="داشبورد متصل";$("connection").className="connection connected";}catch{ $("connection").textContent="اتصال قطع است؛ تلاش مجدد";$("connection").className="connection error";$("warning").hidden=false;$("warning").textContent="ارتباط با داشبورد قطع شده است؛ داده‌های روی صفحه مربوط به آخرین دریافت هستند.";document.querySelectorAll(".node").forEach(e=>{e.className="node stale";const b=e.querySelector(".state-badge");b.className="state-badge stale";b.textContent="دادهٔ قدیمی";});if(snapshot){snapshot.nodes.forEach(n=>n.state="stale");detail();renderEdges(false);}}finally{clearTimeout(timeout);busy=false;timer=setTimeout(refresh,3000);}}
async function resetOverrides(){if(overrideBusy)return;overrideBusy=true;const btn=$("reset-overrides");const old=btn.textContent;btn.disabled=true;btn.textContent="RESETTING...";try{const response=await fetch("/api/override/reset",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});if(!response.ok){let msg="reset failed";try{const data=await response.json();msg=data.error||msg;}catch{}throw new Error(msg);}btn.textContent="RESET DONE";await refresh();}catch(err){btn.textContent="RESET ERROR: "+err.message;}finally{overrideBusy=false;setTimeout(()=>{btn.disabled=false;btn.textContent=old;},1200);}}
function analysisLine(value){return value===null||value===undefined?"—":typeof value==="object"?JSON.stringify(value):String(value);}
function candleLines(rows,label){const exact=!!snapshot?.recent_market?.exact_input;const out=[`=== ${label} CANDLES (oldest → newest; ${exact?"EXACT DECISION INPUT · UTC":"historical fallback"}) ===`];for(const b of rows||[]){let t="—";if(b.time){const iso=new Date(b.time*1000).toISOString().replace(/Z$/,"");t=exact?iso+" UTC":iso+" (broker time; UTC offset unknown)";}out.push(`${t} | O ${b.open} H ${b.high} L ${b.low} C ${b.close} | body ${b.body} range ${b.range} spreadPts ${b.spread_points}`);}if(!(rows||[]).length)out.push("NO DATA");return out;}
function buildAnalysisBundle(){if(!snapshot)return "RAMON ANALYSIS BUNDLE\nNO SNAPSHOT";const selectedNode=snapshot.nodes.find(n=>n.id===selected);const out=[
"=== RAMON ANALYSIS BUNDLE ===",
`Generated: ${snapshot.generated_at||"—"}`,
`Symbol: ${snapshot.symbol||"—"}`,
`EA version: ${snapshot.ea_version||"—"}`,
`Decision ID: ${snapshot.sample_key||"—"}`,
`Signal bar time: ${snapshot.signal_bar_time||"—"}`,
`Decision: ${snapshot.decision||"—"}`,
`Reason: ${snapshot.reason||"—"} | ${snapshot.reason_fa||"—"}`,
`EA status: ${snapshot.ea_status||"—"}`,
`Joined model/EA: ${snapshot.joined}`,
`Active manual passes: ${(snapshot.manual_overrides_active||[]).join(", ")||"NONE"}`,
`Selected step: ${selectedNode?((nodeNumbers[selectedNode.id]||selectedNode.id)+" "+selectedNode.title):"NONE"}`,
"",
"=== SELECTED STEP ===",
selectedNode?JSON.stringify(selectedNode,null,2):"NONE",
"",
"=== ALL DECISION / EXECUTION STEPS ==="
];for(const n of snapshot.nodes||[]){out.push(`[${nodeNumbers[n.id]||n.id}] ${n.title} | state=${n.state} observed=${n.observed_state} manual=${!!n.manual_override} engine=${n.engine||"Logic"}`);out.push(`detail: ${n.detail||"—"}`);out.push(`values: ${JSON.stringify(n.values||{})}`);}out.push("","=== RECENT DECISIONS ===");for(const x of snapshot.timeline||[])out.push(`${x.at||"—"} | ${x.decision} | ${x.reason} | ${x.strategy} | ${x.sample_key}`);out.push("",...candleLines(snapshot.recent_market?.m15,"M15"),"",...candleLines(snapshot.recent_market?.m1,"M1"),"","=== MODEL / HANDLER MAP ===");for(const row of snapshot.model_handler_map||[])out.push(`${row.name} | ${row.handler} | status=${row.status} | condition=${row.condition_state||"—"} | values=${JSON.stringify(row.values||{})}`);out.push("","=== WARNINGS ===",...(snapshot.warnings?.length?snapshot.warnings:["NONE"]));return out.join("\n");}
async function copyAnalysis(){const btn=$("copy-analysis");const old=btn.textContent;btn.disabled=true;btn.textContent="COPYING...";let text="";try{const response=await fetch(`/api/analysis?stage=${encodeURIComponent(selected||"")}`,{cache:"no-store"});if(!response.ok)throw new Error(`HTTP ${response.status}`);text=await response.text();}catch{text=buildAnalysisBundle();}let copied=false;try{if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(text);copied=true;}}catch{}if(!copied){const ta=document.createElement("textarea");ta.value=text;ta.setAttribute("readonly","");ta.style.position="absolute";ta.style.left="-9999px";document.body.append(ta);ta.select();try{copied=document.execCommand("copy");}catch{}ta.remove();}btn.textContent=copied?"COPIED ✓":"COPY FAILED";setTimeout(()=>{btn.disabled=false;btn.textContent=old;},1400);}
$("copy-analysis").addEventListener("click",copyAnalysis);$("reset-overrides").addEventListener("click",resetOverrides);$("zoom").addEventListener("click",()=>{zoomed=!zoomed;$("zoom").textContent=zoomed?"−":"＋";$("zoom").setAttribute("aria-label",zoomed?"نمای کلی فلوچارت":"بزرگ‌نمایی فلوچارت");resize();});$("refresh").addEventListener("click",refresh);new ResizeObserver(resize).observe($("viewport"));document.addEventListener("visibilitychange",()=>{if(!document.hidden){void refresh();void refreshOpportunities();void refreshChronosSlope();setInterval(()=>{if(!document.hidden)void refreshChronosSlope();},5000);}});setInterval(()=>{if(!document.hidden)void refreshOpportunities();},5000);void refresh();void refreshOpportunities();
