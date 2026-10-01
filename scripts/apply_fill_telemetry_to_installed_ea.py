#!/usr/bin/env python3
"""Safely add actual fill-price telemetry to an installed Ramon.mq5.

This patch is telemetry-only. It preserves the installed EA version and does not
change trading gates, thresholds, sizing, entries, exits, SL or TP behavior.
A timestamped backup is written before modification.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import re
import shutil
import sys


def patch_source(source: str) -> str:
    if '\\\"actual_fill_price\\\"' in source and "entry_fill_value" in source:
        return source

    out = source
    anchors = [
        (
            "   double in_volume=0.0,out_volume=0.0,net=0.0,risk=0.0;\n"
            "   double profit=0.0,commission=0.0,swap=0.0,fee=0.0;",
            "   double in_volume=0.0,out_volume=0.0,net=0.0,risk=0.0;\n"
            "   double entry_fill_value=0.0;\n"
            "   double profit=0.0,commission=0.0,swap=0.0,fee=0.0;",
        ),
        (
            "         double fill=HistoryDealGetDouble(deal,DEAL_PRICE);\n"
            "         double loss=0.0;",
            "         double fill=HistoryDealGetDouble(deal,DEAL_PRICE);\n"
            "         if(fill<=0.0) return false;\n"
            "         entry_fill_value+=fill*volume;\n"
            "         double loss=0.0;",
        ),
        (
            '   if(sample=="" || risk<=0.0 || closed<opened || in_volume<=0.0\n'
            "      || MathAbs(in_volume-out_volume)>0.000001) return false;\n"
            "   net=profit+commission+swap+fee;",
            '   if(sample=="" || risk<=0.0 || closed<opened || in_volume<=0.0\n'
            "      || entry_fill_value<=0.0\n"
            "      || MathAbs(in_volume-out_volume)>0.000001) return false;\n"
            "   double actual_fill_price=entry_fill_value/in_volume;\n"
            "   net=profit+commission+swap+fee;",
        ),
        (
            '      +",\\\"net_units\\\":"+DoubleToString(net,8)+",\\\"initial_risk_units\\\":"+DoubleToString(risk,8)\n'
            '      +",\\\"profit_units\\\":"+DoubleToString(profit,8)',
            '      +",\\\"net_units\\\":"+DoubleToString(net,8)+",\\\"initial_risk_units\\\":"+DoubleToString(risk,8)\n'
            '      +",\\\"actual_fill_price\\\":"+DoubleToString(actual_fill_price,_Digits)\n'
            '      +",\\\"profit_units\\\":"+DoubleToString(profit,8)',
        ),
    ]

    for old, new in anchors:
        if new in out:
            continue
        count = out.count(old)
        if count != 1:
            raise ValueError(f"expected one telemetry anchor, found {count}")
        out = out.replace(old, new, 1)

    required = (
        "double entry_fill_value=0.0;",
        "entry_fill_value+=fill*volume;",
        "double actual_fill_price=entry_fill_value/in_volume;",
        '\\\"actual_fill_price\\\"',
    )
    for item in required:
        if item not in out:
            raise ValueError(f"required fill telemetry missing: {item}")

    for call in ("Trade.Buy(", "Trade.Sell(", "Trade.PositionClose(", "Trade.PositionModify("):
        if out.count(call) != source.count(call):
            raise ValueError(f"unsafe change: {call} count changed")

    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path, help="Installed Ramon.mq5")
    parser.add_argument("--check", action="store_true", help="Validate without writing")
    args = parser.parse_args()

    target = args.target.expanduser().resolve()
    if not target.is_file():
        raise SystemExit(f"target not found: {target}")

    source = target.read_text(encoding="utf-8")
    patched = patch_source(source)
    version = re.search(r'#property version\s+"([^"]+)"', source)

    print(f"Target: {target}")
    print(f"EA version preserved: {version.group(1) if version else 'unknown'}")
    print("Actual fill telemetry: READY")
    print("Trading call counts preserved: YES")

    if args.check:
        print("CHECK ONLY: no file changed")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = target.with_name(f"{target.stem}.before-fill-telemetry-{stamp}{target.suffix}")
    shutil.copy2(target, backup)
    target.write_text(patched, encoding="utf-8")
    print(f"Backup: {backup}")
    print("Updated safely.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
