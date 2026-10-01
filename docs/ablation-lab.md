# Ramon Ablation Lab

This research-only lab asks which directional component adds information at the
same real entry times Ramon actually selected.

It reports:

- `ramon`: the real executed direction.
- `chronos_only`: the dominant direction reconstructed from the stored
  Chronos base-decision edges, without Ramon's threshold/veto choice.
- `prev_reversal`: fade the previous completed M15 candle.
- `momentum_4`: direction of the completed-bar move over the previous 4 M15 bars.
- `ma_4_12`: fast/slow simple moving-average direction.
- `random_matched`: deterministic random direction with the same observed
  BUY/SELL ratio as Ramon.

It also reruns the final chronological holdout with spread stress at 1.0x, 1.5x
and 2.0x. SELL uses the same Ask-aware proxy as the main Validation Lab.

## Important rules-only limitation

An exact historical `rules_only` ablation is not identifiable from current
telemetry. Ramon's intrabar and AI-trend checks are themselves conditioned on
Chronos-derived direction/path. Removing Chronos would require inventing a new
independent rule system, which would not be a faithful ablation. The report says
`UNAVAILABLE` instead of fabricating a number.

## Run

```bash
docker compose --profile tools run --rm tools \
  -m ramon.ablation_lab \
  --db /data/ramon_history.sqlite3 \
  --symbol XAUUSD_l \
  --max-bars 24 \
  --holdout 0.30 \
  --momentum-bars 4 \
  --ma-fast 4 \
  --ma-slow 12
```

This remains a matched-timing direction benchmark. It does not test whether a
baseline would have selected the same entry times on its own, and it must not be
used as a direct replacement for live trading logic.
