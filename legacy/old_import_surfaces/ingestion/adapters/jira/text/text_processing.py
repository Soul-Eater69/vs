"""Backward-compat shim. Canonical module: vs_app.modules.tickets.text_processing."""

from vs_app.modules.tickets.text_processing import *  # noqa: F401,F403
from vs_app.modules.tickets.text_processing import (  # noqa: F401
    classify_description,
    clean_description,
    clean_jira_markup,
    extract_adf_html,
    extract_adf_text,
    extract_comment_texts,
    is_bot_author,
    is_comment_chatter,
)
