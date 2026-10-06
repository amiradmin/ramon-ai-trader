"""Decision flow monitor with an explicit, file-backed PRIMARY risk control."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
import tempfile
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
from .opportunities import read_opportunities


ASSETS = Path(__file__).with_name("monitor_assets")
REASONS = {
    "confirmed_countertrend_reversal": "برگشت خلاف روند با تأیید مدل و حرکت کوتاه‌مدت",
    "reversal_sample_not_saved": "تصمیم برگشت ثبت نشده؛ ورود متوقف است",
    "insufficient_model_strength": "قدرت پیش‌بینی کافی نیست",
    "insufficient_model_edge": "مزیت پس از هزینهٔ اسپرد کافی نیست",
    "direction_confirmation_required": "جهت ورود هنوز تأیید نشده",
    "market_direction_conflict": "جهت مستقل بازار خلاف جهت سیگنال است",
    "market_direction_neutral": "جهت مستقل بازار هنوز خنثی است",
    "entry_timing_required": "جهت تأیید شده ولی زمان ورود هنوز مناسب نیست",
    "adverse_intrabar_timing": "حرکت کوتاه‌مدت خلاف جهت ورود است",
    "late_entry_extension": "قیمت بیش از حد در جهت ورود حرکت کرده",
    "trend_conflict": "پیش‌بینی خلاف حرکت شدید اخیر است",
    "spread_or_atr": "اسپرد یا ATR خارج از شرط ورود است",
    "forecast_up": "پیش‌بینی صعودی تأیید شده",
    "forecast_down": "پیش‌بینی نزولی تأیید شده",
    "selector_v2_buy": "Selector v2 خرید را با کیفیت مدل و ریسک قابل‌قبول تأیید کرد",
    "selector_v2_sell": "Selector v2 فروش را با کیفیت مدل و ریسک قابل‌قبول تأیید کرد",
    "ai_engine_v2_buy": "AI Engine v2 خرید را با اجماع مدل‌های تخصصی تأیید کرد",
    "ai_engine_v2_sell": "AI Engine v2 فروش را با اجماع مدل‌های تخصصی تأیید کرد",
    "ai_engine_v2_veto": "AI Engine v2 کیفیت ترکیبی کافی برای ورود ندید",
    "ai_engine_v2_extreme_anomaly": "AI Engine v2 به‌دلیل ناهنجاری بسیار شدید ورود را متوقف کرد",
    "range_reversal_buy": "برگشت از کف رنج؛ نامزد خرید",
    "range_reversal_sell": "برگشت از سقف رنج؛ نامزد فروش",
    "range_sample_not_saved": "تصمیم رنج ذخیره نشده؛ ورود متوقف است",
    "intrabar_reversal_up": "برگشت صعودی با تأیید مدل",
    "intrabar_reversal_down": "برگشت نزولی با تأیید مدل",
    "regime_range_wait": "انتظار در رنج در نسخهٔ قبلی سیاست",
}


MARKET_STATES = {
    "trend_up": "روند صعودی", "trend_down": "روند نزولی",
    "pullback_up": "پولبک روند صعودی", "pullback_down": "پولبک روند نزولی",
    "range_low": "لبهٔ پایین رنج", "range_high": "لبهٔ بالای رنج", "range_middle": "وسط رنج",
    "breakout_up": "شکست صعودی", "breakout_down": "شکست نزولی",
    "breakout_retest_up": "آزمون مجدد شکست صعودی", "breakout_retest_down": "آزمون مجدد شکست نزولی",
    "false_breakout_up": "شکست کاذب سقف", "false_breakout_down": "شکست کاذب کف",
    "regime_transition": "تغییر رژیم", "price_gap": "جهش قیمت",
    "volatility_shock": "شوک نوسان", "low_liquidity": "اسپرد زیاد نسبت به نوسان",
    "flat_market": "بازار تخت", "disorderly_market": "بازار نامنظم",
    "volatility_compression": "فشردگی نوسان", "uncertain": "حالت نامشخص",
    "conflicting_structure": "ساختارهای متعارض",
}
REASONS.update({"market_state_wait_"+k: "سیاست بازار ورود را متوقف کرد: "+v for k,v in MARKET_STATES.items()})


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def object_json(raw):
    try:
        result = json.loads(raw or "{}")
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def utc_time(value):
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def freshness(timestamp, now, threshold=90):
    stamp = number(timestamp)
    if stamp is None:
        return {"state": "unknown", "age_seconds": None, "at": None}
    age = now - stamp
    return {"state": "clock_error" if age < -5 else "fresh" if age <= threshold else "stale",
            "age_seconds": round(max(0, age), 1), "at": utc_time(stamp)}


def default_diagnostic():
    root = Path(os.getenv("RAMON_MT5_USERS_ROOT", str(Path.home() / ".mt5/drive_c/users")))
    candidates = sorted(root.glob("*/AppData/Roaming/MetaQuotes/Terminal/Common/Files/Ramon_Diagnostic.txt"))
    return candidates[0] if candidates else None


def read_diagnostic(path):
    if path is None:
        return {}, "فایل وضعیت اکسپرت مشخص نشده است"
    try:
        raw = Path(path).read_bytes()
        if len(raw) > 100_000:
            raise ValueError("diagnostic exceeds size limit")
        text = raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig", errors="replace")
        lines = text.splitlines()
        result = {}
        for line in lines:
            if ": " in line:
                key, value = line.split(": ", 1)
                # The first Range MAIN line contains the execution opt-in flag.
                result.setdefault(key, value.strip())
        if "RAMON DIAGNOSTIC" not in text or not result.get("EA role", "").startswith("PRIMARY"):
            raise ValueError("expected a PRIMARY Ramon diagnostic")
        captured = result.get("Captured", "")
        result["captured_epoch"] = datetime.strptime(captured, "%Y.%m.%d %H:%M:%S UTC").replace(tzinfo=timezone.utc).timestamp()
        result["sample_key"] = result.get("DecisionID", "").split(" ")[0]
        return result, None
    except (OSError, ValueError) as exc:
        return {}, f"وضعیت اکسپرت خوانده نشد: {type(exc).__name__}"


def read_history(path, symbol, sample_key=""):
    """Bounded queries, one read transaction, no schema migration or writable DB."""
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        return {}, [], [], "تاریخچه هنوز در دسترس نیست"
    try:
        with sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=.3) as con:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA query_only=ON")
            con.execute("BEGIN")
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            rows = []
            if "decision_samples" in tables:
                rows = [dict(r) for r in con.execute(
                    "SELECT * FROM decision_samples WHERE symbol=? ORDER BY id DESC LIMIT 24", (symbol,))]
            sample = next((r for r in rows if sample_key and r.get("sample_key") == sample_key), {})
            if sample_key and not sample and "decision_samples" in tables:
                cols = {r[1] for r in con.execute("PRAGMA table_info(decision_samples)")}
                if "sample_key" in cols:
                    row = con.execute("SELECT * FROM decision_samples WHERE symbol=? AND sample_key=? LIMIT 1",
                                      (symbol, sample_key)).fetchone()
                    sample = dict(row) if row else {}
            if not sample:
                sample = rows[0] if rows else {}
            trades = []
            if "trade_outcomes" in tables:
                trade_cols = {r[1] for r in con.execute("PRAGMA table_info(trade_outcomes)")}
                role_filter = (
                    " AND (trade_role IS NULL OR trade_role='' OR UPPER(trade_role) IN ('PRIMARY','MAIN'))"
                    if "trade_role" in trade_cols else ""
                )
                trades = [dict(r) for r in con.execute(
                    "SELECT * FROM trade_outcomes WHERE symbol=?" + role_filter +
                    " ORDER BY closed DESC LIMIT 150", (symbol,))]
            return sample, rows, trades, None
    except sqlite3.Error as exc:
        return {}, [], [], f"تاریخچه خوانده نشد: {type(exc).__name__}"


def match(text, pattern):
    found = re.search(pattern, text or "")
    return found.group(1) if found else None


def trade_trace(path, symbol, trade_key):
    """Exact historical attribution; never mix in the current terminal snapshot."""
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        return None
    with sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=.3) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        row = con.execute("SELECT * FROM trade_outcomes WHERE symbol=? AND trade_key=? LIMIT 1",
                          (symbol, trade_key)).fetchone()
        if not row:
            return None
        trade = dict(row)
        if str(trade.get("trade_role") or "MAIN").upper() not in {"MAIN", "PRIMARY"}:
            return None
        row = con.execute("SELECT * FROM decision_samples WHERE symbol=? AND sample_key=? LIMIT 1",
                          (symbol, trade.get("sample_key"))).fetchone()
        sample = dict(row) if row else {}
    metadata = object_json(sample.get("model_metadata"))
    audit = metadata.get("decision_audit")
    audit = audit if isinstance(audit, dict) else {}
    def section(name):
        value = audit.get(name)
        return value if isinstance(value, dict) else {}
    base, final, settings = section("base"), section("final"), section("settings")
    stages = []
    def stage(id, title, state, detail, values):
        stages.append({"id": id, "title": title, "state": state, "detail": detail,
                       "values": {k: v for k, v in values.items() if v is not None}})
    def threshold(value, minimum):
        value, minimum = number(value), number(minimum)
        return "unknown" if value is None or minimum is None else "pass" if value >= minimum else "blocked"
    reason = final.get("reason")
    decision = final.get("decision") or sample.get("final_decision")
    joined = bool(sample)
    aligned = decision == trade.get("direction")
    stage("market", "مشاهدهٔ بازار", "observed" if base else "unknown", "قیمت هنگام تصمیم؛ قیمت اجرای سفارش جداست",
          {"قیمت خرید / فروش": f"{metric(base.get('signal_bid'))} / {metric(base.get('signal_ask'))}",
           "اسپرد، point": base.get("spread_points"), "ATR": base.get("atr")})
    stage("forecast", "پیش‌بینی مدل", "observed" if base.get("forecast_median") else "unknown", "پیش‌بینی ثبت‌شده در همین تصمیم",
          {"مدل": sample.get("chronos_model"), "کف / میانه / سقف": " / ".join(metric(base.get(k)) for k in ("forecast_low", "forecast_median", "forecast_high")), "افق، کندل": settings.get("horizon")})
    side = trade.get("direction", "").lower()
    stage("edge", "مزیت پس از اسپرد", threshold(base.get(side + "_edge"), base.get("minimum_edge")),
          "مقایسهٔ مزیت جهت معامله با حداقل ثبت‌شده",
          {"مزیت جهت معامله": base.get(side + "_edge"), "حداقل": base.get("minimum_edge")})
    strength_state = threshold(base.get("signal_strength"), base.get("minimum_strength"))
    weak_allowed = (settings.get("allow_weak_intrabar_entries") is True
                    and base.get("intrabar_confirmed") == 1 and base.get("ai_trend_confirmed") == 1
                    and base.get("intrabar_direction") == base.get("ai_trend_direction") == trade.get("direction")
                    and threshold(base.get("signal_strength"), base.get("intrabar_min_strength")) == "pass")
    stage("strength", "قدرت سیگنال", "observed" if strength_state == "blocked" and weak_allowed else strength_state,
          "قدرت کمتر از حد عادی؛ مسیر ورود ضعیف با دو تأیید مجاز بوده است" if strength_state == "blocked" and weak_allowed else "قدرت سیگنال و حداقل عادی",
          {"قدرت": base.get("signal_strength"), "حداقل عادی": base.get("minimum_strength"), "حداقل مسیر ضعیف": base.get("intrabar_min_strength")})
    confirms = all(k in base for k in ("intrabar_confirmed", "ai_trend_confirmed", "intrabar_direction", "ai_trend_direction"))
    confirmed = base.get("intrabar_confirmed") == base.get("ai_trend_confirmed") == 1 and base.get("intrabar_direction") == base.get("ai_trend_direction") == trade.get("direction")
    stage("confirmation", "تأیید جهت و زمان ورود", "pass" if confirmed else "blocked" if confirms else "unknown",
          REASONS.get(base.get("reason"), base.get("reason") or "تأییدها ثبت نشده‌اند"),
          {"تأیید کوتاه‌مدت": base.get("intrabar_confirmed"), "جهت کوتاه‌مدت": base.get("intrabar_direction"), "تأیید مسیر مدل": base.get("ai_trend_confirmed"), "جهت مسیر مدل": base.get("ai_trend_direction"), "حرکت ATR": base.get("intrabar_move_atr"), "سازگاری مسیر": base.get("ai_trend_consistency")})
    stage("route", "انتخاب مسیر و تصمیم نهایی", "pass" if decision in {"BUY", "SELL"} and aligned else "blocked" if decision else "unknown",
          REASONS.get(reason, reason or "تصمیم ذخیره‌شده پیدا نشد"),
          {"تصمیم پایه": base.get("decision"), "تصمیم نهایی": decision, "مسیر": "RANGE" if final.get("range_execution") == 1 else "CHRONOS" if final else None, "حالت بازار": final.get("market_state"), "واکنش مجاز": final.get("market_state_route")})
    external = section("external_models")
    moment = external.get("moment") if isinstance(external.get("moment"), dict) else {}
    finbert = external.get("finbert") if isinstance(external.get("finbert"), dict) else {}
    stage("guards", "خبر، ناهنجاری و مدل‌های کمکی", "observed" if external or section("news_snapshot") else "unknown",
          "مقادیر تاریخی مدل‌ها؛ عبور مستقل از قفل‌های اجرایی اکسپرت ثبت نشده است",
          {"ناهـنجاری": moment.get("moment_anomaly_label"), "نسبت ناهنجاری": moment.get("moment_anomaly_ratio"), "احساس خبر": finbert.get("finbert_sentiment_label"), "خبر": section("news_snapshot").get("news_event_title"), "رژیم ناظر": final.get("shadow_regime_label"), "نقش‌ها": "فقط ناظر" if final.get("role_shadow") == 1 else None})
    stage("risk", "حجم و ریسک ورود", "observed" if trade.get("initial_risk_units") is not None else "unknown",
          "مقادیر ثبت‌شدهٔ معامله؛ تأیید جداگانهٔ تمام مجوزها و قفل‌ها موجود نیست",
          {"حجم برنامه‌ریزی‌شده": trade.get("planned_volume"), "بودجهٔ ریسک، واحد حساب": trade.get("risk_budget_units"), "ریسک اولیه، واحد حساب": trade.get("initial_risk_units"), "سقف ریسک، دلار": trade.get("max_executable_risk_usd"), "استفاده از لات حداقل": trade.get("min_lot_override_used"), "فاصلهٔ حد ضرر": base.get("stop_distance"), "فاصلهٔ هدف": base.get("target_distance")})
    stage("execution", "اجرای واقعی سفارش", "observed", "ورود با تاریخچهٔ معامله تأیید شده است",
          {"جهت": trade.get("direction"), "قیمت اجرای واقعی": trade.get("actual_fill_price"), "نسخهٔ ثبت‌شده هنگام ورود": trade.get("entry_ea_version"), "نقش": trade.get("trade_role")})
    stage("exit", "خروج و نتیجه", "observed", trade.get("exit_detail") or trade.get("exit_reason") or "علت خروج ثبت نشده",
          {"سود / زیان خالص، واحد حساب": trade.get("net_units"), "نتیجه بر حسب R": trade.get("net_r"), "علت خروج": trade.get("exit_detail") or trade.get("exit_reason")})
    def stamp(name):
        value, offset = trade.get(name), trade.get(name + "_utc_offset_seconds")
        return {"at": utc_time(value - offset) if value is not None and offset is not None else None,
                "broker_at": utc_time(value) if offset is None else None}
    return {"trade_key": trade_key, "sample_key": trade.get("sample_key"), "symbol": symbol,
            "decision_joined": joined, "direction_matches": aligned if joined else None,
            "decision_at": utc_time(sample.get("captured")), "opened": stamp("opened"), "closed": stamp("closed"),
            "net_units": trade.get("net_units"), "direction": trade.get("direction"), "stages": stages,
            "warnings": (["تصمیم ذخیره‌شده برای این معامله پیدا نشد؛ مراحل مدل نامشخص هستند"] if not joined else
                         ["جهت تصمیم ذخیره‌شده با معامله هم‌خوان نیست"] if not aligned else [])}


def metric(value):
    n = number(value)
    return "—" if n is None else f"{n:.3f}"


def dollar_readiness(trades, current_ea_version=None):
    """Score readiness for a small real-dollar forward-test account from PRIMARY outcomes."""
    rows = [r for r in trades if number(r.get("closed")) is not None]
    rows_desc = sorted(rows, key=lambda r: number(r.get("closed")) or 0, reverse=True)
    rows_asc = list(reversed(rows_desc))
    count = len(rows_desc)

    wins = sum(max(0.0, number(r.get("net_units")) or 0.0) for r in rows_desc)
    losses = sum(max(0.0, -(number(r.get("net_units")) or 0.0)) for r in rows_desc)
    profit_factor = (wins / losses) if losses > 0 else (9.99 if wins > 0 else None)

    r_values = [number(r.get("net_r")) for r in rows_asc]
    r_values = [x for x in r_values if x is not None]
    expectancy_r = (sum(r_values) / len(r_values)) if r_values else None
    equity = peak = max_drawdown_r = 0.0
    for value in r_values:
        equity += value
        peak = max(peak, equity)
        max_drawdown_r = max(max_drawdown_r, peak - equity)

    if rows_asc:
        first_open = number(rows_asc[0].get("opened")) or number(rows_asc[0].get("closed")) or 0
        last_close = number(rows_asc[-1].get("closed")) or first_open
        span_days = max(0.0, (last_close - first_open) / 86400.0)
    else:
        span_days = 0.0

    current_version = str(current_ea_version or "")
    if not current_version and rows_desc:
        current_version = str(rows_desc[0].get("entry_ea_version") or "")
    current_version_trades = 0
    if current_version:
        for row in rows_desc:
            if str(row.get("entry_ea_version") or "") != current_version:
                break
            current_version_trades += 1

    sample_score = min(25.0, 25.0 * count / 150.0)
    pf_score = 0.0 if profit_factor is None else min(25.0, max(0.0, (profit_factor - 1.0) / 0.40 * 25.0))
    expectancy_score = 0.0 if expectancy_r is None else min(15.0, max(0.0, expectancy_r / 0.12 * 15.0))
    if not r_values:
        drawdown_score = 0.0
    elif max_drawdown_r <= 4.0:
        drawdown_score = 15.0
    else:
        drawdown_score = min(15.0, max(0.0, (12.0 - max_drawdown_r) / 8.0 * 15.0))
    duration_score = min(10.0, 10.0 * span_days / 21.0)
    stability_score = min(10.0, 10.0 * current_version_trades / 50.0)
    score = round(sample_score + pf_score + expectancy_score + drawdown_score + duration_score + stability_score)

    hard_checks = {
        "trades": count >= 100,
        "profit_factor": profit_factor is not None and profit_factor >= 1.25,
        "duration": span_days >= 14.0,
        "version_stability": current_version_trades >= 30,
        "expectancy": expectancy_r is not None and expectancy_r > 0,
        "drawdown": bool(r_values) and max_drawdown_r <= 10.0,
    }
    ready = score >= 80 and all(hard_checks.values())
    status = "READY" if ready else "CAUTION" if score >= 60 else "NOT READY"
    criteria = [
        {"id": "trades", "label": "معاملات PRIMARY", "value": str(count), "target": "حداقل 100", "pass": hard_checks["trades"]},
        {"id": "profit_factor", "label": "Profit Factor", "value": "—" if profit_factor is None else f"{profit_factor:.2f}", "target": "حداقل 1.25", "pass": hard_checks["profit_factor"]},
        {"id": "duration", "label": "مدت Forward Test", "value": f"{span_days:.1f} روز", "target": "حداقل 14 روز", "pass": hard_checks["duration"]},
        {"id": "version_stability", "label": "معامله روی نسخه فعلی", "value": str(current_version_trades), "target": "حداقل 30", "pass": hard_checks["version_stability"]},
        {"id": "expectancy", "label": "Expectancy", "value": "—" if expectancy_r is None else f"{expectancy_r:+.3f}R", "target": "بیشتر از 0R", "pass": hard_checks["expectancy"]},
        {"id": "drawdown", "label": "Max Drawdown", "value": "—" if not r_values else f"{max_drawdown_r:.2f}R", "target": "حداکثر 10R", "pass": hard_checks["drawdown"]},
    ]
    return {
        "score": score,
        "status": status,
        "ready": ready,
        "trade_count": count,
        "profit_factor": None if profit_factor is None else round(profit_factor, 3),
        "expectancy_r": None if expectancy_r is None else round(expectancy_r, 4),
        "max_drawdown_r": None if not r_values else round(max_drawdown_r, 3),
        "span_days": round(span_days, 2),
        "current_ea_version": current_version or None,
        "current_version_trades": current_version_trades,
        "criteria": criteria,
        "weights": {"sample": 25, "profit_factor": 25, "expectancy": 15, "drawdown": 15, "duration": 10, "version_stability": 10},
    }


def income_roadmap(readiness, diag):
    """Build a conservative, evidence-gated path from validation to income."""
    account_type = (diag.get("AccountType", "").split() or ["UNKNOWN"])[0].upper()
    balance_usd = number(match(diag.get("BalanceUnits"), r"BalanceUSDApprox: ([\d.]+)"))
    is_standard = account_type == "STANDARD"
    pf = number(readiness.get("profit_factor"))
    dd = number(readiness.get("max_drawdown_r"))
    span = number(readiness.get("span_days")) or 0.0
    version_trades = int(readiness.get("current_version_trades") or 0)

    stages = [
        {
            "id": "validate",
            "title": "اثبات روی حساب سنتی",
            "capital": "حساب فعلی",
            "eta": "حدود ۲–۳ هفته",
            "goal": "Readiness ≥ 80 و عبور از همهٔ گیت‌ها",
            "done": bool(readiness.get("ready")),
            "note": "Forward Test واقعی؛ بدون اتکا به بک‌تست به‌تنهایی.",
        },
        {
            "id": "pilot30",
            "title": "پایلوت حساب دلاری",
            "capital": "$30–$50",
            "eta": "حدود ۱–۲ ماه",
            "goal": "حساب STANDARD + حفظ معیارهای Readiness",
            "done": bool(readiness.get("ready")) and is_standard and balance_usd is not None and balance_usd >= 20,
            "note": "هدف این مرحله اثبات اجرای واقعی است، نه خرج خانه.",
        },
        {
            "id": "scale100",
            "title": "درآمد کوچک",
            "capital": "$100–$200",
            "eta": "حدود ۲–۳ ماه",
            "goal": "حداقل 50 معامله روی نسخهٔ پایدار و PF ≥ 1.25",
            "done": is_standard and balance_usd is not None and balance_usd >= 100
                    and version_trades >= 50 and pf is not None and pf >= 1.25,
            "note": "افزایش سرمایه فقط مرحله‌ای؛ ریسک متناسب با حساب.",
        },
        {
            "id": "scale500",
            "title": "درآمد جانبی محسوس",
            "capital": "$500+",
            "eta": "حدود ۳–۶ ماه",
            "goal": "حداقل 30 روز داده، PF ≥ 1.30 و Drawdown ≤ 8R",
            "done": is_standard and balance_usd is not None and balance_usd >= 500
                    and span >= 30 and pf is not None and pf >= 1.30
                    and dd is not None and dd <= 8.0,
            "note": "در این مرحله می‌توان بخشی از سود را برداشت و بقیه را برای رشد نگه داشت.",
        },
        {
            "id": "income200",
            "title": "هدف درآمد $200 / ماه",
            "capital": "≈ $4,000 سناریویی",
            "eta": "حدود ۶–۱۲ ماه",
            "goal": "چند ماه پایداری + سرمایه کافی",
            "done": is_standard and balance_usd is not None and balance_usd >= 4000
                    and span >= 90 and pf is not None and pf >= 1.30
                    and dd is not None and dd <= 8.0,
            "note": "سرمایهٔ $4,000 بر مبنای سناریوی 5٪ ماهانه است؛ پیش‌بینی یا تضمین بازده نیست.",
        },
        {
            "id": "household",
            "title": "درآمد قابل اتکاتر برای خانه",
            "capital": "پس از اثبات چندماهه",
            "eta": "حدود ۹–۱۸ ماه",
            "goal": "حداقل 90 روز عملکرد واقعی و عدم وابستگی به یک دورهٔ خاص بازار",
            "done": is_standard and balance_usd is not None and balance_usd >= 4000
                    and span >= 90 and pf is not None and pf >= 1.35
                    and dd is not None and dd <= 6.0,
            "note": "این مرحله باید با برداشت‌های واقعی و کنترل Drawdown تأیید شود.",
        },
    ]
    current = 0
    for idx, stage in enumerate(stages):
        if stage["done"]:
            current = idx + 1
        else:
            break
    current = min(current, len(stages) - 1)
    for idx, stage in enumerate(stages):
        stage["state"] = "done" if stage["done"] else "current" if idx == current else "locked"
        stage["number"] = idx + 1
    return {
        "account_type": account_type,
        "balance_usd": None if balance_usd is None else round(balance_usd, 2),
        "current_stage": stages[current]["id"],
        "current_stage_number": current + 1,
        "stage_count": len(stages),
        "stages": stages,
        "planning_monthly_return_percent": 5.0,
        "planning_income_target_usd": 200.0,
        "planning_capital_for_target_usd": 4000.0,
        "disclaimer": "مرحله‌های درآمدی سناریوی برنامه‌ریزی هستند و سود را تضمین یا پیش‌بینی نمی‌کنند.",
    }


def exact_decision_market_context(db, sample_key, *, m15_limit=12):
    """Load the immutable market snapshot supplied to this exact decision."""
    if not sample_key or not Path(db).is_file():
        return None
    try:
        import zlib
        with sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True) as con:
            row = con.execute(
                """SELECT b.codec,b.body,a.provenance_json
                   FROM inference_audit a
                   JOIN input_blobs b ON b.sha256=a.input_sha256
                   WHERE a.sample_key=?""",
                (str(sample_key),),
            ).fetchone()
        if not row or row[0] != "zlib-json-v1":
            return None
        context = json.loads(zlib.decompress(row[1]).decode("utf-8"))
        provenance = json.loads(row[2] or "{}")
        request = provenance.get("request") if isinstance(provenance.get("request"), dict) else {}
        offset = number(request.get("broker_utc_offset_seconds"))
        if offset is None:
            return None
        offset = int(offset)
        spread_points = 0
        bid, ask, point = (number(request.get(k)) for k in ("bid", "ask", "point"))
        if bid is not None and ask is not None and point is not None and point > 0:
            spread_points = round((ask - bid) / point)

        def convert(rows, *, latest_spread=False):
            output = []
            rows = list(rows or [])
            for index, bar in enumerate(rows):
                try:
                    output.append({
                        "time": int(bar["time"]) - offset,
                        "broker_time": int(bar["time"]),
                        "time_basis": "UTC canonical from recorded broker offset",
                        "broker_utc_offset_seconds": offset,
                        "open": float(bar["open"]),
                        "high": float(bar["high"]),
                        "low": float(bar["low"]),
                        "close": float(bar["close"]),
                        "spread_points": spread_points if latest_spread and index == len(rows) - 1 else 0,
                        "body": float(bar["close"]) - float(bar["open"]),
                        "range": float(bar["high"]) - float(bar["low"]),
                    })
                except (KeyError, TypeError, ValueError, OverflowError):
                    return []
            return output

        m15 = convert((context.get("bars") or [])[-int(m15_limit):])
        m1 = convert(request.get("micro_bars") or [], latest_spread=True)
        quote_time = number(request.get("quote_time"))
        return {
            "m15": m15,
            "m1": m1,
            "exact_input": True,
            "sample_key": str(sample_key),
            "broker_utc_offset_seconds": offset,
            "quote_time_utc": int(quote_time) - offset if quote_time is not None else None,
            "warnings": [],
        }
    except (sqlite3.Error, OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def recent_market_context(db, symbol="XAUUSD_l", m15_limit=12, m1_limit=15, *,
                          sample_key=None, signal_bar_time=None, quote_time=None):
    """Return exact decision input when available; otherwise historical fallback."""
    exact = exact_decision_market_context(db, sample_key, m15_limit=m15_limit)
    if exact is not None:
        return exact

    path = Path(db)
    if not path.is_file():
        return {"m15": [], "m1": [], "exact_input": False, "warnings": []}
    result = {"m15": [], "m1": [], "exact_input": False}
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as con:
            for timeframe, limit, key in (("M15", m15_limit, "m15"), ("M1", m1_limit, "m1")):
                rows = con.execute(
                    """SELECT time,open,high,low,close,COALESCE(spread_points,0)
                       FROM history_bars
                       WHERE symbol=? AND timeframe=?
                       ORDER BY time DESC LIMIT ?""",
                    (symbol, timeframe, int(limit)),
                ).fetchall()
                rows.reverse()
                result[key] = [
                    {
                        "time": int(t),
                        "time_basis": "broker time unknown offset",
                        "open": float(o),
                        "high": float(h),
                        "low": float(lo),
                        "close": float(cl),
                        "spread_points": int(spread or 0),
                        "body": float(cl - o),
                        "range": float(h - lo),
                    }
                    for t, o, h, lo, cl, spread in rows
                ]
    except sqlite3.Error:
        return {"m15": [], "m1": [], "exact_input": False, "warnings": []}
    warnings = []
    if any(result.values()):
        warnings.append("این تصمیم قبل از ثبت snapshot دقیق/offset ساخته شده است؛ کندل‌های نمایش‌داده‌شده فقط تاریخچهٔ تقریبی‌اند")
    signal = number(signal_bar_time)
    quote = number(quote_time)
    reference = quote if quote is not None else signal + 900 if signal is not None else None
    if signal is not None and result["m15"] and result["m15"][-1]["time"] != signal:
        warnings.append("آخرین کندل M15 تاریخچه با کندل سیگنال تصمیم یکسان نیست")
    if reference is not None:
        if not result["m1"]:
            warnings.append("کندل M1 در تاریخچه موجود نیست؛ ورودی زندهٔ M1 از این گزارش قابل تأیید نیست")
        elif reference - result["m1"][-1]["time"] > 120:
            warnings.append("کندل‌های M1 تاریخچه نسبت به زمان تصمیم قدیمی‌اند؛ آن‌ها را ورودی زندهٔ مدل تلقی نکنید")
        elif result["m1"][-1]["time"] > reference:
            warnings.append("آخرین کندل M1 تاریخچه بعد از زمان تصمیم است؛ ورودی همان تصمیم نیست")
    result["warnings"] = warnings
    return result


def build_snapshot(db, diagnostic=None, *, symbol="XAUUSD_l", now=None, health=None):
    now = time.time() if now is None else now
    diag, diag_error = read_diagnostic(diagnostic)
    sample, recent, trades, db_error = read_history(db, symbol, diag.get("sample_key", ""))
    metadata = object_json(sample.get("model_metadata"))
    audit = metadata.get("decision_audit") if isinstance(metadata.get("decision_audit"), dict) else {}
    base = audit.get("base") if isinstance(audit.get("base"), dict) else {}
    final = audit.get("final") if isinstance(audit.get("final"), dict) else {}
    settings = audit.get("settings") if isinstance(audit.get("settings"), dict) else {}
    if diag and not diag.get("Symbol", "").startswith(symbol + " "):
        diag, diag_error = {}, "نماد فایل وضعیت با نماد داشبورد یکسان نیست"
    model_time = freshness(sample.get("captured"), now)
    ea_time = freshness(diag.get("captured_epoch"), now, 20)
    joined = bool(sample.get("sample_key") and diag.get("sample_key") == sample.get("sample_key"))
    decision = final.get("decision") or sample.get("final_decision") or "UNKNOWN"
    reason = final.get("reason") or "UNKNOWN"
    assessment = metadata.get("market_assessment") or {}
    ea_status = diag.get("Status", "وضعیت اکسپرت در دسترس نیست")
    nodes = []

    engine_labels = {
        "forecast": "AI · Chronos",
        "edge": "AI→Logic",
        "strength": "AI→Logic",
        "base": "AI+Logic",
        "shadow": "AI · Shadow",
        "decision": "AI+Logic",
    }

    def probability_badge(value, label):
        parsed = number(value)
        if parsed is None or parsed < 0.0:
            return None
        if parsed <= 1.0:
            return f"{label} {parsed * 100:.0f}%"
        return f"{label} {parsed:.2f}"

    def node(id, title, status, detail, *, source="model", values=None, score=None):
        stamp = ea_time if source == "ea" else model_time
        if source == "health":
            stamp = {"state": "fresh" if health and health.get("ready") else "unknown", "at": utc_time(now), "age_seconds": 0}
        effective = status if stamp["state"] == "fresh" else "stale" if stamp["state"] in {"stale", "clock_error"} else "unknown"
        nodes.append({"id": id, "title": title, "state": effective, "observed_state": status,
                      "detail": detail, "source": source, "engine": engine_labels.get(id),
                      "score": score,
                      "manual_override_supported": id in {"timing", "extension", "edge", "strength", "market_direction", "entry_timing", "base", "decision", "range"},
                      "manual_review_supported": id in {"news", "account", "limits", "risk", "order", "position"},
                      "observed_at": stamp["at"], "age_seconds": stamp["age_seconds"],
                      "values": values or {}})

    market_status = "blocked" if any(x in ea_status.lower() for x in ("stale", "disconnected", "symbol trading disabled")) else "observed" if diag else "unknown"
    node("market", "دادهٔ بازار و قیمت", market_status, ea_status, source="ea",
         values={"Bid": number(match(diag.get("Bid"), r"^([\d.]+)")),
                 "Ask": number(match(diag.get("Bid"), r"Ask: ([\d.]+)")),
                 "اسپرد، point": number(match(diag.get("Bid"), r"Spread\(points\): ([\d.]+)")),
                 "اتصال ترمینال": match(diag.get("Market"), r"TerminalConnected: (\w+)"),
                 "زمان آخرین درخواست": diag.get("Snapshot cadence"),
                 "حالت بازار و واکنش": metadata.get("market_assessment") or "در نسخهٔ این تصمیم ثبت نشده"})
    node("service", "سرویس تصمیم‌گیری", "pass" if health and health.get("ready") else "unknown",
         "سرویس پاسخ می‌دهد؛ تازگی پیش‌بینی جدا بررسی می‌شود" if health and health.get("ready") else "پاسخ سلامت سرویس در دسترس نیست",
         source="health", values={"مدل": (health or {}).get("model"), "حالت نقش‌ها": (health or {}).get("ensemble_mode")})
    node("forecast", "پیش‌بینی Chronos", "observed" if base.get("forecast_median", 0) else "unknown",
         "پیش‌بینی از کندل‌های بستهٔ M15؛ بازهٔ عدم‌قطعیت همراه آن",
         score=probability_badge(base.get("signal_strength"), "قدرت"),
         values={"کف / میانه / سقف": " / ".join(metric(base.get(k)) for k in ("forecast_low", "forecast_median", "forecast_high")),
                 "ATR": base.get("atr"), "مدل ثبت‌شده": sample.get("chronos_model")})
    edge, minimum = number(max(number(base.get("buy_edge")) or 0, number(base.get("sell_edge")) or 0)), number(base.get("minimum_edge"))
    node("edge", "مزیت پس از اسپرد", "pass" if minimum is not None and edge >= minimum else "blocked" if minimum is not None else "unknown",
         "مزیت جهت برتر باید به حداقل برسد",
         values={"مزیت خرید": base.get("buy_edge"), "مزیت فروش": base.get("sell_edge"), "حداقل مزیت": minimum})
    strength, floor = number(base.get("signal_strength")), number(base.get("minimum_strength"))
    node("strength", "قدرت پیش‌بینی", "pass" if strength is not None and floor is not None and strength >= floor else "blocked" if strength is not None and floor is not None else "unknown",
         "قدرت پایین فقط با تأیید هم‌زمان برگشت و مسیر مدل می‌تواند پذیرفته شود",
         score=probability_badge(strength, "Chronos"),
         values={"قدرت": strength, "حداقل قدرت عادی": floor, "ورود ضعیف مجاز": settings.get("allow_weak_intrabar_entries")})
    proposed_direction = (
        base.get("decision")
        if base.get("decision") in {"BUY", "SELL"}
        else ("BUY" if (number(base.get("buy_edge")) or 0) > (number(base.get("sell_edge")) or 0) else "SELL")
    )
    detected_direction = final.get("market_direction")
    cent_gate_active = final.get("cent_direction_gate_active") == 1
    direction_known = detected_direction in {"BUY", "SELL"}
    direction_matches = direction_known and detected_direction == proposed_direction
    direction_state = (
        "pass" if cent_gate_active and direction_matches
        else "blocked" if cent_gate_active and detected_direction in {"BUY", "SELL", "NEUTRAL"}
        else "observed" if detected_direction else "unknown"
    )
    buy_quality = number(final.get("shadow_buy_success_probability"))
    sell_quality = number(final.get("shadow_sell_success_probability"))
    direction_quality_badge = None
    if buy_quality is not None and buy_quality >= 0 and sell_quality is not None and sell_quality >= 0:
        direction_quality_badge = f"BUY {buy_quality * 100:.0f}% · SELL {sell_quality * 100:.0f}%"
    node("market_direction", "تشخیص جهت بازار", direction_state,
         "جهت مستقل بازار با سیگنال هم‌جهت است" if direction_matches else
         "جهت مستقل بازار خنثی است؛ ورود متوقف می‌شود" if detected_direction == "NEUTRAL" else
         "جهت مستقل بازار خلاف سیگنال است؛ ورود متوقف می‌شود" if direction_known else
         "تشخیص جهت مستقل هنوز ثبت نشده",
         score=direction_quality_badge,
         values={"فعال روی حساب سنتی": "بله" if cent_gate_active else "خیر",
                 "جهت سیگنال": proposed_direction,
                 "جهت مستقل بازار": detected_direction,
                 "امتیاز جهت": final.get("market_direction_score"),
                 "بازده ۱ کندل / ATR": final.get("market_direction_ret_1_atr"),
                 "بازده ۴ کندل / ATR": final.get("market_direction_ret_4_atr"),
                 "حرکت M1 / ATR": final.get("market_direction_micro_move_atr"),
                 "ساختار قیمت": final.get("market_direction_structure")})

    timing_ready = final.get("entry_timing_ready") == 1
    timing_direction = final.get("entry_timing_direction")
    signal_timing_ready = timing_ready and direction_matches and timing_direction == proposed_direction
    timing_state = (
        "pass" if cent_gate_active and signal_timing_ready
        else "blocked" if cent_gate_active and direction_matches
        else "idle" if cent_gate_active
        else "observed" if final.get("entry_timing_direction") else "unknown"
    )
    node("entry_timing", "زمان مناسب ورود", timing_state,
         "زمان ورود جهت مستقل بازار تأیید شده، اما این جهت با سیگنال مدل هم‌جهت نیست؛ زمان ورود سیگنال تأیید نشده" if timing_ready and not signal_timing_ready else
         "چرخش و حرکت کوتاه‌مدت زمان ورود سیگنال را تأیید کرده‌اند؛ مجوز نهایی ورود جدا بررسی می‌شود" if signal_timing_ready else
         "جهت درست است ولی چرخش/حرکت کوتاه‌مدت هنوز ورود را تأیید نکرده" if direction_matches else
         "تا تأیید جهت بازار، زمان ورود سیگنال قابل تأیید نیست",
         score=probability_badge(final.get("entry_probability"), "Entry AI"),
         values={"جهت بررسی": timing_direction,
                 "جهت سیگنال": proposed_direction,
                 "زمان جهت بررسی تأیید شده": "بله" if timing_ready else "خیر",
                 "آمادهٔ ورود": "بله" if signal_timing_ready else "خیر",
                 "حرکت کوتاه / ATR": final.get("entry_timing_move_atr"),
                 "برگشت / ATR": final.get("entry_timing_rebound_atr"),
                 "چرخش کوتاه‌مدت": "بله" if final.get("entry_timing_turn") == 1 else "خیر",
                 "حداقل حرکت / ATR": settings.get("trend_min_micro_move_atr"),
                 "حداقل برگشت / ATR": settings.get("intrabar_min_rebound_atr")})

    base_reason = base.get("reason")
    after_guards = base_reason in {"insufficient_model_edge", "insufficient_model_strength", "direction_confirmation_required"} or base.get("decision") in {"BUY", "SELL"}
    conflict_active = base.get("trend_conflict_active") == 1 or base_reason == "trend_conflict"
    conflict_override = base.get("trend_conflict_override_passed") == 1
    conflict_state = "pass" if conflict_override else "blocked" if base_reason == "trend_conflict" else "pass" if after_guards or base_reason in {"adverse_intrabar_timing", "late_entry_extension"} else "unknown"
    extension_state = "blocked" if base_reason == "late_entry_extension" else "pass" if after_guards else "unknown"

    override_floor = number(base.get("trend_conflict_override_strength"))
    if override_floor is None:
        override_floor = number(settings.get("trend_conflict_override_strength"))
    strength_ok = strength is not None and override_floor is not None and strength >= override_floor
    intrabar_ok = base.get("intrabar_confirmed") == 1
    path_ok = (
        base.get("ai_trend_confirmed") == 1
        and base.get("ai_trend_direction") == base.get("intrabar_direction")
    )
    if conflict_override:
        conflict_detail = "تعارض شدید جهت وجود داشت، اما برگشت پرقدرت با تأیید کوتاه‌مدت و مسیر Chronos مجاز شد"
    elif base_reason == "trend_conflict":
        failed = []
        if not strength_ok:
            failed.append("قدرت Chronos به حد عبور نرسیده")
        if not intrabar_ok:
            failed.append("برگشت کوتاه‌مدت تأیید نشده")
        if not path_ok:
            failed.append("مسیر Chronos برگشت را تأیید نکرده")
        conflict_detail = "تعارض شدید جهت؛ ورود متوقف شد" + (": " + "، ".join(failed) if failed else "")
    elif conflict_active:
        conflict_detail = "تعارض جهت ثبت شده است؛ نتیجهٔ عبور مستقل در این نسخه مشخص نیست"
    elif conflict_state == "pass":
        conflict_detail = "تعارض شدید جهت وجود ندارد"
    else:
        conflict_detail = "نتیجهٔ مستقل این شرط ثبت نشده"

    node("timing", "قفل تعارض جهت", conflict_state, conflict_detail,
         values={"دلیل پایه": base_reason,
                 "حرکت اخیر / ATR": base.get("recent_move_atr"),
                 "حرکت هم‌جهت سیگنال / ATR": base.get("aligned_recent_move_atr"),
                 "بازهٔ بررسی، کندل": settings.get("trend_conflict_lookback"),
                 "حد تعارض ATR": settings.get("trend_conflict_atr"),
                 "قدرت Chronos": strength,
                 "حد قدرت عبور": override_floor,
                 "قدرت کافی برای عبور": "بله" if strength_ok else "خیر" if strength is not None and override_floor is not None else "نامشخص",
                 "تأیید کوتاه‌مدت": "بله" if intrabar_ok else "خیر",
                 "تأیید مسیر Chronos": "بله" if path_ok else "خیر",
                 "نتیجهٔ override": "عبور مجاز" if conflict_override else "مسدود" if base_reason == "trend_conflict" else "لازم نبود"})
    node("extension", "قفل ورود دیرهنگام", extension_state,
         "قیمت بیش از حد در جهت ورود حرکت کرده" if extension_state == "blocked" else "امتداد قیمت از حد مجاز عبور نکرده" if extension_state == "pass" else "بررسی این شرط پس از قفل قبلی متوقف شد؛ نتیجه ثبت نشده",
         values={"دلیل پایه": base_reason, "حد امتداد ATR": settings.get("maximum_entry_extension_atr")})
    node("base", "تصمیم مسیر عادی", "blocked" if base.get("decision") == "WAIT" else "pass" if base.get("decision") in {"BUY", "SELL"} else "unknown",
         REASONS.get(base.get("reason"), base.get("reason", "تصمیم پایه ثبت نشده")),
         values={"تصمیم پایه": base.get("decision"), "دلیل پایه": base.get("reason")})
    range_execution = final.get("range_execution") == 1
    range_setup = metadata.get("range_setup") if isinstance(metadata.get("range_setup"), dict) else {}
    range_disabled = (health or {}).get("range_main_enabled") is False or diag.get("Range MAIN", "").startswith("NO")
    node("range", "مسیر جایگزین: برگشت رنج", "pass" if range_execution else "idle" if range_disabled else "blocked",
         "برگشت رنج تأیید شده؛ اکسپرت سقف ریسک و هدف را دوباره بررسی می‌کند" if range_execution else
         "علت رد هر شرط رنج در تاریخچهٔ این نسخه ذخیره نشده؛ نتیجه را حدس نمی‌زنیم",
         values={"فعال در سرویس": (health or {}).get("range_main_enabled"), "فعال در اکسپرت": diag.get("Range MAIN"),
                 "مجاز برای WAIT": "قدرت / مزیت ناکافی، حرکت مخالف، نبود تأیید جهت", "Setup ثبت‌شده": range_setup or None})
    shadow = final.get("role_shadow") == 1 or metadata.get("ensemble_mode") == "shadow"
    regime_badge = probability_badge(final.get("regime_probability"), "Regime")
    full_sl_badge = probability_badge(final.get("shadow_full_sl_probability"), "Full-SL")
    shadow_badge = " · ".join(x for x in (regime_badge, full_sl_badge) if x) or None
    node("shadow", "تشخیص رژیم و مدل‌های ناظر", "shadow" if shadow else "observed" if final else "unknown",
         "در حالت ناظر روی ورود، خروج و حجم اثر ندارد" if shadow else "حالت فعال نقش‌ها فقط از دادهٔ ثبت‌شده تعیین می‌شود",
         score=shadow_badge,
         values={"رژیم ناظر": final.get("shadow_regime_label"), "احتمال رژیم": final.get("regime_probability"),
                 "نقش‌های فعال": final.get("ensemble_active"), "توجه": "RANGE/UNCLEAR رنج قطعی نیست"})
    final_ai_badge = (
        probability_badge(final.get("ai_engine_v2_score"), "AI Score")
        or probability_badge(final.get("meta_probability"), "Meta")
    )
    node("decision", "تصمیم نهایی مدل", "blocked" if decision == "WAIT" else "pass" if decision in {"BUY", "SELL"} else "unknown",
         REASONS.get(reason, reason), score=final_ai_badge,
         values={"تصمیم": decision, "دلیل": reason, "شناسه": sample.get("sample_key"),
                                             "مسیر": "RANGE" if range_execution else "CHRONOS",
                                             "حالت بازار": assessment.get("state", "ثبت نشده"),
                                             "واکنش مجاز": assessment.get("route", "ثبت نشده"),
                                             "شرایط هم‌زمان": assessment.get("conditions", []),
                                             "شواهد تشخیص": assessment.get("evidence", {})})
    # These are terminal observations, not a replay of gates that short-circuit.
    node("news", "قفل خبر", "blocked" if "NEWS GUARD" in ea_status else "pass" if diag.get("News") else "unknown",
         "ورود از ۳۰ دقیقه قبل تا ۳۰ دقیقه بعدِ خبر پراثر متوقف است؛ تقویم نامعتبر هم مانع ورود است",
         score=probability_badge(final.get("news_probability"), "News AI"),
         source="ea", values={"آخرین خبر": diag.get("News"), "وضعیت اکسپرت": ea_status if "NEWS GUARD" in ea_status else "عبور از این گیت در هر تصمیم ثبت نشده"})
    live = match(diag.get("Live"), r"^(\w+)")
    permissions = diag.get("Trade permissions", "")
    node("account", "حساب، مجوز و حالت زنده", "blocked" if live in {"BLOCKED", "OFF", "OBSERVE"} or "NO" in permissions or "AccountLock: FAIL" in diag.get("Live", "") else "pass" if live else "unknown",
         "وضعیت مشاهده‌شدهٔ مجوزها؛ مجوز به معنی ارسال سفارش نیست", source="ea",
         values={"حالت": live, "قفل حساب": match(diag.get("Live"), r"AccountLock: (\w+)"),
                 "مجوز ترمینال": match(permissions, r"terminal=(\w+)"),
                 "مجوز اکسپرت": match(permissions, r"ea=(\w+)"),
                 "مجوز حساب": match(permissions, r"account=(\w+)"),
                 "واحد حساب در هر دلار": number(match(diag.get("MoneyUnitsConfirmed"), r"MoneyUnitsPerUSD: ([\d.]+)"))})
    cooldown_block = any(x in ea_status for x in ("cooldown", "Daily trade limit", "Entry already used", "history unavailable", "ACCOUNT LOSS LIMITS"))
    node("limits", "محدودیت ورود و زیان حساب", "blocked" if cooldown_block else "pass" if diag else "unknown",
         ea_status if cooldown_block else "بعد از دو SL زیان‌دهٔ پیاپی هم‌جهت: ۳۰ دقیقه وقفه؛ یک ورود MAIN در هر M15",
         source="ea", values={"ورودهای امروز": diag.get("Trades today"), "قفل زیان حساب": diag.get("AccountLossLimits"), "وقفهٔ رنج": "۵ دقیقه پس از بسته‌شدن",
                               "نتیجهٔ اجرای گیت": "ثبت نشده" if not cooldown_block else ea_status})
    risk = diag.get("RiskGate", "")
    node("risk", "حجم، ریسک و مارجین", "blocked" if "BLOCK" in risk or any(x in ea_status for x in ("risk >", "hard risk cap", "Insufficient margin", "SL risk >", "TP not inside", "reward/risk <")) else "pass" if risk else "unknown",
         "پیش‌نمایش ریسک با تأیید نهایی هنگام سفارش فرق دارد", source="ea",
         score=probability_badge(final.get("shadow_full_sl_probability"), "Full-SL"),
         values={"پیش‌نمایش ریسک": risk or None,
                 "بودجهٔ ترجیحی، دلار": number(match(diag.get("RiskPerTradeUSD"), r"^([\d.]+)")),
                 "سقف اجرای لات حداقل، دلار": number(match(diag.get("MinLotOverride"), r"MaxExecutableRiskUSD: ([\d.]+)")),
                 "ریسک تخمینی SL، دلار": number(match(diag.get("EstimatedSLAccountUnits"), r"EstimatedSLUSD: ([\d.]+)")),
                 "حجم پیش‌نمایش، لات": number(match(diag.get("SizingSide"), r"PlannedVolume: ([\d.]+)")),
                 "سقف رنج": "۵ واحد حساب؛ با تبدیل ۱۰۰ واحد/دلار = ۵ سنت"})
    position = diag.get("Managed position", "")
    has_position = bool(position and position != "NONE")
    order_seen = ea_status.startswith("Order sent")
    order_block = any(x in ea_status for x in ("Order rejected", "TRADE BLOCKED", "Range MAIN blocked", "Another robot", "Quote changed", "DIRECTION GUARD"))
    node("order", "اجرا در MT5", "active" if has_position else "pass" if order_seen else "blocked" if order_block else "idle" if diag else "unknown",
         ea_status, source="ea", values={"پوزیشن": position or None, "اتصال به تصمیم مدل": joined,
                                        "شواهد": "وضعیت زندهٔ اکسپرت؛ سیگنال مدل به‌تنهایی سفارش نیست"})
    node("position", "مدیریت پوزیشن و خروج", "active" if has_position else "idle" if position == "NONE" else "unknown",
         "مدیریت رنج: TP سریع، SL مرزی و ۳۰ دقیقه؛ MAIN: مراحل TP و خروج زودهنگام زیان",
         source="ea", values={"پوزیشن": position or None, "مرحلهٔ TP": diag.get("TPStage"), "قفل سود": diag.get("TPStageLock"),
                              "خروج زیان": diag.get("EarlyAdverseExit"), "توقف خروج بازار بسته": diag.get("MarketClosedExitPause")})
    edges = [
        ("market", "service", "داده"), ("service", "forecast", "آماده"),
        ("forecast", "timing", "قفل"), ("timing", "extension", "امتداد"), ("extension", "edge", "مزیت"), ("edge", "strength", "قدرت"),
        ("strength", "market_direction", "جهت"), ("market_direction", "entry_timing", "زمان"),
        ("entry_timing", "base", "تصمیم"),
        ("base", "decision", "عادی"), ("forecast", "shadow", "ناظر"),
        ("base", "range", "WAIT"), ("range", "decision", "برگشت رنج"),
        ("decision", "news", "سیگنال"), ("news", "account", "مجوز"),
        ("account", "limits", "سقف"), ("limits", "risk", "حجم"),
        ("risk", "order", "سفارش"), ("order", "position", "پوزیشن"),
    ]
    timeline = []
    for row in recent:
        a = object_json(row.get("model_metadata")).get("decision_audit") or {}
        f = a.get("final", {}) if isinstance(a, dict) else {}
        timeline.append({"sample_key": row.get("sample_key"), "at": utc_time(row.get("captured")),
                         "decision": f.get("decision") or row.get("final_decision") or "UNKNOWN",
                         "reason": f.get("reason", "UNKNOWN"), "detail": REASONS.get(f.get("reason"), f.get("reason", "دلیل ثبت نشده")),
                         "strategy": "RANGE" if row.get("chronos_model") == "range-reversal-v1" else "CHRONOS"})
    outcomes = []
    for row in trades[:8]:
        offset = row.get("closed_utc_offset_seconds")
        outcomes.append({"trade_key": row.get("trade_key"), "direction": row.get("direction"), "net_units": row.get("net_units"),
                         "exit": row.get("exit_detail") or row.get("exit_reason"),
                         "at": utc_time(row["closed"] - offset) if offset is not None else None,
                         "broker_at": utc_time(row["closed"]) if offset is None else None,
                         "time_basis": "UTC" if offset is not None else "broker time unknown offset",
                         "strategy": row.get("entry_strategy"), "role": row.get("trade_role"),
                         "entry_source": row.get("entry_source") or "AUTO_RAMON"})
    handlers = audit.get("handlers") if isinstance(audit.get("handlers"), dict) else {}
    external_models = audit.get("external_models") if isinstance(audit.get("external_models"), dict) else {}
    moment_snapshot = external_models.get("moment") if isinstance(external_models.get("moment"), dict) else {}
    finbert_snapshot = external_models.get("finbert") if isinstance(external_models.get("finbert"), dict) else {}
    shadow_forecasts = audit.get("shadow_forecasts") if isinstance(audit.get("shadow_forecasts"), dict) else {}
    timesfm_snapshot = shadow_forecasts.get("timesfm3") if isinstance(shadow_forecasts.get("timesfm3"), dict) else {}
    news_snapshot = audit.get("news_snapshot") if isinstance(audit.get("news_snapshot"), dict) else {}
    target_snapshot = metadata.get("target_structure") if isinstance(metadata.get("target_structure"), dict) else {}

    def handler(name, fallback):
        return handlers.get(name) or (health or {}).get(name) or fallback

    def model_row(name, handler_name, status, values, *, live=False):
        return {
            "name": name,
            "handler": handler_name,
            "status": status,
            "live": bool(live),
            "sample_key": sample.get("sample_key"),
            "observed_at": model_time.get("at"),
            "values": values,
        }

    model_handler_map = [
        model_row(
            "Forecast",
            handler("forecast_model_handler", sample.get("chronos_model") or "Chronos"),
            decision if base else "NO SAMPLE",
            {
                "Decision": decision,
                "Reason": reason,
                "Low / Median / High": " / ".join(metric(base.get(k)) for k in ("forecast_low", "forecast_median", "forecast_high")),
                "BUY edge": base.get("buy_edge"),
                "SELL edge": base.get("sell_edge"),
                "Signal strength": base.get("signal_strength"),
                "Minimum strength": base.get("minimum_strength"),
            },
            live=True,
        ),
        model_row(
            "Forecast Shadow",
            handler("forecast_shadow_model_handler", "OFF"),
            "READY" if timesfm_snapshot.get("timesfm3_shadow_ready") == 1 else "OFF" if (health or {}).get("timesfm3_shadow_enabled") == 0 else "NO SNAPSHOT",
            {
                "Direction": timesfm_snapshot.get("timesfm3_shadow_direction"),
                "Low / Median / High": " / ".join(metric(timesfm_snapshot.get(k)) for k in ("timesfm3_shadow_low", "timesfm3_shadow_median", "timesfm3_shadow_high")),
                "Move ATR": timesfm_snapshot.get("timesfm3_shadow_move_atr"),
                "Agrees Chronos": timesfm_snapshot.get("timesfm3_shadow_agrees_chronos"),
                "Effect": "SHADOW / display only",
            },
        ),
        model_row(
            "Regime",
            handler("regime_model_handler", "Ramon/BinaryLogisticModel"),
            "SHADOW" if final.get("role_shadow") == 1 or metadata.get("ensemble_mode") == "shadow" else "ACTIVE" if final else "NO SAMPLE",
            {
                "Probability": final.get("regime_probability"),
                "Label": final.get("shadow_regime_label"),
                "Ensemble active": final.get("ensemble_active"),
                "Market state": assessment.get("state"),
                "Route": assessment.get("route"),
            },
        ),
        model_row(
            "Anomaly Detection",
            handler("anomaly_model_handler", "OFF") + " [AI RISK]",
            "VETO" if moment_snapshot.get("moment_live_veto") == 1 else "READY" if moment_snapshot.get("moment_shadow_ready") == 1 else "NO SNAPSHOT",
            {
                "Label": moment_snapshot.get("moment_anomaly_label"),
                "Score": moment_snapshot.get("moment_anomaly_score"),
                "Ratio": moment_snapshot.get("moment_anomaly_ratio"),
                "Threshold": moment_snapshot.get("moment_live_threshold") or (health or {}).get("moment_live_threshold"),
                "Fresh": moment_snapshot.get("moment_live_fresh"),
                "LIVE veto": moment_snapshot.get("moment_live_veto"),
                "AI anomaly penalty": final.get("ai_engine_v2_anomaly_penalty"),
                "AI hard threshold": moment_snapshot.get("moment_hard_veto_threshold") or (health or {}).get("moment_hard_veto_threshold"),
            },
            live=True,
        ),
        model_row(
            "Entry",
            handler("entry_model_handler", "Ramon/BinaryLogisticModel"),
            "SHADOW" if metadata.get("ensemble_mode") == "shadow" else "ACTIVE" if final else "NO SAMPLE",
            {
                "Entry probability": final.get("entry_probability"),
                "Signal strength": base.get("signal_strength"),
                "Minimum strength": base.get("minimum_strength"),
                "BUY edge": base.get("buy_edge"),
                "SELL edge": base.get("sell_edge"),
                "Intrabar confirmed": base.get("intrabar_confirmed"),
                "Intrabar direction": base.get("intrabar_direction"),
                "AI trend confirmed": base.get("ai_trend_confirmed"),
                "AI trend direction": base.get("ai_trend_direction"),
            },
        ),
        model_row(
            "News Calendar",
            handler("news_source_handler", "ForexFactoryNewsProvider"),
            "READY" if (health or {}).get("news_source_ready") else "NO SNAPSHOT",
            {
                "Source": news_snapshot.get("news_source"),
                "Ready": news_snapshot.get("news_source_ready"),
                "Country": news_snapshot.get("news_event_country"),
                "Impact": news_snapshot.get("news_event_impact"),
                "Event": news_snapshot.get("news_event_title"),
                "Delta minutes": news_snapshot.get("news_event_delta_minutes"),
            },
        ),
        model_row(
            "News Model",
            handler("news_model_handler", "Ramon/BinaryLogisticModel"),
            "SHADOW" if metadata.get("ensemble_mode") == "shadow" else "ACTIVE" if final else "NO SAMPLE",
            {
                "News probability": final.get("news_probability"),
                "Model ready": final.get("news_model_ready"),
                "Event": news_snapshot.get("news_event_title"),
                "Impact": news_snapshot.get("news_event_impact"),
            },
        ),
        model_row(
            "News Sentiment",
            handler("news_sentiment_model_handler", "OFF") + " [AI FEATURE]",
            "VETO" if finbert_snapshot.get("finbert_live_veto") == 1 else "READY" if finbert_snapshot.get("finbert_shadow_ready") == 1 else "NO SNAPSHOT",
            {
                "Sentiment": finbert_snapshot.get("finbert_sentiment_label"),
                "Directional score": finbert_snapshot.get("finbert_directional_score"),
                "Positive": finbert_snapshot.get("finbert_positive"),
                "Negative": finbert_snapshot.get("finbert_negative"),
                "Neutral": finbert_snapshot.get("finbert_neutral"),
                "Threshold": finbert_snapshot.get("finbert_live_threshold") or (health or {}).get("finbert_live_threshold"),
                "LIVE veto": finbert_snapshot.get("finbert_live_veto"),
                "Mode": finbert_snapshot.get("finbert_risk_mode") or "AI feature",
            },
            live=True,
        ),
        model_row(
            "Meta",
            handler("meta_model_handler", "Ramon/BinaryLogisticModel"),
            "SHADOW" if metadata.get("ensemble_mode") == "shadow" else "ACTIVE" if final else "NO SAMPLE",
            {
                "Meta probability": final.get("meta_probability"),
                "Base decision": base.get("decision"),
                "Base reason": base.get("reason"),
                "Final decision": decision,
                "Final reason": reason,
                "Ensemble active": final.get("ensemble_active"),
                "AI Engine active": final.get("ai_engine_v2_active"),
                "AI Engine selected": final.get("ai_engine_v2_selected"),
                "AI Engine score": final.get("ai_engine_v2_score"),
                "AI Engine threshold": final.get("ai_engine_v2_minimum_score"),
                "AI score source": final.get("ai_engine_v2_score_source"),
            },
        ),
        model_row(
            "Risk / SL",
            handler("risk_model_handler", "Ramon/BinaryLogisticModel"),
            "SHADOW" if metadata.get("ensemble_mode") == "shadow" else "ACTIVE" if final else "NO SAMPLE",
            {
                "Risk probability": final.get("risk_probability"),
                "Risk multiplier": final.get("risk_multiplier"),
                "Risk target": final.get("risk_target"),
                "Stop distance": base.get("stop_distance"),
                "Target distance": base.get("target_distance"),
            },
        ),
        model_row(
            "Market State",
            handler("market_state_handler", "market-state-v1"),
            assessment.get("state") or "NO SNAPSHOT",
            {
                "State": assessment.get("state"),
                "Route": assessment.get("route"),
                "Policy": assessment.get("version"),
                "Conditions": assessment.get("conditions"),
                "Evidence": assessment.get("evidence"),
                "ATR": base.get("atr"),
            },
            live=True,
        ),
        model_row(
            "TP Structure",
            handler("target_model_handler", "Ramon/TargetStructure"),
            "READY" if target_snapshot.get("ready") else "FALLBACK" if target_snapshot else "NO SNAPSHOT",
            {
                "Method": target_snapshot.get("method"),
                "Direction": target_snapshot.get("direction"),
                "Impulse ATR": target_snapshot.get("impulse_atr"),
                "TP1": target_snapshot.get("tp1"),
                "TP2": target_snapshot.get("tp2"),
                "TP3": target_snapshot.get("tp3"),
                "Legacy target": target_snapshot.get("legacy_target"),
            },
        ),
    ]

    # Green means the row's explicit condition, never merely a successful fetch.
    def gate_state(text, stored, ready, *, requires_fresh=False):
        if joined and text:
            active = match(text, r"^(YES|NO)")
            veto = match(text, r"Veto: (YES|NO)")
            fresh = match(text, r"Fresh: (YES|NO)")
            if ea_time["state"] != "fresh":
                return "stale"
            if active == "NO":
                return "idle"
            if active != "YES" or ready != 1 or veto is None or (requires_fresh and fresh != "YES"):
                return "unknown"
            return "blocked" if veto == "YES" else "pass"
        prefix = "moment" if requires_fresh else "finbert"
        if stored.get(prefix + "_live_active") == 0:
            return "idle"
        if (stored.get(prefix + "_live_active") != 1 or ready != 1
                or (requires_fresh and stored.get(prefix + "_live_fresh") != 1)
                or stored.get(prefix + "_live_veto") not in (0, 1)):
            return "unknown"
        return "blocked" if stored[prefix + "_live_veto"] == 1 else "pass"

    moment_state = gate_state(diag.get("MOMENT LIVE"), moment_snapshot,
                              moment_snapshot.get("moment_shadow_ready"), requires_fresh=True)
    sentiment_state = gate_state(diag.get("FinBERT LIVE"), finbert_snapshot,
                                 finbert_snapshot.get("finbert_shadow_ready"))
    role_state = "shadow" if shadow else "observed" if final else "unknown"
    conditions = {
        "Forecast": ("pass" if base.get("decision") in {"BUY", "SELL"} else "blocked" if base.get("decision") == "WAIT" else "unknown", "تصمیم پایهٔ پیش‌بینی"),
        "Forecast Shadow": ("shadow" if timesfm_snapshot.get("timesfm3_shadow_ready") == 1 else "idle", "پیش‌بینی ناظر؛ مجوز ورود نیست"),
        "Regime": (role_state, "نقش مدل رژیم"),
        "Anomaly Detection": (moment_state, "گیت زندهٔ ناهنجاری؛ فعال و تازه، بدون وتو"),
        "Entry": (role_state, "نقش مدل ورود؛ SHADOW به معنی تأیید شرط نیست"),
        "News Calendar": ("pass" if news_snapshot.get("news_source_ready") == 1 else "blocked" if news_snapshot.get("news_source_ready") == 0 else "unknown", "آمادگی منبع تقویم خبر؛ مجوز ورود نیست"),
        "News Model": (role_state, "نقش مدل خبر"),
        "News Sentiment": (sentiment_state, "گیت زندهٔ احساس خبر؛ فعال و آماده، بدون وتو"),
        "Meta": (role_state, "نقش مدل ترکیبی"),
        "Risk / SL": (role_state, "نقش مدل ریسک؛ ناظر با تأیید ریسک اجرایی فرق دارد"),
        "Market State": ("pass" if assessment.get("route") in {"CONFIRMED_MODEL", "RANGE_REVERSAL"} else "blocked" if assessment.get("route") == "WAIT" else "observed" if assessment else "unknown", "سیاست بازار مسیر ورود دارد؛ تأیید نهایی سفارش نیست"),
        "TP Structure": ("pass" if target_snapshot.get("ready") else "unknown", "ساختار هدف آماده است"),
    }
    for row in model_handler_map:
        state, label = conditions[row["name"]]
        row["condition_state"] = state if model_time["state"] == "fresh" else "stale" if model_time["state"] in {"stale", "clock_error"} else "unknown"
        row["condition_label"] = label
        if row["name"] in {"Anomaly Detection", "News Sentiment"}:
            row["status"] = {"pass": "OK", "blocked": "VETO", "idle": "OFF", "stale": "STALE", "unknown": "NO GATE SNAPSHOT"}.get(row["condition_state"], row["status"])

    auto_trades = [row for row in trades if (row.get("entry_source") or "AUTO_RAMON") != "DASHBOARD_OPPORTUNITY"]
    dashboard_trades = [row for row in trades if row.get("entry_source") == "DASHBOARD_OPPORTUNITY"]
    readiness = dollar_readiness(auto_trades, diag.get("EA version"))
    dashboard_closed = [row for row in dashboard_trades if number(row.get("closed")) is not None]
    dashboard_wins = [row for row in dashboard_closed if (number(row.get("net_units")) or 0.0) > 0]
    dashboard_net = sum(number(row.get("net_units")) or 0.0 for row in dashboard_closed)
    dashboard_performance = {
        "closed": len(dashboard_closed),
        "wins": len(dashboard_wins),
        "win_rate": (len(dashboard_wins) / len(dashboard_closed)) if dashboard_closed else None,
        "net_units": dashboard_net,
    }
    roadmap = income_roadmap(readiness, diag)
    recent_market = recent_market_context(
        db, symbol, sample_key=sample.get("sample_key"),
        signal_bar_time=base.get("signal_bar_time"), quote_time=sample.get("quote_time")
    )
    warnings = [x for x in (diag_error, db_error) if x]
    warnings.extend(recent_market.get("warnings", []))
    if model_time["state"] != "fresh":
        warnings.append("تصمیم مدل تازه نیست؛ آخرین تصمیم ثبت‌شده نمایش داده می‌شود")
    if ea_time["state"] != "fresh":
        warnings.append("وضعیت اکسپرت تازه نیست؛ از آن نتیجهٔ زنده نمی‌گیریم")
    if diag and sample and not joined:
        warnings.append("شناسهٔ اکسپرت و تصمیم مدل متفاوت است؛ عبور مسیر اجرا قابل تأیید نیست")
    if model_time["state"] == "clock_error" or ea_time["state"] == "clock_error":
        warnings.append("زمان منبع در آینده است؛ ساعت نیاز به بررسی دارد")
    active_manual = final.get("manual_overrides_active") if isinstance(final.get("manual_overrides_active"), list) else []
    for item in nodes:
        if item["id"] in active_manual:
            effective_manual = item["id"] != "range" or range_execution
            if effective_manual:
                item["state"] = "pass" if model_time["state"] == "fresh" else item["state"]
                item["observed_state"] = "pass"
                item["manual_override"] = True
                item["detail"] = "MANUAL PASS — " + item["detail"]

    return {"schema_version": 2, "generated_at": utc_time(now), "symbol": symbol, "read_only": False,
            "model_freshness": model_time, "ea_freshness": ea_time, "joined": joined,
            "sample_key": sample.get("sample_key"), "signal_bar_time": base.get("signal_bar_time"),
            "manual_overrides_active": active_manual,
            "decision": decision, "reason": reason,
            "reason_fa": REASONS.get(reason, reason), "ea_status": ea_status,
            "ea_version": diag.get("EA version"), "nodes": nodes,
            "edges": [{"from": a, "to": b, "label": label} for a, b, label in edges],
            "timeline": timeline, "trades": outcomes, "model_handler_map": model_handler_map,
            "recent_market": recent_market,
            "dollar_readiness": readiness, "dashboard_opportunity_performance": dashboard_performance,
            "income_roadmap": roadmap, "warnings": warnings}


def analysis_bundle(snapshot: dict, selected_stage: str = "") -> str:
    node_numbers = {
        "market": "01", "service": "02", "forecast": "03", "timing": "04",
        "extension": "05", "edge": "06", "strength": "07", "market_direction": "08",
        "entry_timing": "09", "base": "10", "decision": "11", "news": "12",
        "account": "13", "limits": "14", "risk": "15", "order": "16",
        "position": "17", "range": "R1", "shadow": "S1",
    }
    nodes = snapshot.get("nodes") if isinstance(snapshot.get("nodes"), list) else []
    selected = next((row for row in nodes if row.get("id") == selected_stage), None)

    lines = [
        "=== RAMON ANALYSIS BUNDLE ===",
        f"Generated: {snapshot.get('generated_at', '—')}",
        f"Symbol: {snapshot.get('symbol', '—')}",
        f"EA version: {snapshot.get('ea_version', '—')}",
        f"Decision ID: {snapshot.get('sample_key', '—')}",
        f"Signal bar time: {snapshot.get('signal_bar_time', '—')}",
        f"Decision: {snapshot.get('decision', '—')}",
        f"Reason: {snapshot.get('reason', '—')} | {snapshot.get('reason_fa', '—')}",
        f"EA status: {snapshot.get('ea_status', '—')}",
        f"Joined model/EA: {snapshot.get('joined', False)}",
        "Active manual passes: " + (
            ", ".join(str(x) for x in snapshot.get("manual_overrides_active", [])) or "NONE"
        ),
        "Selected step: " + (
            f"{node_numbers.get(selected.get('id'), selected.get('id'))} {selected.get('title')}"
            if selected else "NONE"
        ),
        "",
        "=== SELECTED STEP ===",
        json.dumps(selected, ensure_ascii=False, indent=2) if selected else "NONE",
        "",
        "=== ALL DECISION / EXECUTION STEPS ===",
    ]
    for node in nodes:
        node_id = str(node.get("id", ""))
        lines.append(
            f"[{node_numbers.get(node_id, node_id)}] {node.get('title', '—')} | "
            f"state={node.get('state', '—')} observed={node.get('observed_state', '—')} "
            f"manual={bool(node.get('manual_override'))} engine={node.get('engine') or 'Logic'}"
        )
        lines.append(f"detail: {node.get('detail', '—')}")
        lines.append(
            "values: " + json.dumps(node.get("values") or {}, ensure_ascii=False, sort_keys=True)
        )

    lines.extend(["", "=== RECENT DECISIONS ==="])
    for row in snapshot.get("timeline") or []:
        lines.append(
            f"{row.get('at', '—')} | {row.get('decision', '—')} | "
            f"{row.get('reason', '—')} | {row.get('strategy', '—')} | "
            f"{row.get('sample_key', '—')}"
        )

    recent_market = snapshot.get("recent_market") if isinstance(snapshot.get("recent_market"), dict) else {}
    for key, label in (("m15", "M15"), ("m1", "M1")):
        lines.extend(["", f"=== {label} CANDLES (oldest -> newest; historical context, not exact decision input) ==="])
        rows = recent_market.get(key) if isinstance(recent_market.get(key), list) else []
        if not rows:
            lines.append("NO DATA")
        for bar in rows:
            raw_stamp = utc_time(bar.get("time"))
            stamp = (raw_stamp.removesuffix("+00:00") + " (broker time; UTC offset unknown)") if raw_stamp else "UNKNOWN TIME"
            lines.append(
                f"{stamp} | O {bar.get('open')} H {bar.get('high')} "
                f"L {bar.get('low')} C {bar.get('close')} | "
                f"body {bar.get('body')} range {bar.get('range')} "
                f"spreadPts {bar.get('spread_points')}"
            )

    lines.extend(["", "=== MODEL / HANDLER MAP ==="])
    for row in snapshot.get("model_handler_map") or []:
        lines.append(
            f"{row.get('name', '—')} | {row.get('handler', '—')} | "
            f"status={row.get('status', '—')} | "
            f"condition={row.get('condition_state', '—')} | "
            f"values={json.dumps(row.get('values') or {}, ensure_ascii=False, sort_keys=True)}"
        )

    lines.extend(["", "=== WARNINGS ==="])
    warnings = snapshot.get("warnings") or []
    lines.extend(str(w) for w in warnings) if warnings else lines.append("NONE")
    return "\n".join(lines) + "\n"


def control_state(diagnostic):
    diag, error = read_diagnostic(diagnostic)
    path = Path(diagnostic).with_name("Ramon_Control.txt") if diagnostic else None
    requested = None
    if path and path.exists():
        try:
            requested = number(path.read_text().strip())
        except OSError:
            pass
    observed = number(match(diag.get("MinLotOverride"), r"MaxExecutableRiskUSD: ([\d.]+)"))
    return {"requested": requested, "observed": observed,
            "supported": diag.get("ControlBridge") == "PRIMARY file-v1",
            "fresh": freshness(diag.get("captured_epoch"), time.time(), 30)["state"] == "fresh",
            "writable": bool(path and path.parent.is_dir() and os.access(path.parent, os.W_OK)),
            "error": error, "default": 0.35, "minimum": 0.01, "maximum": 0.50}


def save_control(diagnostic, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.01 <= value <= 0.50:
        raise ValueError("مقدار باید عددی بین ۰٫۰۱ و ۰٫۵۰ دلار باشد")
    if diagnostic is None:
        raise ValueError("مسیر فایل اکسپرت در دسترس نیست")
    path = Path(diagnostic).with_name("Ramon_Control.txt")
    # Atomic replacement prevents the EA from reading a partially written cap.
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as out:
        temp = Path(out.name)
        out.write(f"{value:.8f}\n")
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)



def read_open_dashboard_positions(diagnostic):
    if diagnostic is None:
        return {}
    path = Path(diagnostic).with_name("Ramon_OpenDashboardPositions.txt")
    result = {}
    try:
        if not path.exists():
            return result
        for raw in path.read_text(encoding="ascii", errors="ignore").splitlines():
            parts = raw.strip().split("|")
            if len(parts) != 4:
                continue
            sample_key, direction, ticket, opened = parts
            if not re.fullmatch(r"[a-f0-9]{16}", sample_key) or direction not in {"BUY", "SELL"}:
                continue
            result[sample_key] = {
                "direction": direction,
                "ticket": ticket,
                "opened": int(opened) if opened.isdigit() else None,
            }
    except OSError:
        return {}
    return result


def opportunities_with_execution_state(db, diagnostic, symbol):
    data = read_opportunities(db, symbol)
    opened = read_open_dashboard_positions(diagnostic)
    queue_path = Path(diagnostic).with_name("Ramon_ManualEntries.txt") if diagnostic else None
    queued = set()
    try:
        if queue_path and queue_path.exists():
            for raw in queue_path.read_text(encoding="ascii", errors="ignore").splitlines():
                parts = raw.strip().split("|")
                if len(parts) >= 4 and re.fullmatch(r"[a-f0-9]{16}", parts[3]):
                    queued.add(parts[3])
    except OSError:
        pass
    for row in data.get("opportunities", []):
        sample_key = str(row.get("sample_key") or "")
        open_info = opened.get(sample_key)
        row["position_open"] = bool(open_info)
        row["position_ticket"] = open_info.get("ticket") if open_info else None
        row["entry_queued"] = sample_key in queued
        if row["position_open"]:
            row["actionable"] = False
            row["execution_state"] = "OPEN"
        elif row["entry_queued"]:
            row["actionable"] = False
            row["execution_state"] = "QUEUED"
        elif row.get("executed"):
            row["execution_state"] = "CLOSED_RECORDED"
        else:
            row["execution_state"] = "AVAILABLE" if row.get("actionable") else "INACTIVE"
    data["open_dashboard_positions"] = len(opened)
    data["queued_dashboard_entries"] = len(queued)
    return data


def queue_manual_entry(db, diagnostic, symbol, payload):
    if diagnostic is None:
        raise ValueError("مسیر فایل اکسپرت در دسترس نیست")
    direction = str(payload.get("direction", "")).upper()
    signal_bar_time = int(payload.get("signal_bar_time", 0))
    if direction not in {"BUY", "SELL"} or signal_bar_time <= 0:
        raise ValueError("فرصت انتخاب‌شده نامعتبر است")
    data = opportunities_with_execution_state(db, diagnostic, symbol)
    row = next(
        (
            item for item in data.get("opportunities", [])
            if int(item.get("signal_bar_time") or 0) == signal_bar_time
            and str(item.get("direction") or "").upper() == direction
        ),
        None,
    )
    if not row:
        raise ValueError("فرصت در داده‌های فعلی پیدا نشد")
    if row.get("position_open"):
        raise ValueError("برای این فرصت همین حالا پوزیشن باز است")
    if row.get("entry_queued"):
        raise ValueError("فرمان این فرصت قبلاً در صف اجراست")
    if not row.get("actionable"):
        raise ValueError("این فرصت دیگر تازه و قابل اجرا نیست")
    common_dir = Path(diagnostic).parent
    queue_path = common_dir / "Ramon_ManualEntries.txt"
    sample_key = str(row.get("sample_key") or "")
    risk_distance = float(row.get("risk_distance") or 0.0)
    target_distance = abs(float(row.get("target") or 0.0) - float(row.get("entry") or 0.0))
    edge = float(row.get("edge") or 0.0)
    probability = row.get("success_probability")
    if not re.fullmatch(r"[a-f0-9]{16}", sample_key) or risk_distance <= 0 or target_distance <= 0 or edge <= 0:
        raise ValueError("اطلاعات فرصت برای اجرای دستی کامل نیست")
    probability_value = -1.0 if probability is None else float(probability)
    command = (
        f"{int(time.time())}|{signal_bar_time}|{direction}|{sample_key}|"
        f"{risk_distance:.10f}|{target_distance:.10f}|{edge:.10f}|{probability_value:.10f}\n"
    )
    with open(queue_path, "a", encoding="ascii", newline="") as out:
        out.write(command)
        out.flush()
        os.fsync(out.fileno())
    return {
        "queued": True,
        "direction": direction,
        "signal_bar_time": signal_bar_time,
        "success_probability": row.get("success_probability"),
        "expires_in_seconds": data.get("actionable_seconds", 90),
    }


def handler_for(db, diagnostic, symbol, health_url):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            route = self.path.split("?", 1)[0]
            if route == "/api/trade-trace":
                key = parse_qs(urlsplit(self.path).query).get("trade_key", [""])[0]
                if not key or len(key) > 256:
                    self.send_error(400)
                    return
                try:
                    data = trade_trace(db, symbol, key)
                except sqlite3.Error:
                    self.send_error(503)
                    return
                if data is None:
                    self.send_error(404)
                    return
                self.reply(json.dumps(data, ensure_ascii=False, allow_nan=False).encode(), "application/json; charset=utf-8")
            elif route == "/api/control":
                self.reply(json.dumps(control_state(diagnostic), ensure_ascii=False).encode(), "application/json; charset=utf-8")
            elif route == "/api/analysis":
                params = parse_qs(urlsplit(self.path).query)
                selected_stage = str(params.get("stage", [""])[0])[:64]
                health = None
                try:
                    with urlopen(health_url, timeout=.7) as response:
                        health = object_json(response.read(100_000))
                except (OSError, ValueError):
                    pass
                data = build_snapshot(db, diagnostic, symbol=symbol, health=health)
                body = analysis_bundle(data, selected_stage).encode("utf-8")
                self.reply(body, "text/plain; charset=utf-8")
            elif route == "/api/opportunities":
                self.reply(json.dumps(opportunities_with_execution_state(db, diagnostic, symbol), ensure_ascii=False, allow_nan=False).encode(), "application/json; charset=utf-8")
            elif route == "/api/snapshot":
                health = None
                try:
                    with urlopen(health_url, timeout=.7) as response:
                        health = object_json(response.read(100_000))
                except (OSError, ValueError):
                    pass
                data = build_snapshot(db, diagnostic, symbol=symbol, health=health)
                self.reply(json.dumps(data, ensure_ascii=False, allow_nan=False).encode(), "application/json; charset=utf-8")
            elif route in {"/", "/monitor", "/app.js", "/style.css", "/control", "/control.js"}:
                filename = {"/": "index.html", "/monitor": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/control": "control.html", "/control.js": "control.js"}[route]
                content_type = {"index.html": "text/html", "app.js": "text/javascript", "style.css": "text/css", "control.html": "text/html", "control.js": "text/javascript"}[filename]
                self.reply((ASSETS / filename).read_bytes(), content_type + "; charset=utf-8")
            else:
                self.send_error(404)

        def do_POST(self):
            if self.path not in {"/api/control", "/api/override", "/api/override/reset", "/api/manual-entry"}:
                self.send_error(404)
                return
            origin = self.headers.get("Origin")
            if origin and (urlsplit(origin).netloc != self.headers.get("Host") or urlsplit(origin).scheme not in {"http", "https"}):
                self.send_error(403)
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self.send_error(415)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192:
                    raise ValueError("درخواست نامعتبر است")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("درخواست نامعتبر است")
                if self.path == "/api/control":
                    save_control(diagnostic, payload.get("max_executable_risk_usd"))
                    self.reply(json.dumps(control_state(diagnostic), ensure_ascii=False).encode(), "application/json; charset=utf-8")
                    return

                if self.path == "/api/manual-entry":
                    result = queue_manual_entry(db, diagnostic, symbol, payload)
                    self.reply(json.dumps(result, ensure_ascii=False, allow_nan=False).encode(), "application/json; charset=utf-8")
                    return

                if self.path == "/api/override/reset":
                    model_url = health_url.rsplit("/health", 1)[0] + "/manual-overrides/reset"
                    request = Request(
                        model_url,
                        data=b"{}",
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urlopen(request, timeout=2.0) as response:
                        result = object_json(response.read(100_000))
                    self.reply(json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8")
                    return

                stage = str(payload.get("stage", ""))
                pass_allowed = {"timing", "extension", "edge", "strength", "market_direction", "entry_timing", "base", "decision", "range"}
                review_allowed = {"news", "account", "limits", "risk", "order", "position"}
                if stage not in pass_allowed | review_allowed:
                    raise ValueError("این مرحله قابل ثبت دستی نیست")
                health = None
                try:
                    with urlopen(health_url, timeout=.7) as response:
                        health = object_json(response.read(100_000))
                except (OSError, ValueError):
                    pass
                snap = build_snapshot(db, diagnostic, symbol=symbol, health=health)
                node_row = next((n for n in snap.get("nodes", []) if n.get("id") == stage), None)
                if not node_row:
                    raise ValueError("مرحله جاری پیدا نشد")
                # Analytical stages may always be annotated. A blocked stage is
                # FORCE_PASS; pass/idle/unknown stages are REVIEW_ONLY so the UI can
                # show an action consistently from steps 04 through 17.
                if not snap.get("sample_key") or not snap.get("signal_bar_time"):
                    raise ValueError("تصمیم جاری شناسه معتبر ندارد")
                forward = {
                    "stage": stage,
                    "sample_key": snap["sample_key"],
                    "signal_bar_time": snap["signal_bar_time"],
                    "original_state": node_row.get("state"),
                    "original_reason": snap.get("reason"),
                    "node_values": node_row.get("values", {}),
                    "action": "FORCE_PASS" if stage in pass_allowed else "REVIEW_ONLY",
                }
                model_url = health_url.rsplit("/health", 1)[0] + "/manual-override"
                request = Request(
                    model_url,
                    data=json.dumps(forward, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2.0) as response:
                    result = object_json(response.read(100_000))
                self.reply(json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8")
            except (ValueError, OSError) as exc:
                body = json.dumps(
                    {"ok": False, "error": str(exc)},
                    ensure_ascii=False,
                ).encode("utf-8")
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

        def reply(self, body, content_type):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'self'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/ramon_history.sqlite3")
    parser.add_argument("--diagnostic", type=Path, default=default_diagnostic())
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--port", type=int, default=8013)
    parser.add_argument("--host", default=os.getenv("RAMON_MONITOR_HOST", "127.0.0.1"))
    parser.add_argument("--health-url", default=os.getenv("RAMON_MONITOR_HEALTH_URL", "http://127.0.0.1:8012/health"))
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), handler_for(args.db, args.diagnostic, args.symbol, args.health_url))
    print(f"Ramon read-only flow monitor: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
