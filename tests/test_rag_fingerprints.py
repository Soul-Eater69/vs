from __future__ import annotations

from vs_app.modules.rag.fingerprints import build_rag_debug_fingerprints, fingerprint, fingerprint_names


def test_fingerprint_is_stable_for_equivalent_payloads() -> None:
    assert fingerprint({"b": 2, "a": 1}) == fingerprint({"a": 1, "b": 2})


def test_fingerprint_names_uses_entity_name_order() -> None:
    first = fingerprint_names([{"entity_name": "A"}, {"entity_name": "B"}])
    second = fingerprint_names([{"entity_name": "B"}, {"entity_name": "A"}])

    assert first != second


def test_build_rag_debug_fingerprints_includes_counts() -> None:
    debug = build_rag_debug_fingerprints(
        cleaned_query="clean",
        query_for_prompt="prompt",
        semantic_candidates=[{"entity_name": "Semantic"}],
        historical_support=[{"entity_name": "Historical"}],
        merged_candidates=[],
        llm_candidates=[],
        llm_selected=[],
        final_selected=[],
    )

    assert set(debug) == {"fingerprints", "counts"}
    assert debug["counts"]["semantic_candidates"] == 1
    assert debug["counts"]["historical_support"] == 1
