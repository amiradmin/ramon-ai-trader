# Range shadow — prospective experiment

Built on commit 6adfae3. The primary decision policy stays intact. The shadow component
never sends orders. The separately enabled live MAIN path is described below.

Fixed exploratory rules: use 12 completed M15 bars, close-path efficiency <0.30,
range width 1.5–6 ATR, reversal near the outer 20% of the range with recent micro
movement >=0.03 ATR. Freeze target at midpoint and stop 0.25 ATR beyond boundary.
Require reward >=1.2 initial risk and >=3 spreads. One simulated position per
symbol; maximum hold 30 minutes; cooldown 5 minutes after closure.

Buy entry Ask / exit Bid; sell entry Bid / exit Ask. Store price P/L and R in
range_shadow_trades, never account profit. Quotes are sampled roughly every
30 seconds: intermediate crossings can be missed. Commissions, fees, actual
slippage and lot-size feasibility are excluded. Rules are hypotheses, not
validated profitability. /health exposes OBSERVE_ONLY; failures are isolated
from the primary decision. No EA compilation is required for this server feature.

Inspect with python scripts/report_range_shadow.py. Assess new results using
fixed rules before considering live execution. The earlier retrospective regime
comparison does not validate this experiment.

## User-authorized live cent-account MAIN trial

The shared candidate can now be used as a separate MAIN execution strategy when
RAMON_RANGE_LIVE_ENABLED=1 and the EA explicitly advertises range_execution_ready.
It is a fallback only for primary WAIT due to insufficient edge or strength;
normal primary trades and timing/trend vetoes retain their original paths.
Old EAs cannot receive a live range signal. EnableRangeMain is an EA input.

Range entries have absolute frozen boundary SL and midpoint TP, rechecked against
the fresh execution quote, broker stop minimum, reward/risk and spread. Existing
volume sizing, account lock, daily limits and maximum executable risk remain in
force. Range orders use Ramon:<sample>:R and the existing MAIN magic number.
The EA recognizes them after restart, bypasses normal TP-stage/early-profit-lock
and early-adverse managers, closes after 30 minutes, and observes a 5-minute
cooldown using broker deal history. Failed history reads block new range entries.

Samples use the distinct model/strategy label range-reversal-v1, with the original
Chronos forecast retained in audit metadata; they are excluded by the model-ID
filter from Chronos role training. Shadow observations remain hypothetical and
are not used as evidence of live fills. Live activation is an experimental trial,
not validated profitability. Service health exposes range_main_enabled.

## هدف اجرایی فعلی رنج — نسخهٔ ۰٫۵۴٫۹

midpoint، هدف نامزد در سرویس و paper/shadow است. اکسپرت MAIN پس از انتخاب حجم، هدف سریع ۵ واحد حساب قبل از midpoint را انتخاب می‌کند و سود/ریسک ناخالص همان TP واقعی را با SL مرزی دوباره کنترل می‌کند؛ کمتر از ۱٫۲ ورود را متوقف می‌کند. سقف SL همچنان ۵ واحد حساب است و حجم برای رسیدن به هدف افزایش نمی‌یابد. تبدیل پنج واحد به پنج سنت فقط با ۱۰۰ واحد حساب در هر دلار درست است.

WAITهای مجاز برای بررسی نامزد عبارت‌اند از `insufficient_model_strength`، `insufficient_model_edge`، `adverse_intrabar_timing` و `direction_confirmation_required`. سیاست نهایی حالت بازار پس از این مرحله اعمال می‌شود و نامزد رنج اجازهٔ عبور از قفل آن را ندارد.
