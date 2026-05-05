"""Backward-compat shim. Canonical: vs_app.ingestion.jira.value_stream_labels.theme_extraction."""

from vs_app.ingestion.jira.value_stream_labels.theme_extraction import *  # noqa: F401,F403
from vs_app.ingestion.jira.value_stream_labels.theme_extraction import (  # noqa: F401
    extract_themes,
    resolve_value_streams,
)
