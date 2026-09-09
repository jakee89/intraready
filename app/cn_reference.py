from __future__ import annotations

import json
import re
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

