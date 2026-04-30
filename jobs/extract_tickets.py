"""Root-level wrapper for raw Jira ticket extraction.

This writes one flat raw-extraction JSON per ticket. For historical RAG summary
artifacts, use ``jobs/ingest_tickets.py`` instead.

Usage:
  py -3 jobs/extract_tickets.py IDMT-19761 IDMT-8199 --output-dir jira_extraction
"""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from vs_app.jobs.jira_batch.jobs.extract_tickets import main


if __name__ == "__main__":
    main()
