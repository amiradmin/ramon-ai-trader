"""Account-scoped scalp signal configuration. Defaults preserve the frozen policy."""
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from ramon_pipeline.signalflow.scalping import ScalpConfig

# Cost and holding limits may only be tightened relative to existing live policy.
FIELDS = {
    'm1_window': ('M1 lookback (bars)', 64, 128, 1),
    'm5_window': ('M5 lookback (bars)', 32, 64, 1),
    'minimum_stop_atr': ('Minimum stop distance (ATR)', .5, 2, .05),
    'maximum_stop_atr': ('Maximum stop distance (ATR)', .5, 3, .05),
    'reward_risk': ('Target reward / risk', 1.25, 3, .05),
    'stop_buffer_atr': ('Stop-loss buffer (ATR)', .05, .5, .01),
    'maximum_cost_to_risk': ('Maximum cost / risk (ratio)', .05, .20, .01),
    'max_holding_bars': ('Maximum holding time (minutes)', 1, 5, 1),
}


def validated_policy(overrides):
    if not isinstance(overrides, dict) or set(overrides) - set(FIELDS):
        raise ValueError('Unknown scalp parameter')
    values = asdict(ScalpConfig())
    for key, raw in overrides.items():
        _, low, high, step = FIELDS[key]
        try:
            value = Decimal(str(raw))
            if not value.is_finite() or not Decimal(str(low)) <= value <= Decimal(str(high)):
                raise ValueError('Out of range: '+key)
            if (value-Decimal(str(low))) % Decimal(str(step)):
                raise ValueError('Invalid increment: '+key)
        except (InvalidOperation, TypeError) as exc:
            raise ValueError('Invalid parameter: '+key) from exc
        values[key] = int(value) if step == 1 else float(value)
    if values['minimum_stop_atr'] > values['maximum_stop_atr']:
        raise ValueError('Minimum stop distance exceeds maximum')
    return ScalpConfig(**values)


def field_schema():
    return [dict(key=k,label=v[0],min=v[1],max=v[2],step=v[3]) for k,v in FIELDS.items()]
