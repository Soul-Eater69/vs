from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import health_router, idea_cards_router, search_router, generate_router

app = FastAPI(title="Value Stream Explorer API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(idea_cards_router)
app.include_router(search_router)
app.include_router(generate_router)
