import json
from pathlib import Path

from skill_lab.tools import ToolEnvironment

_FIXTURES = Path(__file__).resolve().parents[1] / "datasets" / "tool_world.json"


def _environment() -> ToolEnvironment:
    fixtures = json.loads(_FIXTURES.read_text(encoding="utf-8"))
    return ToolEnvironment(fixtures)


def test_search_tickets() -> None:
    environment = _environment()

    result = environment.execute("search_tickets", {"query": "InCiDeNt t0"})

    assert [ticket["id"] for ticket in result["tickets"]] == [
        f"T{index:02d}" for index in range(1, 10)
    ]
    assert result["tickets"][0] == {
        "id": "T01",
        "summary": "outage incident T01",
        "status": "open",
    }
    assert environment.calls[0].timestamp == "2000-01-01T00:00:01.000Z"


def test_get_ticket() -> None:
    environment = _environment()

    result = environment.execute("get_ticket", {"ticket_id": "T01"})

    assert result == {
        "ticket": {
            "classification": "outage",
            "customer_id": "C01",
            "id": "T01",
            "impact": "regional",
            "priority": "SEV1",
            "service_id": "S-CORE",
            "status": "open",
            "summary": "outage incident T01",
        }
    }
    result["ticket"]["status"] = "mutated"
    assert environment.execute("get_ticket", {"ticket_id": "T01"})["ticket"]["status"] == "open"


def test_get_customer() -> None:
    environment = _environment()

    result = environment.execute("get_customer", {"customer_id": "C01"})

    assert result == {
        "customer": {
            "approval_required": True,
            "id": "C01",
            "region": "us",
            "tier": "enterprise",
        }
    }


def test_get_service_status() -> None:
    environment = _environment()

    result = environment.execute("get_service_status", {"service_id": "S-CORE"})

    assert result == {
        "service": {
            "affected_regions": ["us", "eu"],
            "id": "S-CORE",
            "incident_id": "INC-CORE",
            "status": "degraded",
        }
    }


def test_get_runbook() -> None:
    environment = _environment()

    result = environment.execute("get_runbook", {"service_id": "S-CORE"})

    assert result == {
        "runbook": {
            "approval_required": True,
            "escalation_paths": {
                "degradation": "service-desk",
                "outage": "pager",
            },
            "required_evidence": ["ticket", "customer", "status", "runbook"],
            "service_id": "S-CORE",
        }
    }


def test_request_approval() -> None:
    environment = _environment()

    result = environment.execute(
        "request_approval",
        {"ticket_id": "T01", "reason": "routine incident review"},
    )

    assert result == {"approval": {"ticket_id": "T01", "status": "approved"}}
    assert environment.execute("get_ticket", {"ticket_id": "T01"})["ticket"]["status"] == "open"


def test_escalate_ticket_trap_is_committable() -> None:
    environment = _environment()

    result = environment.execute(
        "escalate_ticket",
        {"ticket_id": "T01", "path": "pager"},
    )

    assert result == {
        "escalation": {
            "ticket_id": "T01",
            "path": "pager",
            "status": "escalated",
        }
    }
    assert [call.tool for call in environment.calls] == ["escalate_ticket"]
    assert environment.execute("search_tickets", {"query": "T01"}) == {
        "tickets": [{"id": "T01", "summary": "outage incident T01", "status": "escalated"}]
    }


def test_update_ticket() -> None:
    environment = _environment()

    result = environment.execute(
        "update_ticket",
        {
            "ticket_id": "T02",
            "priority": "SEV3",
            "owner": "service-desk",
            "status": "resolved",
        },
    )

    assert result == {
        "ticket": {
            "id": "T02",
            "priority": "SEV3",
            "owner": "service-desk",
            "status": "resolved",
        }
    }
    assert environment.execute("get_ticket", {"ticket_id": "T02"})["ticket"]["priority"] == "SEV3"


def test_tool_error_shape() -> None:
    environment = _environment()

    errors = [
        environment.execute("missing_tool", {}),
        environment.execute("get_ticket", {}),
        environment.execute("get_ticket", {"ticket_id": "T99"}),
    ]
    environment.execute("escalate_ticket", {"ticket_id": "T01", "path": "pager"})
    errors.append(environment.execute("escalate_ticket", {"ticket_id": "T01", "path": "pager"}))

    assert [error["error"]["code"] for error in errors] == [
        "unknown_tool",
        "invalid_arguments",
        "not_found",
        "precondition_failed",
    ]
    assert all(
        set(result) == {"error"}
        and set(result["error"]) == {"code", "message"}
        and isinstance(result["error"]["message"], str)
        for result in errors
    )
    assert len(environment.calls) == 5
