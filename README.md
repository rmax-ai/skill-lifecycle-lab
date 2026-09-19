# Skill Lifecycle Lab

A small, rigorous research harness for studying whether agent skills — procedural markdown
given to an agent before a task — can be automatically improved through
**generate → evaluate → select → promote** cycles.

Thesis under test: *agent skills should be treated as versioned, evaluated procedural
artifacts; automated skill evolution should occur through verified selection, not
unconstrained self-modification.*

The system is built so the thesis **can fail**: negative results, rejected mutations, and
regressions are first-class outputs of every experiment. A run in which the evolved skill is
worse than the human seed is a valid, reportable result.

---

## What the harness does

1. **Task environment.** 24 deterministic support/operations tasks (incident handling) with a
   fixed split: 12 train / 6 validation / 6 test. Each task declares its available tools,
   expected outcome, invariants, and a tool-call budget. The test split is structurally
   unreachable from the mutation and promotion paths — it is read only by the final held-out
   evaluation.
2. **Simulated tools.** Eight fixture-backed tools (`get_ticket`, `request_approval`,
   `escalate_ticket`, …) execute against a static world and record every call with its result.
   Traps make plausible-looking actions violate task invariants, so trajectory quality is
   measurable independently of the final answer.
3. **Deterministic verification.** Each trajectory is scored programmatically — final state,
   required/forbidden actions, ordering constraints, schema validity, tool errors — and the
   structured evidence is retained. No LLM judge is used anywhere in scoring.
4. **Versioned skills.** A skill is a `SKILL.md` plus metadata; every version records its
   parent, generation, and status (`baseline`, `promoted`, `rejected`, `naive_replaced`).
   Rejected candidates are preserved, including their rationale, so the lineage is auditable.
5. **Mutation (train-only).** The mutation model receives the current skill and the selected
   **training** failures (packets with inputs, trajectories, verifier feedback) and returns a
   candidate skill plus failure analysis, procedural change, and rationale. Validation and
   test data never enter this path.
6. **Promotion gate.** A candidate is promoted only when the harness — never the proposing
   model — decides it, using held-out **validation** evidence:

   ```
   PROMOTE iff
     candidate_success  >  parent_success
     AND regression_rate      <= regression_threshold
     AND prohibited_actions   <= parent_prohibited_actions
     AND estimated_cost       <= parent_cost * cost_tolerance
   ```

   Thresholds are configurable. Missing evidence, zero denominators, error outcomes, and
   non-finite metrics all resolve to **inconclusive**, which rejects.
7. **Two evolution modes.** `verified` gates each candidate on validation before selection;
   `naive` replaces the parent immediately. The naive-vs-verified ablation is the experiment's
   headline comparison: does evaluation-gated selection beat unconstrained self-replacement?
8. **Held-out evaluation.** After evolution finishes, the untouched test split evaluates three
   conditions — no skill, the human seed skill, and the final evolved skill — and reports
   whether improvements generalized beyond the failures that produced them.

## Quickstart

```bash
uv sync --dev                                   # install (Python 3.12+, uv-managed)
uv run pytest                                   # hermetic suite: mock model, no network
uv run skill-lab validate-data --config configs/default.json
```

Every command is deterministic and offline by default (a scripted mock model stands in for
the LLM). The live OpenAI-compatible adapter is gated behind `--allow-live` plus a model API
key in the environment.

## Reproducing an experiment

```bash
# Baseline evidence bundles (run before evolution; also produced per-condition)
uv run skill-lab baseline --condition no-skill --runs-per-task 3 \
    --config configs/default.json --output artifacts/baseline-no-skill
uv run skill-lab baseline --condition seed     --runs-per-task 3 \
    --config configs/default.json --output artifacts/baseline-seed

# Evolve a skill for N generations in one mode
uv run skill-lab evolve --skill incident-response --generations 4 --runs-per-task 3 \
    --mode verified --config configs/default.json --output artifacts/run-1

# Run both branches (verified + naive) over the same corpus and write both bundles
uv run skill-lab ablate --skill incident-response --generations 4 --runs-per-task 3 \
    --config configs/default.json --output artifacts/ablation-1

# Evaluate no-skill / seed / evolved on the untouched test split (additive subtree)
uv run skill-lab held-out --experiment artifacts/run-1 --runs-per-task 3

# Read the generated report, or verify an artifact byte-for-byte
uv run skill-lab report --experiment artifacts/run-1
uv run skill-lab rerun  --experiment artifacts/run-1     # regenerates and byte-compares
```

`generate-example` writes the committed offline harness example (`artifacts/example-mock`).

### Experiment artifacts

Every bundle is self-contained and auditable:

```
artifacts/<run>/
  config.json        full configuration + frozen input hashes + branch metadata
  manifest.json      sha256 + size for every file in the bundle
  inputs/            frozen copies of the task dataset and fixture world
  results.csv        one row per executed run (every split, every condition)
  trajectories.jsonl raw trajectory + verifier evidence per run
  prompts.jsonl      exact mutation request/response exchanges (audit trail)
  generations.json   per-generation records incl. rejected and errored generations
  skills/            every skill version incl. rejected candidates and rationales
  report.md          human-readable report: observed results vs interpretation
  held-out/          (after the held-out command) test-split evaluation subtree
```

Reproducibility properties: `rerun` regenerates a bundle from its own frozen inputs and
fails unless the result is **byte-identical** (guaranteed for the fixed-ID mock example);
the held-out command re-run on unchanged inputs is byte-identical; any input or manifest
hash mismatch aborts **before** a model is created, so a live (paid) rerun never spends
against tampered evidence. Latency is recorded as observed metadata, never as a comparison
input; timestamps appear only in experiment identifiers.

## Configuration

`configs/default.json` carries the frozen defaults (mock model; agent step/token budgets;
promotion thresholds; pricing table). Environment overrides use the `SKILL_LAB_*` variables
listed in `.env.example`. The live adapter is selected with `provider: "openai_compatible"`
plus `base_url`, `model`, and `api_key_env`; a missing pricing entry for a live model is a
configuration error, not a silent zero cost (only the mock is free).

## The committed example

`artifacts/example-mock` is an **offline harness example**: it runs the full pipeline with
the scripted mock model and exists to prove the mechanics — the promoted candidate fixes the
seed skill's seeded approval failure, a later candidate is **rejected** for introducing a
forbidden escalation, and the rejected version remains in the lineage with its rationale.
It is explicitly **not** evidence of real-model skill evolution; its manifest records the
mock model id for exactly that reason. Real-model headline experiments are operator-gated
(`--allow-live` + a configured key + pricing).

## Research discipline (how to read results)

- Observed results and interpretation are separated in every report; conclusions are scoped
  to "within this fixture world" unless a live model ran.
- Rejected mutations and negative generations are preserved and reported, never retried or
  hidden.
- The mutation path is train-only; the test split is evaluated exactly once per experiment,
  after evolution, by the held-out gate.
- Verification is deterministic given a trajectory; no LLM-as-judge, no nondeterministic
  scoring.
- External validity is limited by design: a fixture world and a fixed task corpus can
  demonstrate the machinery and within-corpus generalization, not broad real-world transfer.

## Repo map

| Path | Contents |
|---|---|
| `SPEC.md` | the research brief / contract (frozen) |
| `PLAN.md` | build order, contracts, and the remediation amendment (§10) |
| `src/skill_lab/` | harness implementation (agent, tools, verifier, mutation, promotion, evolution, reporting, CLI) |
| `skills/incident-response/v001/` | human seed skill (deliberately imperfect) |
| `datasets/` | task corpus and fixture world |
| `docs/reviews/` | adversarial integrity reviews of this harness, with findings and remediation history |
| `tests/` | hermetic test suite (mock model, no network) |
| `artifacts/example-mock/` | committed offline harness example |

## Development

```bash
uv run pytest                     # full suite
uv run ruff check . && uv run ruff format --check .
uv run skill-lab --help           # all commands
```

One batch changes one concern; contracts are frozen in `PLAN.md` and the lineage of
adversarial review lives in `docs/reviews/`. The harness treats its own correctness the way
it treats skill evaluation: evidence, gates, and preserved negative results.
