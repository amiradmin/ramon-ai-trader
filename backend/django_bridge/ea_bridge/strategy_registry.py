"""Server-owned identities: one independent process and magic per setup."""
STRATEGIES = {
    'TREND_PULLBACK': ('trend-pullback', 'Pullback within an established trend', 26092301),
    'BREAKOUT_RETEST': ('breakout-retest', 'Breakout, retest and continuation', 26092302),
    'FAILED_BREAKOUT_SWEEP': ('failed-breakout-sweep', 'Failed breakout / liquidity sweep', 26092303),
    'RANGE_EDGE_FADE': ('range-edge-fade', 'Reversal at a confirmed range edge', 26092304),
    'SESSION_OPENING_RANGE_BREAKOUT': ('session-opening-range-breakout', 'London / New York opening-range breakout', 26092305),
    'VOLATILITY_COMPRESSION_EXPANSION': ('volatility-compression-expansion', 'Compression followed by expansion', 26092306),
    'EXHAUSTION_REVERSAL': ('exhaustion-reversal', 'Extended move and structural reversal', 26092307),
}


def strategy_commands(feed, setup):
    from .models import EACommand
    return EACommand.objects.filter(instance_id=feed.instance_id,account_login=feed.account_login,
        symbol=feed.symbol,payload__server_name=feed.server_name,payload__engine='STRATEGY',payload__strategy_setup=setup)


def owned_strategy_position(feed, position, *, setup=None):
    if not isinstance(position,dict) or position.get('symbol')!=feed.symbol:
        return None
    choices=[setup] if setup in STRATEGIES else STRATEGIES if setup is None else []
    for name in choices:
        if type(position.get('magic')) is not int or position['magic']!=STRATEGIES[name][2]: continue
        matched=list(strategy_commands(feed,name).filter(action='OPEN',state='EXECUTED',execution_authorized=True,
            result__broker_ticket=str(position.get('ticket','')))[:2])
        if len(matched)==1: return matched[0]
    return None


def verified_engine_positions(feed):
    from .scalper_engine import owned_position
    positions=feed.payload.get('positions')
    return (isinstance(positions,list) and feed.payload.get('account_margin_mode')==2
        and type(feed.payload.get('account_margin_mode')) is int
        and feed.payload.get('execution_profile')=='ticket-magic-v1'
        and all(owned_position(feed,p) is not None or owned_strategy_position(feed,p) is not None for p in positions))
