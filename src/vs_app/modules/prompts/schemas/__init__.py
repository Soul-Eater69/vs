"""Pydantic schemas for LLM-generated structured outputs.

One module per output domain; import from this package directly.
"""

from .capability import (
    L2CapabilityItem,
    L2CapabilityResult,
    L3CapabilityItem,
    L3CapabilityResult,
)
from .stage import StageSupportItem, StageSupportResult
from .summary import SummaryOutput
from .theme import ThemeGenerationResult
from .value_stream import (
    InferenceType,
    ReviewPoolPick,
    ReviewPoolPickResult,
    SelectedValueStream,
    SelectionResult,
    VsClassificationItem,
    VsClassificationResult,
    VsVerifierMapping,
    VsVerifierResult,
)

__all__ = [
    # capability
    "L2CapabilityItem",
    "L2CapabilityResult",
    "L3CapabilityItem",
    "L3CapabilityResult",
    # stage
    "StageSupportItem",
    "StageSupportResult",
    # summary
    "SummaryOutput",
    # theme
    "ThemeGenerationResult",
    # value_stream
    "InferenceType",
    "ReviewPoolPick",
    "ReviewPoolPickResult",
    "SelectedValueStream",
    "SelectionResult",
    "VsClassificationItem",
    "VsClassificationResult",
    "VsVerifierMapping",
    "VsVerifierResult",
]
