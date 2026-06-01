"""Tests for the Theme-generation POC document builder."""

from __future__ import annotations

from vs_app.ingestion.index_documents.theme_generation_document_builder import (
    ThemeGenerationStage,
    ThemeGenerationThemeInput,
    ThemeGenerationValueStream,
    build_theme_generation_documents,
)

_FORBIDDEN_FLAT_FIELDS = {
    "value_stream_names",
    "value_stream_ids",
    "stage_names",
    "stage_ids",
    "direct_value_stream_names",
    "implied_value_stream_names",
    "direct_stage_names",
    "implied_stage_names",
    "taxonomy_text",
    "source_metadata_json",
}


def _docs(**overrides):
    kwargs = dict(
        ticket_id="IDMT-19761",
        summary_text="Quoting support summary",
        idmt_description="Full IDMT description",
        key_terms=["quote", "pricing"],
        stakeholders=["Sales Ops"],
        systems_and_products=["CPQ"],
        value_streams=[
            ThemeGenerationValueStream(
                group_id="GROUP-1",
                value_stream_id="VS1",
                value_stream_name="Configure, Price, and Quote",
                support_type="direct",
                reason="r1",
                evidence="e1",
            ),
            ThemeGenerationValueStream(
                group_id="GROUP-2",
                value_stream_id="VS2",
                value_stream_name="Manage Leads and opportunities",
                support_type="implied",
                reason="r2",
                evidence="e2",
            ),
        ],
        themes=[
            ThemeGenerationThemeInput(
                group_id="GROUP-1",
                theme_description="CPQ theme",
                business_needs="Need quoting",
                stages=[
                    ThemeGenerationStage(
                        epic_id="EP1",
                        stage_id="ST1",
                        stage_name="Account Configuration",
                        support_type="direct",
                        reason="sr1",
                        evidence="se1",
                    )
                ],
            ),
            ThemeGenerationThemeInput(
                group_id="GROUP-2",
                theme_description="Leads theme",
                business_needs="Need leads",
                stages=[
                    ThemeGenerationStage(
                        epic_id="EP2",
                        stage_id="ST2",
                        stage_name="Perform Outreach to Leads and Prospects",
                        support_type="implied",
                    )
                ],
            ),
        ],
    )
    kwargs.update(overrides)
    return build_theme_generation_documents(**kwargs)


def test_idmt_doc_id_is_deterministic() -> None:
    idmt = _docs()[0]
    assert idmt["id"] == "idmt::IDMT-19761"
    assert idmt["document_type"] == "idmt"


def test_theme_doc_id_is_deterministic() -> None:
    themes = _docs()[1:]
    assert [doc["id"] for doc in themes] == [
        "theme::IDMT-19761::GROUP-1",
        "theme::IDMT-19761::GROUP-2",
    ]
    assert all(doc["document_type"] == "theme" for doc in themes)


def test_one_idmt_plus_n_theme_docs() -> None:
    docs = _docs()
    assert len(docs) == 3  # 1 IDMT + 2 themes
    assert docs[0]["document_type"] == "idmt"
    assert [d["document_type"] for d in docs[1:]] == ["theme", "theme"]


def test_idmt_doc_contains_all_value_streams_with_full_fields() -> None:
    idmt = _docs()[0]
    vs = idmt["properties"]["value_streams"]
    assert [row["group_id"] for row in vs] == ["GROUP-1", "GROUP-2"]
    assert vs[0] == {
        "group_id": "GROUP-1",
        "value_stream_id": "VS1",
        "value_stream_name": "Configure, Price, and Quote",
        "support_type": "direct",
        "reason": "r1",
        "evidence": "e1",
    }


def test_idmt_doc_has_empty_stages_and_theme_fields() -> None:
    props = _docs()[0]["properties"]
    assert props["stages"] == []
    assert props["theme_description"] == ""
    assert props["business_needs"] == ""
    assert _docs()[0]["group_id"] == ""


def test_theme_doc_contains_one_matching_value_stream() -> None:
    theme_g2 = _docs()[2]
    vs = theme_g2["properties"]["value_streams"]
    assert len(vs) == 1
    assert vs[0]["group_id"] == "GROUP-2"
    assert vs[0]["value_stream_name"] == "Manage Leads and opportunities"


def test_theme_doc_contains_stages() -> None:
    theme_g1 = _docs()[1]
    stages = theme_g1["properties"]["stages"]
    assert len(stages) == 1
    assert stages[0] == {
        "epic_id": "EP1",
        "stage_id": "ST1",
        "stage_name": "Account Configuration",
        "support_type": "direct",
        "reason": "sr1",
        "evidence": "se1",
    }


def test_theme_docs_have_no_content_vector() -> None:
    for theme in _docs()[1:]:
        assert "content_vector" not in theme


def test_idmt_doc_accepts_optional_content_vector() -> None:
    without = _docs()[0]
    assert "content_vector" not in without

    with_vector = _docs(idmt_content_vector=[0.1, 0.2, 0.3])[0]
    assert with_vector["content_vector"] == [0.1, 0.2, 0.3]


def test_idmt_content_built_from_idmt_fields() -> None:
    content = _docs()[0]["content"]
    assert "Quoting support summary" in content
    assert "Full IDMT description" in content
    assert "Key Terms: quote, pricing" in content
    assert "Stakeholders: Sales Ops" in content
    assert "Systems & Products: CPQ" in content
    assert "Configure, Price, and Quote" in content


def test_theme_content_built_from_theme_and_parent_context() -> None:
    theme_g1 = _docs()[1]["content"]
    assert "Value Stream: Configure, Price, and Quote" in theme_g1
    assert "CPQ theme" in theme_g1
    assert "Business Needs: Need quoting" in theme_g1
    assert "Stages: Account Configuration" in theme_g1
    # parent IDMT context
    assert "IDMT Summary: Quoting support summary" in theme_g1
    assert "IDMT Description: Full IDMT description" in theme_g1


def test_no_flat_duplicate_taxonomy_fields() -> None:
    for doc in _docs():
        assert _FORBIDDEN_FLAT_FIELDS.isdisjoint(doc.keys())
        assert _FORBIDDEN_FLAT_FIELDS.isdisjoint(doc["properties"].keys())


def test_missing_optional_fields_become_empty() -> None:
    docs = build_theme_generation_documents(ticket_id="  IDMT-1  ")
    assert len(docs) == 1
    idmt = docs[0]
    assert idmt["id"] == "idmt::IDMT-1"  # whitespace stripped
    props = idmt["properties"]
    assert props["summary_text"] == ""
    assert props["idmt_description"] == ""
    assert props["key_terms"] == []
    assert props["stakeholders"] == []
    assert props["systems_and_products"] == []
    assert props["value_streams"] == []
    assert props["stages"] == []
    assert idmt["content"] == ""
    assert "content_vector" not in idmt


def test_invalid_support_type_is_blanked_but_row_kept() -> None:
    docs = build_theme_generation_documents(
        ticket_id="IDMT-2",
        value_streams=[
            ThemeGenerationValueStream(
                group_id="GROUP-9",
                value_stream_name="Some VS",
                support_type="weak_broad",
            )
        ],
        themes=[
            ThemeGenerationThemeInput(
                group_id="GROUP-9",
                stages=[ThemeGenerationStage(stage_name="Some Stage", support_type="???")],
            )
        ],
    )
    idmt_vs = docs[0]["properties"]["value_streams"][0]
    assert idmt_vs["value_stream_name"] == "Some VS"
    assert idmt_vs["support_type"] == ""  # invalid -> blanked, row kept
    theme_stage = docs[1]["properties"]["stages"][0]
    assert theme_stage["stage_name"] == "Some Stage"
    assert theme_stage["support_type"] == ""


def test_theme_without_matching_value_stream_gets_empty_value_streams() -> None:
    docs = build_theme_generation_documents(
        ticket_id="IDMT-3",
        value_streams=[
            ThemeGenerationValueStream(group_id="GROUP-1", value_stream_name="VS One")
        ],
        themes=[
            ThemeGenerationThemeInput(
                group_id="GROUP-404",
                theme_description="Orphan theme",
                business_needs="Still useful history",
                stages=[ThemeGenerationStage(stage_name="Kept Stage")],
            )
        ],
    )
    theme = docs[1]
    assert theme["properties"]["value_streams"] == []
    assert [s["stage_name"] for s in theme["properties"]["stages"]] == ["Kept Stage"]
