"""Bounded agent execution with deterministic trajectory capture."""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from skill_lab.mock_model import ModelResponse
from skill_lab.models import Outcome, Task, TaskId, ToolCall, Trajectory, Version
from skill_lab.tools import ToolEnvironment

_MISSING = object()


class AgentConfig(BaseModel):
    """Limits applied to one agent run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_steps: int = Field(default=8, ge=1)
    token_budget: int = Field(default=2400, ge=0)


class AgentRequest(BaseModel):
    """JSON request envelope sent to a chat model for one agent turn."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task: Task
    task_id: TaskId
    skill_markdown: str | None = None
    skill_version: Version | None = None
    seed: int
    tool_history: list[ToolCall] = Field(default_factory=list)
    kind: Literal["agent"] = "agent"


class ChatModel(Protocol):
    """The model interface required by the bounded agent loop."""

    def complete(self, request: dict[str, Any]) -> ModelResponse:
        """Return one JSON action and its usage metadata."""


def run_agent(
    task: Task,
    skill_markdown: str | None,
    model: ChatModel,
    tools: ToolEnvironment,
    config: AgentConfig,
    seed: int,
    experiment_id: str,
    skill_version: str | None,
) -> Trajectory:
    """Run one agent task without retries and return its complete trajectory."""

    messages: list[dict[str, Any]] = []
    if skill_markdown is not None:
        messages.append({"role": "system", "content": skill_markdown})
    messages.append({"role": "user", "content": task.input})

    initial_call_count = len(tools.calls)
    tool_calls: list[ToolCall] = []
    total_tokens = 0
    generated_tokens = 0
    latency_ms = 0
    steps = 0
    final_output: dict | str = {}
    outcome = Outcome.BUDGET_EXHAUSTED

    while True:
        if steps >= config.max_steps or generated_tokens >= config.token_budget:
            outcome = Outcome.BUDGET_EXHAUSTED
            break

        request = AgentRequest(
            task=task,
            task_id=task.id,
            skill_markdown=skill_markdown,
            skill_version=skill_version,
            seed=seed,
            tool_history=tool_calls,
        )
        try:
            response = model.complete(request.model_dump(mode="json"))
        except Exception:
            outcome = Outcome.MODEL_ERROR
            break

        try:
            (
                content,
                input_tokens,
                output_tokens,
                response_tokens,
                response_latency,
                response_valid,
            ) = _response_values_lenient(response)
        except Exception:
            (
                content,
                input_tokens,
                output_tokens,
                response_tokens,
                response_latency,
                response_valid,
            ) = _salvage_response_values(response)

        steps += 1
        total_tokens += input_tokens + output_tokens
        generated_tokens += output_tokens
        latency_ms += response_latency
        if content is not None:
            messages.append({"role": "assistant", "content": content})

        if not response_valid or response_tokens != input_tokens + output_tokens:
            outcome = Outcome.MODEL_ERROR
            break

        if generated_tokens > config.token_budget:
            outcome = Outcome.BUDGET_EXHAUSTED
            break

        try:
            action = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            outcome = Outcome.MODEL_ERROR
            break
        if not isinstance(action, dict):
            outcome = Outcome.AGENT_ERROR
            break

        action_name = action.get("action")
        if action_name == "final":
            output = action.get("output", _MISSING)
            if not isinstance(output, (dict, str)):
                outcome = Outcome.AGENT_ERROR
                break
            final_output = deepcopy(output)
            outcome = Outcome.SUCCESS
            break

        if action_name != "tool":
            outcome = Outcome.AGENT_ERROR
            break

        tool_name = action.get("tool")
        if not isinstance(tool_name, str) or not tool_name:
            outcome = Outcome.AGENT_ERROR
            break
        arguments = action.get("arguments", {})
        try:
            result = tools.execute(tool_name, arguments)
        except Exception:
            outcome = Outcome.TOOL_ERROR
            break
        if not isinstance(result, dict):
            outcome = Outcome.TOOL_ERROR
            break

        messages.append(
            {
                "role": "tool",
                "name": tool_name,
                "content": deepcopy(result),
            }
        )
        tool_calls = list(tools.calls[initial_call_count:])
        if _is_tool_error(result):
            outcome = Outcome.TOOL_ERROR
            break
        if steps >= config.max_steps or generated_tokens >= config.token_budget:
            outcome = Outcome.BUDGET_EXHAUSTED
            break

    return Trajectory(
        experiment_id=experiment_id,
        task_id=task.id,
        skill_version=skill_version,
        messages=messages,
        tool_calls=tool_calls,
        final_output=final_output,
        tokens=total_tokens,
        latency_ms=latency_ms,
        outcome=outcome,
    )


def _response_values(response: Any) -> tuple[str, int, int, int, int]:
    """Extract a model response while accepting the shared typed envelope."""

    if isinstance(response, str):
        content = response
    else:
        content_value = _response_field(response, "content")
        if content_value is _MISSING:
            if isinstance(response, Mapping) and "action" in response:
                content_value = json.dumps(dict(response), sort_keys=True)
            else:
                output = _response_field(response, "output")
                if isinstance(output, Mapping):
                    content_value = json.dumps(dict(output), sort_keys=True)
        if not isinstance(content_value, str):
            raise ValueError("model response content must be a string")
        content = content_value

    input_tokens = _response_integer(response, "input_tokens")
    output_tokens = _response_integer(response, "output_tokens")
    total_value = _response_field(response, "total_tokens")
    if total_value is _MISSING:
        total_tokens = input_tokens + output_tokens
    else:
        total_tokens = _as_nonnegative_integer(total_value, "total_tokens")
        expected_total = input_tokens + output_tokens
        if total_tokens != expected_total:
            raise ValueError("model response total_tokens must equal input_tokens + output_tokens")
    latency_ms = _response_integer(response, "latency_ms")
    return content, input_tokens, output_tokens, total_tokens, latency_ms


def _response_values_lenient(
    response: Any,
) -> tuple[str | None, int, int, int, int, bool]:
    """Extract response values without raising before the agent accounts for a call."""

    content = _response_content_lenient(response)
    input_tokens, input_valid = _response_integer_lenient(response, "input_tokens")
    output_tokens, output_valid = _response_integer_lenient(response, "output_tokens")
    total_value = _response_field_lenient(response, "total_tokens")
    if total_value is _MISSING:
        total_tokens = input_tokens + output_tokens
        total_valid = True
    else:
        total_tokens, total_valid = _nonnegative_integer_lenient(total_value)
    latency_ms, latency_valid = _response_integer_lenient(response, "latency_ms")
    response_valid = (
        content is not None and input_valid and output_valid and total_valid and latency_valid
    )
    return content, input_tokens, output_tokens, total_tokens, latency_ms, response_valid


def _salvage_response_values(
    response: Any,
) -> tuple[str | None, int, int, int, int, bool]:
    """Salvage valid numeric fields after an unexpected extraction failure."""

    content = _response_content_lenient(response)
    input_tokens, _ = _response_integer_lenient(response, "input_tokens")
    output_tokens, _ = _response_integer_lenient(response, "output_tokens")
    latency_ms, _ = _response_integer_lenient(response, "latency_ms")
    return content, input_tokens, output_tokens, input_tokens + output_tokens, latency_ms, False


def _response_field(response: Any, name: str) -> Any:
    if isinstance(response, Mapping):
        return response.get(name, _MISSING)
    return getattr(response, name, _MISSING)


def _response_field_lenient(response: Any, name: str) -> Any:
    try:
        return _response_field(response, name)
    except Exception:
        return _MISSING


def _response_integer(response: Any, name: str) -> int:
    value = _response_field(response, name)
    if value is _MISSING or value is None:
        return 0
    return _as_nonnegative_integer(value, name)


def _response_integer_lenient(response: Any, name: str) -> tuple[int, bool]:
    value = _response_field_lenient(response, name)
    if value is _MISSING or value is None:
        return 0, True
    return _nonnegative_integer_lenient(value)


def _as_nonnegative_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"model response {name} must be a non-negative integer")
    return value


def _nonnegative_integer_lenient(value: Any) -> tuple[int, bool]:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0, False
    return value, True


def _response_content_lenient(response: Any) -> str | None:
    if isinstance(response, str):
        return response
    content_value = _response_field_lenient(response, "content")
    if content_value is _MISSING:
        try:
            if isinstance(response, Mapping) and "action" in response:
                content_value = json.dumps(dict(response), sort_keys=True)
            else:
                output = _response_field_lenient(response, "output")
                if isinstance(output, Mapping):
                    content_value = json.dumps(dict(output), sort_keys=True)
        except Exception:
            return None
    return content_value if isinstance(content_value, str) else None


def _is_tool_error(result: Mapping[str, Any]) -> bool:
    return isinstance(result.get("error"), Mapping)
