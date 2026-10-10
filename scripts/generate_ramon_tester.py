#!/usr/bin/env python3
"""Generate RamonTester.mq5 in an isolated output directory, without Win32 DLL imports.

Does not edit the live Ramon.mq5 or Ramon.ex5. Test runs start disarmed.
This removes tester startup's DLL-loading failure; it does NOT enable HTTP
AI forecasts in Strategy Tester, which disallows WebRequest.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re

IMPORTS = re.compile(
    r'(?m)^#import "user32\.dll"\s*\n.*?^#import\s*$',
    re.DOTALL,
)
CLIPBOARD = re.compile(
    r'(?m)^bool CopyDiagnosticToClipboard\(\)\s*\{.*?^\}\s*(?=datetime NewsHighEventUTC=)',
    re.DOTALL,
)
STUB = """bool CopyDiagnosticToClipboard()
{
   // Tester build: preserve diagnostic generation, never access Win32 DLLs.
   WriteDiagnostic();
   LastCopyStatus="Clipboard unavailable in tester build";
   return false;
}

"""


def create_tester_source(source: str) -> str:
    if source.count('#import "user32.dll"') != 1 or source.count('bool CopyDiagnosticToClipboard()') != 1:
        raise ValueError("Unrecognized source layout: refusing to modify")
    text, imports = IMPORTS.subn("", source)
    if imports != 1:
        raise ValueError(f"Expected exactly one Windows DLL import block, got {imports}")
    text, functions = CLIPBOARD.subn(lambda match: STUB, text)
    if functions != 1:
        raise ValueError(f"Expected exactly one clipboard function, got {functions}")
    if "#import" in text or any(name+"(" in text for name in (
        "OpenClipboard", "GlobalAlloc", "GlobalLock", "GlobalFree",
        "SetClipboardData", "lstrcpyW", "EmptyClipboard",
    )):
        raise ValueError("A DLL import or Win32 invocation remains")
    text, gates = re.subn(
        r'(?m)^input bool EnableLiveTrading\s*=\s*false\s*;',
        "input bool EnableLiveTrading = false;",
        text,
    )
    if gates != 1 or "bool V2Preflight(" not in text:
        raise ValueError("Live-disarmed default or V2 risk preflight is missing")
    text = text.replace(
        '#property description "Independent Chronos-2 XAUUSD_l M15 bot; local model server required."',
        '#property description "Ramon Tester ONLY: no Win32 DLL; never attach to live account."',
        1,
    )
    return text


def main():
    p = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    p.add_argument("--source", type=Path, default=root / "mt5/Ramon.mq5")
    p.add_argument("--output", type=Path, default=root / "build/mt5-tester/RamonTester.mq5")
    args = p.parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    if source == output:
        p.error("Output must differ from source")
    try:
        generated = create_tester_source(source.read_text(encoding="utf-8-sig"))
    except ValueError as exc:
        p.error(str(exc))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(generated, encoding="utf-8")
    print(f"Generated tester-only source: {output}")
    print("No live EA was modified. Build/test this separately in MetaEditor.")


if __name__ == "__main__":
    main()
