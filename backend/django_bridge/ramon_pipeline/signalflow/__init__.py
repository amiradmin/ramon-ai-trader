"""SignalFlow: a small, readable four-stage trading decision pipeline.

Import the building blocks from here; the stage modules are an implementation detail.
"""
from .entry_timing import EntryTimer, EntryTimingConfig
from .learning import InMemoryOutcomeStore, Learning
from .breakout import BreakoutConfig, BreakoutDetector
from .range_reversion import RangeReversionConfig, RangeReversionDetector
from .reversal import ReversalDetector
from .multi_setup import MultiSetupConfig, MultiSetupDetector
from .opportunities import OpportunityConfig, OpportunityDetector
from .pipeline import SignalFlowPipeline
from .risk_management import RiskConfig, RiskManager

__all__ = [
    "BreakoutConfig",
    "BreakoutDetector",
    "RangeReversionConfig",
    "RangeReversionDetector",
    "ReversalDetector",
    "MultiSetupConfig",
    "MultiSetupDetector",
    "EntryTimer",
    "EntryTimingConfig",
    "InMemoryOutcomeStore",
    "Learning",
    "OpportunityConfig",
    "OpportunityDetector",
    "RiskConfig",
    "RiskManager",
    "SignalFlowPipeline",
]
