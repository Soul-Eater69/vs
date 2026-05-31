"""Azure AI Search upload for canonical IDMT documents.

This package will hold the isolated Azure upload code:

    azure_search_client  thin client for the Azure Search index
    index_manager        create / update the idp_idmt_data_v2 schema
    uploader             batch upload canonical IDMT documents with logging

Upload code should stay free of business logic. It is created here as part of
the ingestion framework structure (Feature 2). The existing upload code under
``vs_app.ingestion.persistence`` is moved in during the Azure upload cleanup
(Feature 9); until then this package is intentionally empty and exports
nothing.
"""

from __future__ import annotations

__all__: list[str] = []
