"""SignalFlow: a small, readable four-stage trading decision pipeline.

Import the building blocks from here; the stage modules are an implementation detail.
"""
from .entry_timing import EntryTimer, EntryTimingConfig
from .learning import InMemoryOutcomeStore, Learning
from .opportunities import OpportunityConfig, OpportunityDetector
from .pipeline import SignalFlowPipeline
from .risk_management import RiskConfig, RiskManager

__all__ = [
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
