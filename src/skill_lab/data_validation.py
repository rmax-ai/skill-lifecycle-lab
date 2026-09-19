"""Validation of the operator-authored dataset, fixtures, and seed skill."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import NamedTuple

from pydantic import ValidationError

from skill_lab.models import Task

_ALL_TOOLS = (
    "search_tickets",
    "get_ticket",
    "get_customer",
    "get_service_status",
    "get_runbook",
    "request_approval",
    "escalate_ticket",
    "update_ticket",
)

_BASE_INVARIANTS = (
    "ticket_loaded",
    "customer_loaded",
    "runbook_loaded",
    "evidence_complete",
    "correct_escalation",
    "update_after_escalation",
    "no_forbidden_escalation",
    "no_unnecessary_calls",
)

_EXPECTED_SECTIONS = (
    "Procedure",
    "Decision Rules",
    "Failure Recovery",
    "Verification",
)

_EXPECTED_METADATA = {
    "parent": None,
    "created_by": "human",
    "generation": 0,
    "status": "baseline",
}

_SEED_PACKET_REFERENCE = re.compile(
    r"\b(?:T|C)\d{2}\b|"
    r"\b(?:train|validation|test)\b|"
    r"\b(?:S-CORE|S-AUTH|S-BILL|INC-CORE|INC-AUTH|INC-BILL)\b|"
    r"\bIR-(?:TR|VA|TE)-\d{2}\b",
    re.IGNORECASE,
)

_MISSING = object()


class _TaskSpec(NamedTuple):
    task_id: str
    split: str
    ticket_id: str
    classification: str
    severity: str
    evidence: tuple[str, ...]
    escalation_path: str
    approval_required: bool
    max_calls: int


_TASK_SPECS = (
    _TaskSpec(
        "IR-TR-01",
        "train",
        "T01",
        "outage",
        "SEV1",
        ("ticket", "customer", "status", "runbook"),
        "pager",
        True,
        7,
    ),
    _TaskSpec(
        "IR-TR-02",
        "train",
        "T02",
        "degradation",
        "SEV2",
        ("ticket", "customer", "status", "runbook"),
        "service-desk",
        False,
        6,
    ),
    _TaskSpec(
        "IR-TR-03",
        "train",
        "T03",
        "security",
        "SEV1",
        ("ticket", "customer", "runbook"),
        "security",
        True,
        7,
    ),
    _TaskSpec(
        "IR-TR-04",
        "train",
        "T04",
        "billing",
        "SEV3",
        ("ticket", "customer", "runbook"),
        "support",
        False,
        6,
    ),
    _TaskSpec(
        "IR-TR-05",
        "train",
        "T05",
        "outage",
        "SEV2",
        ("ticket", "customer", "status", "runbook"),
        "pager",
        False,
        6,
    ),
    _TaskSpec(
        "IR-TR-06",
        "train",
        "T06",
        "degradation",
        "SEV3",
        ("ticket", "customer", "status", "runbook"),
        "service-desk",
        False,
        6,
    ),
    _TaskSpec(
        "IR-TR-07",
        "train",
        "T07",
        "security",
        "SEV2",
        ("ticket", "customer", "runbook"),
        "security",
        True,
        7,
    ),
    _TaskSpec(
        "IR-TR-08",
        "train",
        "T08",
        "billing",
        "SEV3",
        ("ticket", "customer", "runbook"),
        "support",
        False,
        6,
    ),
    _TaskSpec(
        "IR-TR-09",
        "train",
        "T09",
        "outage",
        "SEV1",
        ("ticket", "customer", "status", "runbook"),
        "pager",
        True,
        7,
    ),
    _TaskSpec(
        "IR-TR-10",
        "train",
        "T10",
        "degradation",
        "SEV2",
        ("ticket", "customer", "status", "runbook"),
        "service-desk",
        False,
        6,
    ),
    _TaskSpec(
        "IR-TR-11",
        "train",
        "T11",
        "security",
        "SEV1",
        ("ticket", "customer", "runbook"),
        "security",
        True,
        7,
    ),
    _TaskSpec(
        "IR-TR-12",
        "train",
        "T12",
        "billing",
        "SEV3",
        ("ticket", "customer", "runbook"),
        "support",
        False,
        6,
    ),
    _TaskSpec(
        "IR-VA-01",
        "validation",
        "T13",
        "outage",
        "SEV2",
        ("ticket", "customer", "status", "runbook"),
        "pager",
        False,
        6,
    ),
    _TaskSpec(
        "IR-VA-02",
        "validation",
        "T14",
        "degradation",
        "SEV2",
        ("ticket", "customer", "status", "runbook"),
        "service-desk",
        True,
        7,
    ),
    _TaskSpec(
        "IR-VA-03",
        "validation",
        "T15",
        "security",
        "SEV1",
        ("ticket", "customer", "runbook"),
        "security",
        True,
        7,
    ),
    _TaskSpec(
        "IR-VA-04",
        "validation",
        "T16",
        "billing",
        "SEV3",
        ("ticket", "customer", "runbook"),
        "support",
        False,
        6,
    ),
    _TaskSpec(
        "IR-VA-05",
        "validation",
        "T17",
        "outage",
        "SEV1",
        ("ticket", "customer", "status", "runbook"),
        "pager",
        True,
        7,
    ),
    _TaskSpec(
        "IR-VA-06",
        "validation",
        "T18",
        "degradation",
        "SEV3",
        ("ticket", "customer", "status", "runbook"),
        "service-desk",
        False,
        6,
    ),
    _TaskSpec(
        "IR-TE-01",
        "test",
        "T19",
        "security",
        "SEV2",
        ("ticket", "customer", "runbook"),
        "security",
        True,
        7,
    ),
    _TaskSpec(
        "IR-TE-02",
        "test",
        "T20",
        "billing",
        "SEV3",
        ("ticket", "customer", "runbook"),
        "support",
        False,
        6,
    ),
    _TaskSpec(
        "IR-TE-03",
        "test",
        "T21",
        "outage",
        "SEV1",
        ("ticket", "customer", "status", "runbook"),
        "pager",
        True,
        7,
    ),
    _TaskSpec(
        "IR-TE-04",
        "test",
        "T22",
        "degradation",
        "SEV2",
        ("ticket", "customer", "status", "runbook"),
        "service-desk",
        False,
        6,
    ),
    _TaskSpec(
        "IR-TE-05",
        "test",
        "T23",
        "security",
        "SEV1",
        ("ticket", "customer", "runbook"),
        "security",
        True,
        7,
    ),
    _TaskSpec(
        "IR-TE-06",
        "test",
        "T24",
        "billing",
        "SEV3",
        ("ticket", "customer", "runbook"),
        "support",
        False,
        6,
    ),
)

_TASK_SPEC_BY_ID = {spec.task_id: spec for spec in _TASK_SPECS}

# The matrix is implicit in the frozen task fields. These groups ensure every
# task is assigned to the trap family specified by §6 and that every family is
# represented by legal tool calls.
_TRAP_MATRIX = {
    "approval/order": frozenset(
        {
            "IR-TR-01",
            "IR-TR-02",
            "IR-TR-09",
            "IR-TR-10",
            "IR-VA-01",
            "IR-VA-02",
            "IR-TE-01",
            "IR-TE-02",
            "IR-TE-03",
            "IR-TE-04",
        }
    ),
    "wrong path": frozenset({"IR-TR-03", "IR-TR-04", "IR-VA-05", "IR-VA-06"}),
    "unnecessary search": frozenset({"IR-TR-05", "IR-TR-06"}),
    "update-before-escalate": frozenset({"IR-TR-07", "IR-TR-08"}),
    "forbidden path": frozenset(
        {"IR-TR-11", "IR-TR-12", "IR-VA-03", "IR-VA-04", "IR-TE-05", "IR-TE-06"}
    ),
}

_EXPECTED_FIXTURE_KEYS = {"dataset_version", "tickets", "customers", "services", "runbooks"}


def _expanded(path: Path) -> Path:
    return Path(os.path.expanduser(str(path)))


def _read_json(path: Path, label: str, errors: set[str]) -> object:
    try:
        with _expanded(path).open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError):
        errors.add(f"{label} is not readable JSON")
        return _MISSING


def _task_payload(spec: _TaskSpec) -> dict[str, object]:
    invariants = list(_BASE_INVARIANTS)
    if spec.classification in {"outage", "degradation"}:
        invariants.append("service_checked")
    if spec.approval_required:
        invariants.append("approval_before_escalation")
    return {
        "id": spec.task_id,
        "input": (
            f"Handle {spec.task_id}: customer reports {spec.classification} incident "
            f"{spec.ticket_id}. Determine procedure and complete the ticket."
        ),
        "available_tools": list(_ALL_TOOLS),
        "expected_outcome": {
            "ticket_id": spec.ticket_id,
            "classification": spec.classification,
            "severity": spec.severity,
            "evidence": list(spec.evidence),
            "escalation_path": spec.escalation_path,
            "final_status": "resolved",
            "required_updates": {"priority": spec.severity, "owner": spec.escalation_path},
        },
        "invariants": invariants,
        "split": spec.split,
        "max_calls": spec.max_calls,
    }


def _expected_fixture_records() -> dict[str, list[dict[str, object]]]:
    tickets: list[dict[str, object]] = []
    customers: list[dict[str, object]] = []
    for spec in _TASK_SPECS:
        number = int(spec.ticket_id[1:])
        customer_id = f"C{number:02d}"
        if spec.classification in {"outage", "degradation"}:
            service_id = "S-CORE"
        elif spec.classification == "security":
            service_id = "S-AUTH"
        else:
            service_id = "S-BILL"
        tickets.append(
            {
                "id": spec.ticket_id,
                "customer_id": customer_id,
                "service_id": service_id,
                "summary": f"{spec.classification} incident {spec.ticket_id}",
                "impact": "regional",
                "priority": spec.severity,
                "status": "open",
                "classification": spec.classification,
            }
        )
        customers.append(
            {
                "id": customer_id,
                "tier": "enterprise" if number % 2 else "standard",
                "region": "us" if number % 2 else "eu",
                "approval_required": spec.approval_required,
            }
        )

    services = [
        {
            "id": "S-AUTH",
            "status": "outage",
            "affected_regions": ["us"],
            "incident_id": "INC-AUTH",
        },
        {
            "id": "S-BILL",
            "status": "degraded",
            "affected_regions": ["eu"],
            "incident_id": "INC-BILL",
        },
        {
            "id": "S-CORE",
            "status": "degraded",
            "affected_regions": ["us", "eu"],
            "incident_id": "INC-CORE",
        },
    ]
    runbooks = [
        {
            "service_id": "S-AUTH",
            "escalation_paths": {"security": "security"},
            "required_evidence": ["ticket", "customer", "runbook"],
            "approval_required": True,
        },
        {
            "service_id": "S-BILL",
            "escalation_paths": {"billing": "support"},
            "required_evidence": ["ticket", "customer", "runbook"],
            "approval_required": False,
        },
        {
            "service_id": "S-CORE",
            "escalation_paths": {"outage": "pager", "degradation": "service-desk"},
            "required_evidence": ["ticket", "customer", "status", "runbook"],
            "approval_required": True,
        },
    ]
    return {
        "tickets": tickets,
        "customers": customers,
        "services": services,
        "runbooks": runbooks,
    }


def _index_records(
    records: object,
    label: str,
    expected: list[dict[str, object]],
    errors: set[str],
) -> dict[str, dict[str, object]]:
    if not isinstance(records, list):
        errors.add(f"fixture {label} must be a JSON array")
        return {}
    if len(records) != len(expected):
        errors.add(f"fixture {label} must contain {len(expected)} records")
    expected_ids = {record.get("id", record.get("service_id")) for record in expected}
    indexed: dict[str, dict[str, object]] = {}
    for record in records:
        if not isinstance(record, dict):
            errors.add(f"fixture {label} contains a non-object record")
            continue
        record_id = record.get("id") or record.get("service_id")
        if not isinstance(record_id, str):
            errors.add(f"fixture {label} contains a record without a string ID")
            continue
        if record_id in indexed:
            errors.add(f"fixture {label} contains duplicate ID")
        indexed[record_id] = record
        if record_id not in expected_ids:
            errors.add(f"fixture {label} contains an unexpected ID")

    for expected_record in expected:
        record_id = expected_record.get("id", expected_record.get("service_id"))
        actual = indexed.get(record_id)
        if actual is None:
            errors.add(f"fixture {label} is missing an expected record")
        elif actual != expected_record:
            errors.add(f"fixture {label} record does not match the frozen packet")
    return indexed


def _validate_task_trap_matrix(tasks: dict[str, dict[str, object]], errors: set[str]) -> None:
    assigned = set().union(*_TRAP_MATRIX.values())
    expected_ids = set(_TASK_SPEC_BY_ID)
    if assigned != expected_ids:
        errors.add("task trap matrix does not cover the frozen task IDs")
        return
    if len(assigned) != sum(len(task_ids) for task_ids in _TRAP_MATRIX.values()):
        errors.add("task trap matrix assigns a task to multiple trap families")

    for trap_name, task_ids in _TRAP_MATRIX.items():
        for task_id in task_ids:
            task = tasks.get(task_id)
            if task is None:
                continue
            tools = task.get("available_tools")
            invariants = task.get("invariants")
            if not (
                isinstance(tools, list)
                and all(isinstance(tool, str) for tool in tools)
                and isinstance(invariants, list)
                and all(isinstance(item, str) for item in invariants)
            ):
                continue
            if "escalate_ticket" not in tools or "update_ticket" not in tools:
                errors.add(f"task {task_id} cannot express the {trap_name} trap")
            if trap_name == "unnecessary search" and "search_tickets" not in tools:
                errors.add(f"task {task_id} cannot express the {trap_name} trap")
            if "correct_escalation" not in invariants:
                errors.add(f"task {task_id} lacks the {trap_name} check")


def _validate_tasks(data: object, errors: set[str]) -> dict[str, dict[str, object]]:
    if not isinstance(data, list):
        errors.add("dataset must be a JSON array")
        return {}
    if len(data) != len(_TASK_SPECS):
        errors.add(f"dataset must contain exactly {len(_TASK_SPECS)} tasks")

    split_counts = {
        split: sum(
            1 for record in data if isinstance(record, dict) and record.get("split") == split
        )
        for split in ("train", "validation", "test")
    }
    for split, expected_count in (("train", 12), ("validation", 6), ("test", 6)):
        if split_counts[split] != expected_count:
            errors.add(f"split {split} must contain exactly {expected_count} tasks")

    indexed: dict[str, dict[str, object]] = {}
    actual_ids: list[str] = []
    for index, record in enumerate(data):
        if not isinstance(record, dict):
            errors.add(f"task at index {index} must be a JSON object")
            continue
        task_id = record.get("id")
        if isinstance(task_id, str):
            actual_ids.append(task_id)
            if task_id in indexed:
                errors.add(f"dataset contains duplicate task ID {task_id}")
            indexed[task_id] = record
        try:
            Task.model_validate(record)
        except (TypeError, ValidationError):
            errors.add(f"task at index {index} does not match the Task schema")

        if not isinstance(task_id, str):
            continue
        spec = _TASK_SPEC_BY_ID.get(task_id)
        if spec is None:
            errors.add(f"dataset contains an unexpected task ID {task_id}")
            continue
        expected = _task_payload(spec)
        for field in expected:
            if record.get(field) != expected[field]:
                errors.add(f"task {task_id} has invalid frozen field {field}")

    expected_ids = set(_TASK_SPEC_BY_ID)
    actual_id_set = set(actual_ids)
    for task_id in sorted(expected_ids - actual_id_set):
        errors.add(f"dataset is missing expected task ID {task_id}")
    for task_id in sorted(actual_id_set - expected_ids):
        errors.add(f"dataset contains an unexpected task ID {task_id}")
    _validate_task_trap_matrix(indexed, errors)
    return indexed


def _validate_fixture_packet(
    data: object, errors: set[str]
) -> dict[str, dict[str, dict[str, object]]]:
    if not isinstance(data, dict):
        errors.add("fixtures must be a JSON object")
        return {"tickets": {}, "customers": {}, "services": {}, "runbooks": {}}
    if set(data) != _EXPECTED_FIXTURE_KEYS:
        errors.add("fixtures have an invalid top-level schema")

    expected = _expected_fixture_records()
    indexed: dict[str, dict[str, dict[str, object]]] = {}
    for label in ("tickets", "customers", "services", "runbooks"):
        records = data.get(label)
        indexed[label] = _index_records(records, label, expected[label], errors)
    if data.get("dataset_version") != "2026-01":
        errors.add("fixtures have an invalid dataset_version")
    return indexed


def _validate_consistency(
    tasks: dict[str, dict[str, object]],
    fixtures: dict[str, dict[str, dict[str, object]]],
    errors: set[str],
) -> None:
    tickets = fixtures["tickets"]
    customers = fixtures["customers"]
    services = fixtures["services"]
    runbooks = fixtures["runbooks"]

    for spec in _TASK_SPECS:
        task = tasks.get(spec.task_id)
        if task is None:
            continue
        outcome = task.get("expected_outcome")
        if not isinstance(outcome, dict):
            continue
        ticket_id = outcome.get("ticket_id")
        if not isinstance(ticket_id, str):
            continue
        ticket = tickets.get(ticket_id)
        if ticket is None:
            errors.add(f"task {spec.task_id} references a missing fixture ticket")
            continue

        if outcome.get("classification") != ticket.get("classification"):
            errors.add(f"task {spec.task_id} classification contradicts its fixture ticket")
        if outcome.get("severity") != ticket.get("priority"):
            errors.add(f"task {spec.task_id} severity contradicts its fixture ticket")

        customer_id = ticket.get("customer_id")
        service_id = ticket.get("service_id")
        customer = customers.get(customer_id) if isinstance(customer_id, str) else None
        service = services.get(service_id) if isinstance(service_id, str) else None
        runbook = runbooks.get(service_id) if isinstance(service_id, str) else None
        if customer is None:
            errors.add(f"task {spec.task_id} references a missing fixture customer")
        if service is None:
            errors.add(f"task {spec.task_id} references a missing fixture service")
        if runbook is None:
            errors.add(f"task {spec.task_id} references a missing fixture runbook")
        if customer is None or runbook is None:
            continue

        invariants = task.get("invariants")
        approval_invariant = (
            isinstance(invariants, list) and "approval_before_escalation" in invariants
        )
        approval_required = customer.get("approval_required")
        if isinstance(approval_required, bool) and approval_invariant != approval_required:
            errors.add(f"task {spec.task_id} approval semantics contradict its customer fixture")
        if isinstance(approval_required, bool):
            expected_max_calls = 7 if approval_required else 6
            if task.get("max_calls") != expected_max_calls:
                errors.add(f"task {spec.task_id} has an invalid trap-matrix call limit")

        classification = outcome.get("classification")
        paths = runbook.get("escalation_paths")
        if isinstance(classification, str) and isinstance(paths, dict):
            expected_path = paths.get(classification)
            if outcome.get("escalation_path") != expected_path:
                errors.add(f"task {spec.task_id} escalation path contradicts its runbook")

        evidence = outcome.get("evidence")
        required_evidence = runbook.get("required_evidence")
        if (
            isinstance(evidence, list)
            and isinstance(required_evidence, list)
            and all(isinstance(item, str) for item in evidence)
            and all(isinstance(item, str) for item in required_evidence)
            and set(evidence) != set(required_evidence)
        ):
            errors.add(f"task {spec.task_id} evidence contradicts its runbook")

        if service is not None and not isinstance(service.get("id"), str):
            errors.add(f"task {spec.task_id} references an invalid fixture service")


def _read_text(path: Path, label: str, errors: set[str]) -> str | None:
    try:
        return _expanded(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        errors.add(f"{label} is not readable UTF-8 text")
        return None


def _validate_seed_front_matter(text: str, errors: set[str]) -> None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        errors.add("seed SKILL.md is missing front matter")
        return
    try:
        closing = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        errors.add("seed SKILL.md has unterminated front matter")
        return

    front_matter: dict[str, str] = {}
    for line in lines[1:closing]:
        if not line.strip():
            continue
        if ":" not in line:
            errors.add("seed SKILL.md has invalid front matter")
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if key in front_matter:
            errors.add("seed SKILL.md has duplicate front matter keys")
        front_matter[key] = value.strip()
    if set(front_matter) != {"name", "version", "description"}:
        errors.add("seed SKILL.md has an invalid front matter schema")
    if front_matter.get("name") != "incident-response":
        errors.add("seed SKILL.md has an invalid name")
    if front_matter.get("version") != "1":
        errors.add("seed SKILL.md has an invalid version")
    if not front_matter.get("description"):
        errors.add("seed SKILL.md has an empty description")

    headings = re.findall(r"^# ([^\n#]+?)\s*$", "\n".join(lines[closing + 1 :]), re.MULTILINE)
    if tuple(headings) != _EXPECTED_SECTIONS:
        errors.add("seed SKILL.md has invalid section headings")
    else:
        body = "\n".join(lines[closing + 1 :])
        for index, heading in enumerate(_EXPECTED_SECTIONS):
            marker = f"# {heading}"
            start = body.find(marker) + len(marker)
            next_marker = (
                body.find(f"# {_EXPECTED_SECTIONS[index + 1]}", start)
                if index + 1 < len(_EXPECTED_SECTIONS)
                else len(body)
            )
            if not body[start:next_marker].strip():
                errors.add(f"seed SKILL.md section {heading} is empty")


def _validate_seed(seed_dir: Path, errors: set[str]) -> None:
    skill_path = _expanded(seed_dir) / "SKILL.md"
    metadata_path = _expanded(seed_dir) / "metadata.json"
    skill_text = _read_text(skill_path, "seed SKILL.md", errors)
    metadata = _read_json(metadata_path, "seed metadata", errors)
    if skill_text is not None:
        _validate_seed_front_matter(skill_text, errors)
        if _SEED_PACKET_REFERENCE.search(skill_text):
            errors.add("seed SKILL.md contains a dataset, split, or fixture reference")
    if metadata is not _MISSING and (
        not isinstance(metadata, dict) or metadata != _EXPECTED_METADATA
    ):
        errors.add("seed metadata does not match the frozen baseline metadata")


def validate_operator_inputs(dataset: Path, fixtures: Path, seed_dir: Path) -> list[str]:
    """Return sorted validation errors for the frozen operator starter packet."""

    errors: set[str] = set()
    dataset_data = _read_json(dataset, "dataset", errors)
    fixture_data = _read_json(fixtures, "fixtures", errors)
    tasks = _validate_tasks(dataset_data, errors) if dataset_data is not _MISSING else {}
    fixture_indexes = (
        _validate_fixture_packet(fixture_data, errors)
        if fixture_data is not _MISSING
        else {"tickets": {}, "customers": {}, "services": {}, "runbooks": {}}
    )
    _validate_consistency(tasks, fixture_indexes, errors)
    _validate_seed(seed_dir, errors)
    return sorted(errors)
