# Skill Lifecycle Lab

Research harness for studying whether agent skills can be automatically improved through
generate → evaluate → select → promote cycles.

Thesis under test: *agent skills should be treated as versioned, evaluated procedural artifacts;
automated skill evolution should occur through verified selection, not unconstrained
self-modification.*

- `SPEC.md` — operator brief (the research contract, verbatim).
- `PLAN.md` — execution plan and build order (added by the planning pass).
- `artifacts/experiment-<timestamp>/` — experiment outputs (`config.json`, `results.csv`,
  `generations.json`, `report.md`).

Status: scaffold. Setup, methodology, and interpretation sections are filled in by the
implementation (see SPEC §16–§19).

## Quickstart

```bash
uv sync --dev          # host-side dependency install
uv run pytest -q       # test suite (hermetic; mock model, no network)
```

Full reproduction commands land with the implementation.
