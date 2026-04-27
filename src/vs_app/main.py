from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from vs_app.api.middleware.error_handler import register_error_handlers
from vs_app.api.middleware.request_logging import register_request_logging
from vs_app.api.routes.health import router as health_router
from vs_app.api.routes.idea_cards import router as idea_cards_router
from vs_app.api.routes.ingestion import router as ingestion_router
from vs_app.api.routes.rag import router as rag_router


def _allowed_origins() -> list[str]:
    raw = os.environ.get("CORS_ALLOW_ORIGINS")
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return ["http://localhost:3000", "http://127.0.0.1:3000"]


def create_app() -> FastAPI:
    app = FastAPI(title="VS API")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_request_logging(app)
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(idea_cards_router)
    app.include_router(ingestion_router)
    app.include_router(rag_router)
    return app


app = create_app()
