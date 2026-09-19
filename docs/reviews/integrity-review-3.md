# Integrity review 3 — scoped verification of review-2 residuals

Verdict: **PASS**

All three review-2 residual majors are fixed by direct, hermetic probes. The touched live request,
response-metadata, usage-collection, and agent error-accounting paths showed no regression. The
full offline suite and required artifact/CLI regressions are green, and no new major defect was
found.

**Live-spend gate outcome: PASS.** This checkout satisfies the repository's offline gate for an
operator-authorized live (paid) model run. This is not evidence that a real provider is compatible
or behaves correctly; the remaining unverified scope is stated below.

No implementation or test files were changed. The probe was written to
`/tmp/integrity_review3_probe.py`; generated configurations, database, bundle, and captured CLI
output were also kept under `/tmp`.

## Residual disposition

| Review-2 residual | Status | Direct probe result |
|---|---|---|
| 1. Kind bypass | **fixed** | Explicit `kind="bogus"` plus non-empty messages raised `ValueError`; explicit `kind=null` plus valid messages remained accepted; empty messages raised. Agent and mutation serialization also retained the §10 A-1 contract. |
| 2. Observed-model substitution | **fixed** | A provider response without `model` produced `ModelResponse.model=None` and `RunRecord.model_id=None`. A response with `model="routed-x"` remained `routed-x` through client, usage collector, and run record. The mock response explicitly reported `mock-incident-v1`. Collector inspection found no requested/config/model fallback. |
| 3. Error-path accounting | **fixed** | The exact `input=2, output=3, total=99, latency=4` response produced `model_error`, total 5, and latency 4 in both trajectory and run record. A non-integer input-token field produced a consistent persisted record using the valid components and preserved latency. Fresh and committed artifact scans found zero sum violations. |

## Raw probe evidence

Command:

```text
UV_CACHE_DIR=/tmp/integrity-review3-uv-cache \
  uv run python /tmp/integrity_review3_probe.py
```

### 1. Strict request kind and §10 A-1 serialization

```text
RESIDUAL_1_KIND
{"kind": "bogus", "messages": [{"content": "nonempty", "role": "user"}]} => RAISED ValueError: request kind must be 'agent', 'mutation', or absent
{"kind": null, "messages": [{"content": "nonempty", "role": "user"}]} => ACCEPTED
{"messages": []} => RAISED ValueError: messages must contain at least one non-empty message
SERIALIZER_AGENT {'roles': ['system', 'user', 'assistant', 'tool'], 'protocol_tool': True, 'protocol_final': True, 'tool_schema': True, 'max_calls': True, 'skill': True, 'task_input': True, 'history': True, 'answer_fields_absent': True}
SERIALIZER_MUTATION {'roles': ['system', 'user'], 'four_fields': True, 'complete_skill': True, 'canonical_envelope': True}
```

The agent probe checked the exact tool/final action forms, available tool names and argument names,
`max_calls`, skill Markdown, task input, one replayed assistant/tool history pair, and absence of
`expected_outcome`, `invariants`, `split`, and their marker values. The mutation probe checked all
four required response fields, the complete-SKILL requirement, and byte equality with the
`sort_keys=True` current-skill/train-failures envelope.

The strict dispatch is at `src/skill_lab/config.py:237-249`.

### 2. Response-observed model identity only

```text
RESIDUAL_2_MODEL
ABSENT {'response_model': None, 'run_model_id': None}
ROUTED {'response_model': 'routed-x', 'usage_model_id': 'routed-x', 'run_model_id': 'routed-x'}
MOCK {'class_model_id': 'mock-incident-v1', 'response_model': 'mock-incident-v1'}
```

Both provider cases used an offline stub HTTP transport and passed through
`OpenAICompatibleClient.complete()` and `_evaluate_slot()`. The routed case additionally called
`_usage_from_response()` directly to expose the middle value.

Code inspection corroborated the probe:

```text
src/skill_lab/config.py:209:        model = payload.get("model")
src/skill_lab/config.py:210:        if not isinstance(model, str) or not model:
src/skill_lab/config.py:211:            model = None

src/skill_lab/experiment.py:1638:def _usage_from_response(response: Any, model: ChatModel) -> _Usage:
src/skill_lab/experiment.py:1641:    model_id = _response_value(response, "model")
src/skill_lab/experiment.py:1642:    if not isinstance(model_id, str) or not model_id:
src/skill_lab/experiment.py:1643:        model_id = None
```

The collector body has no `config.model`, `model.model_id`, or `getattr(model, "model_id", ...)`
fallback. Other `model_id` uses found elsewhere in `experiment.py` concern wrapper/experiment
metadata, not `_usage_from_response()` or `RunRecord.model_id` collection. `ModelResponse.model`
is optional at `src/skill_lab/mock_model.py:60`, while the mock explicitly sets its ID at
`src/skill_lab/mock_model.py:145-150`.

### 3. Rejected-response accounting

```text
RESIDUAL_3_ACCOUNTING
MISMATCH_TRAJECTORY {'outcome': 'model_error', 'tokens': 5, 'latency_ms': 4, 'assistant_retained': True}
MISMATCH_RUN {'outcome': 'model_error', 'input_tokens': 2, 'output_tokens': 3, 'total_tokens': 5, 'latency_ms': 4, 'consistent': True}
INVALID_RUN {'outcome': 'model_error', 'input_tokens': 0, 'output_tokens': 3, 'total_tokens': 3, 'latency_ms': 4, 'consistent': True}
```

The first response was exactly input 2, output 3, supplied total 99, and latency 4. The direct
`run_agent()` trajectory and harness `_evaluate_slot()` record both followed `model_error`; the
record derived total 5 from the components and retained latency 4. The assistant response was also
retained. The structural probe supplied non-integer `input_tokens="invalid"`; the persisted record
used zero for that invalid component, retained the valid output component and latency, and stored
`total_tokens=3`.

The component accounting and rejection occur at `src/skill_lab/agent.py:94-122`; salvage is at
`src/skill_lab/agent.py:246-255`; the slot maps trajectory total and latency into the record at
`src/skill_lab/experiment.py:1597-1600`.

Artifact scan output:

```text
ARTIFACT_SCAN artifacts/example-mock (108, 0, [])
ARTIFACT_SCAN /tmp/integrity-review3-fresh (48, 0, [])
```

The tuple is `(rows_checked, violations, locations)`. The committed example includes both its
verified and naive result files. Every scanned row satisfied
`total_tokens == input_tokens + output_tokens`.

## Regression evidence

### Full suite and lint

```text
$ UV_CACHE_DIR=/tmp/integrity-review3-uv-cache uv run pytest
........................................................................ [ 60%]
...............................................                          [100%]
119 passed, 2 warnings in 10.12s

$ UV_CACHE_DIR=/tmp/integrity-review3-uv-cache uv run pytest --collect-only
119 tests collected in 0.29s

$ UV_CACHE_DIR=/tmp/integrity-review3-uv-cache uv run ruff check .
All checks passed!
```

The independent collection run confirms that the executed suite and collected suite both contain
exactly 119 tests.

### Mock artifact compatibility and data validation

```text
$ UV_CACHE_DIR=/tmp/integrity-review3-uv-cache uv run skill-lab rerun --experiment artifacts/example-mock
rerun: byte-identical artifacts/example-mock

$ UV_CACHE_DIR=/tmp/integrity-review3-uv-cache uv run skill-lab validate-data --config configs/default.json
valid
```

A fresh two-generation verified mock evolution also completed normally:

```text
$ UV_CACHE_DIR=/tmp/integrity-review3-uv-cache uv run skill-lab evolve \
    --skill incident-response --generations 2 --runs-per-task 1 --mode verified \
    --config /tmp/integrity-review3-config.json \
    --output /tmp/integrity-review3-fresh
artifact_path: /tmp/integrity-review3-fresh
model_id: mock-incident-v1
```

### Live-provider gate

The direct creation probe returned:

```text
LIVE_GATE RAISED LiveProviderError: live provider requires --allow-live
```

A CLI evolution attempt using an `openai_compatible` `/tmp` config, a placeholder key, and no
`--allow-live` exited 1 with:

```text
LiveProviderError: live provider requires --allow-live
exit_code=1
```

The failure occurred at model creation before any HTTP request. No external provider was contacted.

## New findings

None.

## What remains unverified

- No request was sent to a real OpenAI-compatible provider. Actual endpoint interoperability,
  authentication, routing metadata, usage shapes, rate limits, and billing behavior remain
  unverified.
- The offline transport proves how missing and routed `model` fields are handled after receipt; it
  cannot prove that a particular provider reports those fields honestly or consistently.
- This scoped review did not repeat review 2's already-fixed leakage, frozen-input tamper,
  promotion, finite-metric, manifest-completeness, or held-out idempotence probes beyond the full
  regression suite and the checks explicitly requested here.
