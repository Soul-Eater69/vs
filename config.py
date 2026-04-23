"""
Centralised configuration loaded from environment variables / .env file.

Import from here rather than accessing os.environ directly in application code.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# IDP / LLM gateway
# ---------------------------------------------------------------------------
LLM_APP_ID: str = os.environ["LLM_APP_ID"]
LLM_BASE_URL: str = os.environ["LLM_BASE_URL"]

# ---------------------------------------------------------------------------
# IDP identity provider
# ---------------------------------------------------------------------------
IDP_AUTH_URL: str = os.environ["IDP_AUTH_URL"]
IDP_CLIENT_SECRET: str = os.environ["IDP_CLIENT_SECRET"]
IDP_CLIENT_ID: str = os.environ["IDP_CLIENT_ID"]
IDP_USER: str = os.environ["IDP_USER"]
IDP_PASSWORD: str = os.environ["IDP_PASSWORD"]
SEARCH_API_PATH: str = os.environ["SEARCH_API_PATH"]

# ---------------------------------------------------------------------------
# Embedding model
# ---------------------------------------------------------------------------
EMBEDDING_MODEL: str = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small-idp")
EMBEDDING_DIMENSION: int = int(os.environ.get("EMBEDDING_DIMENSION", "1536"))

# ---------------------------------------------------------------------------
# LLM chat endpoint
# ---------------------------------------------------------------------------
CHAT_COMPLETION_PATH: str = os.environ.get(
    "CHAT_COMPLETION_PATH", "/chat/completions"
)

# ---------------------------------------------------------------------------
# Azure AI Search (direct - used for document upload)
# ---------------------------------------------------------------------------
AZURE_SEARCH_ENDPOINT: str = os.environ.get("AZURE_SEARCH_ENDPOINT", "")
AZURE_SEARCH_INDEX_NAME: str = os.environ.get(
    "AZURE_SEARCH_INDEX_NAME", "value-streams"
)
AZURE_SEARCH_API_KEY: str = os.environ.get("AZURE_SEARCH_API_KEY", "")
AZURE_SEARCH_SEMANTIC_CONFIG: str = os.environ.get(
    "AZURE_SEARCH_SEMANTIC_CONFIG", "default"
)
AZURE_TENANT_ID: str = os.environ.get("AZURE_TENANT_ID", "")
AZURE_CLIENT_ID: str = os.environ.get("AZURE_CLIENT_ID", "")
AZURE_CLIENT_SECRET: str = os.environ.get("AZURE_CLIENT_SECRET", "")

# ---------------------------------------------------------------------------
# IDP AI Search gateway (used for search queries)
# ---------------------------------------------------------------------------
AISEARCH_BASE_URL: str = os.environ.get("AISEARCH_BASE_URL", "")
AISEARCH_APP_ID: str = os.environ.get("AISEARCH_APP_ID", "")
AISEARCH_API_PATH: str = "/api/v1/aisearch/search"

# ---------------------------------------------------------------------------
# Neo4j
# ---------------------------------------------------------------------------
NEO4J_URI: str = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_AUTH: tuple[str, str] = (
    os.environ.get("NEO4J_USER", "neo4j"),
    os.environ.get("NEO4J_PASSWORD", "neo4j123"),
)
NEO4J_DATABASE: str = os.environ.get("NEO4J_DATABASE", "neo4j")

JIRA_TOKEN: str = os.environ.get("JIRA_TOKEN", "")
JIRA_BASE_URL: str = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
