from __future__ import annotations

import math
import re
from difflib import SequenceMatcher


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = value.lower().strip()
    normalized = normalized.replace("ё", "е")
    normalized = re.sub(r"\[ci\d+\]", " ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\(и\d+\)", " ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"[^a-zA-Zа-яА-Я0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def similarity(a: str | None, b: str | None) -> float:
    a_norm = normalize_text(a)
    b_norm = normalize_text(b)
    if not a_norm or not b_norm:
        return 0.0
    if a_norm == b_norm:
        return 1.0
    return SequenceMatcher(None, a_norm, b_norm).ratio()


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0
    dot = sum(a * b for a, b in zip(vec1, vec2))
    norm1 = math.sqrt(sum(a * a for a in vec1))
    norm2 = math.sqrt(sum(b * b for b in vec2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)

