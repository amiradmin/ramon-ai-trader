# Ramon EA heartbeat troubleshooting

The EA heartbeat is telemetry only. It does not authorize trades, and it should be sent while the market is closed as long as MT5 is running, the EA is attached, and the local Django API is reachable.

## Authentication must match

1. In the repository's local, untracked `.env` file, configure `RAMON_EA_BEARER_TOKEN` to a private random secret of at least 32 characters.
2. In MT5, open the chart with Ramon attached, press **F7** (Expert properties), and set **DjangoBearerToken** to exactly the same secret.
3. Never commit the secret, paste it into an issue, or share it in chat. The EA logs do not print the token.
4. Restart/re-apply the EA after changing Inputs. Restart the Django service after changing `.env` so it reloads the environment.

The backend reads `RAMON_EA_BEARER_TOKEN`; the EA reads `DjangoBearerToken`. These are two local settings that must match.

## MT5 WebRequest permission

In MT5, open **Tools → Options → Expert Advisors** and add this exact URL to the WebRequest allow-list:

`http://127.0.0.1:8017`

The EA uses:
- `POST http://127.0.0.1:8017/api/ea/v1/heartbeat` every 30 seconds.
- `POST http://127.0.0.1:8017/api/ea/v1/events` for observational snapshots.

The heartbeat is independent of market hours. Do not interpret `trade_allowed: false` or a closed market as a failed heartbeat.

## Check the result

- In the MT5 **Experts** tab, look for `Ramon Django heartbeat OK`, including the instance ID and latency.
- `TOKEN NOT CONFIGURED` means the EA input is empty or shorter than 32 characters.
- A transport error (negative WebRequest result) usually means the URL is not allow-listed, Django is unreachable, or the request timed out.
- HTTP `401` means the EA token does not match the backend token.
- HTTP `503` with `EA bridge token is not configured` means `RAMON_EA_BEARER_TOKEN` is missing in the Django process environment.
- HTTP `405` from a browser or a GET request is expected: the heartbeat endpoint accepts POST only.

To inspect authenticated heartbeat records in Django admin, open **EA heartbeats**. The API's authenticated status endpoint is `GET /api/ea/v1/status`; it reports the last-seen age and whether each heartbeat is fresh.

## Safety

Heartbeat and snapshot telemetry are observational only. Do not remove backend authentication to make a heartbeat appear, and do not put credentials in source control. Market closure does not prevent telemetry; MT5 must still be running with the EA active.
