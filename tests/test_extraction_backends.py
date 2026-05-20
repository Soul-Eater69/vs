from __future__ import annotations

import builtins
from pathlib import Path
import sys
import types

from vs_app.modules.rag.extraction.backends import (
    _render_unstructured_elements,
    extract_uploaded_file_text,
    extracted_rag_text,
    render_extraction_debug,
)


def test_current_backend_routes_by_extension(monkeypatch) -> None:
    calls: list[str] = []

    def fake_pptx(file_bytes: bytes, *, max_slides: int = 60) -> dict:
        calls.append(f"pptx:{max_slides}")
        return {"chunks": [{"text": "pptx slide text", "has_table": False}]}

    def fake_pdf(file_bytes: bytes, *, ocr_enabled: bool = False, max_pages: int = 50) -> dict:
        calls.append(f"pdf:{ocr_enabled}:{max_pages}")
        return {"chunks": [{"text": "pdf page text", "has_table": True}]}

    def fake_docx(file_bytes: bytes) -> dict:
        calls.append("docx")
        return {"chunks": [{"text": "docx section text", "has_table": False}]}

    monkeypatch.setattr("vs_app.integrations.files.pptx_extractor.extract_pptx", fake_pptx)
    monkeypatch.setattr("vs_app.integrations.files.pdf_extractor.extract_pdf", fake_pdf)
    monkeypatch.setattr("vs_app.integrations.files.docx_extractor.extract_docx", fake_docx)

    pptx = extract_uploaded_file_text(b"deck", "sample.pptx", backend="current")
    pdf = extract_uploaded_file_text(b"pdf", "sample.pdf", backend="current")
    docx = extract_uploaded_file_text(b"doc", "sample.docx", backend="current")

    assert pptx["backend"] == "current"
    assert "pptx slide text" in pptx["text"]
    assert pdf["metadata"]["tables_detected"] == 1
    assert "docx section text" in docx["markdown"]
    assert calls == ["pptx:60", "pdf:False:50", "docx"]


def test_extracted_rag_text_returns_text_for_current_backend() -> None:
    result = {
        "backend": "current",
        "text": "clean joined text",
        "markdown": "[Slide]\nstructured markdown",
    }

    assert extracted_rag_text(result) == "clean joined text"


def test_extracted_rag_text_returns_markdown_for_unstructured_backend() -> None:
    result = {
        "backend": "unstructured",
        "text": "clean joined text",
        "markdown": "[NarrativeText | page 1]\nclean joined text",
    }

    assert extracted_rag_text(result) == "[NarrativeText | page 1]\nclean joined text"


def test_extracted_rag_text_falls_back_to_text_for_unstructured_without_markdown() -> None:
    result = {
        "backend": "unstructured",
        "text": "clean joined text",
        "markdown": "",
    }

    assert extracted_rag_text(result) == "clean joined text"


def test_extracted_rag_text_returns_markdown_when_auto_selected_unstructured() -> None:
    result = {
        "backend": "unstructured",
        "text": "auto selected plain text",
        "markdown": "[Title | page 1]\nauto selected plain text",
    }

    assert extracted_rag_text(result) == "[Title | page 1]\nauto selected plain text"


def test_render_extraction_debug_reports_rag_input_kind_and_lengths() -> None:
    result = {
        "backend": "unstructured",
        "filename": "sample.pdf",
        "text": "plain text",
        "markdown": "[NarrativeText | page 1]\nplain text",
        "metadata": {"chars": 10, "words": 2},
    }

    debug = render_extraction_debug(result, backend_requested="auto")

    assert debug["rag_input_kind"] == "markdown"
    assert debug["text_chars"] == len("plain text")
    assert debug["markdown_chars"] == len("[NarrativeText | page 1]\nplain text")


def test_unstructured_backend_handles_import_error_gracefully(monkeypatch) -> None:
    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "unstructured.partition.auto":
            raise ImportError("not installed")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(
        "vs_app.integrations.files.pdf_extractor.extract_pdf",
        lambda file_bytes, *, ocr_enabled=False, max_pages=50: {
            "chunks": [{"text": "fallback current pdf text"}]
        },
    )

    result = extract_uploaded_file_text(b"pdf", "sample.pdf", backend="unstructured")

    assert result["backend"] == "current"
    assert result["text"] == "fallback current pdf text"
    assert any("unstructured is not installed" in warning for warning in result["metadata"]["warnings"])


def test_unstructured_backend_passes_closed_temp_file_to_partition(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_partition(filename: str) -> list[FakeElement]:
        tmp_path = Path(filename)
        captured["exists_during_partition"] = tmp_path.exists()
        captured["name"] = tmp_path.name
        captured["suffix"] = tmp_path.suffix
        captured["bytes"] = tmp_path.read_bytes()
        return [FakeElement("NarrativeText", "temp body", page_number=1)]

    _install_fake_unstructured(monkeypatch, fake_partition)

    result = extract_uploaded_file_text(
        b"fake pdf bytes",
        "nested/sample.pdf",
        backend="unstructured",
        clean=False,
    )

    assert captured["exists_during_partition"] is True
    assert captured["name"] == "sample.pdf"
    assert captured["suffix"] == ".pdf"
    assert captured["bytes"] == b"fake pdf bytes"
    assert result["backend"] == "unstructured"
    assert result["filename"] == "sample.pdf"
    assert "[NarrativeText | page 1]" in result["markdown"]
    assert result["text"] == "temp body"


def test_unstructured_backend_falls_back_to_current_when_partition_fails(monkeypatch) -> None:
    def raise_partition(filename: str) -> list[FakeElement]:
        raise RuntimeError("parser exploded")

    _install_fake_unstructured(monkeypatch, raise_partition)
    monkeypatch.setattr(
        "vs_app.integrations.files.pdf_extractor.extract_pdf",
        lambda file_bytes, *, ocr_enabled=False, max_pages=50: {
            "chunks": [{"text": "fallback current pdf text"}]
        },
    )

    result = extract_uploaded_file_text(b"pdf", "sample.pdf", backend="unstructured")

    assert result["backend"] == "current"
    assert result["text"] == "fallback current pdf text"
    assert any(
        "unstructured failed; fell back to current extractor: RuntimeError: parser exploded"
        in warning
        for warning in result["metadata"]["warnings"]
    )


def test_auto_backend_keeps_current_when_unstructured_partition_fails(monkeypatch) -> None:
    def raise_partition(filename: str) -> list[FakeElement]:
        raise RuntimeError("pdfinfo missing")

    _install_fake_unstructured(monkeypatch, raise_partition)
    monkeypatch.setattr(
        "vs_app.integrations.files.pdf_extractor.extract_pdf",
        lambda file_bytes, *, ocr_enabled=False, max_pages=50: {
            "chunks": [{"text": "tiny"}]
        },
    )

    result = extract_uploaded_file_text(b"pdf", "sample.pdf", backend="auto")

    assert result["backend"] == "current"
    assert result["text"] == "tiny"
    assert any("auto backend kept current extraction" in warning for warning in result["metadata"]["warnings"])
    assert any(
        "unstructured failed; fell back to current extractor: RuntimeError: pdfinfo missing"
        in warning
        for warning in result["metadata"]["warnings"]
    )


def test_auto_backend_falls_back_to_unstructured_when_current_text_is_weak(monkeypatch) -> None:
    monkeypatch.setattr(
        "vs_app.integrations.files.pptx_extractor.extract_pptx",
        lambda file_bytes, *, max_slides=60: {"chunks": [{"text": "tiny"}]},
    )
    _install_fake_unstructured(monkeypatch, [FakeElement("NarrativeText", "better extracted text")])

    result = extract_uploaded_file_text(b"deck", "sample.pptx", backend="auto")

    assert result["backend"] == "unstructured"
    assert result["text"] == "better extracted text"
    assert any("auto backend used unstructured" in warning for warning in result["metadata"]["warnings"])


def test_unstructured_markdown_preserves_category_and_page_labels() -> None:
    elements = [
        FakeElement("NarrativeText", "Page body", page_number=2),
        FakeElement("ListItem", "First\nSecond", page_number=2),
        FakeElement("Table", "Name | Value", page_number=3, text_as_html="<table><tr></tr></table>"),
    ]

    markdown, text, tables_detected = _render_unstructured_elements(elements)

    assert "[NarrativeText | page 2]" in markdown
    assert "[ListItem | page 2]" in markdown
    assert "- First" in markdown
    assert "[Table | page 3]" in markdown
    assert "<table><tr></tr></table>" in markdown
    assert "Name | Value" in text
    assert tables_detected == 1


class FakeMetadata:
    def __init__(self, **values) -> None:
        self.values = values

    def to_dict(self) -> dict:
        return dict(self.values)


class FakeElement:
    def __init__(self, category: str, text: str, **metadata) -> None:
        self.category = category
        self.text = text
        self.metadata = FakeMetadata(**metadata)


def _install_fake_unstructured(monkeypatch, elements: list[FakeElement] | object) -> None:
    unstructured_module = types.ModuleType("unstructured")
    partition_module = types.ModuleType("unstructured.partition")
    auto_module = types.ModuleType("unstructured.partition.auto")
    auto_module.partition = elements if callable(elements) else lambda filename: elements
    partition_module.auto = auto_module
    unstructured_module.partition = partition_module
    monkeypatch.setitem(sys.modules, "unstructured", unstructured_module)
    monkeypatch.setitem(sys.modules, "unstructured.partition", partition_module)
    monkeypatch.setitem(sys.modules, "unstructured.partition.auto", auto_module)
