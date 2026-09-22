from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Lock

from .core import Market, Settings, evaluate
from .model import ChronosForecaster, model_name


def serve(host: str, port: int, model: ChronosForecaster, settings: Settings) -> None:
    """Serve requests on loopback; inference is serialized for one local EA."""
    guard = Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/health":
                self.send_error(404)
                return
            self.reply(200, {"ready": True, "model": model.model_id})

        def do_POST(self) -> None:
            if self.path != "/decision":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 2 or length > 250_000:
                    raise ValueError("invalid body size")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("request must be a JSON object")
                market = Market.from_dict(payload)
                with guard:
                    result = evaluate(market, model, settings)
                self.reply(200, result.to_dict())
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self.reply(400, {"error": str(exc)})
            except Exception:
                # Never synthesize BUY/SELL if the model fails or times out.
                self.reply(503, {"error": "model_unavailable"})

        def reply(self, status: int, data: dict[str, object]) -> None:
            body = json.dumps(data, separators=(",", ":"), allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("only loopback binding is supported")
    HTTPServer((host, port), Handler).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Chronos-2 local decision service")
    parser.add_argument("--model", default=os.getenv("CHRONOS_MODEL", "autogluon/chronos-2-small"))
    parser.add_argument("--device", default=os.getenv("CHRONOS_DEVICE", "cpu"))
    parser.add_argument("--port", type=int, default=8012)
    args = parser.parse_args()
    checkpoint = model_name(args.model)
    model = ChronosForecaster(checkpoint, args.device)
    serve("127.0.0.1", args.port, model, Settings())


if __name__ == "__main__":
    main()
