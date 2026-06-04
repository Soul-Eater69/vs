"""Tests for deterministic theme-title construction.

The title is built from the IDMT ticket title + Value Stream name — no LLM, no
prompt, no network. These tests take no ``llm`` at all, proving title generation
never calls a model.
"""

from __future__ import annotations

from vs_app.theme_generation.title import build_theme_title


def test_title_joins_idmt_and_value_stream() -> None:
    assert (
        build_theme_title(
            idmt_title="Improve Prior Authorization",
            value_stream_name="Manage Utilization Management Program",
        )
        == "Improve Prior Authorization - Manage Utilization Management Program"
    )


def test_missing_idmt_title_returns_value_stream() -> None:
    assert build_theme_title(idmt_title="", value_stream_name="Order to Cash") == "Order to Cash"


def test_missing_value_stream_returns_idmt_title() -> None:
    assert build_theme_title(idmt_title="Improve Prior Authorization", value_stream_name="") == (
        "Improve Prior Authorization"
    )


def test_both_missing_returns_empty() -> None:
    assert build_theme_title(idmt_title="", value_stream_name="") == ""


def test_whitespace_is_normalized() -> None:
    assert build_theme_title(idmt_title="  Improve   PA ", value_stream_name=" Order  to Cash ") == (
        "Improve PA - Order to Cash"
    )
