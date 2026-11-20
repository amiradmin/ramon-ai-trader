#!/usr/bin/env python3
"""Compare unconverted M15 and H1 MT5 exports without altering either file."""
import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path


def load(path):
    records = {}
    with path.open(encoding='utf-16', newline='') as stream:
        for row in csv.reader(stream):
            stamp = datetime.strptime(row[0], '%Y.%m.%d %H:%M')
            if stamp in records:
                raise ValueError(f'duplicate timestamp: {stamp}')
            records[stamp] = tuple(Decimal(value) for value in row[1:5])
    return records


def compare(m15_path, h1_path):
    m15 = load(m15_path)
    h1 = load(h1_path)
    groups = defaultdict(dict)
    for stamp, values in m15.items():
        groups[stamp.replace(minute=0)][stamp.minute] = values
    counts = Counter()
    mismatches = []
    for stamp, actual in sorted(h1.items()):
        group = groups.get(stamp, {})
        if sorted(group) != [0, 15, 30, 45]:
            counts['incomplete_or_missing_m15'] += 1
            continue
        bars = [group[minute] for minute in (0, 15, 30, 45)]
        derived = (bars[0][0], max(bar[1] for bar in bars), min(bar[2] for bar in bars), bars[-1][3])
        if actual == derived:
            counts['matching_ohlc'] += 1
        else:
            counts['mismatched_ohlc'] += 1
            mismatches.append({'broker_timestamp': stamp.isoformat(), 'h1': list(map(str, actual)),
                               'derived_from_m15': list(map(str, derived))})
    return {'m15_sha256': hashlib.sha256(m15_path.read_bytes()).hexdigest(),
            'h1_sha256': hashlib.sha256(h1_path.read_bytes()).hexdigest(),
            'h1_rows': len(h1), 'counts': dict(counts), 'mismatches': mismatches,
            'limitations': ['Raw timestamps are unconverted; matches do not prove UTC correctness or independent origin.',
                'Incomplete H1 or M15 bars require separate investigation; no candles are changed.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('m15', type=Path)
    parser.add_argument('h1', type=Path)
    args = parser.parse_args()
    print(json.dumps(compare(args.m15, args.h1), indent=2))


if __name__ == '__main__':
    main()
