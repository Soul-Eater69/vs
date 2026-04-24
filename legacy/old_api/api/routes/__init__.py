from .health import router as health_router
from .idea_cards import router as idea_cards_router
from .search import router as search_router
from .generate import router as generate_router

__all__ = ["health_router", "idea_cards_router", "search_router", "generate_router"]
