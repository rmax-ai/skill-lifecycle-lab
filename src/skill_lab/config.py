"""Strict application configuration and the operator-gated live model client."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from skill_lab.agent import AgentConfig
from skill_lab.mock_model import ModelResponse, ScriptedMockModel
from skill_lab.tools import _TOOL_ARGUMENTS

DEFAULT_CONFIG: dict[str, Any] = {
    "dataset_path": "datasets/incident_tasks.json",
    "fixtures_path": "datasets/tool_world.json",
    "skills_root": "skills",
    "artifacts_root": "artifacts",
    "database_path": "artifacts/experiments.sqlite3",
    "model": {
        "provider": "mock",
        "base_url": "http://localhost:8000/v1",
        "api_key_env": "SKILL_LAB_API_KEY",
        "model": "mock-incident-v1",
        "temperature": 0.0,
        "max_tokens": 800,
        "timeout_s": 30,
        "extra_body": None,
    },
    "agent": {"max_steps": 8, "token_budget": 2400},
    "promotion": {"regression_threshold": 0.00, "cost_tolerance": 1.10},
    "pricing": {
        "mock-incident-v1": {
            "input_per_million_usd": 0.0,
            "output_per_million_usd": 0.0,
        }
    },
    "seed": 1729,
}

_ENV_MODEL_OVERRIDES = {
    "SKILL_LAB_BASE_URL": "base_url",
    "SKILL_LAB_MODEL": "model",
    "SKILL_LAB_TEMPERATURE": "temperature",
    "SKILL_LAB_MAX_TOKENS": "max_tokens",
    "SKILL_LAB_TIMEOUT_S": "timeout_s",
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelConfig(_StrictModel):
    """Configuration for either the deterministic mock or live provider."""

    provider: Literal["mock", "openai_compatible"]
    base_url: str = Field(min_length=1)
    api_key_env: str = Field(min_length=1)
    model: str = Field(min_length=1)
    temperature: float = Field(ge=0)
    max_tokens: int = Field(ge=1)
    timeout_s: float = Field(gt=0)
    extra_body: dict[str, Any] | None = None


class PromotionConfig(_StrictModel):
    """Thresholds used by the independent promotion gate."""

    regression_threshold: float = Field(ge=0, le=1)
    cost_tolerance: float = Field(ge=0)


class PricingConfig(_StrictModel):
    """Per-million-token prices for one configured model."""

    input_per_million_usd: float = Field(ge=0)
    output_per_million_usd: float = Field(ge=0)


class AppConfig(_StrictModel):
    """The complete persisted application configuration contract."""

    dataset_path: Path
    fixtures_path: Path
    skills_root: Path
    artifacts_root: Path
    database_path: Path
    model: ModelConfig
    agent: AgentConfig
    promotion: PromotionConfig
    pricing: dict[str, PricingConfig]
    seed: int

    @field_validator(
        "dataset_path",
        "fixtures_path",
        "skills_root",
        "artifacts_root",
        "database_path",
        mode="before",
    )
    @classmethod
    def _expand_path(cls, value: object) -> object:
        if isinstance(value, (str, os.PathLike)):
            return os.path.expanduser(os.fspath(value))
        return value


class LiveProviderError(RuntimeError):
    """Raised when a live provider is requested without explicit approval."""


def _expanded(path: Path | str) -> Path:
    return Path(os.path.expanduser(str(path)))


def load_config(path: Path | str, env: Mapping[str, str] | None = None) -> AppConfig:
    """Load a strict JSON config and apply the documented model env overrides.

    ``SKILL_LAB_API_KEY`` is deliberately not copied into the typed config.
    The client reads the value only when an operator explicitly enables live
    inference, so secrets cannot enter persisted config or model requests.
    """

    with _expanded(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("configuration must be a JSON object")

    model_payload = payload.get("model")
    if isinstance(model_payload, Mapping):
        model_payload = dict(model_payload)
        source_env = os.environ if env is None else env
        for env_name, field_name in _ENV_MODEL_OVERRIDES.items():
            if env_name in source_env:
                model_payload[field_name] = source_env[env_name]
        payload = {**payload, "model": model_payload}

    return AppConfig.model_validate(payload)


def default_config() -> AppConfig:
    """Return the frozen in-code representation of the default contract."""

    return AppConfig.model_validate(DEFAULT_CONFIG)


class OpenAICompatibleClient:
    """Small OpenAI-compatible client returned only for an approved live run."""

    def __init__(
        self,
        config: ModelConfig,
        api_key: str,
        *,
        allow_live: bool = False,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not allow_live:
            raise LiveProviderError("live provider requires --allow-live")
        if not api_key:
            raise ValueError(f"missing API key in environment variable {config.api_key_env}")

        self.config = config
        self._base_url = config.base_url.rstrip("/")
        self._client = http_client or httpx.Client(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=config.timeout_s,
        )

    def complete(self, request: dict[str, Any]) -> ModelResponse:
        """Send one agent request to the OpenAI-compatible chat endpoint."""

        request_body = self._request_body(request)
        started_at = time.monotonic()
        response = self._client.post(
            f"{self._base_url}/chat/completions",
            json=request_body,
        )
        latency_ms = max(0, int(round((time.monotonic() - started_at) * 1000)))
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise ValueError("chat completion response must be a JSON object")

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("chat completion response has no choices")
        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise ValueError("chat completion choice must be an object")
        message = choice.get("message")
        if not isinstance(message, Mapping) or not isinstance(message.get("content"), str):
            raise ValueError("chat completion choice has no text content")

        usage = payload.get("usage")
        if not isinstance(usage, Mapping):
            usage = {}
        input_tokens = _usage_integer(usage, "prompt_tokens")
        output_tokens = _usage_integer(usage, "completion_tokens")
        total_tokens = _usage_integer(usage, "total_tokens", input_tokens + output_tokens)
        model = payload.get("model")
        if not isinstance(model, str) or not model:
            model = None
        finish_reason = choice.get("finish_reason")
        if not isinstance(finish_reason, str) or not finish_reason:
            finish_reason = "stop"

        return ModelResponse(
            model=model,
            content=message["content"],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
        )

    def close(self) -> None:
        """Close the underlying HTTP client."""

        self._client.close()

    def __enter__(self) -> OpenAICompatibleClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _request_body(self, request: Mapping[str, Any]) -> dict[str, Any]:
        kind = request.get("kind")
        if kind == "agent":
            messages = _messages_from_agent_request(request)
        elif kind == "mutation":
            messages = _messages_from_mutation_request(request)
        elif kind is None:
            messages = request.get("messages")
            if not isinstance(messages, list):
                raise ValueError("request must have kind='agent', kind='mutation', or messages")
            messages = _validated_messages(messages)
        else:
            raise ValueError("request kind must be 'agent', 'mutation', or absent")
        body = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        extra_body = self.config.extra_body
        if extra_body is None:
            return body
        if not isinstance(extra_body, dict):
            raise ValueError("extra_body must be a JSON object or null")
        contract_fields = {"model", "messages", "temperature", "max_tokens"}
        collisions = contract_fields.intersection(extra_body)
        if collisions:
            raise ValueError(f"extra_body cannot override contract fields: {sorted(collisions)}")
        return {**body, **extra_body}


def create_chat_model(
    config: AppConfig,
    allow_live: bool = False,
    env: Mapping[str, str] | None = None,
) -> ScriptedMockModel | OpenAICompatibleClient:
    """Create the configured model, enforcing the live-provider safety gate."""

    if config.model.provider == "mock":
        return ScriptedMockModel(seed=config.seed)
    if not allow_live:
        raise LiveProviderError("live provider requires --allow-live")

    source_env = os.environ if env is None else env
    api_key = source_env.get(config.model.api_key_env, "")
    return OpenAICompatibleClient(
        config.model,
        api_key,
        allow_live=True,
    )


def create_model(
    config: AppConfig,
    allow_live: bool = False,
    env: Mapping[str, str] | None = None,
) -> ScriptedMockModel | OpenAICompatibleClient:
    """Compatibility name for callers creating the configured chat model."""

    return create_chat_model(config, allow_live=allow_live, env=env)


def _usage_integer(usage: Mapping[str, Any], name: str, default: int = 0) -> int:
    value = usage.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"chat completion usage {name} must be a non-negative integer")
    return value


def _messages_from_agent_request(request: Mapping[str, Any]) -> list[dict[str, str]]:
    task = request.get("task")
    if not isinstance(task, Mapping):
        raise ValueError("agent request must contain a task object")

    task_input = task.get("input")
    if not isinstance(task_input, str) or not task_input:
        raise ValueError("agent task input must be a non-empty string")

    available_tools = task.get("available_tools")
    if not isinstance(available_tools, list):
        raise ValueError("agent task available_tools must be a list")
    if any(not isinstance(name, str) or not name for name in available_tools):
        raise ValueError("agent task available_tools must contain non-empty strings")

    max_calls = task.get("max_calls")
    if isinstance(max_calls, bool) or not isinstance(max_calls, int) or max_calls < 1:
        raise ValueError("agent task max_calls must be a positive integer")

    tool_arguments: dict[str, list[str]] = {}
    for tool_name in available_tools:
        arguments = _TOOL_ARGUMENTS.get(tool_name)
        if arguments is None:
            raise ValueError(f"agent task contains unknown tool: {tool_name}")
        tool_arguments[tool_name] = list(arguments)

    system_content = (
        "Return exactly one JSON object with no prose or code fences. The object must be "
        'either {"action":"tool","tool":<name>,"arguments":{...}} or '
        '{"action":"final","output":{...}}. '
        f"available_tools and argument names: {json.dumps(tool_arguments, sort_keys=True)}. "
        f"max_calls: {max_calls}."
    )
    skill_markdown = request.get("skill_markdown")
    if skill_markdown is not None:
        if not isinstance(skill_markdown, str):
            raise ValueError("agent skill_markdown must be a string or null")
        if skill_markdown:
            system_content += f"\n\nSkill markdown:\n{skill_markdown}"

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": task_input},
    ]

    tool_history = request.get("tool_history", [])
    if not isinstance(tool_history, list):
        raise ValueError("agent tool_history must be a list")
    for entry in tool_history:
        if not isinstance(entry, Mapping):
            raise ValueError("agent tool_history entries must be objects")
        tool_name = entry.get("tool")
        arguments = entry.get("arguments")
        result = entry.get("result")
        if not isinstance(tool_name, str) or not tool_name:
            raise ValueError("agent tool history tool names must be non-empty strings")
        if not isinstance(arguments, Mapping):
            raise ValueError("agent tool history arguments must be objects")
        if not isinstance(result, Mapping):
            raise ValueError("agent tool history results must be objects")
        action = {
            "action": "tool",
            "tool": tool_name,
            "arguments": dict(arguments),
        }
        messages.append({"role": "assistant", "content": _canonical_json(action)})
        messages.append(
            {
                "role": "tool",
                "name": tool_name,
                "content": _canonical_json(dict(result)),
            }
        )
    return messages


def _messages_from_mutation_request(request: Mapping[str, Any]) -> list[dict[str, str]]:
    current_skill = request.get("current_skill")
    train_failures = request.get("train_failures")
    if not isinstance(current_skill, Mapping):
        raise ValueError("mutation request must contain a current_skill object")
    if not isinstance(train_failures, list):
        raise ValueError("mutation request must contain a train_failures list")

    instruction = (
        "Respond with exactly one JSON object containing exactly four string fields: "
        '{"failure_analysis":<string>,"procedural_change":<string>,'
        '"candidate_markdown":<string>,"rationale":<string>}. '
        "candidate_markdown must contain the complete SKILL.md. "
        "Do not include task identifiers or validation/test evidence."
    )
    envelope = {
        "current_skill": dict(current_skill),
        "train_failures": train_failures,
    }
    return [
        {"role": "system", "content": instruction},
        {"role": "user", "content": _canonical_json(envelope)},
    ]


def _validated_messages(messages: list[Any]) -> list[dict[str, Any]]:
    if not messages:
        raise ValueError("messages must contain at least one non-empty message")
    validated: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, Mapping):
            raise ValueError("prebuilt messages must contain objects")
        role = message.get("role")
        content = message.get("content")
        if not isinstance(role, str) or not role:
            raise ValueError("prebuilt messages must have non-empty roles")
        if not isinstance(content, str) or not content:
            raise ValueError("prebuilt messages must have non-empty string content")
        validated.append(dict(message))
    return validated


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True)
