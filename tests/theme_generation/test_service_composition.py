"""Fake-only tests for the theme_generation service composition.

Composition: Value Streams -> stage generation -> description / business_needs.
No live Azure / Jira / LLM: a fake RAG pipeline_fn drives Value Stream
generation, and a single fake LLM (keyed on the requested output schema) drives
both stage selection and description generation.
"""

from __future__ import annotations

import asyncio

from vs_app.modules.rag.service import ValueStreamRagService
from vs_app.theme_generation import service
from vs_app.theme_generation.service import (
    GeneratedTheme,
    ThemeGenerationRequest,
    ThemeGenerationResult,
)

VS = "Configure, Price, and Quote"
STAGE_CATALOG = {
    VS: {
        "stages": [
            {"name": "Account Configuration"},
            {"name": "Generate Quote and Present to Customer"},
        ]
    }
}


def _fake_rag_service(captured: dict | None = None) -> ValueStreamRagService:
    def pipeline_fn(query: str, **kwargs) -> dict:
        if captured is not None:
            captured.update(kwargs)
        return {
            "selected_value_streams": [
                {
                    "entity_id": "VS-CPQ",
                    "entity_name": VS,
                    "confidence": 0.9,
                    "reason": "Quoting automation.",
                    "selection_source": "llm_pick",
                    "supporting_ticket_ids": ["IDMT-1001"],
                }
            ],
            "candidate_value_streams": [
                {
                    "entity_id": "VS-CPQ",
                    "entity_name": VS,
                    "from_semantic": True,
                    "from_historical": True,
                    "supporting_ticket_ids": ["IDMT-1001"],
                    "historical_reasons": ["Prior CPQ ticket."],
                }
            ],
            "historical_source": "azure",
        }

    return ValueStreamRagService(pipeline_fn=pipeline_fn)


class FakeLLM:
    """generate_structured keyed on the requested output schema."""

    def __init__(self) -> None:
        self.seen_schemas: list[str] = []

    def generate_structured(self, *, query, output_schema, system_prompt=None, reasoning_effort=None):
        name = output_schema.__name__
        self.seen_schemas.append(name)
        if name == "ValueStageSelectionResult":
            return output_schema(
                picks=[{"stage": "Account Configuration", "confidence": 0.88, "reason": "Set up accounts."}]
            )
        if name == "ThemeGenerationResult":
            return output_schema(theme_description="A CPQ theme.", business_needs="Faster quoting.")
        if name == "L2CapabilityResult":
            return output_schema(
                capabilities=[{"capability_name": "Quote Management", "rationale": "Manage quotes.", "confidence": 0.9}]
            )
        if name == "L3CapabilityResult":
            return output_schema(
                capabilities=[
                    {
                        "capability_name": "Quote Versioning",
                        "parent_l2_capability_name": "Quote Management",
                        "rationale": "Track versions.",
                        "confidence": 0.7,
                    }
                ]
            )
        return output_schema()


def _run(request: ThemeGenerationRequest, *, captured: dict | None = None) -> ThemeGenerationResult:
    return asyncio.run(
        service.generate_themes(
            request,
            llm=FakeLLM(),
            stage_catalog=STAGE_CATALOG,
            rag_service=_fake_rag_service(captured),
        )
    )


def test_composes_value_stream_stages_and_description() -> None:
    llm = FakeLLM()
    result = asyncio.run(
        service.generate_themes(
            ThemeGenerationRequest(
                idea_card_text="quote automation idea",
                idmt_title="Improve Prior Authorization",
            ),
            llm=llm,
            stage_catalog=STAGE_CATALOG,
            rag_service=_fake_rag_service(),
        )
    )

    assert isinstance(result, ThemeGenerationResult)
    assert len(result.themes) == 1
    theme = result.themes[0]
    assert isinstance(theme, GeneratedTheme)

    # Value Stream
    assert theme.value_stream.name == VS
    assert theme.value_stream.support_type == "direct"

    # Stages (from the new stage_generation wrapper, validated against catalog)
    assert [s.stage_name for s in theme.stages] == ["Account Configuration"]
    assert theme.stages[0].value_stream_name == VS

    # Description / business needs
    assert theme.theme_description == "A CPQ theme."
    assert theme.business_needs == "Faster quoting."

    # L2 / L3 capabilities
    assert [c.capability_name for c in theme.l2_capabilities] == ["Quote Management"]
    assert [c.capability_name for c in theme.l3_capabilities] == ["Quote Versioning"]
    assert theme.l3_capabilities[0].parent_l2_capability_name == "Quote Management"

    # Title is deterministic (IDMT title + Value Stream name), no LLM call.
    assert theme.theme_title == "Improve Prior Authorization - Configure, Price, and Quote"
    assert "ThemeTitleResult" not in llm.seen_schemas


def test_public_to_dict_nests_agreed_contracts() -> None:
    result = _run(ThemeGenerationRequest(idea_card_text="idea"))
    payload = result.to_dict()

    assert set(payload) == {"themes", "warnings", "debug"}
    theme = payload["themes"][0]
    assert set(theme) == {
        "theme_title",
        "value_stream",
        "stages",
        "theme_description",
        "business_needs",
        "l2_capabilities",
        "l3_capabilities",
    }
    # No idmt_title supplied -> title falls back to the Value Stream name.
    assert theme["theme_title"] == VS
    assert set(theme["l2_capabilities"][0]) == {
        "capability_id",
        "capability_name",
        "rationale",
        "confidence_score",
    }
    assert set(theme["l3_capabilities"][0]) == {
        "capability_id",
        "capability_name",
        "parent_l2_capability_name",
        "rationale",
        "confidence_score",
    }

    # nested Value Stream public contract
    assert set(theme["value_stream"]) == {
        "value_stream_id",
        "value_stream_name",
        "rationale",
        "confidence_score",
        "support_type",
        "historic_idmt_ids",
    }
    # nested stage public contract
    assert set(theme["stages"][0]) == {
        "stage_id",
        "stage_name",
        "value_stream_name",
        "rationale",
        "confidence_score",
        "support_type",
    }
    assert theme["stages"][0]["confidence_score"] == 88
    assert theme["value_stream"]["confidence_score"] == 90


def test_debug_counts_and_top_n_passthrough() -> None:
    captured: dict = {}
    result = _run(
        ThemeGenerationRequest(idea_card_text="idea", top_n_value_streams=7),
        captured=captured,
    )
    assert result.debug == {"value_stream_count": 1, "theme_count": 1}
    # top_n flows through to Value Stream generation.
    assert captured.get("final_output_count") == 7


def test_custom_instruction_warning_surfaces() -> None:
    result = _run(ThemeGenerationRequest(idea_card_text="idea", custom_instruction="prefer billing"))
    assert any("custom_instruction" in w for w in result.warnings)


def test_example_provider_is_used_when_supplied() -> None:
    seen: dict = {}

    def provider(value_stream_name: str) -> list[dict]:
        seen["vs"] = value_stream_name
        return [{"ticket_id": "IDMT-9", "theme_description": "Prior theme.", "business_needs": "Prior needs."}]

    asyncio.run(
        service.generate_themes(
            ThemeGenerationRequest(idea_card_text="idea"),
            llm=FakeLLM(),
            stage_catalog=STAGE_CATALOG,
            rag_service=_fake_rag_service(),
            example_provider=provider,
        )
    )
    assert seen["vs"] == VS


def test_missing_catalog_yields_no_stages_but_still_builds_theme() -> None:
    # No allowed stages -> stage generation returns none, theme still composes.
    result = asyncio.run(
        service.generate_themes(
            ThemeGenerationRequest(idea_card_text="idea"),
            llm=FakeLLM(),
            stage_catalog={},  # no entry for the value stream
            rag_service=_fake_rag_service(),
        )
    )
    theme = result.themes[0]
    assert theme.stages == []
    assert theme.value_stream.name == VS
