"""API-facing facade for runtime generation.

A thin entry point the API (and other callers) use instead of reaching into the
generation packages directly. It exposes:

- ``generate_value_streams`` — runtime Value Stream candidates only.
- ``generate_themes`` — the full composition: Value Streams -> stage generation
  -> theme description / business-needs, one Theme per selected Value Stream.

Titles / L2 / L3 are intentionally not part of this composition yet.
"""

from __future__ import annotations

from typing import Any, Callable

from vs_app.modules.rag.service import ValueStreamRagService
from vs_app.modules.stages.stage_catalog import get_allowed_stages
from vs_app.theme_generation.capabilities import (
    generate_l2_capabilities,
    generate_l3_capabilities,
)
from vs_app.theme_generation.business_needs import generate_business_needs
from vs_app.theme_generation.descriptions import generate_theme_description
from vs_app.theme_generation.title import build_theme_title
from vs_app.theme_generation.models import (
    GeneratedL2Capability,
    GeneratedL3Capability,
    GeneratedTheme,
    ThemeGenerationRequest,
    ThemeGenerationResult,
)
from vs_app.stage_generation.generator import generate_stages
from vs_app.stage_generation.models import StageGenerationRequest
from vs_app.value_stream_generation.generator import generate_value_streams as _generate
from vs_app.value_stream_generation.models import (
    GeneratedValueStream,
    ValueStreamGenerationRequest,
    ValueStreamGenerationResult,
)

# Provides historic theme examples (theme_description / business_needs) for a
# Value Stream name. Stage details are never part of these examples.
ExampleProvider = Callable[[str], list[dict]]


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


async def generate_themes(
    request: ThemeGenerationRequest,
    *,
    llm: Any | None = None,
    stage_catalog: dict | None = None,
    rag_service: ValueStreamRagService | None = None,
    example_provider: ExampleProvider | None = None,
    generate_capabilities: bool = True,
) -> ThemeGenerationResult:
    """Compose one Theme per selected Value Stream.

    Flow: Value Stream generation -> stage generation (per Value Stream) ->
    theme description / business-needs -> L2/L3 capabilities -> theme title. The
    title is built deterministically (IDMT title + Value Stream name); it does not
    use the llm. Each dependency is injectable so this is fully testable with
    fakes:
    - ``rag_service`` drives Value Stream generation,
    - ``llm`` drives stage selection, description, and capability generation,
    - ``stage_catalog`` supplies the allowed stage dropdown per Value Stream,
    - ``example_provider`` optionally supplies historic theme examples (no stage
      context); when omitted, generation proceeds without historic examples,
    - ``generate_capabilities`` toggles L2/L3 capability generation (default on).
    """
    catalog = stage_catalog or {}

    vs_result = await generate_value_streams(
        ValueStreamGenerationRequest(
            idea_card_text=request.idea_card_text,
            ticket_id=request.ticket_id,
            top_n=request.top_n_value_streams,
            custom_instruction=request.custom_instruction,
        ),
        rag_service=rag_service,
    )

    idea_context = request.idea_card_text or ""
    themes: list[GeneratedTheme] = []
    for value_stream in vs_result.value_streams:
        theme = _theme_for_value_stream(
            value_stream=value_stream,
            idea_context=idea_context,
            generated_summary=request.generated_summary,
            idmt_title=request.idmt_title,
            catalog=catalog,
            llm=llm,
            example_provider=example_provider,
            generate_capabilities=generate_capabilities,
        )
        themes.append(theme)

    debug = {
        "value_stream_count": len(vs_result.value_streams),
        "theme_count": len(themes),
    }
    return ThemeGenerationResult(
        themes=themes,
        warnings=list(vs_result.warnings),
        debug=debug,
    )


def _theme_for_value_stream(
    *,
    value_stream: GeneratedValueStream,
    idea_context: str,
    generated_summary: str,
    idmt_title: str,
    catalog: dict,
    llm: Any | None,
    example_provider: ExampleProvider | None,
    generate_capabilities: bool,
) -> GeneratedTheme:
    allowed_stages = get_allowed_stages(value_stream.name, catalog)

    # Stage prediction is summary-only: pass the generated summary, never the
    # idea card body / description.
    stage_result = generate_stages(
        StageGenerationRequest(
            value_stream_name=value_stream.name,
            allowed_stages=allowed_stages,
            generated_summary=generated_summary,
        ),
        llm=llm,
    )

    selected_stages = [
        {"stage": stage.stage_name, "confidence": stage.confidence, "reason": stage.rationale}
        for stage in stage_result.stages
    ]
    examples = example_provider(value_stream.name) if example_provider else []

    # Theme Description and Business Needs are independent LLM calls with
    # different Jira formats. Description first, then business needs.
    description_result = generate_theme_description(
        idea_context=idea_context,
        value_stream_name=value_stream.name,
        allowed_stages=allowed_stages,
        selected_stages=selected_stages,
        examples=examples,
        llm=llm,
    )
    theme_description = str(description_result.get("theme_description") or "").strip()

    business_needs_result = generate_business_needs(
        idea_context=idea_context,
        value_stream_name=value_stream.name,
        selected_stages=selected_stages,
        llm=llm,
    )
    business_needs = str(business_needs_result.get("business_needs") or "").strip()

    warnings = (
        list(stage_result.warnings)
        + list(description_result.get("warnings") or [])
        + list(business_needs_result.get("warnings") or [])
    )

    l2_capabilities = []
    l3_capabilities = []
    if generate_capabilities:
        # L2 first, then L3 with the generated L2 capabilities as parent context.
        l2_capabilities, l2_warnings = generate_l2_capabilities(
            idea_context=idea_context,
            value_stream_name=value_stream.name,
            selected_stages=selected_stages,
            theme_description=theme_description,
            business_needs=business_needs,
            examples=examples,
            llm=llm,
        )
        warnings.extend(l2_warnings)
        l3_capabilities, l3_warnings = generate_l3_capabilities(
            idea_context=idea_context,
            value_stream_name=value_stream.name,
            selected_stages=selected_stages,
            theme_description=theme_description,
            business_needs=business_needs,
            l2_capabilities=l2_capabilities,
            llm=llm,
        )
        warnings.extend(l3_warnings)

    # Deterministic title: IDMT ticket title + Value Stream name (no llm).
    theme_title = build_theme_title(
        idmt_title=idmt_title,
        value_stream_name=value_stream.name,
    )

    return GeneratedTheme(
        value_stream=value_stream,
        stages=stage_result.stages,
        theme_title=theme_title,
        theme_description=theme_description,
        business_needs=business_needs,
        l2_capabilities=l2_capabilities,
        l3_capabilities=l3_capabilities,
        warnings=_dedupe(warnings),
    )


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


__all__ = [
    "generate_value_streams",
    "generate_themes",
    "GeneratedValueStream",
    "ValueStreamGenerationRequest",
    "ValueStreamGenerationResult",
    "ThemeGenerationRequest",
    "GeneratedTheme",
    "GeneratedL2Capability",
    "GeneratedL3Capability",
    "ThemeGenerationResult",
]
