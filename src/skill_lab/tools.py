"""Deterministic fixture-backed tools for the incident task environment."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from skill_lab.models import ToolCall

_TOOL_ARGUMENTS = {
    "search_tickets": ("query",),
    "get_ticket": ("ticket_id",),
    "get_customer": ("customer_id",),
    "get_service_status": ("service_id",),
    "get_runbook": ("service_id",),
    "request_approval": ("ticket_id", "reason"),
    "escalate_ticket": ("ticket_id", "path"),
    "update_ticket": ("ticket_id", "priority", "owner", "status"),
}


class ToolEnvironment:
    """Execute the fixed tool set against an isolated copy of fixture data."""

    def __init__(self, fixtures: dict) -> None:
        self._fixtures = deepcopy(fixtures)
        self._calls: list[ToolCall] = []
        self._tickets = self._index("tickets", "id")
        self._customers = self._index("customers", "id")
        self._services = self._index("services", "id")
        self._runbooks = self._index("runbooks", "service_id")
        self._ticket_owners: dict[str, str] = {}
        self._escalations: dict[str, str] = {}

    @property
    def calls(self) -> list[ToolCall]:
        """Return the recorded calls without exposing the call list itself."""

        return list(self._calls)

    def execute(self, name: str, arguments: dict) -> dict:
        """Execute one tool and record its result, including errors."""

        if name not in _TOOL_ARGUMENTS:
            result = self._error("unknown_tool", f"unknown tool: {name!r}")
            return self._record(name, arguments, result)

        if not isinstance(arguments, dict):
            result = self._error(
                "invalid_arguments",
                f"{name} arguments must be an object",
            )
            return self._record(name, {}, result)

        argument_error = self._validate_arguments(name, arguments)
        if argument_error is not None:
            return self._record(name, arguments, argument_error)

        result = getattr(self, f"_run_{name}")(**arguments)
        return self._record(name, arguments, result)

    def _index(self, collection: str, key: str) -> dict[str, dict[str, Any]]:
        records = self._fixtures.get(collection, [])
        if not isinstance(records, list):
            return {}
        return {
            record[key]: record
            for record in records
            if isinstance(record, dict) and isinstance(record.get(key), str)
        }

    @staticmethod
    def _error(code: str, message: str) -> dict:
        return {"error": {"code": code, "message": message}}

    @staticmethod
    def _timestamp(call_index: int) -> str:
        return f"2000-01-01T00:00:{call_index:02d}.000Z"

    def _record(self, name: str, arguments: dict, result: dict) -> dict:
        self._calls.append(
            ToolCall(
                tool=name,
                arguments=deepcopy(arguments),
                result=deepcopy(result),
                timestamp=self._timestamp(len(self._calls) + 1),
            )
        )
        return deepcopy(result)

    def _validate_arguments(self, name: str, arguments: dict) -> dict | None:
        expected = set(_TOOL_ARGUMENTS[name])
        actual = set(arguments)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            details = []
            if missing:
                details.append(f"missing {', '.join(missing)}")
            if extra:
                details.append(f"unexpected {', '.join(extra)}")
            return self._error(
                "invalid_arguments",
                f"{name} arguments have invalid keys ({'; '.join(details)})",
            )
        if any(not isinstance(arguments[key], str) for key in expected):
            return self._error(
                "invalid_arguments",
                f"{name} arguments must contain strings",
            )
        return None

    def _run_search_tickets(self, query: str) -> dict:
        needle = query.casefold()
        tickets = [
            {
                "id": ticket["id"],
                "summary": ticket["summary"],
                "status": ticket["status"],
            }
            for ticket in self._tickets.values()
            if needle in ticket["id"].casefold() or needle in ticket["summary"].casefold()
        ]
        tickets.sort(key=lambda ticket: ticket["id"])
        return {"tickets": tickets}

    def _run_get_ticket(self, ticket_id: str) -> dict:
        ticket = self._tickets.get(ticket_id)
        if ticket is None:
            return self._not_found("ticket", ticket_id)
        return {"ticket": deepcopy(ticket)}

    def _run_get_customer(self, customer_id: str) -> dict:
        customer = self._customers.get(customer_id)
        if customer is None:
            return self._not_found("customer", customer_id)
        return {"customer": deepcopy(customer)}

    def _run_get_service_status(self, service_id: str) -> dict:
        service = self._services.get(service_id)
        if service is None:
            return self._not_found("service", service_id)
        return {"service": deepcopy(service)}

    def _run_get_runbook(self, service_id: str) -> dict:
        runbook = self._runbooks.get(service_id)
        if runbook is None:
            return self._not_found("runbook", service_id)
        return {"runbook": deepcopy(runbook)}

    def _run_request_approval(self, ticket_id: str, reason: str) -> dict:
        if ticket_id not in self._tickets:
            return self._not_found("ticket", ticket_id)
        return {"approval": {"ticket_id": ticket_id, "status": "approved"}}

    def _run_escalate_ticket(self, ticket_id: str, path: str) -> dict:
        ticket = self._tickets.get(ticket_id)
        if ticket is None:
            return self._not_found("ticket", ticket_id)
        if ticket["status"] == "escalated":
            return self._error(
                "precondition_failed",
                f"ticket {ticket_id!r} has already been escalated",
            )
        ticket["status"] = "escalated"
        self._escalations[ticket_id] = path
        return {
            "escalation": {
                "ticket_id": ticket_id,
                "path": path,
                "status": "escalated",
            }
        }

    def _run_update_ticket(
        self,
        ticket_id: str,
        priority: str,
        owner: str,
        status: str,
    ) -> dict:
        ticket = self._tickets.get(ticket_id)
        if ticket is None:
            return self._not_found("ticket", ticket_id)
        ticket["priority"] = priority
        ticket["status"] = status
        self._ticket_owners[ticket_id] = owner
        return {
            "ticket": {
                "id": ticket_id,
                "priority": priority,
                "owner": owner,
                "status": status,
            }
        }

    @staticmethod
    def _not_found(kind: str, identifier: str) -> dict:
        return ToolEnvironment._error(
            "not_found",
            f"{kind} {identifier!r} was not found",
        )
