"""Load mock employee and policy data."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_AS_OF = "2026-09-30"


@dataclass
class ClaimsStore:
    employees: dict[str, dict[str, Any]]
    policies: dict[str, Any]
    review_log: Path
    as_of: date

    @property
    def probation_days(self) -> int:
        return int(self.policies["probation_days"])

    @property
    def refresh_year_days(self) -> int:
        return int(self.policies["refresh_year_days"])


@lru_cache(maxsize=1)
def _load_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def default_store() -> ClaimsStore:
    employees_doc = _load_json(str(DATA_DIR / "employees.json"))
    policies = _load_json(str(DATA_DIR / "policies.json"))
    employees = {row["employee_id"]: row for row in employees_doc["employees"]}
    review_log = Path(
        os.environ.get(
            "EQUIPMENT_REVIEW_LOG",
            str(DATA_DIR / "review_queue.json"),
        )
    )
    as_of = date.fromisoformat(os.environ.get("EQUIPMENT_AS_OF", policies["as_of"]))
    return ClaimsStore(
        employees=employees,
        policies=policies,
        review_log=review_log,
        as_of=as_of,
    )
