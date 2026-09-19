import json
from pathlib import Path

from skill_lab.data_validation import validate_operator_inputs

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "datasets" / "incident_tasks.json"
FIXTURES = ROOT / "datasets" / "tool_world.json"
SEED = ROOT / "skills" / "incident-response" / "v001"


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_operator_packet_validates() -> None:
    assert validate_operator_inputs(DATASET, FIXTURES, SEED) == []


def test_invalid_split_count_is_reported(tmp_path: Path) -> None:
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    next(task for task in dataset if task["id"] == "IR-VA-01")["split"] = "train"
    dataset_path = tmp_path / "incident_tasks.json"
    _write_json(dataset_path, dataset)

    errors = validate_operator_inputs(dataset_path, FIXTURES, SEED)

    assert errors == sorted(errors)
    assert "split validation must contain exactly 6 tasks" in errors


def test_fixture_outcome_mismatch_is_reported(tmp_path: Path) -> None:
    fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))
    next(ticket for ticket in fixtures["tickets"] if ticket["id"] == "T13")["priority"] = "SEV3"
    fixtures_path = tmp_path / "tool_world.json"
    _write_json(fixtures_path, fixtures)

    errors = validate_operator_inputs(DATASET, fixtures_path, SEED)

    assert errors == sorted(errors)
    assert any(
        "fixture tickets record does not match the frozen packet" in error for error in errors
    )
    assert any("IR-VA-01 severity contradicts its fixture ticket" in error for error in errors)
