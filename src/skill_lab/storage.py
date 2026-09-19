"""Durable SQLite storage for experiments and raw run evidence."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from skill_lab.models import CandidateSkill, PromotionDecision, RunRecord

DDL = """PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS experiments (
  experiment_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, mode TEXT NOT NULL,
  config_json TEXT NOT NULL, git_commit TEXT NOT NULL, dataset_sha256 TEXT NOT NULL,
  model_id TEXT NOT NULL, seed INTEGER NOT NULL, runs_per_task INTEGER NOT NULL,
  status TEXT NOT NULL, error_text TEXT
);
CREATE TABLE IF NOT EXISTS skills (
  version TEXT PRIMARY KEY, name TEXT NOT NULL, parent_version TEXT,
  generation INTEGER NOT NULL, created_by TEXT NOT NULL, status TEXT NOT NULL,
  skill_markdown TEXT NOT NULL, metadata_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mutations (
  candidate_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
  parent_version TEXT NOT NULL REFERENCES skills(version), candidate_version TEXT NOT NULL,
  generation INTEGER NOT NULL, status TEXT NOT NULL, prompt_json TEXT NOT NULL,
  response_json TEXT NOT NULL, failure_analysis TEXT NOT NULL, procedural_change TEXT NOT NULL,
  rationale TEXT NOT NULL, candidate_markdown TEXT NOT NULL, error_text TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
  task_id TEXT NOT NULL, split TEXT NOT NULL, condition_name TEXT NOT NULL, skill_version TEXT,
  model_id TEXT,
  run_slot INTEGER NOT NULL, seed INTEGER NOT NULL, outcome TEXT NOT NULL,
  success INTEGER NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
  total_tokens INTEGER NOT NULL, latency_ms INTEGER NOT NULL, estimated_cost_usd REAL NOT NULL,
  trajectory_json TEXT NOT NULL, verification_json TEXT NOT NULL, error_text TEXT,
  UNIQUE(experiment_id, task_id, condition_name, skill_version, run_slot)
);
CREATE TABLE IF NOT EXISTS promotion_decisions (
  decision_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
  candidate_id TEXT NOT NULL REFERENCES mutations(candidate_id), parent_version TEXT NOT NULL,
  candidate_version TEXT NOT NULL, decision TEXT NOT NULL, reason_codes_json TEXT NOT NULL,
  evidence_json TEXT NOT NULL, created_at TEXT NOT NULL
);
"""

SCHEMA = DDL
_TABLES = frozenset({"experiments", "skills", "mutations", "runs", "promotion_decisions"})


class ExperimentStore:
    """Persist experiment records and their complete raw execution evidence."""

    def __init__(self, path: Path | str) -> None:
        self.path = _expanded(path)
        database = str(self.path)
        if database != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(database)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._initialize_schema()

    @property
    def connection(self) -> sqlite3.Connection:
        """Expose the connection for read-only inspection and queries."""

        return self._connection

    def close(self) -> None:
        """Close the underlying database connection."""

        self._connection.close()

    def __enter__(self) -> ExperimentStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def insert_experiment(
        self,
        experiment_id: str,
        created_at: str,
        mode: str,
        config_json: object,
        git_commit: str,
        dataset_sha256: str,
        model_id: str,
        seed: int,
        runs_per_task: int,
        status: str,
        error_text: str | None = None,
    ) -> None:
        """Insert one experiment and canonicalize its persisted configuration."""

        with self._connection:
            self._connection.execute(
                """
                INSERT INTO experiments (
                    experiment_id, created_at, mode, config_json, git_commit,
                    dataset_sha256, model_id, seed, runs_per_task, status, error_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    experiment_id,
                    created_at,
                    mode,
                    _canonical_json(config_json),
                    git_commit,
                    dataset_sha256,
                    model_id,
                    seed,
                    runs_per_task,
                    status,
                    error_text,
                ),
            )

    def insert_skill(
        self,
        version: str,
        name: str,
        parent_version: str | None,
        generation: int,
        created_by: str,
        status: str,
        skill_markdown: str,
        metadata_json: object,
        created_at: str,
    ) -> None:
        """Insert a versioned skill and canonicalize its metadata."""

        with self._connection:
            self._connection.execute(
                """
                INSERT INTO skills (
                    version, name, parent_version, generation, created_by, status,
                    skill_markdown, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version,
                    name,
                    parent_version,
                    generation,
                    created_by,
                    status,
                    skill_markdown,
                    _canonical_json(metadata_json),
                    created_at,
                ),
            )

    def insert_mutation(
        self,
        experiment_id: str,
        candidate: CandidateSkill,
        prompt_json: object,
        response_json: object,
        error_text: str | None = None,
    ) -> None:
        """Insert a candidate mutation with its exact prompt and response evidence."""

        candidate_data = candidate.model_dump(mode="json")
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO mutations (
                    candidate_id, experiment_id, parent_version, candidate_version,
                    generation, status, prompt_json, response_json, failure_analysis,
                    procedural_change, rationale, candidate_markdown, error_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_data["candidate_id"],
                    experiment_id,
                    candidate_data["parent_version"],
                    candidate_data["candidate_version"],
                    candidate_data["generation"],
                    candidate_data["status"],
                    _canonical_json(prompt_json),
                    _canonical_json(response_json),
                    candidate_data["failure_analysis"],
                    candidate_data["procedural_change"],
                    candidate_data["rationale"],
                    candidate_data["candidate_markdown"],
                    error_text,
                ),
            )

    def insert_run(self, run: RunRecord) -> None:
        """Insert one run without dropping failed runs or raw evidence."""

        run_data = run.model_dump(mode="json")
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO runs (
                    run_id, experiment_id, task_id, split, condition_name, skill_version,
                    model_id, run_slot, seed, outcome, success, input_tokens, output_tokens,
                    total_tokens, latency_ms, estimated_cost_usd, trajectory_json,
                    verification_json, error_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_data["run_id"],
                    run_data["experiment_id"],
                    run_data["task_id"],
                    run_data["split"],
                    run_data["condition_name"],
                    run_data["skill_version"],
                    run_data["model_id"],
                    run_data["run_slot"],
                    run_data["seed"],
                    run_data["outcome"],
                    int(run_data["success"]),
                    run_data["input_tokens"],
                    run_data["output_tokens"],
                    run_data["total_tokens"],
                    run_data["latency_ms"],
                    run_data["estimated_cost_usd"],
                    _canonical_json(run_data["trajectory"]),
                    _canonical_json(run_data["verification"]),
                    run_data["error_text"],
                ),
            )

    def insert_promotion_decision(
        self,
        experiment_id: str,
        decision: PromotionDecision,
        decision_id: str,
        created_at: str,
    ) -> None:
        """Insert an independent promotion decision and its evidence."""

        decision_data = decision.model_dump(mode="json")
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO promotion_decisions (
                    decision_id, experiment_id, candidate_id, parent_version,
                    candidate_version, decision, reason_codes_json, evidence_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    experiment_id,
                    decision_data["candidate_id"],
                    decision_data["parent_version"],
                    decision_data["candidate_version"],
                    decision_data["decision"],
                    _canonical_json(decision_data["reason_codes"]),
                    _canonical_json(decision_data["evidence"]),
                    created_at,
                ),
            )

    def get_run(self, run_id: str) -> RunRecord | None:
        """Load a run and reconstruct its typed trajectory and verification."""

        row = self._connection.execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None

        payload = {column: row[index] for index, column in enumerate(row.keys())}
        payload["success"] = bool(payload["success"])
        payload["trajectory"] = json.loads(payload.pop("trajectory_json"))
        payload["verification"] = json.loads(payload.pop("verification_json"))
        payload.pop("error_text", None) if payload["error_text"] is None else None
        return RunRecord.model_validate(payload)

    def list_runs(self, experiment_id: str | None = None) -> list[RunRecord]:
        """Load persisted runs in stable run-id order."""

        if experiment_id is None:
            rows = self._connection.execute("SELECT run_id FROM runs ORDER BY run_id").fetchall()
        else:
            rows = self._connection.execute(
                "SELECT run_id FROM runs WHERE experiment_id = ? ORDER BY run_id",
                (experiment_id,),
            ).fetchall()
        return [run for row in rows if (run := self.get_run(row["run_id"])) is not None]

    def _initialize_schema(self) -> None:
        existing = {
            row["name"]
            for row in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if not existing:
            self._connection.executescript(DDL)
            self._connection.commit()
            return
        if existing != _TABLES:
            raise sqlite3.DatabaseError("database does not match the experiment schema")
        run_columns = {row["name"] for row in self._connection.execute("PRAGMA table_info(runs)")}
        if "model_id" not in run_columns:
            self._connection.execute("ALTER TABLE runs ADD COLUMN model_id TEXT")
            self._connection.commit()


def _expanded(path: Path | str) -> Path:
    return Path(os.path.expanduser(os.fspath(path)))


def _canonical_json(value: object) -> str:
    normalized = _jsonable(value)
    if isinstance(normalized, str):
        with suppress(json.JSONDecodeError):
            normalized = json.loads(normalized)
    return json.dumps(normalized, sort_keys=True)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_jsonable(item) for item in value]
    return value
