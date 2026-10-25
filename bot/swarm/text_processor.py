"""
Text processing utilities for graph ingestion.

Handles text chunking with sentence-boundary-aware splitting and
basic preprocessing. Used by GraphBuilderService to prepare content
for Zep episode ingestion.

Adapted from MiroFish backend/app/services/text_processor.py and
backend/app/utils/file_parser.py.
"""
from __future__ import annotations

import re
from typing import List


def split_text_into_chunks(
    text: str,
    chunk_size: int = 500,
    overlap: int = 50,
) -> List[str]:
    """
    Split text into chunks with sentence-boundary awareness.

    Args:
        text: Raw text to split.
        chunk_size: Target character count per chunk.
        overlap: Character overlap between consecutive chunks.

    Returns:
        List of text chunks.
    """
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    chunks: List[str] = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        # Try to break at a sentence boundary
        if end < len(text):
            for sep in [".\n", "!\n", "?\n", "\n\n", ". ", "! ", "? "]:
                last_sep = text[start:end].rfind(sep)
                if last_sep != -1 and last_sep > chunk_size * 0.3:
                    end = start + last_sep + len(sep)
                    break

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        start = end - overlap if end < len(text) else len(text)

    return chunks


def preprocess_text(text: str) -> str:
    """Normalize whitespace and newlines."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [line.strip() for line in text.split("\n")]
    return "\n".join(lines).strip()


class TextProcessor:
    """Stateless text processing helper."""

    @staticmethod
    def split_text(
        text: str,
        chunk_size: int = 500,
        overlap: int = 50,
    ) -> List[str]:
        return split_text_into_chunks(text, chunk_size, overlap)

    @staticmethod
    def preprocess_text(text: str) -> str:
        return preprocess_text(text)

    @staticmethod
    def get_text_stats(text: str) -> dict:
        return {
            "total_chars": len(text),
            "total_lines": text.count("\n") + 1,
            "total_words": len(text.split()),
        }
