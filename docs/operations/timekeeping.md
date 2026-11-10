# Timekeeping

The laptop displays Asia/Tehran; internal application records use UTC. These are different displays of the same instant. Docker services explicitly use `TZ=UTC`, Django uses `TIME_ZONE='UTC'` with `USE_TZ=True`, and the PostgreSQL connection timezone is UTC. Changing the laptop display timezone is unnecessary.

The main dashboard shows server UTC, the browser's local timezone/time, quote age and time-check state. It warns about a laptop/server difference over five seconds when the HTTP round trip is under two seconds. This is an approximate comparison, not an NTP measurement. The read-only `/api/ea/v1/time-status` endpoint reports UTC quote/telemetry age without account identifiers. A recent quote does not by itself prove trading readiness.

## Laptop verification

On November 10 the laptop had active systemd-timesyncd, NTP synchronization, Asia/Tehran display and the hardware clock in UTC. The observed NTP offset was about 3 milliseconds; no manual clock or timezone change was needed.

```bash
timedatectl status
timedatectl timesync-status
```

Keep automatic time synchronization enabled. Do not set local time manually to match MT5.

## Broker and market sessions

The existing command EA converts broker quote timestamps and completed candle timestamps to UTC using its measured broker/server-to-GMT offset. The broker calendar day remains separate because daily account risk resets follow the broker's day. The broker's named timezone is not sent by the current EA and is displayed as unreported by diagnostics; do not guess it from the server name. Historical conversions across a broker offset change require separate broker-history verification; this update does not rewrite historical bars or certify old conversions.

Opening-range sessions use Europe/London and America/New_York IANA zones so their daylight-saving rules apply separately. Broker session-hours configuration is distinct from those opening ranges. All P&L dashboard date filters use UTC, as labelled on the dashboard.

## Economic calendar

A laptop clock correction cannot establish the timezone of naive provider timestamps. The automatic Forex Factory worker now supplies offset-bearing timestamps directly from its weekly export; UTC conversion uses those offsets. Manual TipRanks snapshots still cannot clear the news gate. See [automatic economic calendar](automatic-economic-calendar.md) and [news entry lock](news-entry-lock.md).
