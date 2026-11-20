#!/usr/bin/env python3
"""Convert a LiteFinance server-time export into a provisional, local CSV.

No database writes. Transition Sundays are rejected because the broker FAQ does
not specify the exact switch hour; this is a research hypothesis, not certification.
"""
import argparse
import calendar
import csv
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from audit_mt5_export import audit

BROKER_RULE = 'https://www.litefinance.org/support/faq/'


def last_sunday(year, month):
    return max(week[calendar.SUNDAY] for week in calendar.monthcalendar(year, month))


def convert(stamp):
    march = datetime(stamp.year, 3, last_sunday(stamp.year, 3))
    october = datetime(stamp.year, 10, last_sunday(stamp.year, 10))
    if stamp.date() in (march.date(), october.date()):
        raise ValueError('Broker DST transition day requires independent verification')
    offset = 3 if march < stamp < october else 2
    return (stamp - timedelta(hours=offset)).replace(tzinfo=timezone.utc), offset


def stage(source, destination, timeframe):
    if destination.exists():
        raise ValueError('Destination already exists')
    result = audit(source, timeframe)
    if not result['rows'] or any(result[k] for k in ('invalid_rows', 'off_grid', 'duplicate_timestamps', 'non_increasing')):
        raise ValueError('Source failed integrity checks')
    previous = None
    count = 0
    created = False
    # Exclusive creation avoids accidental replacement. Delete incomplete output on failure.
    try:
        with source.open(encoding='utf-16', newline='') as infile, destination.open('x', encoding='utf-8', newline='') as out:
            created = True
            writer = csv.writer(out)
            writer.writerow(['broker_timestamp', 'utc_timestamp', 'offset_hours', 'open', 'high', 'low', 'close', 'volume', 'extra_field_7'])
            for row in csv.reader(infile):
                raw = datetime.strptime(row[0], '%Y.%m.%d %H:%M')
                utc, offset = convert(raw)
                if previous is not None and utc <= previous:
                    raise ValueError('Converted timestamps collide or are not increasing')
                writer.writerow([row[0], utc.isoformat(), offset, *row[1:6], row[6] if len(row) > 6 else ''])
                previous = utc
                count += 1
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        raise
    result.update({'output': str(destination), 'output_sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
                   'timezone_rule_source': BROKER_RULE, 'conversion_status': 'PROVISIONAL_NOT_VALIDATED',
                   'warning': 'Historical provenance, DST switch hours and broker session completeness require verification.',
                   'output_rows': count})
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('destination', type=Path)
    p.add_argument('--minutes', type=int, required=True, choices=(1, 5, 15, 60))
    args = p.parse_args()
    print(json.dumps(stage(args.source, args.destination, args.minutes), indent=2))


if __name__ == '__main__':
    main()
