#!/usr/bin/env python3
"""Safely add * markers to Shadow/Learning/Collecting UI on an installed Ramon EA.

The tool edits only display/diagnostic strings. It does not touch trading logic,
thresholds, sizing, entries, exits, SL/TP calculations, or the TP-lock code.
A timestamped backup is created before writing.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import re
import shutil
import sys


REPLACEMENTS = [
    (
        '+"TradeLearning: "+TradeLearningStatus+"\\n"',
        '+"TradeLearning*: "+TradeLearningStatus+"\\n"',
    ),
    (
        '+"RoleModels: "+(LastRoleShadow ? "SHADOW (display only)" : (LastEnsembleReady ? "READY" : "LEARNING"))',
        '+"RoleModels: "+(LastRoleShadow ? "*SHADOW* (display only)" : (LastEnsembleReady ? "READY" : "*LEARNING*"))',
    ),
    (
        '+"  ShadowRegime: "+LastShadowRegimeLabel',
        '+"  *ShadowRegime: "+LastShadowRegimeLabel',
    ),
    (
        '+"  ShadowRiskP: "+DoubleToString(LastShadowRiskProbability,3)',
        '+"  *ShadowRiskP: "+DoubleToString(LastShadowRiskProbability,3)',
    ),
    (
        '+"TargetLearning: "+(LastTargetLearningActive ? "COLLECTING" : "OFF")',
        '+"TargetLearning*: "+(LastTargetLearningActive ? "*COLLECTING*" : "OFF")',
    ),
    (
        '+"TP1/TP2/TP3 learn: "+DoubleToString(LastTargetTP1,_Digits)',
        '+"*TP1/TP2/TP3 learn: "+DoubleToString(LastTargetTP1,_Digits)',
    ),
    (
        '+"=== V0.50 IMPROVEMENT SHADOWS (OBSERVE ONLY) ===\\n"',
        '+"=== * V0.50 IMPROVEMENT SHADOWS (OBSERVE ONLY) ===\\n"',
    ),
    (
        '+"ShadowPack: "+BoolText(EnableImprovementShadowPack)',
        '+"*ShadowPack: "+BoolText(EnableImprovementShadowPack)',
    ),
    (
        '+"DirectionCaution: BUY="+BoolText(ShadowBuyCaution)',
        '+"*DirectionCaution: BUY="+BoolText(ShadowBuyCaution)',
    ),
    (
        '+"ShadowRiskMultiplier: "+DoubleToString(ShadowRiskMultiplier,2)+"x"',
        '+"*ShadowRiskMultiplier: "+DoubleToString(ShadowRiskMultiplier,2)+"x"',
    ),
    (
        '+"ShadowSmallTargetUnits: "+DoubleToString(ShadowSmallTargetUnits,2)',
        '+"*ShadowSmallTargetUnits: "+DoubleToString(ShadowSmallTargetUnits,2)',
    ),
    (
        '+"ShadowSmallTPPlan: "+ShadowSmallTPPlan',
        '+"*ShadowSmallTPPlan: "+ShadowSmallTPPlan',
    ),
    (
        '+"DeadTradeShadow: "+BoolText(ShadowDeadTrade)',
        '+"*DeadTradeShadow: "+BoolText(ShadowDeadTrade)',
    ),
    (
        '+"ShadowExecutionEffect: NONE\\n\\n"',
        '+"*ShadowExecutionEffect: NONE\\n"\n'
        '      +"* = SHADOW / LEARNING / COLLECTING; display/training only unless explicitly activated.\\n\\n"',
    ),
    (
        'UiLabel("ROLE_MODELS",(LastRoleShadow ? "SHADOW | "+LastShadowRegimeLabel : "ROLE MODELS "+(LastEnsembleReady ? "READY" : "LEARNING"))',
        'UiLabel("ROLE_MODELS",(LastRoleShadow ? "*SHADOW* | "+LastShadowRegimeLabel : "ROLE MODELS "+(LastEnsembleReady ? "READY" : "*LEARNING*"))',
    ),
    (
        '+" | model "+(LastNewsModelReady ? "READY" : "LEARNING")',
        '+" | model "+(LastNewsModelReady ? "READY" : "*LEARNING*")',
    ),
    (
        'cstats[6]=(LastTargetStructureReady ? "READY" : "LEARNING");',
        'cstats[6]=(LastTargetStructureReady ? "READY" : "*LEARNING*");',
    ),
    (
        'UiLabel("CHECK_NOTE","DISPLAY ONLY - execution logic unchanged.",',
        'UiLabel("CHECK_NOTE","DISPLAY ONLY - execution logic unchanged.   * = SHADOW / LEARNING",',
    ),
]


def _replace_once_or_already(source: str, old: str, new: str) -> tuple[str, bool]:
    if new in source:
        return source, False
    if old not in source:
        return source, False
    return source.replace(old, new, 1), True


def patch_source(source: str) -> tuple[str, list[str]]:
    changed: list[str] = []
    out = source

    for old, new in REPLACEMENTS:
        before = out
        out, did = _replace_once_or_already(out, old, new)
        if did:
            changed.append(old[:60])
        elif new not in out:
            # Local versions can legitimately omit older shadow rows. Skip them safely.
            pass

    # Special handling for ROLE_MODELS probabilities: add one dynamic marker variable
    # and decorate R/E/N/M/SL only while roles are Shadow or still Learning.
    marker_decl = 'string role_mark=(LastRoleShadow || !LastEnsembleReady ? "*" : "");'
    if marker_decl not in out:
        anchor = 'UiLabel("ROLE_MODELS",'
        pos = out.find(anchor)
        if pos >= 0:
            line_start = out.rfind("\n", 0, pos) + 1
            indent = re.match(r"[ \t]*", out[line_start:pos]).group(0)
            out = out[:line_start] + indent + marker_decl + "\n" + out[line_start:]
            changed.append("role_mark declaration")

    for label in ("R", "E", "N", "M", "SL"):
        old = f'+" {label}:"+RoleProbabilityText('
        new = f'+" {label}"+role_mark+":"+RoleProbabilityText('
        if new not in out and old in out:
            out = out.replace(old, new, 1)
            changed.append(f"{label} role marker")

    # Add legend to tooltip if that exact tooltip exists.
    tooltip_old = '"N/A: پیش‌بینی معتبر موجود نیست. درصدها دقت مدل نیستند.");'
    tooltip_new = (
        '"N/A: پیش‌بینی معتبر موجود نیست. درصدها دقت مدل نیستند.\\n"\n'
        '      "* = SHADOW / LEARNING / COLLECTING؛ هنوز Live نیست.");'
    )
    if tooltip_new not in out and tooltip_old in out:
        out = out.replace(tooltip_old, tooltip_new, 1)
        changed.append("role tooltip legend")

    # Safety assertions: display markers should exist; executable trade calls must be untouched.
    required = [
        "*SHADOW*",
        "*LEARNING*",
        "TargetLearning*",
        "*ShadowRiskP:",
        "* = SHADOW / LEARNING / COLLECTING",
    ]
    missing = [item for item in required if item not in out]
    if missing:
        raise ValueError("required UI markers missing: " + ", ".join(missing))

    if out.count("Trade.PositionClose(") != source.count("Trade.PositionClose("):
        raise ValueError("unsafe change: PositionClose call count changed")
    if out.count("Trade.PositionModify(") != source.count("Trade.PositionModify("):
        raise ValueError("unsafe change: PositionModify call count changed")
    if out.count("Trade.Buy(") != source.count("Trade.Buy("):
        raise ValueError("unsafe change: Trade.Buy call count changed")
    if out.count("Trade.Sell(") != source.count("Trade.Sell("):
        raise ValueError("unsafe change: Trade.Sell call count changed")

    return out, changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path, help="Installed Ramon.mq5")
    parser.add_argument("--check", action="store_true", help="Validate without writing")
    args = parser.parse_args()

    target = args.target.expanduser().resolve()
    if not target.is_file():
        raise SystemExit(f"target not found: {target}")

    source = target.read_text(encoding="utf-8")
    version = re.search(r'#property version\s+"([^"]+)"', source)
    patched, changed = patch_source(source)

    print(f"Target: {target}")
    print(f"EA version: {version.group(1) if version else 'unknown'}")
    print(f"Display-only replacements: {len(changed)}")
    print("Trading call counts preserved: YES")

    if args.check:
        print("CHECK ONLY: no file changed")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = target.with_name(f"{target.stem}.before-star-ui-{stamp}{target.suffix}")
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
