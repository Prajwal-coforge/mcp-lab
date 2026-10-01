"""Unit tests for the plain functions behind each MCP tool."""

from __future__ import annotations

import copy
import json
from datetime import date

from equipment_claims.service import (
    check_request_eligibility,
    flag_for_human_review,
    get_employee_info,
    get_policy_limits,
)
from equipment_claims.store import ClaimsStore, default_store

AS_OF = date(2026, 9, 30)


def _store(tmp_path, employees=None) -> ClaimsStore:
    base = default_store()
    return ClaimsStore(
        employees=base.employees if employees is None else employees,
        policies=base.policies,
        review_log=tmp_path / "review_queue.json",
        as_of=base.as_of,
    )


class TestGetEmployeeInfo:
    def test_returns_role_tenure_and_equipment(self):
        info = get_employee_info("E1001")

        assert info["found"] is True
        assert info["name"] == "Priya Shah"
        assert info["role"] == "individual_contributor"
        assert info["hired_on"] == "2021-01-15"
        assert info["tenure_days"] == (AS_OF - date(2021, 1, 15)).days
        assert info["on_probation"] is False
        assert [row["asset_tag"] for row in info["equipment"]] == ["LT-1001", "MN-1001", "HS-1001"]
        assert info["equipment"][0]["age_days"] == (AS_OF - date(2021, 3, 1)).days

    def test_probation_employee(self):
        info = get_employee_info("E1003")

        assert info["tenure_days"] == (AS_OF - date(2026, 8, 1)).days
        assert info["tenure_days"] < 90
        assert info["on_probation"] is True

    def test_unknown_employee_is_a_result_not_an_error(self):
        info = get_employee_info("E1006")

        assert info["found"] is False
        assert info["error"] == "employee_not_found"
        assert info["role"] is None
        assert info["tenure_days"] is None
        assert info["equipment"] == []

    def test_blank_employee_id(self):
        info = get_employee_info("")

        assert info["found"] is False
        assert info["employee_id"] == ""


class TestGetPolicyLimits:
    def test_individual_contributor_limits(self):
        policy = get_policy_limits("individual_contributor")

        assert policy["found"] is True
        assert policy["probation_days"] == 90
        assert policy["refresh_year_days"] == 365
        assert policy["items"]["laptop"] == {"max_count": 1, "refresh_years": 4}
        assert policy["items"]["monitor"] == {"max_count": 1, "refresh_years": 3}
        assert "docking_station" not in policy["items"]

    def test_manager_gets_a_second_monitor_and_a_shorter_laptop_cycle(self):
        policy = get_policy_limits("manager")

        assert policy["items"]["monitor"]["max_count"] == 2
        assert policy["items"]["monitor"]["refresh_years"] == 3
        assert policy["items"]["laptop"]["refresh_years"] == 2
        assert policy["items"]["docking_station"]["max_count"] == 1

    def test_contractor_laptop_has_no_refresh(self):
        policy = get_policy_limits("contractor")

        assert list(policy["items"]) == ["laptop"]
        assert policy["items"]["laptop"]["refresh_years"] is None

    def test_unknown_role(self):
        policy = get_policy_limits("intern")

        assert policy["found"] is False
        assert policy["error"] == "unknown_role"
        assert policy["items"] == {}


class TestCheckRequestEligibility:
    def test_manager_second_monitor_is_eligible(self):
        result = check_request_eligibility("E1002", "monitor")

        assert result["status"] == "eligible_new"
        assert result["within_policy"] is True
        assert result["canonical_item"] == "monitor"
        assert result["evidence"]["current_count"] == 1
        assert result["evidence"]["max_count"] == 2

    def test_screen_is_an_alias_for_monitor(self):
        result = check_request_eligibility("E1002", "screen")

        assert result["canonical_item"] == "monitor"
        assert result["status"] == "eligible_new"

    def test_monitor_inside_refresh_window_is_denied(self):
        result = check_request_eligibility("E1001", "monitor")

        assert result["status"] == "ineligible_too_soon"
        assert result["within_policy"] is False
        assert result["evidence"]["max_count"] == 1
        assert result["evidence"]["refresh_years"] == 3
        assert result["evidence"]["oldest_age_days"] < result["evidence"]["refresh_days"]

    def test_laptop_past_refresh_is_a_replacement_only(self):
        result = check_request_eligibility("E1001", "laptop")

        assert result["status"] == "eligible_replacement"
        assert result["within_policy"] is True
        assert result["evidence"]["oldest_asset_tag"] == "LT-1001"
        assert result["evidence"]["oldest_age_days"] >= result["evidence"]["refresh_days"]

    def test_item_outside_the_role_catalog(self):
        result = check_request_eligibility("E1004", "monitor")

        assert result["status"] == "ineligible_not_in_catalog"
        assert result["within_policy"] is False
        assert result["canonical_item"] == "monitor"

    def test_contractor_does_not_get_a_laptop_refresh(self):
        result = check_request_eligibility("E1004", "laptop")

        assert result["status"] == "ineligible_no_refresh"
        assert result["within_policy"] is False
        assert result["evidence"]["oldest_asset_tag"] == "LT-1004"

    def test_probation_blocks_an_otherwise_eligible_request(self):
        result = check_request_eligibility("E1003", "monitor")

        assert result["status"] == "ambiguous_probation"
        assert result["within_policy"] is None
        assert result["evidence"]["on_probation"] is True
        assert result["evidence"]["current_count"] == 0

    def test_probation_does_not_hide_a_clear_denial(self):
        result = check_request_eligibility("E1003", "laptop")

        assert result["status"] == "ineligible_too_soon"
        assert result["within_policy"] is False

    def test_phrase_naming_two_items_is_ambiguous(self):
        result = check_request_eligibility("E1005", "screen or dock")

        assert result["status"] == "ambiguous_unknown_item"
        assert result["within_policy"] is None
        assert result["canonical_item"] is None
        assert result["evidence"]["matched_items"] == ["monitor", "docking_station"]

    def test_unknown_item_word(self):
        result = check_request_eligibility("E1001", "standing desk")

        assert result["status"] == "ambiguous_unknown_item"
        assert result["evidence"]["matched_items"] == []

    def test_missing_issue_date_at_the_count_cap(self):
        result = check_request_eligibility("E1008", "keyboard")

        assert result["status"] == "ambiguous_missing_history"
        assert result["within_policy"] is None
        assert "KB-1008" in result["explanation"]

    def test_unknown_employee(self):
        result = check_request_eligibility("E1006", "laptop")

        assert result["status"] == "not_found"
        assert result["within_policy"] is None

    def test_unknown_role_is_ambiguous(self, tmp_path):
        employees = copy.deepcopy(default_store().employees)
        employees["E1001"]["role"] = "intern"
        store = _store(tmp_path, employees)

        result = check_request_eligibility("E1001", "laptop", store)

        assert result["status"] == "ambiguous_unknown_role"
        assert result["within_policy"] is None


class TestFlagForHumanReview:
    def test_appends_a_pending_ticket(self, tmp_path):
        store = _store(tmp_path)
        before = get_employee_info("E1005", store)

        ticket = flag_for_human_review(
            "E1005",
            "Needs a screen or a dock.",
            "The request names two catalog items.",
            store,
        )

        assert ticket["ticket_id"] == "REV-0001"
        assert ticket["status"] == "pending_human_review"
        assert ticket["employee_id"] == "E1005"
        assert ticket["flagged_at"] == "2026-09-30"
        assert ticket["reason"] == "The request names two catalog items."
        saved = json.loads(store.review_log.read_text())
        assert saved == [ticket]
        assert get_employee_info("E1005", store) == before

    def test_second_flag_gets_the_next_id(self, tmp_path):
        store = _store(tmp_path)
        flag_for_human_review("E1005", "first", "ambiguous item", store)
        second = flag_for_human_review("E1007", "second", "conflicting age", store)

        assert second["ticket_id"] == "REV-0002"
        saved = json.loads(store.review_log.read_text())
        assert [row["ticket_id"] for row in saved] == ["REV-0001", "REV-0002"]

    def test_unknown_employee_can_still_be_escalated(self, tmp_path):
        store = _store(tmp_path)

        ticket = flag_for_human_review("E1006", "laptop", "Employee id is not in the directory.", store)

        assert ticket["employee_id"] == "E1006"
        assert ticket["status"] == "pending_human_review"
