"""Backward-compat shim. Canonical: vs_app.modules.ingestion.jira.value_stream_labels.epic_extraction."""

from vs_app.modules.ingestion.jira.value_stream_labels.epic_extraction import *  # noqa: F401,F403
from vs_app.modules.ingestion.jira.value_stream_labels.epic_extraction import (  # noqa: F401
    extract_epics,
    map_value_streams_to_epics,
)
