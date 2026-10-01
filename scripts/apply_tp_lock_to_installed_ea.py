#!/usr/bin/env python3
"""Safely merge TP-stage tick protection into an installed Ramon.mq5.

This tool preserves the installed EA source (for example local v0.53.7) instead
of replacing it with the repository copy. It removes only the standalone OnTick
block added by the TP-lock patch, then injects ObserveTPStageCrossingsOnTick()
into the already-existing OnTick handler exactly once.

A timestamped backup is written next to the target before modification.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import re
import shutil
import sys


PATCH_ONTICK = """void OnTick()
{
   // Price crossings must not wait for the 5-second timer or 30-second model snapshot.
   // This handler only advances TP stages and tightens SL; it never creates entries.
   ObserveTPStageCrossingsOnTick();
}

"""


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


def patch_source(source: str) -> str:
    # Remove only the exact standalone OnTick introduced by the TP-lock patch.
    source = source.replace(PATCH_ONTICK, "", 1)

    on_ticks = [m.start() for m in re.finditer(r"(?m)^void OnTick\(\)\s*$", source)]
    if len(on_ticks) != 1:
        raise ValueError(
            f"expected exactly one existing OnTick after cleanup, found {len(on_ticks)}"
        )

    brace, end = find_function_body(source, "void OnTick()")
    body = source[brace + 1 : end]
    call = "ObserveTPStageCrossingsOnTick();"
    if call not in body:
        insertion = (
            "\n   // Protect reached TP1/TP2 immediately on every market tick.\n"
            "   ObserveTPStageCrossingsOnTick();"
        )
        source = source[: brace + 1] + insertion + source[brace + 1 :]

    on_tick_defs = len(re.findall(r"(?m)^void OnTick\(\)\s*$", source))
    if on_tick_defs != 1:
        raise ValueError(f"unsafe result: {on_tick_defs} OnTick definitions")

    brace, end = find_function_body(source, "void OnTick()")
    body = source[brace + 1 : end]
    if body.count(call) != 1:
        raise ValueError("unsafe result: TP tick observer must be called exactly once in OnTick")

    for required in (
        "void ObserveTPStageCrossingsOnTick()",
        "bool ProtectReachedTPStage(",
        "TPStageLockStatus",
    ):
        if required not in source:
            raise ValueError(f"required TP-lock implementation missing: {required}")

    return source


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
    patched = patch_source(source)

    print(f"Target: {target}")
    print(f"EA version: {version.group(1) if version else 'unknown'}")
    print("OnTick definitions: 1")
    print("TP observer calls inside OnTick: 1")

    if args.check:
        print("CHECK ONLY: no file changed")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = target.with_name(f"{target.stem}.before-safe-tp-lock-{stamp}{target.suffix}")
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
