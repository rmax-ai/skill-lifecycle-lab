# Experiment

## Configuration

| Field | Observed value |
|---|---|
| experiment_id | `exp-20000101T000000Z-deadbeef` |
| mode | `verified` |
| final skill | `incident-response v002` |
| generations requested and recorded | `2` |

## Baselines

| Condition | Split | Skill | Success | Runs | Tool Calls | Tokens | Violations |
|---|---|---|---:|---:|---:|---:|---:|
| no_skill | test | n/a | 0.666667 | 6 | 41 | 20768 | 2 |
| seed | test | v001 | 1.000000 | 6 | 35 | 36709 | 0 |

## Evolution History

| Generation | Parent | Candidate | Validation Lift | Regression | Decision |
|---:|---|---|---:|---:|---|
| 1 | v001 | v002 | 0.166667 | 0.000000 | promote |
| 2 | v002 | v003 | -0.166667 | 0.166667 | reject |

## Held-Out Results

| Condition | Split | Skill | Success | Runs | Tool Calls | Tokens | Violations |
|---|---|---|---:|---:|---:|---:|---:|
| evolved_verified | test | v002 | 1.000000 | 6 | 35 | 27284 | 0 |
| no_skill | test | n/a | 0.666667 | 6 | 41 | 20768 | 2 |
| seed | test | v001 | 1.000000 | 6 | 35 | 36709 | 0 |

## Skill Lineage

The tree retains promoted, rejected, and generation-error outcomes.

```text
v001
└── v002 promote
    v002
    └── v003 rejected (negative generation)
        v003
```

Final selected skill: `v002`.

## Failure Analysis

Negative generations and rejection evidence were retained:
- Generation `2`: candidate `v003`, decision `reject`, reasons `no_validation_lift, regression_threshold_exceeded, prohibited_actions_increased`, train failures `none recorded`.

## Observed Results

All statements in this section describe supplied records and are labeled observed.
| Condition | Split | Skill | Success | Runs | Tool Calls | Tokens | Violations |
|---|---|---|---:|---:|---:|---:|---:|
| evolved_verified | train | v001 | 1.000000 | 12 | 71 | 74618 | 0 |
| evolved_verified | train | v002 | 1.000000 | 12 | 71 | 55543 | 0 |

Observed generation records: `2`.
Observed prompt records: 2
Observed final skill: `v002`.

## Interpretation/Conclusions

- This artifact is descriptive; it does not establish causality between a mutation and any observed metric.
- A scripted mock result, when present, describes the offline harness and is not evidence of general model performance.
- Held-out results include 3 observed summary rows covering 18 observed runs.
