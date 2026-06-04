"""API-facing facade for runtime generation.

A thin entry point the API (and other callers) use instead of reaching into the
generation packages directly. In this phase it exposes Value Stream generation
only; stage selection and theme-description generation compose here in later
phases without changing this signature shape.
"""

from __future__ import annotations

from vs_app.modules.rag.service import ValueStreamRagService
from vs_app.value_stream_generation.generator import generate_value_streams as _generate
from vs_app.value_stream_generation.models import (
    GeneratedValueStream,
    ValueStreamGenerationRequest,
    ValueStreamGenerationResult,
)


async def generate_value_streams(
    request: ValueStreamGenerationRequest,
    *,
    rag_service: ValueStreamRagService | None = None,
) -> ValueStreamGenerationResult:
    """Generate runtime Value Streams for a new IDMT request.

    Delegates to the value_stream_generation generator. ``rag_service`` is
    injectable for tests / wiring; the generator defaults to a real service.
    """
    return await _generate(request, service=rag_service)


__all__ = [
    "generate_value_streams",
    "GeneratedValueStream",
    "ValueStreamGenerationRequest",
    "ValueStreamGenerationResult",
]
