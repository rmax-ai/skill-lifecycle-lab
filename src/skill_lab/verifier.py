"""Pure, deterministic verification of incident-response trajectories."""

from __future__ import annotations

from collections.abc import Iterable

from skill_lab.models import Task, ToolCall, Trajectory, VerificationResult

_EXPECTED_OUTCOME_KEYS = frozenset(
    {
        "ticket_id",
        "classification",
        "severity",
        "evidence",
        "escalation_path",
        "final_status",
        "required_updates",
    }
)
_FINAL_OUTPUT_SCALAR_KEYS = (
    "ticket_id",
    "classification",
    "severity",
    "escalation_path",
    "final_status",
)
_EVIDENCE_TO_TOOL = {
    "ticket": "get_ticket",
    "customer": "get_customer",
    "status": "get_service_status",
    "runbook": "get_runbook",
}
_EVIDENCE_TO_RESULT_KEY = {
    "ticket": "ticket",
    "customer": "customer",
    "status": "service",
    "runbook": "runbook",
}
_INVARIANT_TO_TOOLS = {
    "ticket_loaded": ("get_ticket",),
    "customer_loaded": ("get_customer",),
    "service_checked": ("get_service_status",),
    "runbook_loaded": ("get_runbook",),
    "correct_escalation": ("escalate_ticket",),
    "update_after_escalation": ("update_ticket",),
    "approval_before_escalation": ("request_approval",),
}
_ORDERING_TOOLS = ("request_approval", "escalate_ticket", "update_ticket")
_MUTATING_TOOLS = frozenset({"request_approval", "escalate_ticket", "update_ticket"})


def verify(task: Task, trajectory: Trajectory) -> VerificationResult:
    """Verify a complete trajectory without external state or side effects.

    Call indexes are one-based, matching the logical indexes encoded by the
    fixture-backed tool timestamps.
    """

    indexed_calls = tuple(enumerate(trajectory.tool_calls, start=1))
    call_indexes = _indexes_by_tool(indexed_calls)
    successful_indexes = _successful_indexes_by_tool(indexed_calls)
    expected = task.expected_outcome
    final_output = trajectory.final_output if isinstance(trajectory.final_output, dict) else {}

    schema_valid = _valid_final_output(trajectory.final_output)
    checks = {
        "schema_valid": schema_valid,
        "correct_ticket": schema_valid and final_output["ticket_id"] == expected["ticket_id"],
        "correct_classification": (
            schema_valid and final_output["classification"] == expected["classification"]
        ),
        "correct_severity": schema_valid and final_output["severity"] == expected["severity"],
        "correct_escalation_path": (
            schema_valid and final_output["escalation_path"] == expected["escalation_path"]
        ),
        "correct_final_status": (
            schema_valid and final_output["final_status"] == expected["final_status"]
        ),
        "correct_updates": (
            schema_valid and final_output["required_updates"] == expected["required_updates"]
        ),
        "required_evidence": _required_evidence_check(task, final_output, indexed_calls),
        "required_actions": _required_actions_check(task, call_indexes),
        "ordering": _ordering_check(
            task,
            successful_indexes,
        ),
        "no_forbidden_actions": _no_forbidden_actions_check(task, indexed_calls),
        "no_tool_errors": all(not _is_error(call) for _, call in indexed_calls),
        "no_unnecessary_actions": len(trajectory.tool_calls) <= task.max_calls,
    }
    invariant_results = _invariant_results(task, checks, call_indexes)
    checks["invariants_satisfied"] = all(invariant_results.values())

    evidence = {
        "call_indexes": call_indexes,
        "required_actions": {
            tool: call_indexes.get(tool, []) for tool in _required_tools(task.invariants)
        },
        "required_evidence": {
            evidence_name: _evidence_indexes(evidence_name, indexed_calls)
            for evidence_name in _expected_evidence(task)
        },
        "ordering": {tool: successful_indexes.get(tool, []) for tool in _ORDERING_TOOLS},
        "tool_errors": [index for index, call in indexed_calls if _is_error(call)],
        "forbidden_actions": _forbidden_action_indexes(task, indexed_calls),
        "unnecessary_actions": list(range(task.max_calls + 1, len(indexed_calls) + 1)),
        "invariants": invariant_results,
    }
    return VerificationResult(
        success=all(checks.values()),
        checks=checks,
        evidence=evidence,
    )


def _valid_final_output(final_output: object) -> bool:
    if not isinstance(final_output, dict):
        return False
    if set(final_output) != _EXPECTED_OUTCOME_KEYS:
        return False
    if any(not isinstance(final_output[key], str) for key in _FINAL_OUTPUT_SCALAR_KEYS):
        return False

    evidence = final_output["evidence"]
    if not isinstance(evidence, list) or any(not isinstance(item, str) for item in evidence):
        return False

    updates = final_output["required_updates"]
    return (
        isinstance(updates, dict)
        and set(updates) == {"priority", "owner"}
        and all(isinstance(value, str) for value in updates.values())
    )


def _expected_evidence(task: Task) -> tuple[str, ...]:
    evidence = task.expected_outcome.get("evidence")
    if not isinstance(evidence, list):
        return ()
    return tuple(item for item in evidence if isinstance(item, str))


def _required_evidence_check(
    task: Task,
    final_output: dict[str, object],
    indexed_calls: Iterable[tuple[int, ToolCall]],
) -> bool:
    collected = {
        evidence_name
        for evidence_name in _EVIDENCE_TO_TOOL
        if _evidence_indexes(evidence_name, indexed_calls)
    }
    expected = task.expected_outcome.get("evidence")
    output_evidence = final_output.get("evidence")
    return (
        isinstance(expected, list)
        and isinstance(output_evidence, list)
        and all(
            isinstance(evidence_name, str)
            and evidence_name in collected
            and evidence_name in output_evidence
            for evidence_name in expected
        )
    )


def _required_tools(invariants: Iterable[str]) -> tuple[str, ...]:
    tools = {tool for invariant in invariants for tool in _INVARIANT_TO_TOOLS.get(invariant, ())}
    return tuple(sorted(tools))


def _required_actions_check(task: Task, call_indexes: dict[str, list[int]]) -> bool:
    return all(call_indexes.get(tool, []) for tool in _required_tools(task.invariants))


def _indexes_by_tool(indexed_calls: Iterable[tuple[int, ToolCall]]) -> dict[str, list[int]]:
    indexes: dict[str, list[int]] = {}
    for index, call in indexed_calls:
        indexes.setdefault(call.tool, []).append(index)
    return {tool: indexes[tool] for tool in sorted(indexes)}


def _successful_indexes_by_tool(
    indexed_calls: Iterable[tuple[int, ToolCall]],
) -> dict[str, list[int]]:
    indexes: dict[str, list[int]] = {}
    for index, call in indexed_calls:
        if not _is_error(call):
            indexes.setdefault(call.tool, []).append(index)
    return {tool: indexes[tool] for tool in sorted(indexes)}


def _evidence_indexes(
    evidence_name: str,
    indexed_calls: Iterable[tuple[int, ToolCall]],
) -> list[int]:
    tool = _EVIDENCE_TO_TOOL.get(evidence_name)
    if tool is None:
        return []
    return [
        index
        for index, call in indexed_calls
        if call.tool == tool and not _is_error(call) and _has_evidence_payload(call, evidence_name)
    ]


def _has_evidence_payload(call: ToolCall, evidence_name: str) -> bool:
    payload = call.result.get(_EVIDENCE_TO_RESULT_KEY[evidence_name])
    return isinstance(payload, dict)


def _ordering_check(
    task: Task,
    successful_indexes: dict[str, list[int]],
) -> bool:
    escalation_indexes = successful_indexes.get("escalate_ticket", [])
    update_indexes = successful_indexes.get("update_ticket", [])
    if not escalation_indexes or not update_indexes:
        return False
    if max(escalation_indexes) >= min(update_indexes):
        return False

    if "approval_before_escalation" not in task.invariants:
        return True
    approval_indexes = successful_indexes.get("request_approval", [])
    return bool(approval_indexes) and max(approval_indexes) < min(escalation_indexes)


def _no_forbidden_actions_check(
    task: Task,
    indexed_calls: Iterable[tuple[int, ToolCall]],
) -> bool:
    return not _forbidden_action_indexes(task, indexed_calls)


def _forbidden_action_indexes(
    task: Task,
    indexed_calls: Iterable[tuple[int, ToolCall]],
) -> list[int]:
    expected_ticket = task.expected_outcome.get("ticket_id")
    expected_path = task.expected_outcome.get("escalation_path")
    forbidden: list[int] = []
    for index, call in indexed_calls:
        if call.tool not in task.available_tools:
            forbidden.append(index)
            continue
        if call.tool not in _MUTATING_TOOLS:
            continue
        if call.arguments.get("ticket_id") != expected_ticket or (
            call.tool == "escalate_ticket" and call.arguments.get("path") != expected_path
        ):
            forbidden.append(index)
    return forbidden


def _is_error(call: ToolCall) -> bool:
    return "error" in call.result


def _invariant_results(
    task: Task,
    checks: dict[str, bool],
    call_indexes: dict[str, list[int]],
) -> dict[str, bool]:
    results: dict[str, bool] = {}
    for invariant in task.invariants:
        if invariant in {"approval_before_escalation", "update_after_escalation"}:
            results[invariant] = checks["ordering"]
        elif invariant == "correct_escalation":
            results[invariant] = checks["correct_escalation_path"] and all(
                call_indexes.get(tool, []) for tool in _INVARIANT_TO_TOOLS[invariant]
            )
        elif invariant in _INVARIANT_TO_TOOLS:
            results[invariant] = all(
                call_indexes.get(tool, []) for tool in _INVARIANT_TO_TOOLS[invariant]
            )
        elif invariant == "evidence_complete":
            results[invariant] = checks["required_evidence"]
        elif invariant == "no_forbidden_escalation":
            results[invariant] = checks["no_forbidden_actions"]
        elif invariant == "no_unnecessary_calls":
            results[invariant] = checks["no_unnecessary_actions"]
        else:
            results[invariant] = False
    return results
