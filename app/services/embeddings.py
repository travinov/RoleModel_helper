from __future__ import annotations

import hashlib
import math
from typing import Iterable

from .text import normalize_text


def embed_text(text: str, dimensions: int = 128) -> list[float]:
    tokens = normalize_text(text).split()
    vector = [0.0] * dimensions
    if not tokens:
        return vector
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def vector_literal(vector: Iterable[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"
