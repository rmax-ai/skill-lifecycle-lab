# Integrity review 2 — post-remediation

Verdict: **FIX-FIRST**

The remediation substantially repaired the evidence pipeline, durable bundles, held-out gate,
frozen inputs, leakage boundary, and finite-metric handling. Fresh and committed mock artifacts
are reproducible and complete on their normal paths. The live-spend gate nevertheless remains
closed because three residual major defects violate frozen §10 contracts on adversarial paths:

1. an explicit unknown live request kind is accepted when it also carries prebuilt messages;
2. an absent provider response model ID is silently replaced with the requested model ID; and
3. a rejected inconsistent token response becomes a `model_error` but persists internally
   inconsistent token totals and loses observed latency.

No repository files were changed except this report. All generated bundles, databases, and probe
scripts were placed under `/tmp`.

## Finding-by-finding disposition

| Original finding | Status | Direct probe evidence |
|---|---|---|
| 1. Live serialization | **partially fixed** | Direct `_request_body` probes showed both exact action forms, tool and argument names, `max_calls`, skill Markdown, task input, and two replayed assistant/tool pairs. Unique expected-outcome, invariant, and split markers were absent. Mutation messages contained the four-field instruction and a canonical full envelope. A stub clock returned `latency_ms=123`. Empty prebuilt messages and a bare unknown kind raised. However, `{"kind":"bogus","messages":[...]}` was accepted; see new finding 1. |
| 2. Complete publishable evidence | **fixed** | Fresh two-generation verified evolution: 48 unique result rows and 48 trajectories (24 train, 24 validation), two non-empty prompt records, and skills `v001`, `v002`, `v003`, with `v003` rejected. Prompt request skill bodies and response candidate bodies matched the exported files byte-for-byte. Committed verified/naive branches had 66/42 unique rows respectively, matching train/validation/test phases appropriate to each mode. |
| 3. Frozen scientific inputs | **fixed** | Fresh evolution and baseline bundles contained both frozen files with SHA-256 and size declarations covered by their manifests. Changing the configured external paths to nonexistent paths still gave a byte-identical rerun, proving the frozen copies were used. With a frozen dataset changed and only the manifest refreshed, both `rerun` and `held-out` exited 1 with `frozen input hash mismatch` and a patched `create_chat_model` call count of zero. |
| 4. Actual model identity and latency | **partially fixed** | A routed stub response produced `RunRecord.model_id='z-model'`; CSV rows contained `mock-incident-v1`; storage round-trip preserved `z-model`; opening the pre-column schema twice produced one `model_id` column both times. Stub latency was 123 ms. But a provider response with no `model` field was recorded as configured `mock-incident-v1`, contrary to A-3's `None when unavailable`; see new finding 2. |
| 5. Non-finite promotion metrics | **fixed** | Construction with `inf`, `-inf`, and `nan` was rejected for every float-bearing `EvaluationSummary` field found by model introspection and both `PromotionPolicy` fields. A `model_construct` parent with infinite cost returned `reject / inconclusive`; strict `json.dumps(..., allow_nan=False)` succeeded. |
| 6. Duplicated held-out reporting | **fixed** | The committed verified and naive reports each contained exactly three held-out condition rows and the presence-aware interpretation bullet. A fresh evolution report correctly contained zero held-out rows and the unavailable bullet before the additive gate. Its `held-out/report.md` then contained exactly three rows (`no_skill`, `seed`, `evolved_verified`). |
| 7. Leakage boundary | **fixed** | Independent poisoned ablation put `POISON-VALIDATION` and `POISON-TEST` into input, expected evidence, invariants, generated trajectories, and stored validation/test rows. Both markers were absent from failure packets, mutation requests, prompt records, responses, candidate Markdown, and persisted mutation rows. All failure-selection inputs were train runs. The phase spy observed 0 test requests before held-out and test requests only after it. Direct `evolve([test_task])` raised before any model call. |
| 8. Token accounting | **partially fixed** | Direct extraction returned 5 for consistent and missing totals; a supplied total of 99 raised `ValueError`. End-to-end `run_agent` converted that response to `model_error`. However, `_evaluate_slot` then persisted input/output/total as `2/3/0` and latency 0, violating the aggregate accounting invariant; see new finding 3. |
| 9. Durable CLI and held-out workflow | **fixed** | Fresh `evolve` and `baseline` wrote bundles whose manifests covered 16/16 and 7/7 non-manifest files. Fresh evolution reran byte-identically. `held-out` wrote the additive subtree, 18 test rows, and a complete manifest; two runs produced identical hashes for all eight files. Tree comparison returned false for an unexpected root file and true only for an extra top-level `held-out/` file. The live-provider gate exited before model creation. |

## New findings

### 1. [MAJOR] Explicit unknown live request kinds can bypass fail-loud dispatch

File: `src/skill_lab/config.py:237-247`

`_request_body()` treats every kind other than `agent` and `mutation` as a possible prebuilt-message
request. Therefore an explicit unsupported kind is accepted whenever a valid `messages` list is
also present. This contradicts the review requirement that unknown kinds raise and weakens the
claimed kind-based fail-loud dispatch.

Reproduction:

```text
UV_CACHE_DIR=/tmp/review2-uv-cache uv run python - <<'PY'
# Construct approved client with a no-network transport, then:
for req in (
    {'kind':'bogus','messages':[{'role':'user','content':'nonempty'}]},
    {'kind':None,'messages':[{'role':'user','content':'nonempty'}]},
    {'messages':[]},
):
    try: print('ACCEPTED', req, '=>', client._request_body(req)['messages'])
    except Exception as e: print('RAISED', type(e).__name__, e)
PY
```

Observed:

```text
ACCEPTED {'kind': 'bogus', 'messages': [...]} => [...]
ACCEPTED {'kind': None, 'messages': [...]} => [...]
RAISED ValueError messages must contain at least one non-empty message
```

Prebuilt messages can remain a supported kindless compatibility path, but the presence of an
explicit unknown `kind` must not fall through to it.

### 2. [MAJOR] Missing provider model identity is mislabeled as the requested model

Files: `src/skill_lab/config.py:209-211`; `src/skill_lab/experiment.py:1638-1650`

A-3 requires observed response model IDs and `None` when unavailable. The live client instead
substitutes `self.config.model` when the provider response omits `model`. The usage collector has a
second requested/configured-model fallback. The resulting CSV looks like observed routing evidence
even though the provider supplied none, defeating the purpose of detecting provider routing.

Reproduction with a stub response containing choices and usage but no `model` field:

```text
provider_model_absent -> response.model = mock-incident-v1
```

The requested model belongs in bundle configuration; it must not be persisted in the observed
`RunRecord.model_id` field unless the response actually reports it.

### 3. [MAJOR] Rejected token mismatch creates an internally inconsistent run record

Files: `src/skill_lab/experiment.py:55-65`; `src/skill_lab/agent.py:88-100`;
`src/skill_lab/experiment.py:1568-1600`

The usage wrapper records input/output usage before `run_agent()` validates `total_tokens`.
`run_agent()` rejects the mismatch before adding total tokens or latency to its trajectory. The slot
builder then combines the wrapper's components with the trajectory's zero total and zero latency.
Thus the run does enter the required `MODEL_ERROR` path, but its persisted accounting is neither
derived nor coherently rejected.

Reproduction using a model response with input 2, output 3, total 99, latency 4:

```text
{'outcome': 'model_error', 'input_tokens': 2, 'output_tokens': 3,
 'total_tokens': 0, 'latency_ms': 0, 'model_id': 'routed', ...}
sum_invariant False
```

This can make live failure rows, summaries, and artifacts violate
`total_tokens == input_tokens + output_tokens`, while also under-reporting the latency of a call that
actually occurred. The normal mock artifacts were consistent, so the defect is specific to the
adversarial response path.

## Checked and clean — raw evidence

### Live request serialization and latency

Command: `UV_CACHE_DIR=/tmp/review2-uv-cache uv run python /tmp/review2_core_probe.py`, plus a raw
system-message probe.

Observed:

```text
roles= ['system', 'user', 'assistant', 'tool', 'assistant', 'tool']
answer_values_absent=true call_budget=true content_nonempty=true
history_replayed=true skill=true task_input=true tool_argument_names=true
tool_protocol_exact= True
final_protocol_exact= True
SERIALIZER_MUTATION canonical_envelope=true content_nonempty=true
four_fields=true full_envelope=true
SERIALIZER_UNKNOWN ValueError: request must have kind='agent', kind='mutation', or messages
LATENCY_MS 123
```

This is a hermetic transport/clock probe. Per the no-network constraint, interoperability with an
actual OpenAI-compatible endpoint was not tested.

### Fresh durable bundles and complete evidence

Commands:

```text
UV_CACHE_DIR=/tmp/review2-uv-cache uv run skill-lab evolve \
  --skill incident-response --generations 2 --runs-per-task 1 --mode verified \
  --config /tmp/review2-config.json --output /tmp/review2-bundle

UV_CACHE_DIR=/tmp/review2-uv-cache uv run skill-lab baseline \
  --condition seed --runs-per-task 1 \
  --config /tmp/review2-config.json --output /tmp/review2-baseline
```

Observed:

```text
artifact_path: /tmp/review2-bundle
artifact_path: /tmp/review2-baseline
BUNDLE review2-bundle manifest_complete=True files=16 rows=48
BUNDLE review2-baseline manifest_complete=True files=7 rows=12

/tmp/review2-bundle results=48 trajectories=48 unique_rows=48
splits {'train': 24, 'validation': 24}
prompts=2; generations: v002 promoted, v003 rejected
skills ['v001', 'v002', 'v003']
tokens_consistent=True

generation 1 request_skill_exact=True response_skill_exact=True response_string_preserved=True
generation 2 request_skill_exact=True response_skill_exact=True response_string_preserved=True
```

### Held-out gate, manifest, idempotence, and rerun scope

Commands:

```text
UV_CACHE_DIR=/tmp/review2-uv-cache uv run skill-lab held-out \
  --experiment /tmp/review2-bundle --runs-per-task 1
find /tmp/review2-bundle/held-out -type f -print0 | sort -z | xargs -0 sha256sum
# Repeat both commands.
UV_CACHE_DIR=/tmp/review2-uv-cache uv run skill-lab rerun \
  --experiment /tmp/review2-bundle
```

Observed on both held-out invocations: identical hashes for `config.json`, `held-out.json`, both
inputs, `manifest.json`, `report.md`, `results.csv`, and `trajectories.jsonl`.

```text
heldout rows 18
splits ['test']
conditions {'evolved_verified': 6, 'no_skill': 6, 'seed': 6}
report condition rows 3
manifest_complete True
rerun: byte-identical /tmp/review2-bundle
TREE_COMPARE_SCOPE nonheldout_extra=False heldout_extra=True
```

Tamper/live/frozen-source spy (`uv run python /tmp/review2_cli_probe.py`):

```text
TAMPER_RERUN 1 frozen input hash mismatch: inputs/incident_tasks.json model_calls=0
TAMPER_HELDOUT 1 frozen input hash mismatch: inputs/incident_tasks.json model_calls=0
LIVE_GATE 1 live held-out evaluation requires --allow-live model_calls=0
FROZEN_SOURCE_RERUN 0 rerun: byte-identical /tmp/review2-frozen-use
```

### Model metadata, schema migration, and finite metrics

Observed from the core probe:

```text
MODEL_ID_OBSERVED z-model tokens=(2, 3, 5)
MODEL_ID_ROUNDTRIP z-model True
MIGRATION_IDEMPOTENT 1 1
NONFINITE_CONSTRUCTION {...all discovered EvaluationSummary float fields: true,
                         both PromotionPolicy fields: true}
NONFINITE_BYPASS reject ['inconclusive'] json_safe
DEFAULT_CONFIG_EQUAL True
```

The construction probe covered `success_rate`, `success_variance`, `regression_rate`,
`parent_success_rate`, `skill_lift`, `no_skill_success_rate`, `improvement_over_no_skill`,
`estimated_cost_usd`, and the float values within `task_success_means`, with each of `inf`, `-inf`,
and `nan`.

### Poisoned leakage matrix

Command: `UV_CACHE_DIR=/tmp/review2-uv-cache uv run python /tmp/review2_leakage_probe.py`.

Observed:

```text
SELECTION_TRAIN_ONLY True True packets=3
POISON_ABSENCE POISON-VALIDATION {all six inspected surfaces: True}
POISON_ABSENCE POISON-TEST {all six inspected surfaces: True}
POISON_INJECTED_STORE {'validation': True, 'test': True}
HELDOUT_PHASE boundary=317 test_before=0 test_after=256 poison_after=True
EVOLVE_TEST_GUARD ValueError ... model_calls=0
```

The six absence surfaces were mutation requests, exported prompt records, mutation responses,
candidate Markdown, persisted mutation rows, and serialized failure packets.

### Committed example, default config, validation, and full suite

Commands and observed results:

```text
UV_CACHE_DIR=/tmp/review2-uv-cache uv run skill-lab rerun \
  --experiment artifacts/example-mock
rerun: byte-identical artifacts/example-mock

UV_CACHE_DIR=/tmp/review2-uv-cache uv run skill-lab validate-data \
  --config configs/default.json
valid

UV_CACHE_DIR=/tmp/review2-uv-cache uv run pytest -q
[100%] (no failures; two Typer/Click deprecation warnings)

UV_CACHE_DIR=/tmp/review2-uv-cache uv run pytest --collect-only
114 tests collected in 0.29s
```

Committed artifact inspection:

```text
verified: results=66 trajectories=66 unique_rows=66
          splits test=18 train=24 validation=24; prompts=2
          skills=v001,v002,v003; held-out rows=3; manifest 31/31 complete
naive:    results=42 trajectories=42 unique_rows=42
          splits test=18 train=24; prompts=2
          skills=v001,v002,v003; held-out rows=3
```

The root contains `inputs/incident_tasks.json` and `inputs/tool_world.json`, with declarations in
`config.inputs` and coverage in the root manifest. `configs/default.json` exists and its parsed
`AppConfig.model_dump(mode="json")` exactly equals the in-code default under an empty environment.

## Uncertainty

- No external provider was contacted, as required. Live behavior was verified through the actual
  client and agent paths with stub HTTP transports and monkeypatched monotonic time.
- Pre-spend ordering was verified with patched model-construction spies. This proves call ordering
  in-process, not behavior under process termination or filesystem hardware failure.
- The mock artifacts are internally reproducible; they do not establish performance of any live
  model or causal benefit from skill mutation.
