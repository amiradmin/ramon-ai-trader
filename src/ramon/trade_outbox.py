"""Deliver durable EA outcomes independently of the decision cadence. No trade actions."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.request import Request, urlopen


def deliver_pending(directory: Path, url: str, *, limit: int = 8) -> int:
    delivered = 0
    pending = sorted(directory.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in pending[:limit]:
        try:
            if path.stat().st_size > 250_000:
                continue
            body = path.read_bytes()
            payload = json.loads(body)
            key = payload.get('trade_key') if isinstance(payload, dict) else None
            if not isinstance(key, str) or not key or len(key) > 256 or '\n' in key or '\r' in key:
                continue
            receipt = path.with_suffix('.ack')
            if receipt.exists() and receipt.read_text() == key:
                path.unlink()  # Resume a crash after acknowledgment publication.
                continue
            with urlopen(Request(url, data=body, headers={'Content-Type': 'application/json'}, method='POST'), timeout=4) as response:
                result = json.loads(response.read(4096))
                if response.status != 200 or not isinstance(result, dict) or result.get('saved') is not True:
                    continue
            # Acknowledgments mean the existing model API actually persisted the outcome.
            fd, name = tempfile.mkstemp(dir=directory, prefix='.receipt-', suffix='.tmp')
            try:
                with os.fdopen(fd, 'w') as stream:
                    stream.write(key)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, receipt)
            finally:
                Path(name).unlink(missing_ok=True)
            path.unlink()
            delivered += 1
        except (OSError, ValueError):
            continue  # Durable item stays queued on outage or invalid response.
    return delivered


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-url', default='http://127.0.0.1:8012/trades')
    parser.add_argument('--outbox', type=Path)
    args = parser.parse_args()
    users = Path(os.getenv('RAMON_MT5_USERS_ROOT', str(Path.home() / '.mt5/drive_c/users')))
    while True:
        directories = [args.outbox] if args.outbox else list(users.glob('*/AppData/Roaming/MetaQuotes/Terminal/Common/Files/RamonTradeOutbox'))
        for directory in directories:
            count = deliver_pending(directory, args.model_url)
            if count:
                print(f'Saved {count} queued trade outcome(s)', flush=True)
        time.sleep(2)


if __name__ == '__main__':
    main()
