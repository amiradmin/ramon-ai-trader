# V2 execution receipt: broker-confirmed closed-position reconciliation

`mt5/Ramon.mq5` adds terminal journal phase `CLOSED` after `RECEIPT`.

- The unchanged path verifies broker history entry order state `FILLED`,
  matching symbol, volume, entry direction, deal and position identifier.
- If a still-open protected position is visible, it finalizes as `VISIBLE`
  (existing behavior).
- Otherwise the journal may finalize as `CLOSED` only if
  `HistorySelectByPosition(position_id)` confirms that all deals on that
  position are from the same symbol, entry is solely from the exact
  originating order and direction, all exits use the opposite direction,
  entry total equals receipt volume, exit total equals entry total,
  and no open position with the same identifier remains.
- Reversals, INOUT, foreign entries, partial exits, mismatched volumes,
  missing records, disconnected terminal, PREPARED/UNCERTAIN phase,
  or invalid checks retain the lock. The old candidate retains its
  duplicate identifier: no retry of an already-recorded intent.
- Journal hashes remain validated. Old VISIBLE records retain meaning.
  This phase does NOT depend on JSONL as proof of execution.

**Safety checks before production:**
1. Merge code locally without modifying the actual journal or clearing locks.
2. Open `Ramon.mq5` in MetaEditor, F7; capture warnings/errors.
3. Use MT5 Strategy Tester or an isolated demo account to cover:
   open position with valid SL, closed full position, still open after
   partial close, changed position ID, reverse/netting, missing history,
   malformed journal, disconnected account, repeated identical sample.
4. Review the affected LIVE broker order `50381529969` / deal
   `50333609740` and verify its exact position history with MT5.
5. Only then deploy to the real EA under supervision. A successful compile
   alone is not sufficient approval to turn off an execution safety lock.

No SQLite/Postgres changes or model-policy changes are included.
