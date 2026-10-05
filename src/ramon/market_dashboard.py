"""Share-safe live market dashboard for human review.

This service intentionally exposes no trading controls or manual-pass endpoints.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .monitor import ASSETS, build_snapshot, default_diagnostic, object_json
from .opportunities import read_opportunities


def handler_for(db: str, diagnostic, symbol: str, health_url: str, model_url: str):
    class Handler(BaseHTTPRequestHandler):
        def _health(self):
            try:
                with urlopen(health_url, timeout=.7) as response:
                    return object_json(response.read(100_000))
            except (OSError, ValueError):
                return None

        def reply(self, body: bytes, content_type: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'self'"
            )
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route = self.path.split("?", 1)[0]
            if route in {"/", "/market", "/market.html"}:
                self.reply((ASSETS / "market.html").read_bytes(), "text/html; charset=utf-8")
                return
            if route == "/market.js":
                self.reply((ASSETS / "market.js").read_bytes(), "text/javascript; charset=utf-8")
                return
            if route == "/style.css":
                self.reply((ASSETS / "style.css").read_bytes(), "text/css; charset=utf-8")
                return
            if route == "/api/snapshot":
                data = build_snapshot(db, diagnostic, symbol=symbol, health=self._health())
                self.reply(
                    json.dumps(data, ensure_ascii=False, allow_nan=False).encode(),
                    "application/json; charset=utf-8",
                )
                return
            if route == "/api/opportunities":
                self.reply(json.dumps(read_opportunities(db, symbol), ensure_ascii=False, allow_nan=False).encode(), "application/json; charset=utf-8")
                return
            if route == "/api/opinions":
                try:
                    with urlopen(model_url.rstrip("/") + "/human-opinions?symbol=" + symbol + "&limit=12", timeout=2.0) as response:
                        body = response.read(200_000)
                    self.reply(body, "application/json; charset=utf-8")
                except OSError as exc:
                    self.reply(
                        json.dumps({"opinions": [], "error": str(exc)}, ensure_ascii=False).encode(),
                        "application/json; charset=utf-8",
                        502,
                    )
                return
            self.send_error(404)

        def do_POST(self):
            if self.path != "/api/opinion":
                self.send_error(404)
                return
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                self.send_error(403)
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self.send_error(415)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 1 <= length <= 8192:
                    raise ValueError("invalid body size")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("invalid payload")
                opinion = str(payload.get("opinion", "")).upper()
                confidence = int(payload.get("confidence", 0))
                note = str(payload.get("note", "")).strip()[:2000]
                if opinion not in {"BUY", "SELL", "WAIT"}:
                    raise ValueError("opinion must be BUY, SELL or WAIT")
                if confidence not in {1, 2, 3, 4, 5}:
                    raise ValueError("confidence must be 1..5")

                snap = build_snapshot(db, diagnostic, symbol=symbol, health=self._health())
                if not snap.get("sample_key") or not snap.get("signal_bar_time"):
                    raise ValueError("live decision context unavailable")

                market_snapshot = {
                    "generated_at": snap.get("generated_at"),
                    "decision": snap.get("decision"),
                    "reason": snap.get("reason"),
                    "ea_status": snap.get("ea_status"),
                    "manual_overrides_active": snap.get("manual_overrides_active", []),
                    "nodes": snap.get("nodes", []),
                    "recent_market": snap.get("recent_market", {}),
                }
                forward = {
                    "reviewer": "Sahar",
                    "symbol": symbol,
                    "sample_key": snap["sample_key"],
                    "signal_bar_time": snap["signal_bar_time"],
                    "opinion": opinion,
                    "confidence": confidence,
                    "note": note,
                    "snapshot": market_snapshot,
                }
                request = Request(
                    model_url.rstrip("/") + "/human-opinion",
                    data=json.dumps(forward, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=2.0) as response:
                    result = object_json(response.read(100_000))
                self.reply(
                    json.dumps(result, ensure_ascii=False).encode(),
                    "application/json; charset=utf-8",
                )
            except (ValueError, OSError) as exc:
                self.reply(
                    json.dumps({"saved": False, "error": str(exc)}, ensure_ascii=False).encode(),
                    "application/json; charset=utf-8",
                    400,
                )

        def log_message(self, fmt, *args):
            pass

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/ramon_history.sqlite3")
    parser.add_argument("--diagnostic", type=Path, default=default_diagnostic())
    parser.add_argument("--symbol", default="XAUUSD_l")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8014)
    parser.add_argument("--health-url", default="http://127.0.0.1:8012/health")
    parser.add_argument("--model-url", default="http://127.0.0.1:8012")
    args = parser.parse_args()
    server = ThreadingHTTPServer(
        (args.host, args.port),
        handler_for(args.db, args.diagnostic, args.symbol, args.health_url, args.model_url),
    )
    print(f"Ramon market review dashboard: http://0.0.0.0:{server.server_port}/market", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
