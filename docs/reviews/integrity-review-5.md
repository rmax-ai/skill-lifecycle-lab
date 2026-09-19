# Integrity Review 5 — §10.3 Content-JSON Multi-Turn Protocol

**Verdict: FIX-FIRST**

The scoped A-10a through A-10d protocol checks pass, as do all offline regression gates. The
operator should nevertheless **not proceed to live smoke 3**: the new retry loop under-reports
latency after a successful retry, which compromises a first-class experimental measurement. See
finding 1.

No provider was contacted. All custom probes and fresh artifacts were created under `/tmp`; this
report is the only workspace file written by the review.

## Evidence table

| Item | Result | Direct evidence |
|---|---|---|
| A-10a replay shapes | **PASS** | A two-entry request serialized to roles `system,user,assistant,user,assistant,user`. Both assistant contents were byte-equal to canonical action JSON; both user contents were byte-equal to `json.dumps({"tool_result": ...}, sort_keys=True)`. Every message had exactly `role` and `content`; no message had role `tool` or fields `name`, `tool_call_id`, or `tool_calls`. Source: `src/skill_lab/config.py:393-428`. |
| A-10b invariant | **PASS** | Agent and mutation instructions contain `exactly one JSON object`, `nothing else`, and the no-Markdown/XML/function-call-tags/commentary clause. The agent instruction contains both exact action forms and says user-role tool results are authoritative environment output. The mutation instruction requires exactly the four named string fields and no others. Neither generated request body has a stop parameter. Source: `src/skill_lab/config.py:371-386`, `src/skill_lab/config.py:439-455`. |
| A-10c JSON mode | **PASS** | `configs/live-deepseek.json` parsed successfully. `extra_body` is exactly `thinking:{"type":"disabled"}` plus `response_format:{"type":"json_object"}`. Direct bodies for both `kind="agent"` and `kind="mutation"` contained both objects unchanged. Source: `configs/live-deepseek.json:13-20`, `src/skill_lab/config.py:295-304`. |
| A-10d client operations | **PASS, with measurement defect** | Parseable content with `finish_reason="length"` raised after one attempt. `503,503,200`, `429,429,200`, and two connection failures followed by success each used exactly three attempts and sleeps `[1,3]`. Three 503s, three connection failures, and three empty-stop responses raised after exactly three attempts. 401 and 422 raised on the first attempt with no sleep. Exhaustion passed through `run_agent` as `model_error`. Finding 1 concerns latency metadata after successful retries, not retry bounds or error classification. Source: `src/skill_lab/config.py:181-263`. |
| No-regression | **PASS** | Ruff lint and format checks passed. Full suite: **127 passed** with two pre-existing Typer/Click deprecation warnings. `validate-data` returned `valid`. The committed example rerun reported byte-identical and all 32 before/after SHA-256 entries matched. A fresh two-generation, one-run-per-task verified mock evolution completed under `/tmp` and reran byte-identically. |

The A-10b check treats the action forms as the agent reply schema and the exact four-field object as
the mutation reply schema, matching PLAN.md §10.3 B35's protocol-specific requirements. Both
messages share the same no-extra-content invariant.

## Raw probe output

### A-10a replay

```text
A-10a PASS roles= ['system', 'user', 'assistant', 'user', 'assistant', 'user']
A-10a exact replay= ["{\"action\": \"tool\", \"arguments\": {\"ticket_id\": \"T01\"}, \"tool\": \"get_ticket\"}", "{\"tool_result\": {\"arguments\": {\"ticket_id\": \"T01\"}, \"result\": {\"a\": 1, \"z\": 2}, \"tool\": \"get_ticket\"}}", "{\"action\": \"tool\", \"arguments\": {\"customer_id\": \"C01\"}, \"tool\": \"get_customer\"}", "{\"tool_result\": {\"arguments\": {\"customer_id\": \"C01\"}, \"result\": {\"customer\": {\"id\": \"C01\", \"tier\": \"enterprise\"}}, \"tool\": \"get_customer\"}}"]
```

The probe separately asserted exact message key sets and absence of `role="tool"`, `name`,
`tool_call_id`, and `tool_calls`; it would have terminated before printing PASS on any mismatch.

### A-10b invariant

```text
A-10b PASS common invariant, agent forms, authority, mutation four fields, no stop parameters
```

The assertion set checked these literal fragments:

```text
exactly one JSON object
nothing else
Do not use Markdown, XML, function-call tags, or commentary
{"action":"tool","tool":<name>,"arguments":{...}}
{"action":"final","output":{...}}
Tool results arrive as user messages
authoritative environment output
exactly four string fields and no others
```

It also asserted that both request bodies omitted `stop` and `stop_sequences`.

### A-10c JSON mode

```text
A-10c agent PASS extra= {"response_format": {"type": "json_object"}, "thinking": {"type": "disabled"}}
A-10c mutation PASS extra= {"response_format": {"type": "json_object"}, "thinking": {"type": "disabled"}}
```

### A-10d client operations

```text
A-10d length PASS attempts=1 sleeps=[] result=ValueError
A-10d 503,503,200 PASS attempts=3 sleeps=[1, 3] result=success
A-10d 503x3 PASS attempts=3 sleeps=[1, 3] result=HTTPStatusError
A-10d 429x2,200 PASS attempts=3 sleeps=[1, 3] result=success
A-10d connectionx2,200 PASS attempts=3 sleeps=[1, 3] result=success
A-10d connectionx3 PASS attempts=3 sleeps=[1, 3] result=ConnectError
A-10d empty-stopx3 PASS attempts=3 sleeps=[1, 3] result=ValueError
A-10d 401 PASS attempts=1 sleeps=[] result=HTTPStatusError
A-10d 422 PASS attempts=1 sleeps=[] result=HTTPStatusError
A-10d exhaustion harness PASS outcome=model_error attempts=3
```

### No-regression

```text
$ UV_CACHE_DIR=/tmp/integrity-review5-uv-cache uv run ruff check .
All checks passed!

$ UV_CACHE_DIR=/tmp/integrity-review5-uv-cache uv run ruff format --check .
45 files already formatted

$ UV_CACHE_DIR=/tmp/integrity-review5-uv-cache uv run pytest -q
........................................................................ [ 56%]
.......................................................                  [100%]
```

`pytest --collect-only -q` listed 127 tests. The only warnings were the two existing Typer imports
of deprecated Click stream helpers.

```text
$ UV_CACHE_DIR=/tmp/integrity-review5-uv-cache uv run skill-lab validate-data --config configs/default.json
valid

$ UV_CACHE_DIR=/tmp/integrity-review5-uv-cache uv run skill-lab rerun --experiment artifacts/example-mock
rerun: byte-identical artifacts/example-mock

before/after hash_manifest_identical=true
files=32

$ UV_CACHE_DIR=/tmp/integrity-review5-uv-cache uv run skill-lab evolve --skill incident-response --generations 2 --runs-per-task 1 --mode verified --config configs/default.json --output /tmp/integrity-review5-fresh.VdOfLt/evolution
artifact_path: /tmp/integrity-review5-fresh.VdOfLt/evolution
model_id: mock-incident-v1

$ UV_CACHE_DIR=/tmp/integrity-review5-uv-cache uv run skill-lab rerun --experiment /tmp/integrity-review5-fresh.VdOfLt/evolution
rerun: byte-identical /tmp/integrity-review5-fresh.VdOfLt/evolution
```

## New findings

### 1. MAJOR — Successful transport retries discard earlier attempt latency

**Location:** `src/skill_lab/config.py:188-193`, returned at `src/skill_lab/config.py:250-257`

`started_at` is reset inside the retry loop, and `latency_ms` is overwritten on every response.
When an eventual response succeeds, the returned `ModelResponse` therefore records only the final
HTTP attempt. It excludes all earlier failed-request time and both fixed backoffs. This makes live
latency observations systematically low precisely when the new retry path is exercised, weakening
the experiment's latency/cost-of-improvement measurement.

Direct reproduction used response statuses `503,503,200`, monkeypatched monotonic readings giving
attempt durations of 2 s, 3 s, and 4 s, and no-op sleeps so the expected values were explicit:

```text
FINDING-1 retry latency observed= 4000 request_elapsed_sum_ms=9000 wall_span_ms=16000
```

The client reported 4,000 ms rather than the 9,000 ms spent inside HTTP attempts (or 16,000 ms for
end-to-end completion including the simulated intervals/backoffs). The live-client contract should
define whether latency includes backoff, but it must at minimum retain all HTTP-attempt time rather
than only the final attempt. This is a touched-surface defect introduced by bounded retries.

## Explicitly unverified

- Real-provider acceptance of the user-role `tool_result` replay shape, resistance to native-syntax
  drift, and DeepSeek's current JSON-mode behavior remain live-smoke responsibilities.
- Provider latency, billing, rate limits, response quality, and actual 429/5xx behavior were not
  exercised; only hermetic transports were used.
- The review did not test every malformed provider response shape outside the scoped length,
  empty-stop, status, and transport-error cases.
- No paid headline run or external service was contacted.
