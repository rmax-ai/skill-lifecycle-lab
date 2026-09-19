# Integrity Review 4 — `extra_body` Live Plumbing

**Verdict: PASS**

Card B34 satisfies the scoped contract in PLAN.md §10.2. The request extension is merged into
both agent and mutation bodies, fixed request fields cannot be replaced, absent/null behavior is
unchanged, the two configuration surfaces and regenerated example are internally consistent, and
the offline regression gates pass. No new defect was found in the reviewed surface.

The operator may proceed with the live smoke. This review does not authorize or verify the paid
headline run itself.

## Evidence

| # | Verification | Result | Observed evidence |
|---:|---|---|---|
| 1 | Merge semantics | **PASS** | Independent `/tmp` probe called `OpenAICompatibleClient._request_body` for explicit `kind="agent"` and `kind="mutation"` requests using `extra_body={"thinking":{"type":"disabled"}}`. Both bodies retained `model`, generated `messages`, `temperature`, and `max_tokens`, and contained the top-level `thinking` object unchanged. Implementation: `src/skill_lab/config.py:239-267`. Exact bodies are printed below. |
| 2 | Contract-field refusal and absent/null compatibility | **PASS** | Separate probes for `model`, `messages`, `temperature`, and `max_tokens` each raised `ValueError` naming the collision. A list was rejected during normal `ModelConfig` validation with `ValidationError` (a `ValueError` subclass); a deliberately bypassed model carrying a list was also rejected by `_request_body` with `ValueError: extra_body must be a JSON object or null`. Configurations with the field missing and explicitly null produced byte-identical compact JSON bodies, also byte-identical to a directly constructed pre-B34 four-field body. |
| 3 | Config surfaces | **PASS** | `configs/default.json` parsed with `extra_body is None`; `configs/live-deepseek.json` parsed with exactly `{"thinking":{"type":"disabled"}}`. Parsed `configs/default.json == DEFAULT_CONFIG`, and `load_config(...).model_dump(mode="json") == DEFAULT_CONFIG` were both true. A model-level unknown key still raised Pydantic `extra_forbidden`; JSON round-trip equality passed. Sources: `src/skill_lab/config.py:55-69`, `configs/default.json:10-18`, `configs/live-deepseek.json:10-22`. |
| 4 | Regenerated example integrity | **PASS** | `uv run --offline skill-lab rerun --experiment artifacts/example-mock` reported `rerun: byte-identical`; a before/after SHA-256 list comparison also matched. The root manifest declared exactly all 31 non-manifest files, and every declared size/hash matched. `configuration.model.extra_body` in root `config.json` is null. Verified branch: 66 result rows, 66 trajectories, 66 unique row keys, two prompts for generations `[1,2]`, three held-out report rows. Naive branch: 42/42/42, two prompts for `[1,2]`, three held-out report rows. Across both branches there were zero rows where `total_tokens != input_tokens + output_tokens`. |
| 5 | No regressions | **PASS** | `ruff check .`: clean. `ruff format --check .`: 45 files already formatted. Full `pytest -q`: 122 tests collected and all passed; only two pre-existing Typer/Click deprecation warnings were emitted. `skill-lab validate-data`: `valid`. A fresh offline mock evolution (`verified`, two generations, one run/task) wrote `/tmp/integrity-review-4-fresh-evolve`, and rerun reported it byte-identical. |
| 6 | New defects | **PASS — none found** | Adversarial probes extended beyond the named B34 tests by exercising both request kinds, every protected key independently, both parse-time and runtime non-object rejection, missing-vs-null serialization, strict unknown-key behavior, complete manifest hashing, uniqueness/count invariants, and fresh-run determinism. |

## Observed request bodies

Agent body, printed by the offline probe:

```json
{"max_tokens":321,"messages":[{"content":"Return exactly one JSON object with no prose or code fences. The object must be either {\"action\":\"tool\",\"tool\":<name>,\"arguments\":{...}} or {\"action\":\"final\",\"output\":{...}}. available_tools and argument names: {}. max_calls: 1.\n\nSkill markdown:\n# Procedure\nInspect safely.","role":"system"},{"content":"Investigate incident.","role":"user"}],"model":"deepseek-chat","temperature":0.25,"thinking":{"type":"disabled"}}
```

Mutation body, printed by the offline probe:

```json
{"max_tokens":321,"messages":[{"content":"Respond with exactly one JSON object containing exactly four string fields: {\"failure_analysis\":<string>,\"procedural_change\":<string>,\"candidate_markdown\":<string>,\"rationale\":<string>}. candidate_markdown must contain the complete SKILL.md. Do not include task identifiers or validation/test evidence.","role":"system"},{"content":"{\"current_skill\": {\"description\": \"Procedure.\", \"markdown\": \"# Procedure\\nInspect safely.\", \"name\": \"incident-response\", \"version\": \"v001\"}, \"train_failures\": []}","role":"user"}],"model":"deepseek-chat","temperature":0.25,"thinking":{"type":"disabled"}}
```

## Commands and isolation

All probes were offline. Temporary probe scripts and fresh artifacts were placed under `/tmp`.
`UV_CACHE_DIR` was redirected to `/tmp/integrity-review-4-uv-cache`; `uv run --offline` was used
for CLI, test, and lint commands. No provider endpoint was called.

Key commands:

```text
uv run --offline skill-lab rerun --experiment artifacts/example-mock
uv run --offline ruff check .
uv run --offline ruff format --check .
uv run --offline pytest -q
uv run --offline skill-lab validate-data
uv run --offline skill-lab evolve --skill incident-response --generations 2 \
  --runs-per-task 1 --mode verified --config configs/default.json \
  --output /tmp/integrity-review-4-fresh-evolve
uv run --offline skill-lab rerun --experiment /tmp/integrity-review-4-fresh-evolve
```

After the committed-example rerun, its complete before/after hash lists were identical. Before
writing this report, `git status --short` was empty and `git diff --check` passed, confirming the
review commands caused no workspace change.

## New findings

None.

## Explicitly unverified

- No external provider call was made, so DeepSeek's current acceptance and interpretation of the
  `thinking` extension remains a live-smoke responsibility.
- Provider latency, billing, rate limits, response quality, and the paid headline run were not
  exercised.
- No pre-B34 artifact snapshot was supplied for a historical whole-tree comparison. Artifact
  semantics were checked against the documented 66/42 row, prompt, held-out, token-accounting,
  manifest, and deterministic-rerun invariants instead.
