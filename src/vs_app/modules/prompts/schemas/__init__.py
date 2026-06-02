"""Pydantic schemas for LLM-generated structured outputs.

One module per output domain; import from this package directly.
"""

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
