"""Text cleaning + sentence segmentation + a lightweight token estimate.

Dependency-free on purpose: cleaning and sentence splitting must run in the
chunking Lambda without pulling a tokenizer or NLP model. The token estimate is
a deliberate heuristic (~4 chars/token, the common rule of thumb) used only for
*budgeting* chunk sizes — exact token counts don't matter for that, and a real
tokenizer is an avoidable cold-start + package-size cost.
"""

from __future__ import annotations

import re
import unicodedata

# Split on sentence-final punctuation followed by whitespace and an
# uppercase/quote/paren/digit start. Not perfect (abbreviations, decimals), but
# robust and dependency-free; chunking only needs *reasonable* boundaries.
_SENTENCE_BREAK = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"\'(\[])')
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n+")
_INLINE_WS = re.compile(r"[ \t]+")
_MANY_NEWLINES = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    """Normalise raw text: unicode NFC, strip control chars, tidy whitespace.

    - NFC-normalises so visually-identical strings compare/hash equally.
    - Drops Unicode control characters (category ``C*``) except newline/tab,
      which removes the NULs, vertical tabs, and stray control bytes that PDFs
      and copy-paste introduce.
    - Collapses runs of spaces/tabs to one space per line, trims each line, and
      collapses 3+ blank lines to a single paragraph break — so the paragraph
      splitter sees clean boundaries.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Strip control chars (except newline/tab) BEFORE normalising, so NFC can
    # compose any base+combining-mark pair a control char was separating — a
    # real PDF artifact (e.g. a soft hyphen between a letter and its accent).
    # Normalising AFTER the strip makes clean_text idempotent and keeps the
    # content-addressed ids derived from it stable.
    text = "".join(
        ch for ch in text if ch in "\n\t" or not unicodedata.category(ch).startswith("C")
    )
    text = unicodedata.normalize("NFC", text)
    lines = [_INLINE_WS.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = _MANY_NEWLINES.sub("\n\n", text)
    return text.strip()


def paragraphs(text: str) -> list[str]:
    return [p.strip() for p in _PARAGRAPH_BREAK.split(text) if p.strip()]


def split_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    return [s.strip() for s in _SENTENCE_BREAK.split(text) if s.strip()]


def estimate_tokens(text: str) -> int:
    """Rough token count for budgeting (~4 chars/token). Always >= 1 for non-empty."""
    n = len(text)
    return max(1, (n + 3) // 4) if n else 0
