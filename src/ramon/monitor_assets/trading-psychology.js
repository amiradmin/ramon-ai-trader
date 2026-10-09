"use strict";

// Behavioral reminders only: no model, network request, diagnosis or trading action.
function buildTradingPsychology(context,nowSeconds=Date.now()/1000){
  const general={title:"یادآور ترید — تصمیم از روی برنامه",reason:"قبل از ورود، دلیل معامله، حد ضرر و حداکثر زیان را مشخص کن. اگر فقط می‌خواهی سودی را از دست ندهی یا ضرر قبلی را پس بگیری، چند دقیقه مکث کن.",evidence:"یادآوری عمومی؛ درباره احساسات تو قضاوت نمی‌کند.",mode:"wait"};
  const received=context.opportunitiesReceived;
  const openFresh=typeof received==="number"&&nowSeconds-received>=0&&nowSeconds-received<=15;
  const open=openFresh?(context.opportunities||[]).filter(row=>row.position_open===true):[];
  const losing=open.filter(row=>Number.isFinite(row.live_profit_units)&&row.live_profit_units<0);
  const guardian=open.filter(row=>row.guardian_state==="ARMED"||row.guardian_state==="RECOVERY");
  const generated=Date.parse(context.generatedAt||"")/1000;
  const tradesFresh=Number.isFinite(generated)&&nowSeconds-generated>=0&&nowSeconds-generated<=15;
  const seen=new Set();
  const recent=tradesFresh?(context.trades||[]).filter(row=>{
    if(!["DASHBOARD_OPPORTUNITY","HUMAN_ASSISTED"].includes(row.entry_source)||!Number.isFinite(row.net_units))return false;
    // Unknown broker timezone cannot establish that a trade just happened.
    const closed=Date.parse(row.at||"")/1000;
    if(!Number.isFinite(closed)||closed>nowSeconds||nowSeconds-closed>3600)return false;
    const key=row.trade_key;
    if(!key||seen.has(key))return false;
    seen.add(key);return true;
  }).sort((a,b)=>Date.parse(b.at)-Date.parse(a.at)):[];
  const note=" · این یک یادآور رفتاری است، نه سیگنال معامله یا تشخیص حالت روانی.";
  if(recent.length>=2&&recent[0].net_units<0&&recent[1].net_units<0){
    return {title:"مکث بعد از دو ضرر — دنبال پس‌گرفتن فوری پول نباش",reason:"دو معامله دستی اخیرِ ثبت‌شده زیان‌ده بوده‌اند. ده دقیقه فاصله بگیر، علت ورود و خروجشان را مرور کن و فقط با setup مستقل برگرد؛ حجم یا سقف ریسک را برای جبران بالا نبر.",evidence:"دو معامله دستی بسته‌شده در یک ساعت اخیر؛ بر اساس سود و ضرر خالص ثبت‌شده"+note,mode:"wait"};
  }
  if(guardian.length){
    return {title:"محافظ فعال است — تصمیم تازه را به برنامه بسپار",reason:"کلیک دوباره یا ورود دستیِ هم‌زمان برای نجات همان معامله می‌تواند ریسک را بیشتر کند. وضعیت محافظ و حد ضرر را بررسی کن؛ جبران ضرر تضمین نیست.",evidence:guardian.length+" پوزیشن دستی با وضعیت محافظ فعال یا ورود محافظ"+note,mode:"wait"};
  }
  if(losing.length){
    return {title:"پوزیشن در ضرر — از حد ضررت دفاع کن",reason:"ضرر باز به‌تنهایی دلیل ورود جدید نیست. حد ضرر را صرفاً برای عقب‌انداختن قبول ضرر دورتر نبر؛ قبل از بستن، نگه‌داشتن یا محافظت، دلیل اولیه معامله را دوباره بررسی کن.",evidence:losing.length+" پوزیشن دستی زیان‌ده از "+open.length+" پوزیشن دستی باز"+note,mode:"wait"};
  }
  const rapid=recent.filter(row=>nowSeconds-Date.parse(row.at)/1000<=900);
  if(rapid.length>=4){
    return {title:"فعالیت زیاد — کیفیت را دوباره بررسی کن",reason:"چند معامله دستی در مدت کوتاه بسته شده‌اند. قبل از معامله بعدی بررسی کن آیا فرصت تازه‌ای داری یا فقط به معامله‌کردن ادامه می‌دهی؛ یک وقفه کوتاه می‌تواند به بازبینی برنامه کمک کند.",evidence:rapid.length+" معامله دستی بسته‌شده در ۱۵ دقیقه اخیر"+note,mode:"wait"};
  }
  if(recent.length>=2&&recent[0].net_units>0&&recent[1].net_units>0){
    return {title:"بعد از برد — همان انضباط را نگه دار",reason:"دو معامله دستی اخیرِ ثبت‌شده سودده بوده‌اند. برای معامله بعدی همان حجم، سقف ریسک و شروط ورود را نگه دار؛ دو برد به‌تنهایی دلیل افزایش ریسک نیست.",evidence:"دو معامله دستی سودده در یک ساعت اخیر"+note,mode:"wait"};
  }
  if(open.length>=3){
    return {title:"چند پوزیشن باز — ریسک مجموع را ببین",reason:"قبل از ورود بعدی، زیان احتمالی مجموع پوزیشن‌ها را حساب کن. چند معامله روی طلا ممکن است هم‌زمان از یک حرکت بازار آسیب ببینند.",evidence:open.length+" پوزیشن دستی باز"+note,mode:"wait"};
  }
  if(!openFresh||!tradesFresh)general.evidence="اطلاعات کافی و تازه برای توصیه مرتبط با معاملات موجود نیست؛ این یادآوری عمومی است.";
  else general.evidence="بر اساس پوزیشن‌های دستی و معاملات بسته‌شده اخیرِ موجود، شرط ویژه‌ای دیده نشد؛ این یادآوری عمومی است.";
  return general;
}
