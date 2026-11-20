"""Descriptive evidence assessment; never grants execution eligibility."""
import math


def assess_evidence(*, trades, interval, dataset_verified=False, holdout_untouched=False,
                    selection_adjusted=False, cost_sensitivity_verified=False,
                    forward_demo_verified=False):
    """Classify the interval without confusing inconclusive with rejection.

    The caller's attestations are informational, not proof. No sample-size
    threshold is imposed and no combination here can promote or suspend trading.
    """
    if isinstance(trades, bool) or not isinstance(trades, int) or trades < 0:
        raise ValueError('trades must be a non-negative integer')
    direction = 'UNAVAILABLE'
    if interval is not None:
        if not isinstance(interval, (list, tuple)) or len(interval) != 2 or not trades:
            raise ValueError('interval requires two bounds and at least one trade')
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in interval):
            raise ValueError('interval bounds must be finite numbers')
        lower, upper = interval
        if lower > upper:
            raise ValueError('interval bounds must be ordered')
        direction = 'POSITIVE_INTERVAL' if lower > 0 else 'NEGATIVE_INTERVAL' if upper < 0 else 'INCONCLUSIVE'
    assertions = {'dataset_quality': dataset_verified, 'untouched_holdout': holdout_untouched,
                  'multiple_testing': selection_adjusted, 'cost_sensitivity': cost_sensitivity_verified,
                  'forward_demo': forward_demo_verified}
    if any(type(value) is not bool for value in assertions.values()):
        raise ValueError('evidence attestations must be booleans')
    return {'status': 'VALIDATION_PENDING', 'interval_assessment': direction,
            'missing_attestations': [key for key, value in assertions.items() if not value],
            'execution_authority': False,
            'limitations': ['Attestations are not verified evidence.',
                'Intervals can be descriptive and may ignore serial correlation and selection.',
                'Positive intervals alone cannot promote a strategy; negative intervals do not automatically suspend it.']}
