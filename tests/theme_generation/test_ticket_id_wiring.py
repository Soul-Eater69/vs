"""Fake-only tests for runtime ticket_id wiring in generate_themes.

No live Jira/client construction, no Azure/LLM. A fake duck-typed Jira client +
fake summarizer + fake RAG/LLM exercise the resolution flow, and we prove the raw
idea-card body never reaches stage prediction (only the summarizer output does).
"""

from __future__ import annotations

import asyncio

from vs_app.modules.rag.service import ValueStreamRagService
from vs_app.theme_generation import service
from vs_app.theme_generation.service import ThemeGenerationRequest

VS = "Manage Utilization Management Program"  # not in the foundational-stage map
CATALOG = {VS: {"stages": [{"name": "Manage UM Operations"}]}}

# Raw idea-card body carries a sentinel that must never reach stage prediction.
IDEA_CARD_BODY = "RAW IDEA CARD BODY [RAW-SENTINEL] covering prior auth and quoting."
SUMMARY_TOKEN = "STAGE-SUMMARY-TOKEN about prior authorization."


def _fake_rag() -> ValueStreamRagService:
    def pipeline_fn(query, **kwargs):
        return {
            "selected_value_streams": [
                {"entity_id": "VS-UM", "entity_name": VS, "confidence": 0.9,
                 "reason": "Prior auth is central to UM.", "selection_source": "llm_pick",
                 "supporting_ticket_ids": ["IDMT-1"]},
            ],
            "candidate_value_streams": [
                {"entity_id": "VS-UM", "entity_name": VS, "from_semantic": True, "from_historical": True,
                 "supporting_ticket_ids": ["IDMT-1"], "historical_reasons": ["prior"]},
            ],
            "historical_source": "azure",
        }

    return ValueStreamRagService(pipeline_fn=pipeline_fn)


class FakeLLM:
    def __init__(self) -> None:
        self.stage_query = ""
        self.description_query = ""

    def generate_structured(self, *, query, output_schema, system_prompt=None, reasoning_effort=None):
        name = output_schema.__name__
        if name == "ValueStageSelectionResult":
            self.stage_query = query
            return output_schema(
                picks=[{"stage": "Manage UM Operations", "confidence": 0.9, "reason": "r", "support": "direct"}]
            )
        if name == "ThemeDescriptionResult":
            self.description_query = query
            return output_schema(theme_description="desc")
        if name == "BusinessNeedsResult":
            return output_schema(business_needs="needs")
        if name == "L2CapabilityResult":
            return output_schema(capabilities=[{"capability_name": "Cap", "rationale": "r", "confidence": 0.8}])
        if name == "L3CapabilityResult":
            return output_schema(
                capabilities=[{"capability_name": "Sub", "parent_l2_capability_name": "Cap", "rationale": "r", "confidence": 0.7}]
            )
        return output_schema()


class FakeJiraClient:
    """Duck-typed client with an idea-card attachment; records calls."""

    def __init__(self, idea_card_text=IDEA_CARD_BODY) -> None:
        self._idea_card_text = idea_card_text
        self.calls: list[str] = []

    def get_issue(self, ticket_id):
        self.calls.append("get_issue")
        return {"fields": {"summary": "Ticket Title From Jira", "description": "D"}}

    def get_attachments(self, ticket_id):
        return [{"id": "ic", "filename": "Idea Card.docx"}, {"id": "x", "filename": "spec.pdf"}]

    def get_attachment_text(self, attachment):
        return self._idea_card_text if attachment.get("id") == "ic" else "OTHER SPEC TEXT"

    def get_linked_issues(self, ticket_id):
        return []

    def get_child_epics(self, group_id):
        return []


class FakeSummarizer:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    def __call__(self, text):
        self.inputs.append(text)
        return SUMMARY_TOKEN


def _run(request, *, llm=None, jira_client=None, summarizer=None, idmt_extractor=None):
    kwargs = dict(llm=llm or FakeLLM(), stage_catalog=CATALOG, rag_service=_fake_rag(),
                  jira_client=jira_client, summarizer=summarizer)
    if idmt_extractor is not None:
        kwargs["idmt_extractor"] = idmt_extractor
    return asyncio.run(service.generate_themes(request, **kwargs))


def test_ticket_id_resolves_summarizes_and_routes_summary_to_stages() -> None:
    llm = FakeLLM()
    summarizer = FakeSummarizer()
    client = FakeJiraClient()
    result = _run(
        ThemeGenerationRequest(ticket_id="IDMT-9"),
        llm=llm, jira_client=client, summarizer=summarizer,
    )

    # extractor was used
    assert "get_issue" in client.calls
    assert result.debug["ticket_resolved"] is True
    # summarizer received the authoritative idea-card text
    assert any("[RAW-SENTINEL]" in text for text in summarizer.inputs)
    # generated_summary (summarizer output) reached stage prediction
    assert SUMMARY_TOKEN in llm.stage_query
    # raw idea-card body did NOT reach stage prediction
    assert "[RAW-SENTINEL]" not in llm.stage_query
    # but the idea-card body IS used by the description (idea context)
    assert "[RAW-SENTINEL]" in llm.description_query
    # idmt_title filled from the Jira ticket title
    assert result.themes[0].theme_title == f"Ticket Title From Jira - {VS}"


def test_caller_generated_summary_wins_over_summarizer() -> None:
    llm = FakeLLM()
    summarizer = FakeSummarizer()
    _run(
        ThemeGenerationRequest(ticket_id="IDMT-9", generated_summary="CALLER-SUMMARY"),
        llm=llm, jira_client=FakeJiraClient(), summarizer=summarizer,
    )
    assert "CALLER-SUMMARY" in llm.stage_query
    assert SUMMARY_TOKEN not in llm.stage_query
    # summarizer never invoked because the caller already provided the summary
    assert summarizer.inputs == []


def test_missing_summarizer_warns_and_no_raw_text_to_stages() -> None:
    llm = FakeLLM()
    result = _run(
        ThemeGenerationRequest(ticket_id="IDMT-9"),
        llm=llm, jira_client=FakeJiraClient(), summarizer=None,
    )
    assert any("summarizer not provided" in w for w in result.warnings)
    # generated_summary stays empty; the raw idea-card body never reaches stages
    assert "[RAW-SENTINEL]" not in llm.stage_query


def test_missing_ticket_id_does_not_call_extractor() -> None:
    client = FakeJiraClient()
    summarizer = FakeSummarizer()
    result = _run(
        ThemeGenerationRequest(idea_card_text="direct idea", generated_summary="direct summary"),
        jira_client=client, summarizer=summarizer,
    )
    assert client.calls == []
    assert summarizer.inputs == []
    assert result.debug["ticket_resolved"] is False


def test_extractor_failure_is_lenient() -> None:
    def boom_extractor(*, ticket_id, client):
        raise RuntimeError("extractor exploded")

    result = _run(
        ThemeGenerationRequest(ticket_id="IDMT-9"),
        jira_client=object(), summarizer=FakeSummarizer(), idmt_extractor=boom_extractor,
    )
    assert any("ticket extraction failed" in w for w in result.warnings)
    # did not crash; still produced output
    assert len(result.themes) == 1


def test_summarizer_object_with_summarize_method() -> None:
    class ObjSummarizer:
        def summarize(self, text):
            return "OBJ-SUMMARY"

    llm = FakeLLM()
    _run(
        ThemeGenerationRequest(ticket_id="IDMT-9"),
        llm=llm, jira_client=FakeJiraClient(), summarizer=ObjSummarizer(),
    )
    assert "OBJ-SUMMARY" in llm.stage_query


def test_full_output_still_produced_via_ticket_id() -> None:
    result = _run(
        ThemeGenerationRequest(ticket_id="IDMT-9"),
        jira_client=FakeJiraClient(), summarizer=FakeSummarizer(),
    )
    theme = result.themes[0]
    assert theme.value_stream.name == VS
    assert [s.stage_name for s in theme.stages] == ["Manage UM Operations"]
    assert theme.theme_description == "desc"
    assert theme.business_needs == "needs"
    assert [c.capability_name for c in theme.l2_capabilities] == ["Cap"]
    assert [c.capability_name for c in theme.l3_capabilities] == ["Sub"]
