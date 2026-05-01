from jobs.audit_faiss_index import audit_summaries


def test_audit_summaries_reports_rows_faiss_will_skip() -> None:
    report = audit_summaries(
        [
            {
                "ticket_id": "IDMT-1",
                "summary_text": "Something useful",
                "business_problem": "",
                "business_capability": "",
                "key_terms": [],
            },
            {
                "ticket_id": "IDMT-2",
                "summary_text": "",
                "business_problem": "",
                "business_capability": "",
                "key_terms": [],
            },
        ]
    )

    assert report["source_summary_count"] == 2
    assert report["indexable_summary_count"] == 1
    assert report["skipped_summary_count"] == 1
    assert report["indexable_ticket_ids"] == ["IDMT-1"]
    assert report["skipped_summary_docs"][0]["ticket_id"] == "IDMT-2"
    assert report["skipped_summary_docs"][0]["reason"] == "empty_formatted_summary_text"
