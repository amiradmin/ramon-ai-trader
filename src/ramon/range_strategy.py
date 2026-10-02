"""Fixed range-reversal candidate, shared by paper and explicitly enabled MAIN."""
from .core import Market, atr14
from .ensemble import regime_features

def candidate(market: Market) -> dict:
    status = {'range_candidate': False}
    atr = atr14(market.bars)
    if len(market.bars)<16 or len(market.micro_bars)<3 or atr<=0:
        return status | {'range_shadow_status': 'MISSING_CONTEXT'}
    bars = market.bars[-12:]
    low, high = min(b.low for b in bars), max(b.high for b in bars)
    width, spread = high-low, market.ask-market.bid
    if regime_features(market.bars)['body_efficiency_12'] >= .3 or not 1.5*atr <= width <= 6*atr:
        return status | {'range_shadow_status': 'NOT_RANGE'}
    if not low <= market.bid <= high:
        return status | {'range_shadow_status': 'OUTSIDE_RANGE'}
    previous = market.micro_bars[-2].close
    anchor = market.micro_bars[-3].close
    direction = ('BUY' if market.bid <= low+.2*width and market.bid>previous and market.bid-anchor>=.03*atr else
             'SELL' if market.bid >= high-.2*width and market.bid<previous and anchor-market.bid>=.03*atr else '')
    if not direction:
        return status | {'range_shadow_status': 'WAIT_BOUNDARY_REVERSAL'}
    entry = market.ask if direction=='BUY' else market.bid
    stop = low-.25*atr if direction=='BUY' else high+.25*atr
    target = (low+high)/2
    reward = target-entry if direction=='BUY' else entry-target-spread
    risk = entry-stop if direction=='BUY' else stop-entry
    if risk<=0 or reward < max(3*spread,1.2*risk):
        return status | {'range_shadow_status': 'INSUFFICIENT_REWARD'}
    return status | {'range_candidate': True, 'direction': direction, 'entry': entry,
                     'stop': stop, 'target': target, 'low': low, 'high': high,
                     'stop_distance': risk, 'target_distance': abs(target-entry)}

def live_candidate(market: Market, response: dict, *, enabled: bool, capable: bool, quote_time: int | None, max_spread_points: int) -> dict | None:
    if not enabled or not capable or quote_time is None:
        return None
    # RANGE execution may take over selected WAIT states, including a primary
    # adverse-intrabar veto. The range candidate still requires a fresh boundary
    # reversal, so this does not buy/sell into the adverse move itself.
    if response.get('decision') != 'WAIT' or response.get('reason') not in {
        'insufficient_model_strength',
        'insufficient_model_edge',
        'adverse_intrabar_timing',
    }:
        return None
    if (market.ask-market.bid)/market.point > max_spread_points:
        return None
    result = candidate(market)
    return result if result['range_candidate'] else None
