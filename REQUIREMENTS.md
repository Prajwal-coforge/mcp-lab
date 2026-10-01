# IT equipment request handler

Employees submit equipment requests. The system looks up the person, applies that role's policy, and returns one of three decisions: approve, deny, or escalate. It does not guess when the record and the request disagree.

The policy clock is **2026-09-30**. A refresh year is **365 days**. Tenure under **90 days** is probation.

## Request data

Every request has four facts. The role is never taken from the message; it comes from the directory.

| Field | Source | Required |
| --- | --- | --- |
| Employee id | The message, shaped `E` plus four digits (`E1002`) | Yes |
| Role | `get_employee_info` | Yes |
| Item | Catalog key resolved from the message | Yes, exactly one |
| Reason | The rest of the message: refresh, extra unit, accommodation, or a claim about age | Yes |

Catalog keys: `laptop`, `monitor`, `headset`, `keyboard`, `mouse`, `docking_station`.

Phrases map as follows: screen or display → monitor; dock or docking station → docking station; notebook → laptop; headphones → headset. A phrase that maps to zero keys, or to more than one key, is not a request the system can decide.

## Policy rules

Counts are the maximum number of that item on file. A refresh year applies only when the employee is already at that maximum. Under the maximum, another unit is allowed. At the maximum, the oldest unit may be replaced once it is at least `refresh_years * 365` days old. A replacement does not raise the count. `refresh_years: null` means no automatic replacement.

### Individual contributor

One laptop every 4 years. One monitor every 3 years. One headset, one keyboard, and one mouse every 2 years. Docking stations are not covered.

### Manager

One laptop every 2 years. Up to two monitors every 3 years. One docking station every 3 years. One headset, one keyboard, and one mouse every 2 years.

### Executive

One laptop every 2 years. Up to two monitors every 2 years. One docking station every 2 years. One headset, one keyboard, and one mouse every 2 years.

### Contractor

One loaner laptop, and only when none is already on file. No refresh cycle. No peripherals.

## Decisions

**Approve** only when all of these are true:

- The employee is in the directory and the role has a policy.
- The message names exactly one catalog item.
- `check_request_eligibility` returns `eligible_new`, or it returns `eligible_replacement` and the message is clearly a replacement (replace, refresh, broken, worn, too slow, or years old) and not also a request for an extra unit.
- The employee is not in probation.
- The message does not cite a medical accommodation, and it does not claim an age that disagrees with the asset record by more than one year.

**Deny** only when the eligibility tool returns an `ineligible_*` status and none of the escalation rules below apply. A clear denial is final. It is not sent to the review queue.

| Status | Meaning |
| --- | --- |
| `ineligible_too_soon` | Already at the count limit, and the oldest unit is inside the refresh window |
| `ineligible_not_in_catalog` | That role cannot have the item |
| `ineligible_no_refresh` | At the limit, and the role has no refresh cycle |

`eligible_replacement` plus a clear request for an additional unit (second, another, additional, extra) is also a denial: the refresh does not raise the cap.

**Escalate** by calling `flag_for_human_review` in every other case. Escalation does not approve or deny.

| Situation | Why it is not decided automatically |
| --- | --- |
| Employee id is missing or not in the directory | There is no role to apply |
| The message names zero catalog items, or more than one | Choosing an item would be a guess |
| Eligibility status is `ambiguous_probation` | Tenure is under 90 days and the request would otherwise be eligible |
| Eligibility status is `ambiguous_missing_history` | At the count limit, and an asset of that type has no issue date |
| Eligibility status is `ambiguous_unknown_role` | The directory role has no policy |
| Eligibility status is `ambiguous_unknown_item` | The item phrase does not resolve |
| `eligible_replacement`, and the message does not say replacement versus additional | Either decision could be wrong |
| The message cites a doctor's note, physician, disability, ADA, or medical accommodation | Standard limits do not decide accommodations |
| The message says the item is N years old, and the newest asset of that type differs by more than one year | The record and the request disagree |

## Tool contracts

`get_employee_info(employee_id)` returns `found`, `role`, `tenure_days`, `on_probation`, and `equipment` (item, issued date, asset tag, age in days). An unknown id returns `found: false` and does not raise.

`get_policy_limits(role)` returns `probation_days`, `refresh_year_days`, a summary, and per-item `max_count` and `refresh_years`. An unknown role returns `found: false`.

`check_request_eligibility(employee_id, item)` returns `status`, `within_policy` (`true`, `false`, or `null`), `canonical_item`, `explanation`, and `evidence`. `within_policy` is true only for `eligible_new` and `eligible_replacement`. It is false for every `ineligible_*` status. It is null when the case is ambiguous or the employee does not exist.

`flag_for_human_review(employee_id, request, reason)` appends a ticket to the review queue and returns `ticket_id`, `status: pending_human_review`, and the reason. It does not change the employee's equipment.

## Worked examples

These are the four messages the agent runs. Expectations follow the rules above, with the policy clock at 2026-09-30.

1. **Approve.** "Please process this for employee E1002, Jordan Lee. I need a second monitor so I can keep our team dashboard up during calls." Jordan is a manager with one monitor. Managers may have two. Status `eligible_new`.

2. **Deny.** "Employee E1001 wants another monitor. The current one works, they just prefer a bigger screen." Priya is an individual contributor with monitor MN-1001 issued 2024-01-10 (994 days before the clock). The limit is one monitor every 3 years (1095 days). Status `ineligible_too_soon`.

3. **Escalate, ambiguous item.** "Employee E1005 says the desk setup is uncomfortable and needs a better screen or maybe a dock, whichever fits policy. Please just pick one and ship it." The phrase matches monitor and docking station. Status `ambiguous_unknown_item`. The agent must not pick one.

4. **Escalate, conflicting record and accommodation.** "Employee E1007 here. My laptop is 4 years old and too slow to do my job. Please replace it. I also have a doctor's note about needing an ergonomic setup." Morgan's laptop LT-1007 was issued 2025-06-01, about 1.3 years before the clock, so a pure policy read is `ineligible_too_soon`. The message claims 4 years and cites a doctor's note. Both facts block a denial.
