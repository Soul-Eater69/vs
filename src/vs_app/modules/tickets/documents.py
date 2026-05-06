"""Document schemas for summary ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TicketSummaryDocument:
    """Structured summary of one Jira idea-card ticket (Index B)."""

    ticket_id: str

    summary_text: str
    business_problem: str
    business_capability: str
    key_terms: list[str]

    stakeholders: list[str] = field(default_factory=list)
    systems_and_products: list[str] = field(default_factory=list)

    value_stream_ids: list[str] = field(default_factory=list)
    value_stream_names: list[str] = field(default_factory=list)
    jira_group_ids: list[str] = field(default_factory=list)
    label_source: str = "jira_issuelinks"
    direct_vs_names: list[str] = field(default_factory=list)
    implied_vs_names: list[str] = field(default_factory=list)
    value_streams: list[dict[str, str]] = field(default_factory=list)

    summary_embedding: list[float] = field(default_factory=list)

    def to_index_doc(self) -> dict:
        return {
            "ticket_id": self.ticket_id,
            "summary_text": self.summary_text,
            "business_problem": self.business_problem,
            "business_capability": self.business_capability,
            "stakeholders": self.stakeholders,
            "systems_and_products": self.systems_and_products,
            "key_terms": self.key_terms,
            "value_stream_ids": self.value_stream_ids,
            "value_stream_names": self.value_stream_names,
            "jira_group_ids": self.jira_group_ids,
            "label_source": self.label_source,
            "direct_vs_names": self.direct_vs_names,
            "implied_vs_names": self.implied_vs_names,
            "value_streams": self.value_streams,
            "summary_embedding": self.summary_embedding,
        }

    @classmethod
    def from_index_doc(cls, doc: dict) -> "TicketSummaryDocument":
        return cls(
            ticket_id=doc["ticket_id"],
            summary_text=doc.get("summary_text", ""),
            business_problem=doc.get("business_problem", ""),
            business_capability=doc.get("business_capability", ""),
            key_terms=doc.get("key_terms", []),
            stakeholders=doc.get("stakeholders", []),
            systems_and_products=doc.get("systems_and_products", []),
            value_stream_ids=doc.get("value_stream_ids", []),
            value_stream_names=doc.get("value_stream_names", []),
            jira_group_ids=doc.get("jira_group_ids", []),
            label_source=doc.get("label_source", "jira_issuelinks"),
            direct_vs_names=doc.get("direct_vs_names", []),
            implied_vs_names=doc.get("implied_vs_names", []),
            value_streams=doc.get("value_streams", []),
            summary_embedding=doc.get("summary_embedding", []),
        )
