from __future__ import annotations

from fastapi import FastAPI

from vs_app.api.middleware.error_handler import register_error_handlers
from vs_app.api.middleware.request_logging import register_request_logging
from vs_app.api.routes.health import router as health_router
from vs_app.api.routes.ingestion import router as ingestion_router
from vs_app.api.routes.rag import router as rag_router


def create_app() -> FastAPI:
    app = FastAPI(title="VS API")
    register_request_logging(app)
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(ingestion_router)
    app.include_router(rag_router)
    return app


app = create_app()
