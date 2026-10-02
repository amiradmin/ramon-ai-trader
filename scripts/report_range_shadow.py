"""Read-only range experiment summary (price units, not account profit)."""
import json
import sqlite3
c=sqlite3.connect('file:data/ramon_history.sqlite3?mode=ro',uri=True)
if not c.execute("select 1 from sqlite_master where name='range_shadow_trades'").fetchone():
 print(json.dumps({'status':'NO_OBSERVATIONS_YET'}))
else:
 rows=c.execute('select closed,net_price,net_r from range_shadow_trades').fetchall()
 done=[r for r in rows if r[0] is not None]
 pos=sum(max(r[1],0) for r in done);loss=-sum(min(r[1],0) for r in done)
 print(json.dumps({'mode':'OBSERVE_ONLY','positions':len(rows),'closed':len(done),'open':len(rows)-len(done),'wins':sum(r[1]>0 for r in done),'net_price':round(pos-loss,4),'net_r':round(sum(r[2] for r in done),4),'profit_factor':pos/loss if loss else None,'limitations':'sampled quotes; spread included; fees and actual slippage excluded'},indent=2))
