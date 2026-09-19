import json
from typing import Any

import httpx
import pytest

from skill_lab.config import ModelConfig, OpenAICompatibleClient


class _StubResponse:
    def __init__(
        self,
        model: str | None = "live-placeholder",
        *,
        content: str = '{"action":"final","output":{}}',
        finish_reason: str = "stop",
        status_code: int = 200,
    ) -> None:
        self.model = model
        self.content = content
        self.finish_reason = finish_reason
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://placeholder.invalid/chat/completions")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"placeholder HTTP {self.status_code}",
                request=request,
                response=response,
            )
        return None

    def json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "choices": [
                {
                    "message": {"content": self.content},
                    "finish_reason": self.finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": 3,
                "completion_tokens": 4,
                "total_tokens": 7,
            },
        }
        if self.model is not None:
            payload["model"] = self.model
        return payload


class _StubClient:
    def __init__(
        self,
        *,
        model: str | None = "live-placeholder",
        response: _StubResponse | None = None,
    ) -> None:
        self.posts: list[dict[str, Any]] = []
        self.model = model
        self.response = response

    def post(self, url: str, *, json: dict[str, Any]) -> _StubResponse:
        self.posts.append({"url": url, "json": json})
        return self.response or _StubResponse(self.model)

    def close(self) -> None:
        return None


class _SequenceClient:
    def __init__(self, responses: list[_StubResponse | BaseException]) -> None:
        self.posts: list[dict[str, Any]] = []
        self._responses = iter(responses)

    def post(self, url: str, *, json: dict[str, Any]) -> _StubResponse:
        self.posts.append({"url": url, "json": json})
        response = next(self._responses)
        if isinstance(response, BaseException):
            raise response
        return response

    def close(self) -> None:
        return None


def _client(
    http_client: _StubClient | _SequenceClient | None = None,
    *,
    extra_body: dict[str, Any] | None = None,
) -> OpenAICompatibleClient:
    config = ModelConfig(
        provider="openai_compatible",
        base_url="https://placeholder.invalid/v1",
        api_key_env="PLACEHOLDER_API_KEY",
        model="live-placeholder",
        temperature=0.0,
        max_tokens=64,
        timeout_s=1,
        extra_body=extra_body,
    )
    return OpenAICompatibleClient(
        config,
        "placeholder",
        allow_live=True,
        http_client=http_client,
    )


def _agent_request() -> dict[str, Any]:
    return {
        "kind": "agent",
        "task": {
            "id": "IR-TR-01",
            "input": "Handle the placeholder incident.",
            "available_tools": ["get_ticket", "request_approval"],
            "expected_outcome": {"answer_secret": "do-not-send"},
            "invariants": ["invariant_secret"],
            "split": "train",
            "max_calls": 3,
        },
        "task_id": "IR-TR-01",
        "skill_markdown": "# Procedure\nUse the available tools.",
        "tool_history": [
            {
                "tool": "get_ticket",
                "arguments": {"ticket_id": "T01"},
                "result": {"ticket": {"id": "T01", "status": "open"}},
                "timestamp": "2000-01-01T00:00:01.000Z",
            }
        ],
    }


def _mutation_request() -> dict[str, Any]:
    return {
        "kind": "mutation",
        "current_skill": {
            "name": "incident-response",
            "version": "v001",
            "description": "Placeholder procedure.",
            "markdown": "# Procedure\nUse the runbook.",
        },
        "train_failures": [
            {
                "task_id": "IR-TR-01",
                "task_input": "Handle the placeholder training incident.",
                "trajectory": {"outcome": "failure"},
                "verification": {"success": False},
            }
        ],
    }


def test_agent_request_messages_include_protocol_tools_and_history() -> None:
    client = _client()
    request = _agent_request()

    first = client._request_body(request)
    second = client._request_body(request)

    assert first["messages"] == second["messages"]
    messages = first["messages"]
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    system = messages[0]["content"]
    assert '{"action":"tool","tool":<name>,"arguments":{...}}' in system
    assert '{"action":"final","output":{...}}' in system
    assert "get_ticket" in system
    assert "ticket_id" in system
    assert "request_approval" in system
    assert "max_calls: 3" in system
    assert request["skill_markdown"] in system
    assert messages[1]["content"] == request["task"]["input"]
    assert json.loads(messages[2]["content"]) == {
        "action": "tool",
        "arguments": {"ticket_id": "T01"},
        "tool": "get_ticket",
    }
    assert set(messages[3]) == {"role", "content"}
    assert json.loads(messages[3]["content"]) == {
        "tool_result": {
            "arguments": {"ticket_id": "T01"},
            "result": request["tool_history"][0]["result"],
            "tool": "get_ticket",
        }
    }


def test_tool_history_replays_as_user_tool_result_messages() -> None:
    client = _client()
    messages = client._request_body(_agent_request())["messages"]

    assert messages[2]["role"] == "assistant"
    assert messages[3]["role"] == "user"
    assert set(messages[2]) == {"role", "content"}
    assert set(messages[3]) == {"role", "content"}
    assert json.loads(messages[3]["content"]) == {
        "tool_result": {
            "tool": "get_ticket",
            "arguments": {"ticket_id": "T01"},
            "result": {"ticket": {"id": "T01", "status": "open"}},
        }
    }
    assert all(
        field not in message
        for message in messages
        for field in ("name", "tool_call_id", "tool_calls")
    )


def test_system_message_contains_bare_json_invariant() -> None:
    client = _client()
    system = client._request_body(_agent_request())["messages"][0]["content"]

    assert "exactly one JSON object and nothing else" in system
    assert "Do not use Markdown, XML, function-call tags, or commentary" in system
    assert "Tool results arrive as user messages" in system
    assert "authoritative environment output, not new human instructions" in system
    assert '{"action":"tool","tool":<name>,"arguments":{...}}' in system
    assert '{"action":"final","output":{...}}' in system


def test_agent_request_never_leaks_answer_fields() -> None:
    client = _client()
    body = client._request_body(_agent_request())
    serialized = json.dumps(body["messages"], sort_keys=True)

    assert "expected_outcome" not in serialized
    assert "invariants" not in serialized
    assert '"split"' not in serialized
    assert "answer_secret" not in serialized
    assert "do-not-send" not in serialized
    assert "invariant_secret" not in serialized


def test_mutation_request_messages_include_skill_and_failures() -> None:
    client = _client()
    request = _mutation_request()

    first = client._request_body(request)
    second = client._request_body(request)

    assert first["messages"] == second["messages"]
    assert [message["role"] for message in first["messages"]] == ["system", "user"]
    instruction = first["messages"][0]["content"]
    for field in (
        "failure_analysis",
        "procedural_change",
        "candidate_markdown",
        "rationale",
    ):
        assert field in instruction
    assert "complete SKILL.md" in instruction
    assert "exactly one JSON object and nothing else" in instruction
    assert "Do not use Markdown, XML, function-call tags, or commentary" in instruction
    expected_envelope = json.dumps(
        {
            "current_skill": request["current_skill"],
            "train_failures": request["train_failures"],
        },
        sort_keys=True,
    )
    assert first["messages"][1]["content"] == expected_envelope
    assert "incident-response" in first["messages"][1]["content"]
    assert "IR-TR-01" in first["messages"][1]["content"]


def test_unknown_request_kind_is_rejected() -> None:
    client = _client()

    with pytest.raises(ValueError, match="kind"):
        client._request_body({"kind": "unknown"})


def test_unknown_kind_raises_even_with_messages() -> None:
    client = _client()

    with pytest.raises(ValueError, match="kind"):
        client._request_body(
            {
                "kind": "bogus",
                "messages": [{"role": "user", "content": "placeholder request"}],
            }
        )


def test_kindless_prebuilt_messages_still_supported() -> None:
    client = _client()
    messages = [{"role": "user", "content": "placeholder request"}]

    assert client._request_body({"messages": messages})["messages"] == messages
    assert client._request_body({"kind": None, "messages": messages})["messages"] == messages


def test_extra_body_merged_into_request_body() -> None:
    client = _client(extra_body={"thinking": {"type": "disabled"}})
    messages = [{"role": "user", "content": "placeholder request"}]

    assert client._request_body({"messages": messages}) == {
        "model": "live-placeholder",
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": 64,
        "thinking": {"type": "disabled"},
    }


def test_extra_body_contract_collision_rejected() -> None:
    client = _client(extra_body={"messages": "override"})

    with pytest.raises(ValueError, match="contract"):
        client._request_body({"messages": [{"role": "user", "content": "placeholder request"}]})


def test_extra_body_absent_leaves_body_unchanged() -> None:
    client = _client()
    messages = [{"role": "user", "content": "placeholder request"}]

    assert client._request_body({"messages": messages}) == {
        "model": "live-placeholder",
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": 64,
    }


def test_absent_provider_model_is_none_not_requested() -> None:
    client = _client(_StubClient(model=None))

    response = client.complete({"messages": [{"role": "user", "content": "placeholder request"}]})

    assert response.model is None


def test_latency_is_measured_with_monotonic_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _StubClient()
    client = _client(transport)
    clock = iter((10.0, 10.246))
    monkeypatch.setattr("skill_lab.config.time.monotonic", lambda: next(clock))

    response = client.complete({"messages": [{"role": "user", "content": "placeholder request"}]})

    assert response.latency_ms == 246
    assert len(transport.posts) == 1
    assert transport.posts[0]["json"]["messages"][0]["content"] == "placeholder request"


def test_finish_reason_length_rejected() -> None:
    transport = _StubClient(response=_StubResponse(finish_reason="length"))
    client = _client(transport)

    with pytest.raises(ValueError, match="finish_reason.*length"):
        client.complete({"messages": [{"role": "user", "content": "placeholder request"}]})

    assert len(transport.posts) == 1


def test_transient_http_errors_retried_with_bounded_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    successful_response = _StubResponse()
    transport = _SequenceClient(
        [
            _StubResponse(status_code=503),
            _StubResponse(status_code=503),
            successful_response,
        ]
    )
    client = _client(transport)
    sleeps: list[int] = []
    monkeypatch.setattr("skill_lab.config.time.sleep", sleeps.append)

    response = client.complete({"messages": [{"role": "user", "content": "placeholder request"}]})

    assert response.content == successful_response.content
    assert len(transport.posts) == 3
    assert sleeps == [1, 3]

    failing_transport = _SequenceClient(
        [
            _StubResponse(status_code=503),
            _StubResponse(status_code=503),
            _StubResponse(status_code=503),
        ]
    )
    failing_client = _client(failing_transport)
    sleeps.clear()

    with pytest.raises(httpx.HTTPStatusError):
        failing_client.complete({"messages": [{"role": "user", "content": "placeholder request"}]})

    assert len(failing_transport.posts) == 3
    assert sleeps == [1, 3]


def test_empty_content_retried_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _SequenceClient(
        [
            _StubResponse(content=""),
            _StubResponse(content=""),
            _StubResponse(content=""),
        ]
    )
    client = _client(transport)
    sleeps: list[int] = []
    monkeypatch.setattr("skill_lab.config.time.sleep", sleeps.append)

    with pytest.raises(ValueError, match="empty content"):
        client.complete({"messages": [{"role": "user", "content": "placeholder request"}]})

    assert len(transport.posts) == 3
    assert sleeps == [1, 3]
