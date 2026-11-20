#!/usr/bin/env python3
"""Read-only integrity inventory of an MT5 CSV with unverified server timestamps."""
import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


def audit(path, minutes):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    report = {'file': str(path), 'sha256': digest.hexdigest(), 'timeframe_minutes': minutes,
              'timestamp_timezone': 'UNVERIFIED_BROKER_SERVER_TIME', 'rows': 0, 'years': {},
              'first': None, 'last': None, 'off_grid': 0, 'duplicate_timestamps': 0,
              'non_increasing': 0, 'invalid_rows': 0, 'gap_intervals': 0,
              'elapsed_slots_without_bar': 0}
    years = Counter()
    seen = set()
    previous = None
    with path.open(encoding='utf-16', newline='') as source:
        for row in csv.reader(source):
            report['rows'] += 1
            try:
                if len(row) < 6:
                    raise ValueError('missing fields')
                stamp = datetime.strptime(row[0], '%Y.%m.%d %H:%M')
                prices = [Decimal(value) for value in row[1:5]]
                volume = Decimal(row[5])
                if not all(x.is_finite() for x in [*prices, volume]):
                    raise ValueError('nonfinite')
                o, h, l, c = prices
                if min(prices) <= 0 or volume < 0 or h < max(o, l, c) or l > min(o, h, c):
                    raise ValueError('invalid OHLCV')
            except (ValueError, InvalidOperation):
                report['invalid_rows'] += 1
                continue
            years[str(stamp.year)] += 1
            if report['first'] is None:
                report['first'] = row[0]
            report['last'] = row[0]
            if (stamp.hour * 60 + stamp.minute) % minutes:
                report['off_grid'] += 1
            if stamp in seen:
                report['duplicate_timestamps'] += 1
            seen.add(stamp)
            if previous is not None:
                elapsed = (stamp - previous).total_seconds()
                if elapsed <= 0:
                    report['non_increasing'] += 1
                elif elapsed > minutes * 60:
                    report['gap_intervals'] += 1
                    report['elapsed_slots_without_bar'] += int(elapsed // (minutes * 60)) - 1
            previous = stamp
    report['years'] = dict(years)
    report['note'] = 'Elapsed gaps include market closures. This report does not establish timezone, origin or completeness.'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    parser.add_argument('--minutes', type=int, required=True, choices=[1, 5, 15, 60])
    args = parser.parse_args()
    print(json.dumps(audit(args.file, args.minutes), indent=2))


if __name__ == '__main__':
    main()
