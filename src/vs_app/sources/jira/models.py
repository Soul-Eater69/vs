"""Records for the reusable Jira source-extraction layer.

Clean, transport-agnostic data shapes describing what an IDMT ticket and its
linked Theme/GROUP issues and child Epics look like after extraction. These feed
both batch ingestion and the runtime flow (when a user enters an IDMT id).

Pure data: no network, no client construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ExtractedEpicRecord:
    """A child Epic under a Theme/GROUP issue."""

    epic_id: str
    summary: str = ""
    status: str = ""
    source: str = ""
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "epic_id": self.epic_id,
            "summary": self.summary,
            "status": self.status,
            "source": self.source,
            "raw": dict(self.raw),
        }


@dataclass(slots=True)
class ExtractedThemeRecord:
    """A linked Theme/GROUP issue with its Value Stream and child Epics."""

    group_id: str
    theme_summary: str = ""
    business_needs: str = ""
    value_stream_id: str = ""
    value_stream_name: str = ""
    epics: list[ExtractedEpicRecord] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "group_id": self.group_id,
            "theme_summary": self.theme_summary,
            "business_needs": self.business_needs,
            "value_stream_id": self.value_stream_id,
            "value_stream_name": self.value_stream_name,
            "epics": [epic.to_dict() for epic in self.epics],
            "raw": dict(self.raw),
        }


@dataclass(slots=True)
class ExtractedIDMTRecord:
    """An IDMT ticket plus its idea card, attachments, and linked themes."""

    ticket_id: str
    title: str = ""
    summary: str = ""
    description: str = ""
    idea_card_text: str = ""
    attachment_text: str = ""
    extracted_text: str = ""
    themes: list[ExtractedThemeRecord] = field(default_factory=list)
    source_metadata: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ticket_id": self.ticket_id,
            "title": self.title,
            "summary": self.summary,
            "description": self.description,
            "idea_card_text": self.idea_card_text,
            "attachment_text": self.attachment_text,
            "extracted_text": self.extracted_text,
            "themes": [theme.to_dict() for theme in self.themes],
            "source_metadata": dict(self.source_metadata),
            "raw": dict(self.raw),
        }


__all__ = [
    "ExtractedEpicRecord",
    "ExtractedThemeRecord",
    "ExtractedIDMTRecord",
]
