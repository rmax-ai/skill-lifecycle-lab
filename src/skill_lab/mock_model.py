"""Deterministic, offline model responses for the incident-response harness."""

from __future__ import annotations

import hashlib
import json
import random
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from skill_lab.models import CandidateSkill

_MODEL_ID = "mock-incident-v1"
_MUTATION_MODES = frozenset({"mutation", "mutate", "propose_mutation"})
_TOOL_ACTION = "tool"
_FINAL_ACTION = "final"
_SERVICE_CLASSES = {
    "outage": "S-CORE",
    "degradation": "S-CORE",
    "security": "S-AUTH",
    "billing": "S-BILL",
}
_CLASSIFICATIONS = ("outage", "degradation", "security", "billing")
_SEVERITIES = (
    "SEV1",
    "SEV2",
    "SEV1",
    "SEV3",
    "SEV2",
    "SEV3",
    "SEV2",
    "SEV3",
    "SEV1",
    "SEV2",
    "SEV1",
    "SEV3",
    "SEV2",
    "SEV2",
    "SEV1",
    "SEV3",
    "SEV1",
    "SEV3",
    "SEV2",
    "SEV3",
    "SEV1",
    "SEV2",
    "SEV1",
    "SEV3",
)


class ModelResponse(BaseModel):
    """The small response envelope shared by the mock and later model clients."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str | None = None
    content: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    finish_reason: str = "stop"

    @property
    def text(self) -> str:
        """Expose the JSON body under the common text response name."""

        return self.content

    @property
    def payload(self) -> dict[str, Any]:
        """Decode the JSON body for callers that need structured actions."""

        payload = json.loads(self.content)
        if not isinstance(payload, dict):
            raise ValueError("mock model response must contain a JSON object")
        return payload

    @property
    def output(self) -> dict[str, Any]:
        """Alias for the structured response body."""

        return self.payload

    def __getitem__(self, key: str) -> Any:
        """Allow response access without losing the typed response envelope."""

        return self.payload[key]

    def get(self, key: str, default: Any = None) -> Any:
        """Provide mapping-like access for simple model clients."""

        return self.payload.get(key, default)


class ScriptedMockModel:
    """A seeded model with fixed agent and mutation behavior.

    Agent requests are independent: the caller sends the complete tool history
    on every request, so this model does not need mutable conversation state.
    Mutation requests only select between the two planned candidate templates.
    """

    model_id = _MODEL_ID

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self._random = random.Random(seed)
        self._candidate_nonce = self._random.getrandbits(48)

    def complete(self, request: dict) -> ModelResponse:
        """Return a deterministic response for a JSON-compatible request."""

        if not isinstance(request, Mapping):
            raise TypeError("mock model requests must be JSON objects")

        normalized_request = _jsonable(request)
        if self._is_mutation_request(normalized_request):
            payload = self._mutation_payload(normalized_request)
        else:
            payload = self._agent_payload(normalized_request)
        return self._response(normalized_request, payload)

    @staticmethod
    def _is_mutation_request(request: Mapping[str, Any]) -> bool:
        kind = request.get("kind", request.get("type", request.get("operation")))
        mode = request.get("mode")
        return (
            kind in _MUTATION_MODES
            or mode in _MUTATION_MODES
            or mode in {"promote", "reject", "bad", "good"}
            or "current_skill" in request
            or "train_failures" in request
        )

    def _response(self, request: dict[str, Any], payload: dict[str, Any]) -> ModelResponse:
        request_json = _canonical_json(request)
        content = _canonical_json(payload)
        input_tokens = max(1, (len(request_json) + 3) // 4)
        output_tokens = max(1, (len(content) + 3) // 4)
        return ModelResponse(
            model=_MODEL_ID,
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            latency_ms=max(1, len(content) // 8),
        )

    def _agent_payload(self, request: Mapping[str, Any]) -> dict[str, Any]:
        task = _mapping(request.get("task")) or dict(request)
        task_id = request.get("task_id") or task.get("id")
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("agent request must include task_id or task.id")

        expected = task.get("expected_outcome")
        if not isinstance(expected, Mapping):
            expected = _fallback_expected(task_id)
        else:
            expected = dict(expected)

        invariants = task.get("invariants")
        if not isinstance(invariants, Sequence) or isinstance(invariants, (str, bytes)):
            invariants = _fallback_invariants(task_id, expected)
        invariants = set(invariants)
        skill_version = request.get("skill_version")
        if not isinstance(skill_version, str):
            skill_version = None
        history = _history(request.get("tool_history", request.get("tool_calls", [])))
        task_id = str(task_id)

        next_action = self._next_agent_action(
            task_id=task_id,
            expected=expected,
            invariants=invariants,
            skill_version=skill_version,
            history=history,
            no_skill=skill_version is None,
        )
        return next_action

    def _next_agent_action(
        self,
        *,
        task_id: str,
        expected: Mapping[str, Any],
        invariants: set[Any],
        skill_version: str | None,
        history: list[dict[str, Any]],
        no_skill: bool,
    ) -> dict[str, Any]:
        successful_tools = {
            entry["tool"]
            for entry in history
            if entry.get("tool") and not _is_error(entry.get("result"))
        }
        attempted_tools = {entry["tool"] for entry in history if entry.get("tool")}
        last_results = {
            entry["tool"]: entry.get("result", {}) for entry in history if entry.get("tool")
        }

        if no_skill and "search_tickets" not in attempted_tools:
            return _tool_action("search_tickets", {"query": task_id})

        ticket_id = _string_value(expected.get("ticket_id"), _ticket_from_task(task_id))
        ticket_result = _nested_mapping(last_results.get("get_ticket"), "ticket")
        customer_id = _string_value(
            ticket_result.get("customer_id"),
            _customer_from_ticket(ticket_id),
        )
        service_id = _string_value(
            ticket_result.get("service_id"),
            _service_from_classification(expected.get("classification")),
        )

        if "get_ticket" not in successful_tools:
            return _tool_action("get_ticket", {"ticket_id": ticket_id})
        if "get_customer" not in successful_tools:
            return _tool_action("get_customer", {"customer_id": customer_id})
        if (
            "status" in _string_set(expected.get("evidence"))
            and "get_service_status" not in successful_tools
        ):
            return _tool_action("get_service_status", {"service_id": service_id})
        if "get_runbook" not in successful_tools:
            return _tool_action("get_runbook", {"service_id": service_id})

        approval_required = "approval_before_escalation" in invariants
        skips_seed_approval = skill_version == "v001" and task_id == "IR-VA-02"
        if (
            approval_required
            and not skips_seed_approval
            and "request_approval" not in successful_tools
        ):
            return _tool_action(
                "request_approval",
                {"ticket_id": ticket_id, "reason": "customer incident escalation"},
            )

        escalation_seen = "escalate_ticket" in attempted_tools
        if not escalation_seen:
            path = _string_value(expected.get("escalation_path"), "support")
            if skill_version == "v003" and task_id == "IR-VA-03":
                path = "forbidden-path"
            return _tool_action(
                "escalate_ticket",
                {"ticket_id": ticket_id, "path": path},
            )

        if "update_ticket" not in successful_tools and "update_ticket" not in attempted_tools:
            updates = _mapping(expected.get("required_updates")) or {}
            return _tool_action(
                "update_ticket",
                {
                    "ticket_id": ticket_id,
                    "priority": _string_value(expected.get("severity"), "SEV3"),
                    "owner": _string_value(updates.get("owner"), "support"),
                    "status": _string_value(expected.get("final_status"), "resolved"),
                },
            )

        return {"action": _FINAL_ACTION, "output": dict(expected)}

    def _mutation_payload(self, request: Mapping[str, Any]) -> dict[str, Any]:
        mode = _mutation_mode(request)
        current_skill = _mapping(request.get("current_skill"))
        parent_version = _skill_version(current_skill) or request.get("parent_version")
        if not isinstance(parent_version, str) or not re.fullmatch(r"v[0-9]{3}", parent_version):
            parent_version = "v001"

        if mode in {"reject", "bad"} or (mode is None and parent_version != "v001"):
            if parent_version == "v001":
                parent_version = "v002"
            candidate_version = "v003"
            generation = 2
            candidate_kind = "reject"
        else:
            candidate_version = "v002"
            generation = 1
            candidate_kind = "promote"

        name, description = _skill_identity(current_skill)
        candidate_markdown = _candidate_markdown(
            name=name,
            description=description,
            version=candidate_version,
            candidate_kind=candidate_kind,
        )
        candidate_id = self._candidate_id(parent_version, candidate_version)
        candidate = CandidateSkill(
            candidate_id=candidate_id,
            parent_version=parent_version,
            candidate_version=candidate_version,
            generation=generation,
            failure_analysis=(
                "The procedure needs an explicit approval decision before escalation."
                if candidate_kind == "promote"
                else "The proposed security escalation rule permits an invalid path."
            ),
            procedural_change=(
                "Require customer approval before every approval-gated escalation."
                if candidate_kind == "promote"
                else "Add a security escalation pre-check using a restricted path."
            ),
            candidate_markdown=candidate_markdown,
            rationale=(
                "The change makes the approval requirement procedural and verifiable."
                if candidate_kind == "promote"
                else "The scripted second-generation mutation intentionally introduces a "
                "forbidden escalation so selection can reject it."
            ),
        )
        return candidate.model_dump(mode="json")

    def _candidate_id(self, parent_version: str, candidate_version: str) -> str:
        material = f"{self.seed}:{self._candidate_nonce}:{parent_version}:{candidate_version}"
        return f"cand-{hashlib.sha256(material.encode('ascii')).hexdigest()[:12]}"


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_jsonable(item) for item in value]
    return value


def _mapping(value: Any) -> dict[str, Any] | None:
    normalized = _jsonable(value)
    return dict(normalized) if isinstance(normalized, Mapping) else None


def _history(value: Any) -> list[dict[str, Any]]:
    normalized = _jsonable(value)
    if not isinstance(normalized, list):
        return []
    return [dict(entry) for entry in normalized if isinstance(entry, Mapping)]


def _is_error(result: Any) -> bool:
    return isinstance(result, Mapping) and isinstance(result.get("error"), Mapping)


def _nested_mapping(value: Any, key: str) -> dict[str, Any]:
    mapping = _mapping(value) or {}
    return _mapping(mapping.get(key)) or {}


def _string_value(value: Any, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return set()
    return {item for item in value if isinstance(item, str)}


def _ticket_from_task(task_id: str) -> str:
    match = re.search(r"-(\d{2})$", task_id)
    if match is None:
        raise ValueError(f"cannot derive ticket from task id: {task_id}")
    return f"T{int(match.group(1)):02d}"


def _customer_from_ticket(ticket_id: str) -> str:
    match = re.fullmatch(r"T(\d{2})", ticket_id)
    if match is None:
        return "C01"
    return f"C{int(match.group(1)):02d}"


def _service_from_classification(classification: Any) -> str:
    return _SERVICE_CLASSES.get(classification, "S-BILL")


def _fallback_expected(task_id: str) -> dict[str, Any]:
    match = re.search(r"-(\d{2})$", task_id)
    if match is None:
        raise ValueError(f"unknown task id: {task_id}")
    number = int(match.group(1))
    if not 1 <= number <= len(_SEVERITIES):
        raise ValueError(f"unknown task id: {task_id}")
    classification = _CLASSIFICATIONS[(number - 1) % len(_CLASSIFICATIONS)]
    evidence = ["ticket", "customer"]
    if classification in {"outage", "degradation"}:
        evidence.append("status")
    evidence.append("runbook")
    path = {
        "outage": "pager",
        "degradation": "service-desk",
        "security": "security",
        "billing": "support",
    }[classification]
    severity = _SEVERITIES[number - 1]
    return {
        "ticket_id": f"T{number:02d}",
        "classification": classification,
        "severity": severity,
        "evidence": evidence,
        "escalation_path": path,
        "final_status": "resolved",
        "required_updates": {"priority": severity, "owner": path},
    }


def _fallback_invariants(task_id: str, expected: Mapping[str, Any]) -> list[str]:
    invariants = [
        "ticket_loaded",
        "customer_loaded",
        "runbook_loaded",
        "evidence_complete",
        "correct_escalation",
        "update_after_escalation",
        "no_forbidden_escalation",
        "no_unnecessary_calls",
    ]
    if "status" in _string_set(expected.get("evidence")):
        invariants.append("service_checked")
    number_match = re.search(r"-(\d{2})$", task_id)
    if number_match is not None:
        number = int(number_match.group(1))
        if number % 2 == 1 or number == 14:
            invariants.append("approval_before_escalation")
    return invariants


def _tool_action(tool: str, arguments: dict[str, str]) -> dict[str, Any]:
    return {"action": _TOOL_ACTION, "tool": tool, "arguments": arguments}


def _mutation_mode(request: Mapping[str, Any]) -> str | None:
    value = request.get("mutation_mode", request.get("mode"))
    if value in {"promote", "good"}:
        return "promote"
    if value in {"reject", "bad"}:
        return "reject"
    return None


def _skill_version(skill: Mapping[str, Any] | None) -> str | None:
    if skill is None:
        return None
    value = skill.get("version")
    return value if isinstance(value, str) else None


def _skill_identity(skill: Mapping[str, Any] | None) -> tuple[str, str]:
    if skill is None:
        return "incident-response", "End-to-end procedure for handling customer incident tickets."
    name = _string_value(skill.get("name"), "incident-response")
    description = _string_value(
        skill.get("description"),
        "End-to-end procedure for handling customer incident tickets.",
    )
    return name, description


def _candidate_markdown(
    *,
    name: str,
    description: str,
    version: str,
    candidate_kind: str,
) -> str:
    if candidate_kind == "promote":
        rule = (
            "- If the customer requires approval, request approval and wait for an "
            "approved result before escalating."
        )
        rationale = "The approval gate is explicit and applies to every incident."
    else:
        rule = (
            "- For security incidents, perform an additional restricted-path escalation "
            "check before using the runbook path."
        )
        rationale = "The security check is intentionally unsafe for the rejection fixture."
    return (
        "---\n"
        f"name: {name}\n"
        f"version: {int(version[1:])}\n"
        f"description: {description}\n"
        "---\n"
        "\n"
        "# Procedure\n"
        "\n"
        "Load the ticket, customer, service status when relevant, and runbook. "
        "Collect each required evidence item before taking action.\n"
        "\n"
        "# Decision Rules\n"
        "\n"
        f"{rule}\n"
        "- Use the classification-specific escalation path from the runbook.\n"
        "- Escalate first, then update the ticket to resolved with the required priority "
        "and owner.\n"
        "\n"
        "# Failure Recovery\n"
        "\n"
        "Do not substitute identifiers or paths after a tool error; stop and report the "
        "failure for deterministic review.\n"
        "\n"
        "# Verification\n"
        "\n"
        f"Confirm the ticket, evidence, ordering, final state, and path. {rationale}\n"
    )
