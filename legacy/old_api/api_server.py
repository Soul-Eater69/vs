"""
FastAPI backend for the Value Stream Explorer UI.

Run with:
    uvicorn api_server:app --reload --port 8000
"""

from api.app import app

__all__ = ["app"]
