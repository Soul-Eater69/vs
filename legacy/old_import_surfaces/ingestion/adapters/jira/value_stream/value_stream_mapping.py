"""Backward-compat shim. Canonical: vs_app.modules.ingestion.jira.value_stream_labels.value_stream_mapping."""

from vs_app.modules.ingestion.jira.value_stream_labels.value_stream_mapping import *  # noqa: F401,F403
from vs_app.modules.ingestion.jira.value_stream_labels.value_stream_mapping import (  # noqa: F401
    canonicalize_value_stream_names,
    resolve_value_stream_epics_mapping,
    resolve_value_stream_mapping,
)
