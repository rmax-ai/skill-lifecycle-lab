# Integrity Review 7 — Final-Output Schema in Protocol Message

**Verdict: PASS**

Card B37 correctly communicates the complete frozen `final.output` contract in the agent
system message. The sentence names exactly the verifier's seven top-level keys, accurately
states every enforced type, and gives the exact two-key `required_updates` shape. It is emitted
verbatim once for both no-skill and skill requests, before optional skill markdown.

No answer-envelope name or value leaks into serialized agent messages. The clause is constant
across tasks and contains no task-specific value. Under SPEC/PLAN A-1, the task input and
`max_calls` are intentionally present elsewhere in the protocol; the independent probe therefore
distinguishes those required fields from prohibited answer data. Task IDs remain absent globally.
Mutation serialization is unchanged.

No new defects were identified. No provider was contacted. All probes and fresh artifacts were
created under `/tmp`; this report is the only workspace file written by the review. **Operator
live smoke 4 may proceed.**

## Evidence table

| Item | Result | Direct evidence |
|---|---|---|
| Sentence versus verifier constants | **PASS** | Independent extraction found sentence keys `classification`, `escalation_path`, `evidence`, `final_status`, `required_updates`, `severity`, and `ticket_id`, exactly equal to `verifier._EXPECTED_OUTCOME_KEYS`; missing and extra sets were empty. Nested keys are exactly `owner` and `priority`. Source: `src/skill_lab/config.py:54-60`; `src/skill_lab/verifier.py:9-20,117-135`. |
| Type contract | **PASS** | The five scalar fields are described as strings; `evidence` is described as an array of evidence-label strings collected from the runbook; `required_updates` is described as an object with exactly `owner` and `priority`, both strings; no additional fields are allowed. Direct verifier probes accepted the valid shape and rejected a non-string scalar, non-list evidence, non-string evidence member, extra update key, and non-string update value. |
| Real request rendering | **PASS** | `_request_body` rendered the sentence verbatim for a request with `skill_markdown: null` and for one with skill markdown. It occurred exactly once in each system message and preceded the optional skill block. Source: `src/skill_lab/config.py:378-397`. |
| Leakage boundary | **PASS** | Across two requests with distinct sentinels in task ID, input, call budget, every expected-outcome value, invariant value, and split value: the schema clause contained no sentinel; task IDs were absent from all messages; `expected_outcome`, `invariants`, `"split"`, and all of their sentinel values were absent from all messages. As A-1 requires, each task input appears as the user message and each call budget appears in the system protocol. |
| Determinism | **PASS** | Re-rendering the identical input dict produced byte-identical canonical message JSON. The extracted sentence was identical across different tasks/calls and occurred once per system message. |
| Mutation isolation | **PASS** | The production diff does not touch `_messages_from_mutation_request`. Independent serialization remained deterministic, retained the exact mutation envelope sentinels, and did not acquire the final-output schema sentence. |
| Existing tests not weakened | **PASS** | `test_agent_request_messages_include_protocol_tools_and_history` and `test_agent_request_never_leaks_answer_fields` have no removed or altered assertions in `HEAD^..HEAD`; the test diff only inserts the new test between existing tests. Their determinism, protocol/history, task-input, and leakage assertions remain intact. Source: `tests/test_live_client.py:157-193,277-287`. |
| B37 regression test | **PASS** | `test_system_message_declares_final_output_schema` contains the frozen literal locally, compares exact extracted sentences across two requests, checks all top-level and nested names, and checks task sentinels are absent from the clause. Source: `tests/test_live_client.py:196-240`. |
| Nothing else moved | **PASS** | Pre-report working tree was clean. `HEAD^..HEAD` contains exactly `src/skill_lab/config.py` and `tests/test_live_client.py` (`55 insertions, 1 deletion`), the two B37 allowlist files. Production movement is limited to the module constant and appending it after `max_calls`; test movement is limited to the new 47-line regression test. `git diff --check` passed. |
| Full suite | **PASS** | `pytest -q`: **129 passed**, with only two Typer/Click deprecation warnings. Independent per-file collection sum: **129**. |
| Ruff | **PASS** | `ruff check .`: `All checks passed!`; `ruff format --check .`: `45 files already formatted`. |
| Committed mock rerun | **PASS** | `skill-lab rerun --experiment artifacts/example-mock` reported `rerun: byte-identical artifacts/example-mock`. |
| Fresh mock evolution rerun | **PASS** | A fresh one-generation, one-run-per-task verified evolution using `mock-incident-v1`, with config/database/output under `/tmp`, completed; its immediate rerun reported byte-identical. |
| Data validation | **PASS** | `skill-lab validate-data --config configs/default.json` returned `valid`. |

## Raw probe output — items 1–2

Probe: `/tmp/integrity_review7_probe.py` (offline only).

```text
ITEM 1 — SCHEMA / RENDER
constant_exact_frozen_literal=True
sentence=The final output object must contain exactly these fields: ticket_id (string), classification (string), severity (string), escalation_path (string), final_status (string), required_updates (an object with exactly the keys "owner" and "priority", both strings), and evidence (an array of the evidence-label strings collected from the runbook). No additional fields are allowed.
sentence_top_level_fields=['classification', 'escalation_path', 'evidence', 'final_status', 'required_updates', 'severity', 'ticket_id']
verifier_expected_keys=['classification', 'escalation_path', 'evidence', 'final_status', 'required_updates', 'severity', 'ticket_id']
field_set_missing=[]
field_set_extra=[]
verifier_scalar_keys=['ticket_id', 'classification', 'severity', 'escalation_path', 'final_status']
sentence_required_updates_keys=["owner", "priority"]
verifier_required_updates_keys=["owner", "priority"]
no_skill_sentence_verbatim=True
skill_sentence_verbatim=True
skill_follows_sentence=True
valid_shape_accepted=True
invalid_scalar_not_string_rejected=True
invalid_evidence_not_list_rejected=True
invalid_evidence_item_not_string_rejected=True
invalid_updates_extra_key_rejected=True
invalid_updates_value_not_string_rejected=True
ITEM 2 — LEAKAGE / DETERMINISM
schema_constant_across_tasks=True
schema_contains_task_sentinel=False
forbidden_envelope_terms_absent_a=True
forbidden_envelope_terms_absent_b=True
forbidden_envelope_values_absent_both=True
task_id_absent_a=True
task_id_absent_b=True
task_input_present_as_required_a=True
task_input_present_as_required_b=True
max_calls_present_as_required_a=True
max_calls_present_as_required_b=True
identical_request_byte_identical=True
sentence_occurrences_no_skill=1
sentence_occurrences_skill=1
mutation_deterministic=True
mutation_has_schema_sentence=False
mutation_envelope_preserved=True
```

The whole serialized message must contain the task input and call budget because A-1 explicitly
requires both. The relevant static-text assertion is that no task value occurs inside the frozen
schema sentence; the global leakage assertion applies to the answer envelope and its values.
Both boundaries pass above.

## No-regression command output

```text
$ uv run pytest -q
........................................................................ [ 55%]
.........................................................                [100%]
129 passed; 2 Typer/Click deprecation warnings

$ uv run pytest --collect-only -q | per-file count sum
129

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
45 files already formatted

$ uv run skill-lab validate-data --config configs/default.json
valid

$ uv run skill-lab rerun --experiment artifacts/example-mock
rerun: byte-identical artifacts/example-mock

$ uv run skill-lab evolve --skill incident-response --generations 1 --runs-per-task 1 --mode verified --config /tmp/integrity-review7-config.json --output /tmp/integrity-review7-run-a
artifact_path: /tmp/integrity-review7-run-a
model_id: mock-incident-v1

$ uv run skill-lab rerun --experiment /tmp/integrity-review7-run-a
rerun: byte-identical /tmp/integrity-review7-run-a
```

## New findings

None.

## Explicitly unverified

- Real-provider acceptance of the schema sentence remains the responsibility of operator live
  smoke 4.
- No paid run, provider call, or external service was contacted.
- Provider-specific interpretation, token accounting, latency, and response quality were outside
  this scoped offline review.
