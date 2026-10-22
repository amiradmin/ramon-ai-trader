"""Persistent quote-sampled range experiment; never produces an execution decision."""
import sqlite3
from .core import Market, atr14
from .range_strategy import candidate

SCHEMA = '''CREATE TABLE IF NOT EXISTS range_shadow_trades (
 id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, direction TEXT NOT NULL,
 opened INTEGER NOT NULL, closed INTEGER, entry REAL NOT NULL, stop REAL NOT NULL,
 target REAL NOT NULL, exit REAL, net_price REAL, net_r REAL, reason TEXT,
 last_quote INTEGER NOT NULL, range_low REAL NOT NULL, range_high REAL NOT NULL)'''


def observe(db: str, market: Market, quote_time: int | None) -> dict:
    status = {'range_shadow_mode': 'OBSERVE_ONLY', 'range_shadow_effect': 'NONE'}
    if quote_time is None:
        return status | {'range_shadow_status': 'MISSING_QUOTE_TIME'}
    atr = atr14(market.bars)
    with sqlite3.connect(db, timeout=2) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(SCHEMA)
        active = conn.execute('SELECT * FROM range_shadow_trades WHERE symbol=? AND closed IS NULL', (market.symbol,)).fetchone()
        if active:
            if quote_time <= active['last_quote']:
                return status | {'range_shadow_status': 'STALE_QUOTE'}
            mark = market.bid if active['direction'] == 'BUY' else market.ask
            sign = 1 if active['direction'] == 'BUY' else -1
            reason = ('STOP' if sign*(mark-active['stop']) <= 0 else
                      'TARGET' if sign*(mark-active['target']) >= 0 else
                      'TIMEOUT' if quote_time-active['opened'] >= 1800 else '')
            conn.execute('UPDATE range_shadow_trades SET last_quote=? WHERE id=?', (quote_time,active['id']))
            if reason:
                pnl = sign*(mark-active['entry'])
                conn.execute('UPDATE range_shadow_trades SET closed=?,exit=?,net_price=?,net_r=?,reason=? WHERE id=?',
                             (quote_time,mark,pnl,pnl/abs(active['entry']-active['stop']),reason,active['id']))
                return status | {'range_shadow_status': 'CLOSED_'+reason, 'range_shadow_net_r': pnl/abs(active['entry']-active['stop'])}
            return status | {'range_shadow_status': 'OPEN', 'range_shadow_direction': active['direction']}
        last = conn.execute('SELECT max(closed) FROM range_shadow_trades WHERE symbol=?', (market.symbol,)).fetchone()[0]
        if last is not None and quote_time-last < 300:
            return status | {'range_shadow_status': 'COOLDOWN'}
        setup = candidate(market)
        if not setup['range_candidate']:
            return status | {'range_shadow_status': setup['range_shadow_status']}
        direction, entry, stop, target, low, high = (setup[k] for k in ['direction','entry','stop','target','low','high'])
        conn.execute('INSERT INTO range_shadow_trades(symbol,direction,opened,entry,stop,target,last_quote,range_low,range_high) VALUES(?,?,?,?,?,?,?,?,?)',
                     (market.symbol,direction,quote_time,entry,stop,target,quote_time,low,high))
        return status | {'range_shadow_status': 'OPENED', 'range_shadow_direction': direction}
