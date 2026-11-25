"""Descriptive sizing overlay on a fixed non-overlapping paper replay; never trades."""
from decimal import Decimal, ROUND_FLOOR

RISKS = ('0.067','0.15','0.25','0.50','0.75','1.00')


def compare_risk(trades, *, starting_balance, point, tick_size, tick_value,
                 minimum, step, maximum, allowance_points):
    def d(value):
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError('Nonfinite input')
        return result

    start, point, tick, value, minimum, step, maximum, allowance = map(d, (
        starting_balance, point, tick_size, tick_value, minimum, step, maximum, allowance_points))
    if min(start, point, tick, value, minimum, step, maximum) <= 0 or minimum > maximum or allowance < 0:
        raise ValueError('Invalid balance or contract')
    scenarios = []
    for risk in RISKS:
        balance = peak = start
        drawdown = wins_money = losses_money = Decimal(0)
        accepted = rejected = wins = streak = max_streak = 0
        for trade in trades:
            distance = abs(d(trade['entry']) - d(trade['plan']['stop']))
            per_lot = (distance + allowance * point) / tick * value
            if per_lot <= 0:
                raise ValueError('Invalid stop distance')
            budget = max(Decimal(0), balance) * d(risk) / 100
            volume = (min(maximum, budget/per_lot)/step).to_integral_value(rounding=ROUND_FLOOR)*step
            if volume < minimum:
                rejected += 1
                continue
            pnl = d(trade['net_points']) * point / tick * value * volume
            accepted += 1
            balance += pnl
            if pnl > 0:
                wins += 1
                wins_money += pnl
                streak = 0
            elif pnl < 0:
                losses_money -= pnl
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
            peak = max(peak, balance)
            drawdown = max(drawdown, (peak-balance)/peak*100)
        scenarios.append(dict(risk_percent=risk, eligible=accepted, rejected_minimum_lot=rejected,
            win_rate_percent=float(Decimal(wins)/accepted*100) if accepted else None,
            net_profit=float(balance-start), ending_balance=float(balance),
            max_closed_balance_drawdown_percent=float(drawdown),
            profit_factor=float(wins_money/losses_money) if losses_money else None,
            consecutive_losses=max_streak))
    return dict(mode='FIXED_PAPER_TRADE_SIZING_OVERLAY', starting_balance=str(start),
        contract=dict(point=str(point), tick_size=str(tick), tick_value=str(value),
            minimum=str(minimum), step=str(step), maximum=str(maximum)),
        candidate_count=len(trades), scenarios=scenarios,
        limitations=['Not a full live-policy replay: rejected candidates do not release the fixed paper replay holding window.',
            'Constant current contract and assumed costs; no historical margin, portfolio or daily-loss checks.',
            'Closed-balance drawdown only; no claim of profitability or live authorization.'])
