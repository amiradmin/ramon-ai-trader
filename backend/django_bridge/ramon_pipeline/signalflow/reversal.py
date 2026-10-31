"""Reserved reversal detector interface; intentionally non-trading."""
from typing import Any,Mapping
from ._detector_utils import result

class ReversalDetector:
    def evaluate(self,market:Mapping[str,Any])->dict[str,Any]:
        return result("DEFERRED","NOT_IMPLEMENTED",detail="reversal detection is intentionally not implemented")
