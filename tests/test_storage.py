import json
import sqlite3
from pathlib import Path

import pytest

from skill_lab.models import RunRecord
from skill_lab.storage import ExperimentStore


def _trajectory_payload() -> dict[str, object]:
    return {
        "experiment_id": "exp-20000101T000000Z-00000000",
        "task_id": "IR-TR-01",
        "skill_version": "v001",
        "messages": [
            {"role": "assistant", "content": "complete", "details": {"z": 1, "a": 2}},
        ],
        "tool_calls": [
            {
                "tool": "get_ticket",
                "arguments": {"ticket_id": "T01"},
                "result": {"ticket": {"id": "T01", "summary": "placeholder"}},
                "timestamp": "2000-01-01T00:00:01.000Z",
            }
        ],
        "final_output": {
            "ticket_id": "T01",
            "classification": "outage",
            "severity": "SEV1",
            "evidence": ["ticket"],
            "escalation_path": "pager",
            "final_status": "resolved",
            "required_updates": {"priority": "SEV1", "owner": "pager"},
        },
        "tokens": 10,
        "latency_ms": 5,
        "outcome": "success",
    }


def _run() -> RunRecord:
    return RunRecord(
        run_id="run-0000000000000000",
        experiment_id="exp-20000101T000000Z-00000000",
        task_id="IR-TR-01",
        split="train",
        condition_name="seed",
        skill_version="v001",
        run_slot=0,
        seed=1729,
        outcome="success",
        success=True,
        input_tokens=5,
        output_tokens=5,
        total_tokens=10,
        latency_ms=5,
        estimated_cost_usd=0.0,
        trajectory=_trajectory_payload(),
        verification={
            "success": True,
            "checks": {"schema_valid": True, "required_evidence": True},
            "evidence": {"call_indexes": {"get_ticket": [1]}},
        },
    )


def _insert_experiment(store: ExperimentStore) -> None:
    store.insert_experiment(
        experiment_id="exp-20000101T000000Z-00000000",
        created_at="2000-01-01T00:00:00Z",
        mode="verified",
        config_json={"z": 1, "a": {"d": 4, "b": 2}},
        git_commit="placeholder",
        dataset_sha256="placeholder",
        model_id="mock-incident-v1",
        seed=1729,
        runs_per_task=1,
        status="running",
    )


def test_schema_matches_contract(tmp_path: Path) -> None:
    with ExperimentStore(tmp_path / "experiments.sqlite3") as store:
        expected = {
            "experiments": [
                "experiment_id",
                "created_at",
                "mode",
                "config_json",
                "git_commit",
                "dataset_sha256",
                "model_id",
                "seed",
                "runs_per_task",
                "status",
                "error_text",
            ],
            "skills": [
                "version",
                "name",
                "parent_version",
                "generation",
                "created_by",
                "status",
                "skill_markdown",
                "metadata_json",
                "created_at",
            ],
            "mutations": [
                "candidate_id",
                "experiment_id",
                "parent_version",
                "candidate_version",
                "generation",
                "status",
                "prompt_json",
                "response_json",
                "failure_analysis",
                "procedural_change",
                "rationale",
                "candidate_markdown",
                "error_text",
            ],
            "runs": [
                "run_id",
                "experiment_id",
                "task_id",
                "split",
                "condition_name",
                "skill_version",
                "run_slot",
                "seed",
                "outcome",
                "success",
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "latency_ms",
                "estimated_cost_usd",
                "trajectory_json",
                "verification_json",
                "error_text",
            ],
            "promotion_decisions": [
                "decision_id",
                "experiment_id",
                "candidate_id",
                "parent_version",
                "candidate_version",
                "decision",
                "reason_codes_json",
                "evidence_json",
                "created_at",
            ],
        }

        for table, columns in expected.items():
            rows = store.connection.execute(f"PRAGMA table_info({table})").fetchall()
            assert [row["name"] for row in rows] == columns


def test_run_round_trip(tmp_path: Path) -> None:
    run = _run()
    with ExperimentStore(tmp_path / "experiments.sqlite3") as store:
        _insert_experiment(store)
        store.insert_run(run)

        stored = store.get_run(run.run_id)
        assert stored == run

        row = store.connection.execute(
            "SELECT trajectory_json, verification_json FROM runs WHERE run_id = ?",
            (run.run_id,),
        ).fetchone()
        assert row is not None
        assert json.loads(row["trajectory_json"]) == run.trajectory.model_dump(mode="json")
        assert json.loads(row["verification_json"]) == run.verification.model_dump(mode="json")
        assert row["trajectory_json"] == json.dumps(
            run.trajectory.model_dump(mode="json"),
            sort_keys=True,
        )


def test_duplicate_run_is_rejected(tmp_path: Path) -> None:
    run = _run()
    with ExperimentStore(tmp_path / "experiments.sqlite3") as store:
        _insert_experiment(store)
        store.insert_run(run)

        with pytest.raises(sqlite3.IntegrityError):
            store.insert_run(run)
