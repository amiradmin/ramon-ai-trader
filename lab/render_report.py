"""Render a self-contained offline HTML market-laboratory report from JSON.

Standard-library only. No server, MT5 connection, scripts or network requests.
"""
import argparse
import html
import json
from pathlib import Path


def h(value):
    return html.escape(str(value) if value is not None else "—", quote=True)


def fmt(value, *, sign=False):
    if value is None:
        return "—"
    if isinstance(value, (float, int)):
        return f"{value:+,.2f}" if sign else f"{value:,.2f}"
    return h(value)


def render(payload):
    if payload.get("mode") != "ISOLATED_READ_ONLY_LABORATORY":
        raise ValueError("Expected isolated market laboratory JSON")
    periods=payload.get("historical_period_comparisons") or []
    if len(periods)!=2 or any(len(p.get("periods",[]))==0 for p in periods):
        raise ValueError("Missing chronological comparison results")
    timeline = []
    for i in range(max(len(p.get("periods",[])) for p in periods)):
        for p in periods:
            if i<len(p.get("periods",[])):
                row=p["periods"][i]
                timeline.append((p["name"],row))
    maximum=max([abs(r["net_gain"]) for _,r in timeline]+[1])
    cards=[]
    for name, row in timeline:
        net=float(row["net_gain"])
        bar_width=max(1, round(100*abs(net)/maximum))
        tone="positive" if net>0 else "negative" if net<0 else "neutral"
        cards.append(f"""<article class="card">
          <div class="eyebrow">{h(row['name'])}</div>
          <h3>{h(name.replace('_',' '))}</h3>
          <div class="kpi {tone}">{fmt(net,sign=True)}</div>
          <div class="bar"><div class="fill {tone}" style="width:{bar_width}%"></div></div>
          <dl><div><dt>Trades</dt><dd>{h(row['closed_trades'])}</dd></div>
          <div><dt>Win rate</dt><dd>{fmt(row['win_rate'])}%</dd></div>
          <div><dt>Profit factor</dt><dd>{fmt(row['profit_factor'])}</dd></div>
          <div><dt>Expectancy / trade</dt><dd>{fmt(row['expectancy_per_trade'])}</dd></div>
          <div><dt>Closed-trade drawdown</dt><dd>{fmt(row['max_closed_trade_drawdown_pct'])}%</dd></div>
          <div><dt>Gap-affected trades</dt><dd>{h(row['gap_affected_trades'])}</dd></div></dl>
          <p class="tiny">{h(row['start'])} through {h(row['end'])}</p>
        </article>""")
    quality=payload["data_quality"]
    risk=payload.get("live_eligibility") or {}
    alerts="".join(f"<li>{h(x.replace('_',' '))}</li>" for x in risk.get("warnings",[]))
    notes=f"""<div class="notice">
        <strong>Historical research only — not approved for real trading</strong>
        <p>{h(payload.get('limitations',''))}</p>
        <ul>{alerts or '<li>Profitability and broker execution remain unverified.</li>'}</ul>
        </div>"""
    summary="".join(f"""<tr><th>{h(n.replace('_',' '))}</th>
        <td>{h(p['closed_trades'])}</td><td>{fmt(p['net_gain'],sign=True)}</td>
        <td>{fmt(p['win_rate'])}%</td><td>{fmt(p['profit_factor'])}</td>
        <td>{fmt(p['max_drawdown_pct'])}%</td></tr>""" for n,p in (
            ("TREND_PULLBACK",payload["trend_pullback"]),
            ("IMPULSE_CONTINUATION",payload["impulse_continuation"])))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ramon Market Research Laboratory</title>
<style>
:root{{color-scheme:dark;--bg:#0a101c;--surface:#111c2e;--line:#29374a;--text:#eaf1f8;--muted:#91a3bb;--gold:#d8ac57}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);font:15px/1.55 system-ui,-apple-system,sans-serif;color:var(--text)}}main{{max-width:1180px;margin:auto;padding:32px 20px}}
header{{display:flex;justify-content:space-between;align-items:center;gap:15px;flex-wrap:wrap}}
h1{{margin:0;font-size:clamp(23px,4vw,36px)}}h2{{font-size:20px;margin:32px 0 14px}}h3{{font-size:16px;margin:4px 0}}
.tag{{font-size:12px;letter-spacing:.12em;color:var(--gold);font-weight:750}}
.sub,.tiny,dt{{color:var(--muted)}}.tiny{{font-size:12px}}.notice{{margin-top:24px;padding:18px;border:1px solid #9a6335;border-radius:12px;background:#332419}}
.notice strong{{color:#ffd49b}}.notice p{{margin-bottom:4px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr));gap:14px}}.card{{background:var(--surface);border:1px solid var(--line);padding:18px;border-radius:15px}}
.eyebrow{{font-size:11px;letter-spacing:.13em;color:var(--gold);font-weight:750}}.kpi{{font-size:27px;font-weight:780;font-variant-numeric:tabular-nums;margin:8px 0}}.positive{{color:#57c89b}}.negative{{color:#fa9186}}.neutral{{color:var(--muted)}}
.bar{{height:9px;background:#273448;border-radius:30px;overflow:hidden}}.fill{{height:100%;background:currentColor;border-radius:30px}}
dl{{display:grid;grid-template-columns:1fr 1fr;gap:8px 10px}}dt{{font-size:11px}}dd{{font-size:15px;font-weight:650;margin:0;font-variant-numeric:tabular-nums}}
.tablewrap{{overflow-x:auto;border:1px solid var(--line);border-radius:12px}}table{{border-collapse:collapse;width:100%;min-width:620px}}th,td{{padding:13px;text-align:left;border-bottom:1px solid var(--line)}}thead{{background:var(--surface)}}td{{font-variant-numeric:tabular-nums}}
.meta{{background:var(--surface);border-radius:12px;padding:18px;display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}
small{{color:var(--muted)}}.meta b{{display:block;overflow-wrap:anywhere}}footer{{margin:40px 0 12px;color:var(--muted);font-size:13px}}
@media print{{:root{{color-scheme:light;--bg:white;--surface:#f3f5f7;--text:#15202a;--muted:#566675;--line:#d5dce3}}.notice{{background:#fff2e5}}}}
</style></head><body><main>
<header><div><div class="tag">RAMON / MARKET RESEARCH LAB</div>
<h1>Strategy comparison</h1><div class="sub">XAUUSD_l · Read-only historical research dashboard</div></div>
<div class="tag">NO LIVE ORDERS</div></header>
{notes}
<h2>Overall replay results</h2>
<div class="tablewrap"><table><thead><tr><th>Strategy</th><th>Closed trades</th><th>Net gain*</th><th>Win rate</th><th>Profit factor</th><th>Max DD</th></tr></thead>
<tbody>{summary}</tbody></table></div>
<h2>Chronological train / validation / untouched test</h2>
<p class="sub">Single continuous simulation across PostgreSQL fetch batches. Trades are grouped by entry date; results are descriptive, not a trained model.</p>
<section class="cards">{''.join(cards)}</section>
<h2>Dataset provenance</h2><div class="meta">
<div><small>Source</small><b>{h(payload.get('market_source'))}</b></div>
<div><small>Symbol</small><b>{h(payload.get('symbol'))}</b></div>
<div><small>Period</small><b>{h(payload.get('start'))} → {h(payload.get('end'))}</b></div>
<div><small>M15 candles</small><b>{h(payload.get('selected_candles'))}</b></div>
<div><small>PostgreSQL fetch batches</small><b>{h(payload.get('database_fetch_batches'))}</b></div>
<div><small>Missing intervals</small><b>{h(quality.get('gap_count'))}</b></div>
<div><small>History fingerprint (SHA256)</small><b>{h(payload.get('history_fingerprint'))}</b></div>
<div><small>Modeled spread (points)</small><b>{h(payload.get('assumptions',{}).get('spread_points'))}</b></div>
</div>
<footer>* Profit is in assumed broker account units. Cent-account tick-value economics, exact historical spreads, unobserved intrabar paths and broker fills are not independently verified. Do not enable real execution from this report.</footer>
</main></body></html>"""


def main():
    parser=argparse.ArgumentParser(description="Offline HTML comparison dashboard; no server.")
    parser.add_argument("input",help="Lab JSON report")
    args=parser.parse_args()
    print(render(json.loads(Path(args.input).read_text(encoding="utf-8"))))


if __name__=="__main__":
    main()
