# Ramon V3 — MetaTrader integration track (observation only)

This change creates the *foundation* for the eight MT5 capabilities. It does **not**
claim that native MT5 MCP servers, Strategy Tester, MQL5 trade event hooks,
ONNX runtime, the built-in economic calendar, MetaTrader5 Python package,
distributed testing agents or native MQL5 OpenBLAS have been activated.

| Capability | Implemented in this branch | Remaining terminal integration |
| --- | --- | --- |
| MCP | local-only/read-only policy validator and prerequisite probe | configure MCP in supported MT5 build, verify permissions and Wine compatibility |
| Strategy Tester | as-of signal replay helper, prevents using future decisions | EA FileOpen replay mode; real-tick tester runs and reconciliation |
| OnTradeTransaction | idempotent SQLite event receiver schema | instrument `Ramon.mq5` and outbox for deal/order/SL/TP/manual changes |
| ONNX | shadow-only artifact probe | export small compatible model, validate MQL5 tensor shapes and inference |
| Economic Calendar | strict UTC event normalization | gather MT5 events in EA and deliver to model; reconcile existing ForexFactory NewsGuard |
| Python Integration | local package availability probe | implement authenticated/isolated terminal reader where Python MT5 API works |
| Optimization Agents | deterministic shadow grid-search utility | run tester agents and walk-forward/OOS performance gate |
| OpenBLAS | check whether NumPy reports OpenBLAS | benchmark and selectively port expensive MQL5 math |

## Safety
No live-order operations are introduced. V3 remains shadow. No change to
`Ramon.mq5`, execution limits, risk defaults or existing Docker services.

## Probe
```bash
uv run python -m ramon.v3_mt5_integrations
uv run pytest -q tests/test_v3_mt5_integrations.py
```

Install project's Python package in the environment if the module is not found.
Read-only checks are not proof of integration. Before enabling any native MCP tool,
confirm its permission and read-only behavior in the installed terminal build.
For Strategy Tester, MT5 WebRequest is unavailable; importing future model signals
into a replay would produce false backtest performance. Keep training/validation/test
chronologically separated and include commissions, bid/ask, slippage and spread.

References:
- https://www.mql5.com/en/docs/event_handlers/ontradetransaction
- https://www.mql5.com/en/docs/onnx
- https://www.mql5.com/en/docs/calendar
- https://www.mql5.com/en/docs/python_metatrader5
- https://www.metatrader5.com/en/terminal/help/mcp_and_ai/capabilities
