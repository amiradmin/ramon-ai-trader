const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const context=vm.createContext({Date,Number,Set});
vm.runInContext(fs.readFileSync('src/ramon/monitor_assets/trading-psychology.js','utf8'),context);
const advice=context.buildTradingPsychology;
const now=1800000000;
const fresh={generatedAt:new Date(now*1000).toISOString(),opportunitiesReceived:now,opportunities:[],trades:[]};
const trade=(id,net,age=60,source='HUMAN_ASSISTED')=>({trade_key:id,net_units:net,at:new Date((now-age)*1000).toISOString(),entry_source:source});
test('two recent manual losses trigger pause; auto trades do not',()=>{
 assert.match(advice({...fresh,trades:[trade('a',-1),trade('b',-2,120)]},now).title,/دو ضرر/);
 assert.doesNotMatch(advice({...fresh,trades:[trade('a',-1,60,'AUTO_RAMON'),trade('b',-2,120,'AUTO_RAMON')]},now).title,/دو ضرر/);
});
test('duplicates, unknown timezones, future and old closes cannot create a loss streak',()=>{
 for(const rows of [[trade('a',-1),trade('a',-1)], [trade('a',-1),{...trade('b',-1),at:null,broker_at:'2027-01-01'}], [trade('a',-1),trade('b',-1,-120)], [trade('a',-1),trade('b',-1,3601)]]){
  assert.doesNotMatch(advice({...fresh,trades:rows},now).title,/دو ضرر/);
 }
});
test('loss position and guardian advice use fresh confirmed open positions',()=>{
 const row={position_open:true,live_profit_units:-2,guardian_state:'OFF'};
 assert.match(advice({...fresh,opportunities:[row]},now).title,/پوزیشن در ضرر/);
 assert.match(advice({...fresh,opportunities:[{...row,guardian_state:'ARMED'}]},now).title,/محافظ فعال/);
 assert.doesNotMatch(advice({...fresh,opportunities:[row],opportunitiesReceived:now-16},now).title,/پوزیشن در ضرر/);
 assert.doesNotMatch(advice({...fresh,opportunities:[{...row,position_open:false}]},now).title,/پوزیشن در ضرر/);
});
test('two wins and rapid closes give different reminders',()=>{
 assert.match(advice({...fresh,trades:[trade('a',2),trade('b',3,120)]},now).title,/بعد از برد/);
 assert.match(advice({...fresh,trades:[trade('a',1),trade('b',-1,120),trade('c',1,180),trade('d',-1,240)]},now).title,/فعالیت زیاد/);
});
test('stale snapshot cannot make specific recent-trade claims',()=>{
 const result=advice({...fresh,generatedAt:new Date((now-20)*1000).toISOString(),trades:[trade('a',-1),trade('b',-1)]},now);
 assert.doesNotMatch(result.title,/دو ضرر/);
 assert.match(result.evidence,/یادآوری عمومی/);
});
test('closed-trade counts never claim an entry count or diagnose emotion',()=>{
 const result=advice({...fresh,trades:[trade('a',-1),trade('b',-1)]},now);
 assert.match(result.evidence,/بسته‌شده/);
 assert.match(result.evidence,/نه سیگنال معامله/);
});
