"""The four requests the demo and the pipeline both run."""

SCENARIOS = [
    {
        "id": "approve-second-monitor",
        "expected": "approve",
        "reflection": "confirms",
        "request": (
            "Please process this for employee E1002, Jordan Lee. I need a second "
            "monitor so I can keep our team dashboard up during calls."
        ),
    },
    {
        "id": "deny-extra-monitor",
        "expected": "deny",
        "reflection": "confirms",
        "request": (
            "Employee E1001 wants another monitor. The current one works, they "
            "just prefer a bigger screen."
        ),
    },
    {
        "id": "escalate-ambiguous-item",
        "expected": "escalate",
        "reflection": "revises",
        "request": (
            "Employee E1005 says the desk setup is uncomfortable and needs a better "
            "screen or maybe a dock, whichever fits policy. Please just pick one and ship it."
        ),
    },
    {
        "id": "escalate-conflict-and-accommodation",
        "expected": "escalate",
        "reflection": "revises",
        "request": (
            "Employee E1007 here. My laptop is 4 years old and too slow to do my job. "
            "Please replace it. I also have a doctor's note about needing an ergonomic setup."
        ),
    },
]
