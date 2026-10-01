#!/usr/bin/env python3
"""Safely add actual fill-price telemetry to an installed Ramon.mq5.

This patch is telemetry-only. It preserves the installed EA version and does not
change trading gates, thresholds, sizing, entries, exits, SL or TP behavior.
It is intentionally function-aware so locally newer Ramon.mq5 files can be
patched without replacing them with the repository copy.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import re
import shutil
import sys


def find_function_body(source: str, signature: str) -> tuple[int, int]:
    start = source.find(signature)
    if start < 0:
        raise ValueError(f"{signature} not found")
    brace = source.find("{", start + len(signature))
    if brace < 0:
        raise ValueError(f"{signature} opening brace not found")
    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return brace, index
    raise ValueError(f"{signature} closing brace not found")


def replace_one_regex(text: str, pattern: str, repl, label: str) -> str:
    matches = list(re.finditer(pattern, text, flags=re.MULTILINE))
    if len(matches) != 1:
        raise ValueError(f"{label}: expected exactly one match, found {len(matches)}")
    return re.sub(pattern, repl, text, count=1, flags=re.MULTILINE)


def patch_closed_trade_body(body: str) -> str:
    if '\\"actual_fill_price\\"' in body and "entry_fill_value" in body:
        return body

    out = body

    if "double entry_fill_value=0.0;" not in out:
        pattern = (
            r"^(?P<indent>[ \t]*)double[^;\n]*\bin_volume\b[^;\n]*"
            r"\bout_volume\b[^;\n]*\brisk\b[^;\n]*;[ \t]*$"
        )

        def add_accumulator(match: re.Match[str]) -> str:
            indent = match.group("indent")
            return match.group(0) + f"\n{indent}double entry_fill_value=0.0;"

        out = replace_one_regex(out, pattern, add_accumulator, "entry-volume declaration")

    if "entry_fill_value+=fill*volume;" not in out:
        pattern = (
            r"^(?P<indent>[ \t]*)double[ \t]+fill[ \t]*=[ \t]*"
            r"HistoryDealGetDouble\([ \t]*deal[ \t]*,[ \t]*DEAL_PRICE[ \t]*\);[ \t]*$"
        )

        def add_fill_capture(match: re.Match[str]) -> str:
            indent = match.group("indent")
            return (
                match.group(0)
                + f"\n{indent}if(fill<=0.0) return false;"
                + f"\n{indent}entry_fill_value+=fill*volume;"
            )

        out = replace_one_regex(out, pattern, add_fill_capture, "DEAL_PRICE fill capture")

    if "double actual_fill_price=entry_fill_value/in_volume;" not in out:
        pattern = (
            r"^(?P<indent>[ \t]*)net[ \t]*=[ \t]*profit[ \t]*\+[ \t]*commission"
            r"[ \t]*\+[ \t]*swap[ \t]*\+[ \t]*fee[ \t]*;[ \t]*$"
        )

        def add_weighted_fill(match: re.Match[str]) -> str:
            indent = match.group("indent")
            return (
                f"{indent}if(entry_fill_value<=0.0 || in_volume<=0.0) return false;\n"
                f"{indent}double actual_fill_price=entry_fill_value/in_volume;\n"
                + match.group(0)
            )

        out = replace_one_regex(out, pattern, add_weighted_fill, "net calculation")

    if '\\"actual_fill_price\\"' not in out:
        lines = out.splitlines()
        candidates = [
            i for i, line in enumerate(lines)
            if "initial_risk_units" in line and "DoubleToString(risk" in line
        ]
        if len(candidates) != 1:
            raise ValueError(
                "payload initial_risk_units: expected exactly one match, "
                f"found {len(candidates)}"
            )
        i = candidates[0]
        indent = re.match(r"[ \t]*", lines[i]).group(0)
        lines.insert(
            i + 1,
            indent + '+",\\\"actual_fill_price\\\":"+DoubleToString(actual_fill_price,_Digits)'
        )
        out = "\n".join(lines)

    required = (
        "double entry_fill_value=0.0;",
        "entry_fill_value+=fill*volume;",
        "double actual_fill_price=entry_fill_value/in_volume;",
        '\\"actual_fill_price\\"',
    )
    missing = [item for item in required if item not in out]
    if missing:
        raise ValueError("required fill telemetry missing: " + ", ".join(missing))
    return out


def patch_source(source: str) -> str:
    brace, end = find_function_body(source, "bool ClosedTradePayload(")
    body = source[brace + 1:end]
    patched_body = patch_closed_trade_body(body)
    out = source[:brace + 1] + patched_body + source[end:]

    # Hard safety contract: this patch may enrich telemetry only.
    for call in ("Trade.Buy(", "Trade.Sell(", "Trade.PositionClose(", "Trade.PositionModify("):
        if out.count(call) != source.count(call):
            raise ValueError(f"unsafe change: {call} count changed")

    for token in (
        "bool ClosedTradePayload(",
        "actual_fill_price",
        "DEAL_PRICE",
    ):
        if token not in out:
            raise ValueError(f"required source token missing after patch: {token}")
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
    print("ClosedTradePayload located: YES")
    print("Actual fill telemetry: READY")
    print("Trading call counts preserved: YES")
    print(f"Would change source: {'YES' if patched != source else 'NO (already patched)'}")

    if args.check:
        print("CHECK ONLY: no file changed")
        return 0

    if patched == source:
        print("Already patched; no file changed.")
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
