"""Research-only external advisor registry for Ramon.

Nothing in this module can place trades or alter the live EA decision.  It exists
to make third-party research candidates explicit, auditable and shadow-only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


Status = Literal["DATASET", "FEATURE_SOURCE", "SHADOW_ONLY", "REFERENCE_ONLY", "REJECTED_FOR_DIRECT_LOAD"]


@dataclass(frozen=True, slots=True)
class ResearchCandidate:
    key: str
    source: str
    status: Status
    timeframe: str
    use: str
    artifact_policy: str
    notes: str


CANDIDATES = (
    ResearchCandidate(
        "kaggle_xauusd_history",
        "Kaggle XAUUSD historical archive",
        "DATASET",
        "M15",
        "Historical benchmark / walk-forward research",
        "CSV/ZIP data only; imported under XAUUSD_KAGGLE",
        "Already supported by ramon.kaggle_history; never mix with LiteFinance execution history.",
    ),
    ResearchCandidate(
        "hf_smc_v2",
        "JonusNattapong/xauusd-trading-ai-smc-v2",
        "FEATURE_SOURCE",
        "M15",
        "Reproduce technical/SMC features locally, then benchmark as a shadow advisor",
        "Do not automatically load remote pickle/joblib artifacts",
        "Published M15 feature importance is dominated by SMA/EMA/Bollinger/OHLC; published FVG/OB features have zero importance.",
    ),
    ResearchCandidate(
        "hf_romeo_v8",
        "JonusNattapong/romeo-v8-super-ensemble-trading-ai",
        "REFERENCE_ONLY",
        "mixed",
        "Architecture reference for stacking, calibration, dynamic weighting and consensus",
        "No direct execution; independently reproduce components and validate on Ramon folds",
        "Use the ensemble design as inspiration, not its self-reported performance as evidence.",
    ),
    ResearchCandidate(
        "hf_ppo_gold",
        "JonusNattapong/Reinforcement-Learning-for-Gold-Trading-Model",
        "SHADOW_ONLY",
        "M15",
        "Future RL shadow advisor after observation/action schema reproduction",
        "Require exact environment and VecNormalize schema validation before model loading",
        "Stable-Baselines3 PPO artifact includes normalization state; incompatible feature order would invalidate inference.",
    ),
    ResearchCandidate(
        "chartick_xauusd",
        "chartick.ai/markets/xau-usd",
        "REFERENCE_ONLY",
        "multi",
        "Manual external trend/regime comparison only",
        "No scraping dependency in live decision path",
        "Useful as an occasional external sanity check, not as a production signal dependency.",
    ),
    ResearchCandidate(
        "deriv_tradersview_gold",
        "tradersview.deriv.com instrument explorer",
        "REFERENCE_ONLY",
        "multi",
        "Manual external market-context comparison",
        "No scraping dependency in live decision path",
        "Keep external website availability out of trading execution.",
    ),
    ResearchCandidate(
        "ctrader_xauusd_robots",
        "cTrader Store XAUUSD robots",
        "REFERENCE_ONLY",
        "mixed",
        "Feature/risk-control idea mining only",
        "Never import vendor performance claims as validation",
        "ATR/EMA/VWAP/ADX/session/breakout patterns are research prompts, not evidence of edge.",
    ),
)


def registry() -> list[dict[str, str]]:
    return [asdict(candidate) for candidate in CANDIDATES]


def candidate(key: str) -> ResearchCandidate:
    for item in CANDIDATES:
        if item.key == key:
            return item
    raise KeyError(key)
