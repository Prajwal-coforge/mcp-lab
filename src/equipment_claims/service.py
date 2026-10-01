"""Plain functions behind the equipment-claims tools.

These functions are the system of record. The MCP server wraps them; unit tests
call them directly and never go through the protocol.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from equipment_claims.store import ClaimsStore, default_store

ELIGIBLE_NEW = "eligible_new"
ELIGIBLE_REPLACEMENT = "eligible_replacement"
INELIGIBLE_TOO_SOON = "ineligible_too_soon"
INELIGIBLE_NOT_IN_CATALOG = "ineligible_not_in_catalog"
INELIGIBLE_NO_REFRESH = "ineligible_no_refresh"
AMBIGUOUS_PROBATION = "ambiguous_probation"
AMBIGUOUS_UNKNOWN_ITEM = "ambiguous_unknown_item"
AMBIGUOUS_MISSING_HISTORY = "ambiguous_missing_history"
AMBIGUOUS_UNKNOWN_ROLE = "ambiguous_unknown_role"
NOT_FOUND = "not_found"

# Longer phrases first so "docking station" wins over a bare "dock" check.
_ITEM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("docking_station", re.compile(r"\b(docking station|dock)\b", re.IGNORECASE)),
    ("monitor", re.compile(r"\b(monitors?|screens?|displays?)\b", re.IGNORECASE)),
    ("laptop", re.compile(r"\b(laptops?|notebooks?)\b", re.IGNORECASE)),
    ("headset", re.compile(r"\b(headsets?|headphones?)\b", re.IGNORECASE)),
    ("keyboard", re.compile(r"\bkeyboards?\b", re.IGNORECASE)),
    ("mouse", re.compile(r"\b(mice|mouse)\b", re.IGNORECASE)),
]


def find_items(text: str) -> list[str]:
    """Return catalog items named in text, in order, without duplicates."""
    hits: list[tuple[int, str]] = []
    for canonical, pattern in _ITEM_PATTERNS:
        for match in pattern.finditer(text):
            hits.append((match.start(), canonical))
    hits.sort()
    found: list[str] = []
    for _, canonical in hits:
        if canonical not in found:
            found.append(canonical)
    return found


def item_query(text: str) -> str:
    """Phrase to pass as the eligibility tool's item argument.

    A single match becomes the catalog key. Several matches are joined with
    " or " so the tool can reject the phrase instead of us choosing one.
    """
    found = find_items(text)
    if len(found) == 1:
        return found[0]
    surfaces: list[str] = []
    hits: list[tuple[int, str]] = []
    for _, pattern in _ITEM_PATTERNS:
        for match in pattern.finditer(text):
            hits.append((match.start(), match.group(0)))
    hits.sort()
    for _, surface in hits:
        if surface.lower() not in {existing.lower() for existing in surfaces}:
            surfaces.append(surface)
    if not surfaces:
        return text.strip()
    return " or ".join(surfaces)


def canonicalize_item(item: str) -> str | None:
    found = find_items(item)
    if len(found) == 1:
        return found[0]
    return None


def _age_days(issued_on: str, as_of: date) -> int:
    return (as_of - date.fromisoformat(issued_on)).days


def _label(item: str, count: int | None = None) -> str:
    text = item.replace("_", " ")
    if count is not None and count != 1:
        return "mice" if item == "mouse" else text + "s"
    return text


def _role_phrase(role: str) -> str:
    text = role.replace("_", " ")
    article = "an" if text[0] in "aeiou" else "a"
    return f"{article} {text}"


def age_conflict(request_text: str, employee: dict[str, Any], item: str) -> str | None:
    """Return an explanation when the request's claimed age disagrees with the record.

    A gap of one year or less is treated as rounding. A larger gap is not something
    the agent should resolve by preferring either the employee or the asset record.
    """
    match = re.search(r"\b(\d+)\s+years?\s+old\b", request_text, re.IGNORECASE)
    if match is None:
        return None
    claimed = int(match.group(1))
    rows = [
        row
        for row in employee.get("equipment", [])
        if row["item"] == item and row.get("issued_on") and row.get("age_days") is not None
    ]
    if not rows:
        return (
            f"The request says the {item} is {claimed} years old, but no issued date "
            "is on file to confirm that."
        )
    current = max(rows, key=lambda row: row["issued_on"])
    actual_years = current["age_days"] / 365.25
    if abs(claimed - actual_years) > 1:
        return (
            f"The request says the {item} is {claimed} years old, but asset "
            f"{current['asset_tag']} was issued on {current['issued_on']} "
            f"({actual_years:.1f} years before the policy clock)."
        )
    return None


def get_employee_info(employee_id: str, store: ClaimsStore | None = None) -> dict[str, Any]:
    """Return role, tenure, and current equipment. Unknown ids are a result, not a crash."""
    store = store or default_store()
    record = store.employees.get(employee_id)
    if record is None:
        return {
            "found": False,
            "employee_id": employee_id,
            "error": "employee_not_found",
            "name": None,
            "role": None,
            "hired_on": None,
            "tenure_days": None,
            "on_probation": None,
            "equipment": [],
        }

    hired = date.fromisoformat(record["hired_on"])
    tenure_days = (store.as_of - hired).days
    equipment: list[dict[str, Any]] = []
    for row in record["equipment"]:
        issued_on = row["issued_on"]
        equipment.append(
            {
                "item": row["item"],
                "issued_on": issued_on,
                "asset_tag": row["asset_tag"],
                "age_days": None if issued_on is None else _age_days(issued_on, store.as_of),
            }
        )
    return {
        "found": True,
        "employee_id": record["employee_id"],
        "name": record["name"],
        "role": record["role"],
        "hired_on": record["hired_on"],
        "tenure_days": tenure_days,
        "on_probation": tenure_days < store.probation_days,
        "equipment": equipment,
    }


def get_policy_limits(role: str, store: ClaimsStore | None = None) -> dict[str, Any]:
    """Return the catalog, counts, and refresh cadence for a role."""
    store = store or default_store()
    role_policy = store.policies["roles"].get(role)
    if role_policy is None:
        return {
            "found": False,
            "role": role,
            "error": "unknown_role",
            "probation_days": store.probation_days,
            "refresh_year_days": store.refresh_year_days,
            "summary": None,
            "items": {},
        }
    return {
        "found": True,
        "role": role,
        "probation_days": store.probation_days,
        "refresh_year_days": store.refresh_year_days,
        "summary": role_policy["summary"],
        "items": role_policy["items"],
    }


def check_request_eligibility(
    employee_id: str,
    item: str,
    store: ClaimsStore | None = None,
) -> dict[str, Any]:
    """Decide whether one item is inside policy, outside policy, or ambiguous.

    Ambiguous means the directory does not contain enough facts to approve or
    deny. A clear miss against the catalog or the refresh window is ineligible.
    """
    store = store or default_store()
    employee = get_employee_info(employee_id, store)
    if not employee["found"]:
        return _result(
            employee_id,
            item,
            None,
            NOT_FOUND,
            None,
            f"No directory record for employee_id {employee_id}.",
            {},
        )

    canonical = canonicalize_item(item)
    if canonical is None:
        named = find_items(item)
        if len(named) > 1:
            detail = ", ".join(named)
            explanation = (
                f"Ambiguous. {item!r} matches more than one catalog item ({detail})."
            )
        else:
            explanation = f"Ambiguous. {item!r} does not map to a catalog item."
        return _result(
            employee_id,
            item,
            None,
            AMBIGUOUS_UNKNOWN_ITEM,
            None,
            explanation,
            {"role": employee["role"], "matched_items": named},
        )

    policy = get_policy_limits(employee["role"], store)
    if not policy["found"]:
        return _result(
            employee_id,
            item,
            canonical,
            AMBIGUOUS_UNKNOWN_ROLE,
            None,
            f"Ambiguous. Role {employee['role']!r} has no policy.",
            {"role": employee["role"]},
        )

    rule = policy["items"].get(canonical)
    owned = [row for row in employee["equipment"] if row["item"] == canonical]
    evidence: dict[str, Any] = {
        "role": employee["role"],
        "current_count": len(owned),
        "tenure_days": employee["tenure_days"],
        "on_probation": employee["on_probation"],
        "probation_days": policy["probation_days"],
        "max_count": None if rule is None else rule["max_count"],
        "refresh_years": None if rule is None else rule["refresh_years"],
        "refresh_days": None,
        "oldest_issued_on": None,
        "oldest_age_days": None,
        "oldest_asset_tag": None,
    }
    if rule is None:
        return _result(
            employee_id,
            item,
            canonical,
            INELIGIBLE_NOT_IN_CATALOG,
            False,
            (
                f"Outside policy. {_role_phrase(employee['role']).capitalize()} is not eligible "
                f"for a {_label(canonical)}. {policy['summary']}"
            ),
            evidence,
        )

    undated = [row for row in owned if row["issued_on"] is None]
    if undated and len(owned) >= rule["max_count"]:
        tags = ", ".join(row["asset_tag"] for row in undated)
        return _result(
            employee_id,
            item,
            canonical,
            AMBIGUOUS_MISSING_HISTORY,
            None,
            (
                f"Ambiguous. {canonical} count is already {len(owned)}, at the limit of "
                f"{rule['max_count']}, and asset {tags} has no issue date, so the refresh "
                "window cannot be checked."
            ),
            evidence,
        )

    refresh_years = rule["refresh_years"]
    if refresh_years is not None:
        evidence["refresh_days"] = refresh_years * store.refresh_year_days

    dated = [row for row in owned if row["issued_on"] is not None]
    if dated:
        oldest = min(dated, key=lambda row: row["issued_on"])
        evidence["oldest_issued_on"] = oldest["issued_on"]
        evidence["oldest_age_days"] = oldest["age_days"]
        evidence["oldest_asset_tag"] = oldest["asset_tag"]

    if len(owned) < rule["max_count"]:
        status = ELIGIBLE_NEW
        within: bool | None = True
        explanation = (
            f"Within policy. {_role_phrase(employee['role']).capitalize()} may have up to "
            f"{rule['max_count']} {_label(canonical, rule['max_count'])}. The employee currently "
            f"has {len(owned)}, so another {_label(canonical)} can be issued."
        )
    elif refresh_years is None:
        status = INELIGIBLE_NO_REFRESH
        within = False
        issued = evidence["oldest_issued_on"] or "an unknown date"
        tag = evidence["oldest_asset_tag"] or "the asset on file"
        explanation = (
            f"Outside policy. {_role_phrase(employee['role']).capitalize()} does not get a "
            f"{_label(canonical)} refresh while one is on file. Asset {tag} was issued on {issued}."
        )
    else:
        age_days = evidence["oldest_age_days"]
        window = evidence["refresh_days"]
        if age_days >= window:
            status = ELIGIBLE_REPLACEMENT
            within = True
            explanation = (
                f"Within policy as a replacement only. {_role_phrase(employee['role']).capitalize()} "
                f"may have up to {rule['max_count']} {_label(canonical, rule['max_count'])}, "
                f"refreshed every {refresh_years} years ({window} days). Asset "
                f"{evidence['oldest_asset_tag']} was issued on {evidence['oldest_issued_on']} "
                f"({age_days} days ago), which is past that window. An additional "
                f"{_label(canonical)} above the limit is not allowed."
            )
        else:
            status = INELIGIBLE_TOO_SOON
            within = False
            explanation = (
                f"Outside policy. {_role_phrase(employee['role']).capitalize()} may have up to "
                f"{rule['max_count']} {_label(canonical, rule['max_count'])}, refreshed every "
                f"{refresh_years} years ({window} days). Asset {evidence['oldest_asset_tag']} "
                f"was issued on {evidence['oldest_issued_on']} ({age_days} days ago), which is "
                "inside that window."
            )

    if status in {ELIGIBLE_NEW, ELIGIBLE_REPLACEMENT} and employee["on_probation"]:
        status = AMBIGUOUS_PROBATION
        within = None
        explanation = (
            f"Ambiguous. The request would otherwise be eligible, but the employee has "
            f"{employee['tenure_days']} days of tenure, inside the {policy['probation_days']}-day "
            "probation window. Probation exceptions are reviewed by a person."
        )

    return _result(employee_id, item, canonical, status, within, explanation, evidence)


def flag_for_human_review(
    employee_id: str,
    request: str,
    reason: str,
    store: ClaimsStore | None = None,
) -> dict[str, Any]:
    """Append the request to the human review queue. Does not approve or deny it."""
    store = store or default_store()
    path = store.review_log
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict[str, Any]] = []
    if path.exists():
        existing = json.loads(path.read_text())
    ticket = {
        "ticket_id": f"REV-{len(existing) + 1:04d}",
        "employee_id": employee_id,
        "request": request,
        "reason": reason,
        "status": "pending_human_review",
        "flagged_at": store.as_of.isoformat(),
    }
    existing.append(ticket)
    path.write_text(json.dumps(existing, indent=2) + "\n")
    return ticket


def _result(
    employee_id: str,
    item: str,
    canonical: str | None,
    status: str,
    within_policy: bool | None,
    explanation: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "employee_id": employee_id,
        "item": item,
        "canonical_item": canonical,
        "status": status,
        "within_policy": within_policy,
        "explanation": explanation,
        "evidence": evidence,
    }
