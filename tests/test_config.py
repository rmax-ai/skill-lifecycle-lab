import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from skill_lab.config import (
    DEFAULT_CONFIG,
    LiveProviderError,
    create_chat_model,
    load_config,
)


def _write_config(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_default_config_contract(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    _write_config(path, DEFAULT_CONFIG)

    config = load_config(path, {})

    assert config.dataset_path == Path("datasets/incident_tasks.json")
    assert config.fixtures_path == Path("datasets/tool_world.json")
    assert config.skills_root == Path("skills")
    assert config.artifacts_root == Path("artifacts")
    assert config.database_path == Path("artifacts/experiments.sqlite3")
    assert config.model.provider == "mock"
    assert config.model.base_url == "http://localhost:8000/v1"
    assert config.model.api_key_env == "SKILL_LAB_API_KEY"
    assert config.model.model == "mock-incident-v1"
    assert config.model.temperature == 0.0
    assert config.model.max_tokens == 800
    assert config.model.timeout_s == 30
    assert config.agent.max_steps == 8
    assert config.agent.token_budget == 2400
    assert config.promotion.regression_threshold == 0.0
    assert config.promotion.cost_tolerance == 1.1
    assert config.pricing["mock-incident-v1"].input_per_million_usd == 0.0
    assert config.seed == 1729

    unknown = dict(DEFAULT_CONFIG)
    unknown["unexpected"] = "value"
    _write_config(path, unknown)
    with pytest.raises(ValidationError):
        load_config(path, {})


def test_environment_overrides(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    _write_config(path, DEFAULT_CONFIG)

    config = load_config(
        path,
        {
            "SKILL_LAB_BASE_URL": "https://example.invalid/v1",
            "SKILL_LAB_MODEL": "override-model",
            "SKILL_LAB_TEMPERATURE": "0.4",
            "SKILL_LAB_MAX_TOKENS": "640",
            "SKILL_LAB_TIMEOUT_S": "12",
        },
    )

    assert config.model.base_url == "https://example.invalid/v1"
    assert config.model.model == "override-model"
    assert config.model.temperature == 0.4
    assert config.model.max_tokens == 640
    assert config.model.timeout_s == 12
    assert config.model.provider == "mock"


def test_live_provider_requires_allow_live(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    payload = json.loads(json.dumps(DEFAULT_CONFIG))
    payload["model"]["provider"] = "openai_compatible"
    _write_config(path, payload)
    config = load_config(path, {})

    with pytest.raises(LiveProviderError, match="allow-live"):
        create_chat_model(
            config,
            allow_live=False,
            env={"SKILL_LAB_API_KEY": "placeholder"},
        )
