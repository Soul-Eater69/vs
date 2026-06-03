"""Compatibility tests: old ingestion import path must still resolve to the new
``vs_app.theme_generation`` package (same function/class objects)."""

from __future__ import annotations


def test_orchestrator_shim_is_new_path() -> None:
    from vs_app.ingestion.theme_generation.orchestrator import (
        generate_themes_for_idea as old,
    )
    from vs_app.theme_generation.orchestrator import generate_themes_for_idea as new

    assert old is new


def test_generate_theme_for_value_stream_shim_is_new_path() -> None:
    from vs_app.ingestion.theme_generation.orchestrator import (
        generate_theme_for_value_stream as old,
    )
    from vs_app.theme_generation.orchestrator import (
        generate_theme_for_value_stream as new,
    )

    assert old is new


def test_generation_shim_points_to_descriptions() -> None:
    from vs_app.ingestion.theme_generation.generation import (
        generate_theme_description as old,
    )
    from vs_app.theme_generation.descriptions import generate_theme_description as new

    assert old is new


def test_retrieval_shim_is_new_path() -> None:
    from vs_app.ingestion.theme_generation.retrieval import search_idmt_examples as old
    from vs_app.theme_generation.retrieval import search_idmt_examples as new

    assert old is new


def test_search_adapter_shim_is_new_path() -> None:
    from vs_app.ingestion.theme_generation.search_adapter import (
        ThemeGenerationSearchAdapter as old,
    )
    from vs_app.theme_generation.search_adapter import (
        ThemeGenerationSearchAdapter as new,
    )

    assert old is new


def test_package_init_shim_reexports_new_path() -> None:
    from vs_app import theme_generation as new_pkg
    from vs_app.ingestion import theme_generation as old_pkg

    assert old_pkg.generate_themes_for_idea is new_pkg.generate_themes_for_idea
    assert old_pkg.generate_theme_description is new_pkg.generate_theme_description
    assert old_pkg.ThemeGenerationSearchAdapter is new_pkg.ThemeGenerationSearchAdapter
