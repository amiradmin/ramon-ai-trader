"""Read-only, account-scoped open-position monitoring for TREND_PULLBACK.

This module never creates EA commands. Existing +1R protection remains under
its independent, disabled-by-default live execution gates.
"""
from django.conf import settings

from .profit_protection import protection_status


def trend_pullback_management_snapshot(feed):
    """Return a bounded, JSON-serializable lifecycle snapshot.

    Only verified TREND_PULLBACK positions receive actionable R-multiple data.
    The broker, not the planner, remains the source of truth for SL/TP state.
    """
    status = protection_status(feed, strategy_setup='TREND_PULLBACK')
    positions = []
    for row in status['positions']:
        # Unverified/manual positions cannot acquire management eligibility.
        if not row.get('original_command_id'):
            continue
        positions.append({
            'ticket': row['ticket'],
            'state': row['state'],
            'side': row['side'],
            'entry': row['entry'],
            'current_r': row['current_r'],
            'original_risk': row['original_risk'],
            'current_stop': row['current_stop'],
            'original_target': row['take_profit'],
            'breakeven_stop': row['proposed_stop'],
            'breakeven_command_id': row['command_id'],
        })
    return {
        'schema_version': 1,
        'strategy_setup': 'TREND_PULLBACK',
        'state': status['state'],
        'mode': 'LIVE_ENABLED' if (
            getattr(settings, 'RAMON_REAL_CENT_PROFIT_PROTECTION_ENABLED', False)
            and getattr(settings, 'RAMON_EA_COMMAND_CREATION_ENABLED', False)
            and getattr(settings, 'RAMON_EA_COMMAND_EXECUTION_ENABLED', False)
        ) else 'OBSERVE_ONLY',
        'breakeven_rule': 'PLUS_1_ORIGINAL_R',
        'trailing_stop': 'DISABLED',
        'dynamic_take_profit': 'DISABLED',
        'time_exit': 'DISABLED',
        'positions': positions,
        'position_count': len(positions),
    }
