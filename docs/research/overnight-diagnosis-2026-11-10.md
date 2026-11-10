# Ramon overnight diagnosis — 10 November 2026

Read-only diagnosis of runtime, database, MT5 logs and host journal. All times below are Tehran (UTC+03:30). No service, policy, broker order or system setting was changed.

## Findings

1. MT5 terminal log records broker connection loss at 21:20:00 on November 9. Successful authorization resumes at 06:22:43 November 10. Terminal log timestamps were checked against matching persisted trade times. Local Django continued receiving requests: 940 stale-M15 rejections were logged from 21:45:14 through 05:34:44. Therefore the missing decisions were not simply an idle strategy or a nine-hour Django outage. The underlying broker connectivity cause (local network, route or broker access server) is not established by these logs.
2. Host journal confirms orderly poweroff at 05:35 November 10 and next boot at 06:19:46. This explains the final part of the interruption, not its initial hours. Current container metadata also records a later stop/start at 07:19; restart counters are zero and OOM flags false. Those counters alone cannot rule out prior interruptions.
3. Failed breakeven request at 19:42 November 9 encoded position_ticket as a quoted string. EA v1.07 reads it through JsonNumber/StringToDouble, yielding ticket zero. Result was FAILED / BROKER_MODIFY_0_unknown retcode 0, broker_ticket 0. Existing research documentation already records this incident and its correction. Runtime source inspection confirms integer serialization and the bounded numeric-ticket-v2 recovery path are present. No new patch is needed for this known defect. Successful broker modification using the corrected path remains unverified; no test order was sent.
4. StrategyDecisionRecord.update_or_create overwrites the same decision key during repeated evaluations. RiskVerdictRecord preserves repeated verdicts, explaining why a filled BUY links to a later WAIT decision row. An append-only evaluation record linked to commands and verdicts is the concrete audit fix; implementation is outside this read-only diagnosis.

## Updated account snapshot

At approximately 09:21 November 10, telemetry reported connected, balance/equity USD 2,209.45 and no positions. Since the earlier 08:16 snapshot, a TREND_PULLBACK SELL opened at 08:33:29, volume 0.08, fill 4114.80, SL 4119.29135, TP 4109.1858125. It closed at 08:52:06 with SL exit and recorded realized P&L -36.80 USD. Combined recorded closed outcomes since November 9 18:00 are +26.18 -34.00 -36.80 = -44.62 USD. One winner entered before that review window. This is a descriptive three-trade sample, not an expectancy estimate.

## Concrete next actions

- Operate the MT5 terminal and Django host continuously during intended monitoring hours; verify shutdown/sleep schedules and reliable network access. For unattended operation, use an always-on host. The network fault itself still needs route/access-server evidence if it recurs.
- Add durable connectivity/stale-feed incident records and recovery visibility. Avoid treating a healthy Django process as proof of a connected broker feed.
- Verify the corrected stop-modification path on an eligible demo position, retaining request, acknowledgement and observed broker stop. Do not manufacture a live test order or blindly retry uncertain commands.
- Preserve each evaluation with an immutable identifier and attach the actual evaluation to each command. Retain existing records rather than rewriting history.
- Keep forecasting observational and leave strategy thresholds unchanged while collecting continuous, auditable demo sessions.
