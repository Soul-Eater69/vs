"""Original IDMT context extraction and consolidation.

This package will hold the code that turns Jira artifacts and attachments into
the original IDMT text packet used for prediction:

    attachment_ranker        rank idea card / business attachments
    document_text_extractor  extract text from PPT/PPTX, PDF, DOCX
    text_consolidator        combine summary, description, attachment text,
                             and extracted text into one packet

It is created here as part of the ingestion framework structure (Feature 2).
The modules are moved in during the text extraction cleanup (Feature 4); until
then this package is intentionally empty and exports nothing.
"""

from __future__ import annotations

__all__: list[str] = []
