import json
from pathlib import Path

from skill_lab.agent import AgentConfig
from skill_lab.experiment import evaluate_condition
from skill_lab.metrics import summarize_runs
from skill_lab.mock_model import ScriptedMockModel
from skill_lab.models import RunRecord
from skill_lab.skills import load_skill
from skill_lab.storage import ExperimentStore
from skill_lab.tasks import load_tasks, tasks_for_split

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "datasets" / "incident_tasks.json"
FIXTURES = ROOT / "datasets" / "tool_world.json"
SKILLS = ROOT / "skills"
EXPERIMENT_ID = "exp-20000101T000000Z-00000000"


def _trajectory(task_id: str, experiment_id: str, skill_version: str | None) -> dict:
    return {
        "experiment_id": experiment_id,
        "task_id": task_id,
        "skill_version": skill_version,
        "messages": [{"role": "assistant", "content": "placeholder"}],
        "tool_calls": [
            {
                "tool": "get_ticket",
                "arguments": {"ticket_id": "T01"},
                "result": {"ticket": {"id": "T01"}},
                "timestamp": "2000-01-01T00:00:01.000Z",
            }
        ],
        "final_output": {},
        "tokens": 10,
        "latency_ms": 3,
        "outcome": "success",
    }


def _run(
    task_id: str,
    slot: int,
    success: bool,
    experiment_id: str = EXPERIMENT_ID,
) -> RunRecord:
    return RunRecord(
        run_id=f"run-{slot + 1:016x}",
        experiment_id=experiment_id,
        task_id=task_id,
        split="train",
        condition_name="seed",
        skill_version="v001",
        run_slot=slot,
        seed=1729 + slot,
        outcome="success" if success else "failure",
        success=success,
        input_tokens=4,
        output_tokens=6,
        total_tokens=10,
        latency_ms=3,
        estimated_cost_usd=0.25,
        trajectory=_trajectory(task_id, experiment_id, "v001"),
        verification={
            "success": success,
            "checks": {},
            "evidence": {
                "forbidden_actions": [] if success else [1],
                "unnecessary_actions": [],
            },
        },
    )


def _insert_experiment(store: ExperimentStore, runs_per_task: int = 1) -> None:
    store.insert_experiment(
        experiment_id=EXPERIMENT_ID,
        created_at="2000-01-01T00:00:00Z",
        mode="baseline",
        config_json={"seed": 1729},
        git_commit="placeholder",
        dataset_sha256="placeholder",
        model_id="mock-incident-v1",
        seed=1729,
        runs_per_task=runs_per_task,
        status="running",
    )


def _fixtures() -> dict:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def test_repetition_aggregation() -> None:
    runs = [
        _run("IR-TR-01", 0, True),
        _run("IR-TR-01", 1, False),
        _run("IR-TR-02", 0, False),
        _run("IR-TR-02", 1, True),
    ]

    summary = summarize_runs(runs, {"input_per_million_usd": 1.0})

    assert summary.run_count == 4
    assert summary.task_count == 2
    assert summary.successful_runs == 2
    assert summary.success_rate == 0.5
    assert summary.success_variance == 0.0
    assert summary.total_tokens == 40
    assert summary.model_validate_json(summary.model_dump_json()) == summary


def test_baseline_conditions_fixed_controls(tmp_path: Path) -> None:
    tasks = load_tasks(DATASET)
    seed_skill = load_skill(SKILLS, "incident-response", "v001")
    config = AgentConfig(max_steps=8, token_budget=2400)
    pricing = {"mock-incident-v1": {"input_per_million_usd": 0.0, "output_per_million_usd": 0.0}}

    with ExperimentStore(tmp_path / "experiments.sqlite3") as store:
        _insert_experiment(store)
        no_skill = evaluate_condition(
            tasks=tasks,
            fixtures=_fixtures(),
            model=ScriptedMockModel(seed=1729),
            agent_config=config,
            experiment_id=EXPERIMENT_ID,
            split="train",
            condition_name="no_skill",
            runs_per_task=1,
            base_seed=1729,
            store=store,
            pricing=pricing,
        )
        seed = evaluate_condition(
            tasks=tasks,
            fixtures=_fixtures(),
            model=ScriptedMockModel(seed=1729),
            agent_config=config,
            experiment_id=EXPERIMENT_ID,
            split="train",
            condition_name="seed",
            skill_markdown=seed_skill.markdown,
            skill_version=seed_skill.version,
            runs_per_task=1,
            base_seed=1729,
            store=store,
            pricing=pricing,
        )

        train_tasks = tasks_for_split(tasks, "train")
        rows = store.connection.execute(
            """
            SELECT condition_name, task_id, run_slot, seed
            FROM runs
            WHERE experiment_id = ?
            ORDER BY condition_name, task_id, run_slot
            """,
            (EXPERIMENT_ID,),
        ).fetchall()

    assert seed.success_rate > no_skill.success_rate
    assert [row["task_id"] for row in rows[: len(train_tasks)]] == [task.id for task in train_tasks]
    no_skill_seeds = {
        (row["task_id"], row["run_slot"]): row["seed"]
        for row in rows
        if row["condition_name"] == "no_skill"
    }
    seed_seeds = {
        (row["task_id"], row["run_slot"]): row["seed"]
        for row in rows
        if row["condition_name"] == "seed"
    }
    assert no_skill_seeds == seed_seeds


def test_baseline_persists_failures(tmp_path: Path) -> None:
    tasks = load_tasks(DATASET)

    with ExperimentStore(tmp_path / "experiments.sqlite3") as store:
        _insert_experiment(store)
        summary = evaluate_condition(
            tasks=tasks,
            fixtures=_fixtures(),
            model=ScriptedMockModel(seed=1729),
            agent_config=AgentConfig(max_steps=8, token_budget=2400),
            experiment_id=EXPERIMENT_ID,
            split="train",
            condition_name="no_skill",
            runs_per_task=2,
            base_seed=1729,
            store=store,
        )
        persisted = store.list_runs(EXPERIMENT_ID)

    assert len(persisted) == summary.run_count == 24
    assert sum(not run.success for run in persisted) == summary.failed_runs
    assert all(run.error_text for run in persisted if not run.success)
