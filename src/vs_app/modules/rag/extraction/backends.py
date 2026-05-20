from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any, Literal

from vs_app.shared.text_cleaning import clean_extracted_text

ExtractorBackend = Literal["current", "unstructured", "auto"]

_SUPPORTED_DOCUMENT_EXTENSIONS = {".pptx", ".ppt", ".pdf", ".docx", ".doc"}
_WEAK_TEXT_WORD_THRESHOLD = 80


def extract_uploaded_file_text(
    file_bytes: bytes,
    filename: str,
    *,
    backend: str = "current",
    clean: bool = True,
) -> dict:
    requested_backend = _normalize_backend(backend)
    if requested_backend == "current":
        return _extract_current(file_bytes, filename, clean=clean)
    if requested_backend == "unstructured":
        result = _extract_unstructured(file_bytes, filename, clean=clean)
        if result["metadata"]["warnings"] and not result["text"].strip():
            fallback = _extract_current(file_bytes, filename, clean=clean)
            fallback["metadata"]["warnings"].extend(result["metadata"]["warnings"])
            return fallback
        return result
    return _extract_auto(file_bytes, filename, clean=clean)


def extracted_rag_text(result: dict) -> str:
    backend = str(result.get("backend") or "").strip().lower()
    markdown = str(result.get("markdown") or "").strip()
    text = str(result.get("text") or "").strip()

    if backend == "unstructured" and markdown:
        return markdown
    return text or markdown


def render_extraction_debug(result: dict, *, backend_requested: str, preview_chars: int = 1500) -> dict:
    metadata = dict(result.get("metadata") or {})
    text = str(result.get("text") or "")
    markdown = str(result.get("markdown") or "")
    backend_used = str(result.get("backend") or "")
    rag_input_kind = "markdown" if backend_used.strip().lower() == "unstructured" and markdown.strip() else "text"
    return {
        "backend_requested": _normalize_backend(backend_requested),
        "backend_used": backend_used,
        "filename": str(result.get("filename") or ""),
        "chars": int(metadata.get("chars") or len(text)),
        "rag_input_kind": rag_input_kind,
        "text_chars": len(text),
        "markdown_chars": len(markdown),
        "words": int(metadata.get("words") or _word_count(text)),
        "element_count": int(metadata.get("element_count") or 0),
        "tables_detected": int(metadata.get("tables_detected") or 0),
        "warnings": list(metadata.get("warnings") or []),
        "preview": text[:preview_chars],
    }


def _extract_auto(file_bytes: bytes, filename: str, *, clean: bool) -> dict:
    current = _extract_current(file_bytes, filename, clean=clean)
    if _word_count(current["text"]) >= _WEAK_TEXT_WORD_THRESHOLD:
        return current

    unstructured = _extract_unstructured(file_bytes, filename, clean=clean)
    if unstructured["text"].strip():
        unstructured["metadata"]["warnings"].append(
            "auto backend used unstructured because current extraction produced fewer than 80 words"
        )
        return unstructured

    current["metadata"]["warnings"].append(
        "auto backend kept current extraction because unstructured produced no usable text"
    )
    current["metadata"]["warnings"].extend(unstructured["metadata"]["warnings"])
    return current


def _extract_current(file_bytes: bytes, filename: str, *, clean: bool) -> dict:
    ext = _extension(filename)
    if ext not in _SUPPORTED_DOCUMENT_EXTENSIONS:
        raise ValueError(f"Unsupported idea-card file type for current extractor: {ext or '<none>'}")

    if ext in {".pptx", ".ppt"}:
        from vs_app.integrations.files.pptx_extractor import extract_pptx

        extraction_result = extract_pptx(file_bytes, max_slides=60)
    elif ext == ".pdf":
        from vs_app.integrations.files.pdf_extractor import extract_pdf

        extraction_result = extract_pdf(file_bytes, ocr_enabled=False, max_pages=50)
    else:
        from vs_app.integrations.files.docx_extractor import extract_docx

        extraction_result = extract_docx(file_bytes)

    extracted_units = list(extraction_result.get("chunks") or [])
    markdown = "\n\n".join(
        str(unit.get("text") or "")
        for unit in extracted_units
        if isinstance(unit, dict) and unit.get("text")
    ).strip()
    text = clean_extracted_text(markdown) if clean else markdown
    warnings: list[str] = []
    return _result(
        backend="current",
        filename=filename,
        extension=ext,
        text=text,
        markdown=markdown,
        element_count=len(extracted_units),
        tables_detected=_table_count(extracted_units, markdown),
        warnings=warnings,
    )


def _extract_unstructured(file_bytes: bytes, filename: str, *, clean: bool) -> dict:
    ext = _extension(filename)
    if ext not in _SUPPORTED_DOCUMENT_EXTENSIONS:
        raise ValueError(f"Unsupported idea-card file type for unstructured extractor: {ext or '<none>'}")

    try:
        from unstructured.partition.auto import partition
    except ImportError as exc:
        return _result(
            backend="unstructured",
            filename=filename,
            extension=ext,
            text="",
            markdown="",
            element_count=0,
            tables_detected=0,
            warnings=[f"unstructured is not installed: {exc}"],
        )

    with tempfile.TemporaryDirectory() as tmp_dir:
        safe_name = Path(filename or f"input{ext}").name
        if not Path(safe_name).suffix:
            safe_name = f"{safe_name}{ext}"

        tmp_path = Path(tmp_dir) / safe_name
        tmp_path.write_bytes(file_bytes)

        # Windows keeps NamedTemporaryFile handles locked; this path is closed before partition reads it.
        elements = list(partition(filename=str(tmp_path)) or [])

    markdown, text, tables_detected = _render_unstructured_elements(elements)
    if clean:
        text = clean_extracted_text(text)
    return _result(
        backend="unstructured",
        filename=filename,
        extension=ext,
        text=text,
        markdown=markdown,
        element_count=len(elements),
        tables_detected=tables_detected,
        warnings=[],
    )


def _render_unstructured_elements(elements: list[Any]) -> tuple[str, str, int]:
    markdown_parts: list[str] = []
    text_parts: list[str] = []
    tables_detected = 0

    for element in elements:
        category = str(
            getattr(element, "category", None) or element.__class__.__name__ or "Element"
        )
        text = str(getattr(element, "text", "") or "").strip()
        metadata = _element_metadata(element)
        page_number = metadata.get("page_number")
        html = str(metadata.get("text_as_html") or "").strip()
        if not text and not html:
            continue

        if category.lower() == "table" or html:
            tables_detected += 1

        label = category
        if page_number not in (None, ""):
            label = f"{label} | page {page_number}"

        if category.lower() == "listitem":
            rendered_text = "\n".join(
                f"- {line.strip().lstrip('- ').strip()}"
                for line in text.splitlines()
                if line.strip()
            )
        else:
            rendered_text = text

        block = [f"[{label}]"]
        if rendered_text:
            block.append(rendered_text)
            text_parts.append(rendered_text)
        if html:
            block.append(html)
            text_parts.append(html)
        markdown_parts.append("\n".join(block))

    return "\n\n".join(markdown_parts).strip(), "\n\n".join(text_parts).strip(), tables_detected


def _element_metadata(element: Any) -> dict[str, Any]:
    metadata = getattr(element, "metadata", None)
    if metadata is None:
        return {}
    to_dict = getattr(metadata, "to_dict", None)
    if callable(to_dict):
        try:
            return dict(to_dict() or {})
        except Exception:
            return {}
    if isinstance(metadata, dict):
        return dict(metadata)
    return {}


def _result(
    *,
    backend: str,
    filename: str,
    extension: str,
    text: str,
    markdown: str,
    element_count: int,
    tables_detected: int,
    warnings: list[str],
) -> dict:
    return {
        "backend": backend,
        "filename": Path(filename or "idea-card").name,
        "text": text,
        "markdown": markdown,
        "metadata": {
            "extension": extension.lstrip("."),
            "chars": len(text),
            "words": _word_count(text),
            "element_count": element_count,
            "tables_detected": tables_detected,
            "warnings": warnings,
        },
    }


def _table_count(extracted_units: list[Any], markdown: str) -> int:
    count = 0
    for unit in extracted_units:
        if isinstance(unit, dict) and (unit.get("has_table") or "|" in str(unit.get("text") or "")):
            count += 1
    return count or (1 if "|" in markdown else 0)


def _extension(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def _normalize_backend(backend: str) -> ExtractorBackend:
    value = str(backend or "current").strip().lower()
    if value in {"current", "unstructured", "auto"}:
        return value  # type: ignore[return-value]
    return "current"


def _word_count(text: str) -> int:
    return len(str(text or "").split())
