from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from urllib.request import Request, urlopen
from uuid import uuid4

from .bundles import FEATURES, atomic_json, load_active_bundle
from .shadow_roles import DIRECTION_EXTRA_FEATURES
from .train_roles import fit_direction_quality_role, load_trade_examples, train_bundle


HTML = r"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ramon Training Dashboard</title>
<style>
:root{color-scheme:dark;--bg:#08111d;--card:#101c2b;--line:#26394e;--text:#e9f1f8;--muted:#91a4b8;--green:#52d89c;--red:#ff7d86;--amber:#f1bd69;--blue:#65a9ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Tahoma,Arial,sans-serif}
main{max-width:1240px;margin:auto;padding:24px}.top{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;flex-wrap:wrap}
h1{margin:0 0 6px;font-size:24px}.sub{color:var(--muted);font-size:13px;line-height:1.8}
button{border:1px solid #39735f;background:#173b31;color:#b9f5dc;border-radius:10px;padding:12px 18px;font-weight:700;cursor:pointer;min-height:44px}
button:disabled{opacity:.5;cursor:not-allowed}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:20px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:15px}.label{color:var(--muted);font-size:12px}.value{font:700 24px monospace;margin-top:7px}.ok{color:var(--green)}.bad{color:var(--red)}.warn{color:var(--amber)}
.status{padding:14px;border:1px solid var(--line);border-radius:12px;background:#0c1724;line-height:1.9;margin-bottom:16px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);font-size:12px}
th,td{padding:10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}th{color:#bad0e4;background:#0d1927}
.scroll{overflow:auto;border-radius:12px}.foot{color:var(--muted);font-size:12px;margin-top:12px;line-height:1.8}
code{font-family:monospace;color:#c8d9e8}
.live-process{margin-bottom:16px;border:1px solid var(--line);border-radius:12px;background:#091522;overflow:hidden}
.live-head{display:flex;justify-content:space-between;gap:12px;padding:12px 14px;border-bottom:1px solid var(--line)}
.live-head span{color:var(--amber);font:700 12px monospace}
.events{max-height:320px;overflow:auto;padding:8px 14px;direction:ltr;text-align:left;font:12px/1.75 monospace}
.event{padding:4px 0;border-bottom:1px dashed #1d3044;white-space:pre-wrap;word-break:break-word}
.event:last-child{border-bottom:0}.event .ts{color:#758aa0}.event .stage{color:var(--blue)}.event .details{color:#98b3ca}.muted{color:var(--muted)}
@media(max-width:800px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
</head>
<body>
<main>
  <div class="top">
    <div>
      <h1>داشبورد آموزش Ramon</h1>
      <div class="sub">آموزش Role Modelها از معاملات واقعی، شامل معامله‌های جدول. مدل جدید فقط در صورت عبور از Validation/Promotion Gate فعال می‌شود.</div>
    </div>
    <button id="train">آموزش Ramon از داده‌های فعلی</button>
  </div>

  <section class="grid">
    <div class="card"><div class="label">نمونه‌های قابل آموزش</div><div id="trainable" class="value">—</div></div>
    <div class="card"><div class="label">معاملات جدول قابل آموزش</div><div id="manual" class="value">—</div></div>
    <div class="card"><div class="label">برد / باخت جدول</div><div id="wl" class="value">—</div></div>
    <div class="card"><div class="label">خالص معاملات جدول</div><div id="net" class="value">—</div></div>
  </section>

  <div id="status" class="status">در حال دریافت وضعیت واقعی آموزش…</div>
  <section class="live-process">
    <div class="live-head"><strong>پروسس زندهٔ آموزش</strong><span id="stage">—</span></div>
    <div id="events" class="events"><div class="event muted">هنوز آموزشی اجرا نشده است.</div></div>
  </section>

  <div class="scroll">
    <table>
      <thead><tr><th>زمان بسته‌شدن UTC</th><th>جهت</th><th>نتیجه</th><th>منبع</th><th>وضعیت آموزش</th></tr></thead>
      <tbody id="rows"><tr><td colspan="5">در حال دریافت…</td></tr></tbody>
    </table>
  </div>
  <div class="foot">حداقل واقعی trainer: <code id="minimum">—</code>. این عدد از تنظیم واقعی آموزش می‌آید؛ هیچ threshold نمایشی یا force-train وجود ندارد. وجود هر دو کلاس برد/باخت، پنجره‌های زمانی پاک، holdout کافی و بهتر بودن نسبت به مدل فعلی الزامی است.</div>
</main>
<script>
const $=id=>document.getElementById(id);
let busy=false;
const signed=v=>(v>=0?"+":"")+Number(v||0).toFixed(2);
function statusText(s){
  if(s.running)return "آموزش در حال اجراست؛ داده‌ها، پنجره‌های زمانی و Promotion Gate بررسی می‌شوند…";
  const r=s.report||{};
  if(!s.last_finished)return "هنوز از این داشبورد آموزشی اجرا نشده است.";
  if(s.error)return "آخرین آموزش خطا داشت: "+s.error;
  const map={
    promoted:"مدل کاندید از Promotion Gate عبور کرد؛ Roleهای تأییدشده به مسیر فعلی Ramon منتقل و بدون restart بارگذاری شدند.",
    validation_rejected:"آموزش انجام شد، اما کاندید از Validation Gate رد شد و مدل فعلی دست‌نخورده ماند.",
    waiting_for_closed_trades:"نمونه‌های بسته برای حداقل تعیین‌شده هنوز کافی نیست.",
    waiting_for_training_classes:"نمونه هست، اما توزیع برد/باخت برای آموزش یکی از Roleها کافی نیست.",
    insufficient_purged_windows:"پنجره‌های زمانی پاک برای Train/Meta/Holdout کافی نیست.",
    waiting_for_fresh_holdout:"برای ارزیابی جدید باید outcomeهای تازه بیشتری جمع شود.",
    waiting_for_regime_history:"دادهٔ رژیم بازار برای آموزش کافی نیست.",
    incumbent_holdout_overlap:"Holdout با مدل فعلی overlap دارد؛ برای جلوگیری از leakage آموزش متوقف شد."
  };
  let t=map[r.status]||("وضعیت آخرین آموزش: "+(r.status||"نامشخص"));
  if(r.bundle_id)t+=" · Bundle: "+r.bundle_id;
  if(s.reload&&s.reload.reloaded)t+=" · Reload: OK";
  return t;
}
function render(data){
  const st=data.stats||{}, s=data.training||{};
  $("trainable").textContent=st.trainable_samples??"—";
  $("manual").textContent=st.dashboard_trainable??"—";
  $("wl").textContent=(st.dashboard_wins??0)+" / "+(st.dashboard_losses??0);
  $("net").textContent=signed(st.dashboard_net_units||0)+" units";
  $("net").className="value "+((st.dashboard_net_units||0)>0?"ok":(st.dashboard_net_units||0)<0?"bad":"");
  $("minimum").textContent=data.minimum_samples??"—";
  $("status").firstChild.textContent=statusText(s);
  $("train").disabled=!!s.running;
  $("train").textContent=s.running?"در حال آموزش…":"آموزش Ramon از داده‌های فعلی";
  $("stage").textContent=s.current_stage||"—";
  const events=$("events");events.replaceChildren();
  for(const e of s.events||[]){
    const div=document.createElement("div");div.className="event";
    const ts=document.createElement("span");ts.className="ts";ts.textContent="["+String(e.at||"").replace("T"," ").replace("+00:00","Z")+"] ";
    const stage=document.createElement("span");stage.className="stage";stage.textContent=(e.stage||"")+" ";
    const msg=document.createElement("span");msg.textContent=e.message||"";
    div.append(ts,stage,msg);
    if(e.details&&Object.keys(e.details).length){
      const details=document.createElement("span");details.className="details";details.textContent=" · "+JSON.stringify(e.details);
      div.append(details);
    }
    events.append(div);
  }
  if(!(s.events||[]).length){const div=document.createElement("div");div.className="event muted";div.textContent="هنوز آموزشی اجرا نشده است.";events.append(div);}
  events.scrollTop=events.scrollHeight;
  const body=$("rows");body.replaceChildren();
  for(const r of data.recent||[]){
    const tr=document.createElement("tr");
    [r.closed_utc,r.direction,signed(r.net_units),r.entry_source,r.training_status].forEach((v,i)=>{
      const td=document.createElement("td");td.textContent=v??"—";
      if(i===2)td.className=Number(r.net_units)>0?"ok":Number(r.net_units)<0?"bad":"";
      tr.append(td);
    });body.append(tr);
  }
  if(!(data.recent||[]).length){const tr=document.createElement("tr");const td=document.createElement("td");td.colSpan=5;td.textContent="معامله‌ای ثبت نشده";tr.append(td);body.append(tr);}
}
async function refresh(){
  try{const r=await fetch("/api/status",{cache:"no-store"});render(await r.json());}catch(e){$("status").firstChild.textContent="ارتباط با داشبورد آموزش قطع است."}
}
$("train").addEventListener("click",async()=>{
  if(busy)return;busy=true;$("train").disabled=true;$("train").textContent="شروع آموزش…";
  try{
    const r=await fetch("/api/train",{method:"POST",headers:{"Content-Type":"application/json"},body:"{}"});
    const d=await r.json();if(!r.ok)throw new Error(d.error||"training start failed");
    await refresh();
  }catch(e){$("status").firstChild.textContent="شروع آموزش ناموفق: "+e.message}
  finally{busy=false}
});
refresh();setInterval(refresh,2000);
</script>
</body></html>"""


def _publish_promoted_shadow(db: Path, out: Path, symbol: str, chronos_model: str) -> dict[str, object]:
    """Mirror the validated promoted role bundle into the currently used shadow route.

    Base roles are byte-equivalent model objects loaded from the promoted bundle.
    Direction-quality roles are fitted from all clean aligned trades because the
    promoted bundle does not contain these display/quality specialists.
    """
    manifest, models = load_active_bundle(out, chronos_model)
    examples = load_trade_examples(db, symbol, chronos_model)
    roles: dict[str, object] = {}
    published = dict(models)
    for name in ("regime", "entry", "news", "meta", "risk"):
        roles[name] = {
            "status": "validated_promoted",
            "source_bundle": manifest.get("bundle_id"),
        }
    for direction, name in (("BUY", "buy_quality"), ("SELL", "sell_quality")):
        try:
            published[name] = fit_direction_quality_role(examples, direction)
            roles[name] = {"status": "experimental_ready", "samples": len([r for r in examples if r.direction == direction])}
        except (ValueError, KeyError, TypeError) as exc:
            roles[name] = {"status": "waiting_for_data", "reason": str(exc)}

    bundle_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-validated-" + uuid4().hex[:8]
    directory = out / "shadow" / "versions" / bundle_id
    directory.mkdir(parents=True, exist_ok=False)
    hashes: dict[str, str] = {}
    expected = {**FEATURES, **DIRECTION_EXTRA_FEATURES}
    for role, model in published.items():
        if role not in expected:
            continue
        path = directory / f"{role}.json"
        model.save(path)
        hashes[role] = hashlib.sha256(path.read_bytes()).hexdigest()
    shadow_manifest = {
        "mode": "shadow",
        "schema_version": 1,
        "bundle_id": bundle_id,
        "chronos_model": chronos_model,
        "symbol": symbol,
        "roles": roles,
        "sha256": hashes,
        "closed_trade_samples": len(examples),
        "validation": "mirrored_from_promoted_bundle",
        "promotion_gate_passed": True,
        "source_bundle_id": manifest.get("bundle_id"),
    }
    atomic_json(directory / "manifest.json", shadow_manifest)
    atomic_json(out / "shadow" / "current.json", {"bundle_id": bundle_id})
    return shadow_manifest


class TrainingState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.last_started: str | None = None
        self.last_finished: str | None = None
        self.report: dict[str, object] | None = None
        self.error = ""
        self.reload: dict[str, object] | None = None
        self.current_stage = ""
        self.events: list[dict[str, object]] = []


def _stats(db: Path, symbol: str, chronos_model: str) -> tuple[dict[str, object], list[dict[str, object]]]:
    examples = load_trade_examples(db, symbol, chronos_model)
    stats: dict[str, object] = {"trainable_samples": len(examples)}
    recent: list[dict[str, object]] = []
    with sqlite3.connect(db) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            """
            SELECT COUNT(*) AS n,
                   SUM(CASE WHEN t.net_units>0 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN t.net_units<=0 THEN 1 ELSE 0 END) AS losses,
                   COALESCE(SUM(t.net_units),0) AS net
            FROM decision_samples s JOIN trade_outcomes t ON t.sample_key=s.sample_key
            WHERE s.symbol=? AND t.symbol=s.symbol AND s.chronos_model=?
              AND s.schema_version>=3 AND s.news_features IS NOT NULL
              AND t.direction=s.direction AND t.training_status='LEARNABLE'
              AND t.entry_source='DASHBOARD_OPPORTUNITY'
              AND t.opened>=s.quote_time AND t.opened<=s.quote_time+90
              AND t.closed>=t.opened
            """,
            (symbol, chronos_model),
        ).fetchone()
        stats.update(
            dashboard_trainable=int(row["n"] or 0),
            dashboard_wins=int(row["wins"] or 0),
            dashboard_losses=int(row["losses"] or 0),
            dashboard_net_units=round(float(row["net"] or 0.0), 4),
        )
        recent = [
            dict(r)
            for r in con.execute(
                """
                SELECT datetime(closed,'unixepoch') AS closed_utc,direction,net_units,
                       entry_source,training_status
                FROM trade_outcomes
                WHERE symbol=? AND entry_source='DASHBOARD_OPPORTUNITY'
                ORDER BY closed DESC LIMIT 30
                """,
                (symbol,),
            )
        ]
    return stats, recent


def serve(host: str, port: int, db: Path, out: Path, symbol: str, chronos_model: str,
          model_url: str, minimum_samples: int) -> None:
    state = TrainingState()
    persisted_report = out / "training_report.json"
    if persisted_report.is_file():
        try:
            state.report = json.loads(persisted_report.read_text(encoding="utf-8"))
            state.last_finished = str(state.report.get("generated_at_utc") or "") or None
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass

    def run_training() -> None:
        def progress(stage: str, message: str, details: dict[str, object]) -> None:
            event = {
                "at": datetime.now(timezone.utc).isoformat(),
                "stage": stage,
                "message": message,
                "details": details,
            }
            with state.lock:
                state.current_stage = stage
                state.events.append(event)
                state.events = state.events[-200:]
        with state.lock:
            state.error = ""
            state.reload = None
            state.current_stage = "starting"
            state.events = [{
                "at": datetime.now(timezone.utc).isoformat(),
                "stage": "starting",
                "message": "درخواست آموزش دریافت شد",
                "details": {},
            }]
        try:
            report = train_bundle(
                db=db,
                symbol=symbol,
                chronos_model=chronos_model,
                out=out,
                minimum_samples=minimum_samples,
                regime_minimum=300,
                minimum_trades=20,
                progress=progress,
            )
            atomic_json(out / "training_report.json", report)
            reload_result = None
            if report.get("status") == "promoted":
                progress("publish_shadow", "در حال انتقال مدل تأییدشده به مسیر فعلی Ramon", {})
                shadow_manifest = _publish_promoted_shadow(db, out, symbol, chronos_model)
                request = Request(
                    model_url.rstrip("/") + "/reload-roles",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                progress("reload_roles", "در حال Reload کردن Roleهای تأییدشده در سرویس مدل", {})
                with urlopen(request, timeout=15) as response:
                    reload_result = json.loads(response.read().decode("utf-8"))
                progress("reload_roles", "Roleهای جدید با موفقیت Reload شدند", {"bundle": reload_result.get("bundle")})
                reload_result["shadow_bundle"] = shadow_manifest.get("bundle_id")
                reload_result["source_promoted_bundle"] = shadow_manifest.get("source_bundle_id")
            with state.lock:
                state.report = report
                state.reload = reload_result
                state.current_stage = str(report.get("status") or "finished")
        except Exception as exc:
            with state.lock:
                state.error = f"{type(exc).__name__}: {exc}"
                state.current_stage = "error"
                state.events.append({
                    "at": datetime.now(timezone.utc).isoformat(),
                    "stage": "error",
                    "message": state.error,
                    "details": {},
                })
        finally:
            with state.lock:
                state.running = False
                state.last_finished = datetime.now(timezone.utc).isoformat()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            return

        def _json(self, status: int, data: dict[str, object]) -> None:
            body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            route = self.path.split("?", 1)[0]
            if route == "/":
                body = HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if route != "/api/status":
                self.send_error(404)
                return
            try:
                stats, recent = _stats(db, symbol, chronos_model)
                with state.lock:
                    training = {
                        "running": state.running,
                        "last_started": state.last_started,
                        "last_finished": state.last_finished,
                        "report": state.report,
                        "error": state.error,
                        "reload": state.reload,
                        "current_stage": state.current_stage,
                        "events": list(state.events),
                    }
                self._json(200, {
                    "stats": stats,
                    "recent": recent,
                    "training": training,
                    "minimum_samples": minimum_samples,
                    "symbol": symbol,
                    "chronos_model": chronos_model,
                })
            except Exception as exc:
                self._json(503, {"error": f"{type(exc).__name__}: {exc}"})

        def do_POST(self) -> None:
            if self.path.split("?", 1)[0] != "/api/train":
                self.send_error(404)
                return
            with state.lock:
                if state.running:
                    self._json(409, {"error": "training already running"})
                    return
                state.running = True
                state.last_started = datetime.now(timezone.utc).isoformat()
            threading.Thread(target=run_training, name="ramon-manual-training", daemon=True).start()
            self._json(202, {"started": True})

    ThreadingHTTPServer((host, port), Handler).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ramon manual training dashboard")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8015)
    parser.add_argument("--db", default="/data/ramon_history.sqlite3")
    parser.add_argument("--out", default="/checkpoints/ensemble")
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--chronos-model", default=os.getenv("CHRONOS_MODEL", "autogluon/chronos-2-small"))
    parser.add_argument("--model-url", default="http://model:8012")
    parser.add_argument("--minimum-samples", type=int, default=500)
    args = parser.parse_args()
    serve(args.host, args.port, Path(args.db), Path(args.out), args.symbol,
          args.chronos_model, args.model_url, args.minimum_samples)


if __name__ == "__main__":
    main()
