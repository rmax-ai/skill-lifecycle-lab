# Integrity Review 6 — Retry Latency Span

**Verdict: PASS**

Review 5 finding 1 is fixed. A successful retry now records the full span from the first
attempt's start through successful response decoding, including the time between attempts. The
exact `503,503,200` reproduction reports **16,000 ms**, explicitly not the final attempt's
**4,000 ms**. The same result holds for `429,429,200` and two connection failures followed by
success. No-retry and exhausted-retry behavior remains unchanged.

No new findings were identified. No provider was contacted. All custom probes and fresh
artifacts were created under `/tmp`; this report is the only workspace file written by the
review. **Operator live smoke 3 may proceed.**

## Evidence table

| Item | Result | Direct evidence |
|---|---|---|
| Exact finding-1 reproduction | **PASS** | Independent stub-transport probe used `503,503,200`, attempt durations `2/3/4 s`, explicit inter-attempt intervals `4/3 s`, and no-op sleeps recording `[1,3]`. Returned `latency_ms=16000`, with an explicit assertion that it was not `4000`. See raw output below. |
| Other retry shapes | **PASS** | Independent `429,429,200` and connection-failure, connection-failure, success probes each made three attempts, recorded sleeps `[1,3]`, and returned `latency_ms=16000`. Source retry classification remains `429` or `5xx`, plus `httpx.RequestError`, `ConnectionError`, and `TimeoutError`: `src/skill_lab/config.py:193-209,259-263,486-494`. |
| No-retry success | **PASS** | One 200 response with a 4 s attempt returned `latency_ms=4000`, made one attempt, and did not sleep. |
| Exhausted retries | **PASS** | Three 503 responses raised `HTTPStatusError` after exactly three attempts and sleeps `[1,3]`. Instrumentation around `ModelResponse` recorded zero constructions, so no response/latency was produced or persisted. |
| Timing implementation | **PASS** | `started_at = time.monotonic()` occurs once, before `while True`; `latency_ms` is assigned once, only on the successful decode path. An AST probe asserted one assignment of each and the ordering. Source: `src/skill_lab/config.py:184-187,239-257`. This is the sole production semantic movement identified against review 5's captured implementation: the start timestamp moved outside the loop. |
| Preserved response semantics | **PASS** | Latency remains a non-negative integer via `max(0, int(round(...)))` and the persisted model contract retains `Field(ge=0)`. `finish_reason` defaulting, `length` rejection, and returned value are unchanged. Source: `src/skill_lab/config.py:221-225,249-257`; `src/skill_lab/models.py:93`. |
| Preserved retry bounds/backoff | **PASS** | Retry helper still has exactly two backoffs and sleeps before attempts 2 and 3; probes observed three attempts and `[1,3]` seconds on both success and exhaustion. Source: `src/skill_lab/config.py:490-494`. |
| B36 regression test | **PASS** | `test_retry_latency_includes_all_attempts_and_backoff` covers the exact 16,000/4,000 distinction and the 4,000 ms no-retry case. Source: `tests/test_live_client.py:403-449`. The suite count increased from review 5's 127 to 128, exactly matching the one named B36 test. |
| Nothing else moved | **PASS** | Working tree was clean before this report. Direct `HEAD^..HEAD` diff contains exactly the two B36-allowlisted files: `src/skill_lab/config.py` and `tests/test_live_client.py` (`51 insertions, 2 deletions`). The production diff only relocates `started_at` before the loop and the latency calculation to the success path; the test diff only adds the named 49-line regression test. No finish-reason, retry classification, retry-limit, backoff, or other behavior moved. |
| Full suite | **PASS** | `uv run pytest -q`: **128 passed**, with only the same two Typer/Click deprecation warnings recorded by review 5. `pytest --collect-only -q` independently counted 128 tests. |
| Ruff | **PASS** | `ruff check .`: `All checks passed!`; `ruff format --check .`: `45 files already formatted`. |
| Committed mock rerun | **PASS** | `skill-lab rerun --experiment artifacts/example-mock` reported `rerun: byte-identical artifacts/example-mock`. |
| Fresh mock evolution rerun | **PASS** | A fresh two-generation, one-run-per-task verified evolution under `/tmp` completed with `mock-incident-v1`; its immediate `rerun` reported byte-identical. |
| Data validation | **PASS** | `skill-lab validate-data --config configs/default.json` returned `valid`. |

## Raw probe output — item 1

Probe: `/tmp/integrity_review_6_probe.py` (offline only).

```text
503,503,200: latency_ms=16000; not_final_attempt=4000; attempts=3; attempt_durations_s=[2, 3, 4]; inter_attempt_intervals_s=[4, 3]; sleeps=[1, 3]; PASS
429,429,200: latency_ms=16000; not_final_attempt=4000; attempts=3; attempt_durations_s=[2, 3, 4]; inter_attempt_intervals_s=[4, 3]; sleeps=[1, 3]; PASS
connection,connection,200: latency_ms=16000; not_final_attempt=4000; attempts=3; attempt_durations_s=[2, 3, 4]; inter_attempt_intervals_s=[4, 3]; sleeps=[1, 3]; PASS
200: latency_ms=4000; attempts=1; attempt_durations_s=[4]; sleeps=[]; PASS
503x3 exhausted: raised=HTTPStatusError(503); attempts=3; sleeps=[1, 3]; ModelResponse_constructed=0; persisted_latency=none; PASS
implementation AST: started_at_assignments=1; before_attempt_loop=yes; latency_ms_assignments=1; PASS
```

The probe's sleep replacement only recorded the requested schedule; the monkeypatched monotonic
clock explicitly supplied the complete 16 s span. That construction preserves review 5's
2+3+4 = 9 s attempt time and 4+3 = 7 s inter-attempt time while independently confirming that
the client returns the full span rather than the final-attempt duration.

## No-regression command output

```text
$ UV_CACHE_DIR=/tmp/review6-uv-pytest uv run pytest -q
........................................................................ [ 56%]
........................................................                 [100%]

$ UV_CACHE_DIR=/tmp/review6-uv-collect uv run pytest --collect-only -q
tests/test_live_client.py: 17
...
128 tests collected by per-file sum

$ UV_CACHE_DIR=/tmp/review6-uv-ruff uv run ruff check .
All checks passed!

$ UV_CACHE_DIR=/tmp/review6-uv-ruff-format uv run ruff format --check .
45 files already formatted

$ UV_CACHE_DIR=/tmp/review6-uv-validate uv run skill-lab validate-data --config configs/default.json
valid

$ UV_CACHE_DIR=/tmp/review6-uv-rerun uv run skill-lab rerun --experiment artifacts/example-mock
rerun: byte-identical artifacts/example-mock

$ uv run skill-lab evolve --skill incident-response --generations 2 --runs-per-task 1 --mode verified --config configs/default.json --output /tmp/integrity-review-6-evolution.sbFOxQ/fresh-evolution
artifact_path: /tmp/integrity-review-6-evolution.sbFOxQ/fresh-evolution
model_id: mock-incident-v1

$ uv run skill-lab rerun --experiment /tmp/integrity-review-6-evolution.sbFOxQ/fresh-evolution
rerun: byte-identical /tmp/integrity-review-6-evolution.sbFOxQ/fresh-evolution
```

## New findings

None.

## Explicitly unverified

- Real-provider retry timing, latency, rate-limit behavior, billing, response quality, and
  protocol acceptance remain operator live-smoke responsibilities.
- No paid headline run or external service was contacted.
- Malformed provider response shapes outside the retry/latency surface were not re-audited in
  this scoped review.
