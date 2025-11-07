"""Utilities for normalising text for alignment."""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List

_APOSTROPHE_PATTERN = re.compile(r"[’`]")
_NON_WORD_PATTERN = re.compile(r"[^\w\s']", re.UNICODE)
_WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Return a normalised version of ``text`` suitable for comparison."""
    text = unicodedata.normalize("NFKC", text)
    text = _APOSTROPHE_PATTERN.sub("'", text)
    text = text.lower()
    text = _NON_WORD_PATTERN.sub(" ", text)
    text = _WHITESPACE_PATTERN.sub(" ", text).strip()
    return text


def tokenize(text: str) -> List[str]:
    """Split text into normalised tokens."""
    normalised = normalize_text(text)
    return [token for token in normalised.split(" ") if token]


def join_tokens(tokens: Iterable[str]) -> str:
    """Join tokens into a single comparison string."""
    return " ".join(tokens)
