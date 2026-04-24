"""Backward-compat shim. Canonical module: vs_app.integrations.files.attachment_extraction."""

from vs_app.integrations.files.attachment_extraction import *  # noqa: F401,F403
from vs_app.integrations.files.attachment_extraction import (  # noqa: F401
    download_attachment,
    fetch_attachment_content,
)
