"""Application settings.

Reads from environment / the existing repo-root config module so we do not
duplicate secret handling. Add fields here as we migrate off the legacy
`config.py` top-level module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True)
class Settings:
    jira_base_url: str = ""
    jira_token: str = ""
    verify_ssl: bool = False

    neo4j_uri: str = ""
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"
    neo4j_ticket_label: str = "JIRA"
    neo4j_ticket_key_property: str = "key"
    neo4j_group_link_field: str = "inwardIssues"
    neo4j_issue_type_property: str = "issueType"

    default_ticket_source: str = "jira"

    @classmethod
    def from_env(cls) -> "Settings":
        """Build Settings from the legacy root-level `config` module + env overrides."""
        kwargs: dict = {}
        try:
            import config as legacy_config  # type: ignore[import-not-found]
        except Exception:
            legacy_config = None

        def _legacy(name: str, default):
            if legacy_config is None:
                return default
            return getattr(legacy_config, name, default)

        kwargs["jira_base_url"] = _legacy("JIRA_BASE_URL", "")
        kwargs["jira_token"] = _legacy("JIRA_TOKEN", "")

        neo4j_auth = _legacy("NEO4J_AUTH", ("neo4j", ""))
        if isinstance(neo4j_auth, (tuple, list)) and len(neo4j_auth) == 2:
            kwargs["neo4j_user"], kwargs["neo4j_password"] = neo4j_auth
        kwargs["neo4j_uri"] = _legacy("NEO4J_URI", "")
        kwargs["neo4j_database"] = _legacy("NEO4J_DATABASE", "neo4j")
        kwargs["neo4j_ticket_label"] = _legacy("NEO4J_TICKET_LABEL", "JIRA")
        kwargs["neo4j_ticket_key_property"] = _legacy("NEO4J_TICKET_KEY_PROPERTY", "key")
        kwargs["neo4j_group_link_field"] = _legacy("NEO4J_GROUP_LINK_FIELD", "inwardIssues")
        kwargs["neo4j_issue_type_property"] = _legacy("NEO4J_ISSUE_TYPE_PROPERTY", "issueType")

        kwargs["default_ticket_source"] = (
            os.environ.get("INGESTION_TICKET_SOURCE", "jira").strip().lower()
        )

        return cls(**kwargs)
