"""Backward-compat shim. Canonical module: vs_app.modules.tickets.text_assembly."""

from vs_app.modules.tickets.text_assembly import *  # noqa: F401,F403
from vs_app.modules.tickets.text_assembly import (  # noqa: F401
    build_retrieval_text,
    clean_description,
    extract_adf_text,
    extract_comment_texts,
)
