from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _reference() -> dict:
    return json.loads((Path(__file__).parent / "cn_2026.json").read_text(encoding="utf-8"))


def cn_requirement(value: str) -> dict:
    code = re.sub(r"\D", "", value or "")[:8]
    codes = _reference()["codes"]
    return {
        "year": _reference()["year"],
        "code": code,
        "valid": len(code) == 8 and code in codes,
        "supp_unit": codes.get(code, ""),
    }


@lru_cache(maxsize=1)
def _search_catalogue() -> dict:
    return json.loads((Path(__file__).parent / "cn_search_2026.json").read_text(encoding="utf-8"))["codes"]


def search_cn(query: str, limit: int = 15) -> list[dict]:
    normalized = re.sub(r"[^a-z0-9 ]", " ", query.lower())
    words = {word for word in normalized.split() if len(word) > 2}
    if not words:
        return []
    ranked = []
    for code, description in _search_catalogue().items():
        haystack = re.sub(r"[^a-z0-9 ]", " ", description.lower())
        hay_words = set(haystack.split())
        overlap = len(words & hay_words) / len(words)
        phrase = SequenceMatcher(None, normalized, haystack).ratio()
        score = overlap * 0.75 + phrase * 0.25
        if score >= 0.12:
            ranked.append((score, code, description))
    ranked.sort(reverse=True)
    return [
        {"code": code, "description": description, "score": round(score, 3),
         "supp_unit": cn_requirement(code)["supp_unit"], "source": "CN 2026"}
        for score, code, description in ranked[:limit]
    ]

