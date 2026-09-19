# Experiment

## Configuration

| Field | Observed value |
|---|---|
| experiment_id | `exp-20000101T000000Z-deadbeef` |
| mode | `naive` |
| final skill | `incident-response v003` |
| generations requested and recorded | `2` |

## Baselines

| Condition | Split | Skill | Success | Runs | Tool Calls | Tokens | Violations |
|---|---|---|---:|---:|---:|---:|---:|
| no_skill | test | n/a | 0.666667 | 6 | 41 | 20768 | 2 |
| seed | test | v001 | 1.000000 | 6 | 35 | 36709 | 0 |

## Evolution History

| Generation | Parent | Candidate | Validation Lift | Regression | Decision |
|---:|---|---|---:|---:|---|
| 1 | v001 | v002 | n/a | n/a | naive_replace |
| 2 | v002 | v003 | n/a | n/a | naive_replace |

## Held-Out Results

| Condition | Split | Skill | Success | Runs | Tool Calls | Tokens | Violations |
|---|---|---|---:|---:|---:|---:|---:|
| evolved_naive | test | v003 | 1.000000 | 6 | 35 | 27448 | 0 |
| no_skill | test | n/a | 0.666667 | 6 | 41 | 20768 | 2 |
| seed | test | v001 | 1.000000 | 6 | 35 | 36709 | 0 |

## Skill Lineage

The tree retains promoted, rejected, and generation-error outcomes.

```text
v002
└── v003 naive_replace
    v003
v001 (additional lineage root for generation 1)
v001
└── v002 naive_replace
    v002
```

Final selected skill: `v003`.

## Failure Analysis

No rejected or generation-error generations were recorded.

## Observed Results

All statements in this section describe supplied records and are labeled observed.
| Condition | Split | Skill | Success | Runs | Tool Calls | Tokens | Violations |
|---|---|---|---:|---:|---:|---:|---:|
| evolved_naive | train | v001 | 1.000000 | 12 | 71 | 74618 | 0 |
| evolved_naive | train | v002 | 1.000000 | 12 | 71 | 55543 | 0 |

Observed generation records: `2`.
Observed prompt records: 2
Observed final skill: `v003`.

## Interpretation/Conclusions

- This artifact is descriptive; it does not establish causality between a mutation and any observed metric.
- A scripted mock result, when present, describes the offline harness and is not evidence of general model performance.
- Held-out results include 3 observed summary rows covering 18 observed runs.
