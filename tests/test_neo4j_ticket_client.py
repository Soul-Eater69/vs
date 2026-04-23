from __future__ import annotations

import asyncio

from jira.neo4j_ticket_client import Neo4jTicketClient


def test_get_ticket_data_normalizes_graph_ticket_payload() -> None:
    client = Neo4jTicketClient(
        uri="bolt://unused:7687",
        auth=("neo4j", "password"),
    )

    async def fake_ensure_ready() -> None:
        return None

    def fake_query(ticket_id: str) -> dict[str, object]:
        assert ticket_id == "IDMT-19761"
        return {
            "ticket_node": {
                "key": "IDMT-19761",
                "summary": "External audit workflow",
                "description": "See [Idea Card](https://example.com/idea-card.pdf)",
                "issueType": "Engagement Request",
                "creationDate": "2024-01-01T12:00:00.000+0000",
                "status": "In Progress",
                "labels": '["audit", "compliance"]',
                "components": '["Claims"]',
                "attachmentsMetaData": (
                    '[{"id":"att-1","filename":"deck.pptx",'
                    '"content":"https://example.com/deck.pptx"}]'
                ),
                "comments": (
                    '["This request needs an external audit workflow and an '
                    'updated evidence package for the compliance review."]'
                ),
                "inwardIssues": ["GROUP-22223"],
            },
            "theme_nodes": [
                {
                    "key": "GROUP-22223",
                    "summary": "Conduct Audit",
                    "issueType": "Theme",
                    "status": "Open",
                }
            ],
        }

    client._ensure_ready = fake_ensure_ready  # type: ignore[method-assign]
    client._query_ticket_sync = fake_query  # type: ignore[method-assign]

    payload = asyncio.run(client.get_ticket_data("IDMT-19761"))

    assert payload["key"] == "IDMT-19761"
    assert payload["value_stream_names"] == ["Conduct Audit"]
    assert payload["value_stream_ids"] == ["GROUP-22223"]

    fields = payload["fields"]
    assert fields["summary"] == "External audit workflow"
    assert fields["issuetype"] == {"name": "Engagement Request"}
    assert fields["status"] == {"name": "In Progress"}
    assert fields["labels"] == ["audit", "compliance"]
    assert fields["components"] == [{"name": "Claims"}]
    assert len(fields["comment"]["comments"]) == 1
    assert "external audit workflow" in fields["comment"]["comments"][0]["body"].lower()

    attachments = payload["attachments"]
    assert len(attachments) == 2
    assert any(att["filename"] == "deck.pptx" for att in attachments)
    assert any(att.get("is_description_link") for att in attachments)
    assert len(payload["description_attachments"]) == 1
    assert len(fields["attachment"]) == 1

