> **Provenance.** This is the operator brief, reproduced verbatim (received 2026-09-19). It is
> the research contract for this repository; do not edit. `PLAN.md` is the execution plan derived
> from it; `AGENTS.md` carries implementation conventions. Section references like "brief §9"
> elsewhere in the repo resolve to this file.

---

Build: Skill Lifecycle Lab

You are a senior AI systems researcher and Python engineer.

Build a small but rigorous experimental system for studying whether agent skills can be automatically improved through generate → evaluate → select → promote cycles.

The project is a companion PoC for the research thesis:

Agent skills should be treated as versioned, evaluated procedural artifacts. Automated skill evolution should occur through verified selection, not unconstrained self-modification.

The goal is not to build a production skill registry. Build the smallest credible research harness capable of testing this hypothesis.

## 1. Research Question

Primary question:

Can automated skill evolution produce cumulative improvements in agent task performance when candidate mutations must outperform their parent skill on held-out tasks before promotion?

Secondary questions:

• How much improvement comes from the skill versus model stochasticity?
• Do improvements generalize beyond the tasks used to generate the mutation?
• How frequently do apparently better skills regress on previously solved tasks?
• What kinds of mutations produce improvements?
• Does repeated evolution eventually plateau or degrade?
• What are the token, latency, and tool-call costs of improvement?

## 2. Core Experimental Loop

Implement:

```
                 ┌──────────────┐
                 │ Current Skill│
                 │     vN       │
                 └──────┬───────┘
                        │
                        ▼
                 Run training tasks
                        │
                        ▼
                 Collect trajectories
                        │
                        ▼
                  Failure analysis
                        │
                        ▼
                 Generate mutation
                        │
                        ▼
                  Candidate vN+1
                        │
              ┌─────────┴─────────┐
              ▼                   ▼
        Held-out eval       Regression eval
              │                   │
              └─────────┬─────────┘
                        ▼
                    Compare
                        │
                ┌───────┴───────┐
                ▼               ▼
             Promote          Reject
                │
                ▼
             Skill vN+1
```

Never allow the agent that proposes a mutation to directly promote it.

Promotion must be determined by the evaluation harness.

## 3. Scope

Keep the PoC intentionally small.

Use:

• Python 3.12+
• uv
• Pydantic for typed schemas
• SQLite for experiments/results
• Markdown SKILL.md files
• a configurable OpenAI-compatible LLM API
• pytest
• CLI-first interface

Avoid:

• web UI
• Kubernetes
• vector databases
• distributed workers
• complex agent frameworks
• production authentication
• MCP unless clearly necessary

Prefer explicit Python over framework abstractions.

The entire experiment should be understandable by one engineer reading the repository.

## 4. Task Environment

Create a reproducible synthetic enterprise task environment.

Use tasks that require procedural reasoning rather than factual recall.

Example domain: support/operations ticket handling.

Each task contains:

```python
class Task(BaseModel):
    id: str
    input: str
    available_tools: list[str]
    expected_outcome: dict
    invariants: list[str]
    split: Literal["train", "validation", "test"]
```

Example tasks:

• classify an incident
• identify severity
• gather required evidence
• choose the correct escalation path
• execute a sequence of simulated API calls
• avoid prohibited operations
• produce the required structured output

Implement approximately 20–30 deterministic tasks.

Divide them into:

training      ~50%
validation    ~25%
held-out test ~25%

The test set must never be exposed to the skill mutation process.

## 5. Simulated Tools

Implement a small deterministic tool environment.

For example:

search_tickets()
get_ticket()
get_customer()
get_service_status()
get_runbook()
escalate_ticket()
request_approval()
update_ticket()

Tools should operate against static fixtures.

Record every call:

```python
ToolCall(
    tool,
    arguments,
    result,
    timestamp
)
```

Include traps where plausible-looking actions violate task invariants.

This makes trajectory quality measurable independently of final-answer quality.

## 6. Skill Format

Each skill lives in:

```
skills/
  incident-response/
    v001/
      SKILL.md
      metadata.json
```

SKILL.md should contain:

```markdown
---
name: incident-response
version: 1
description: ...
---

# Procedure

...

# Decision Rules

...

# Failure Recovery

...

# Verification

...
```

Metadata should additionally record:

```json
{
  "parent": null,
  "created_by": "human",
  "generation": 0,
  "status": "baseline"
}
```

Later versions must preserve lineage.

## 7. Baselines

Implement at least three experimental conditions.

A — No skill
Agent receives only task + tool schemas.

B — Human seed skill
Agent receives a deliberately useful but imperfect hand-authored skill.

C — Evolved skill
Agent receives the currently promoted skill.

Everything else must remain fixed:

• model
• system prompt
• temperature
• task set
• tool implementation
• maximum steps
• token budget

Where nondeterminism exists, run multiple repetitions.

## 8. Trajectory Capture

Persist the complete execution trajectory:

```python
class Trajectory(BaseModel):
    experiment_id: str
    task_id: str
    skill_version: str | None
    messages: list
    tool_calls: list
    final_output: dict | str
    tokens: int
    latency_ms: int
    outcome: str
```

Store enough evidence to reconstruct why a run succeeded or failed.

Do not rely only on final answers.

## 9. Deterministic Verification

Prefer programmatic verification over LLM-as-judge.

Each task should expose:

```python
verify(task, trajectory) -> VerificationResult
```

Check:

• correct final state
• required actions performed
• forbidden actions absent
• correct ordering where relevant
• schema validity
• task invariants
• tool errors

Return structured evidence.

Example:

```python
VerificationResult(
    success=True,
    checks={
        "correct_severity": True,
        "evidence_collected": True,
        "approval_before_escalation": True,
        "no_forbidden_calls": True,
    }
)
```

## 10. Metrics

Calculate at minimum:

Outcome metrics
• task success rate
• regression rate
• previously-unsolved tasks solved
• previously-solved tasks broken

Trajectory metrics
• tool calls
• invalid tool calls
• prohibited actions
• unnecessary actions
• steps to completion

Economics
• input tokens
• output tokens
• total tokens
• latency
• estimated cost

Define:

Skill Lift =
success(candidate) - success(parent)

Also calculate improvement relative to the no-skill baseline.

## 11. Mutation Engine

Implement:

```python
propose_mutation(
    current_skill,
    failed_trajectories,
    verifier_feedback
) -> CandidateSkill
```

Give the mutation model:

• current skill
• selected training failures
• execution trajectories
• verifier results

Ask it to produce:

1. failure analysis
2. proposed procedural change
3. complete candidate SKILL.md
4. rationale for the mutation

Do NOT expose validation or test results.

Store mutation rationale separately from the skill itself.

## 12. Promotion Gate

A candidate cannot replace its parent merely because it improves training performance.

Evaluate it on validation tasks.

Implement an explicit promotion policy.

Initial policy:

PROMOTE iff:

candidate_success > parent_success
AND regression_rate <= configured_threshold
AND prohibited_actions <= parent
AND token_cost <= parent * configured_cost_tolerance

Make thresholds configurable.

If evidence is inconclusive, reject the candidate.

Record the complete promotion decision.

## 13. Evolution Experiment

Implement:

```bash
skill-lab evolve \
  --skill incident-response \
  --generations 10 \
  --runs-per-task 3
```

Each generation:

```
evaluate current skill
        ↓
select training failures
        ↓
generate candidate
        ↓
evaluate candidate
        ↓
compare parent/candidate
        ↓
promote or reject
        ↓
record experiment
```

Prevent infinite mutation loops.

## 14. Critical Experimental Requirement: Held-Out Evaluation

After evolution finishes, evaluate:

No Skill
Human Seed Skill
Final Evolved Skill

against the untouched test set.

This is the key result.

Do not use test performance for mutation or promotion.

Report whether improvements generalized.

## 15. Ablations

Implement at least these experiments:

Experiment 1 — No skill vs seed skill
Does procedural context improve performance?

Experiment 2 — Seed vs evolved skill
Does verified evolution improve the human seed?

Experiment 3 — Verified vs naive evolution
Naive condition:
generate mutation → automatically replace parent
Verified condition:
generate mutation → evaluate → promote/reject
Compare final held-out performance.

This is the most important ablation.

Experiment 4 — Training vs held-out lift
Measure whether skill improvements overfit their observed failures.

## 16. Reporting

Generate:

```
artifacts/
  experiment-<timestamp>/
    config.json
    results.csv
    generations.json
    report.md
```

report.md should include:

```markdown
# Experiment

## Configuration

## Baselines

## Evolution History

Generation | Parent | Candidate | Validation Lift | Regression | Decision

## Held-Out Results

Condition | Success | Tool Calls | Tokens | Violations

## Skill Lineage

v001
 └── v002 rejected
 └── v003 promoted
      └── v004 promoted
           ...

## Failure Analysis

## Conclusions
```

Clearly distinguish observed results from interpretation.

## 17. Reproducibility

Every experiment records:

• model identifier
• model parameters
• prompts
• git commit
• random seed
• skill versions
• task dataset version
• timestamps
• token budgets

A researcher should be able to rerun an experiment from its artifact directory.

## 18. Repository Structure

Prefer:

```
skill-lifecycle-lab/
├── README.md
├── pyproject.toml
├── src/skill_lab/
│   ├── agent.py
│   ├── tasks.py
│   ├── tools.py
│   ├── skills.py
│   ├── trajectories.py
│   ├── verifier.py
│   ├── mutation.py
│   ├── promotion.py
│   ├── experiment.py
│   ├── metrics.py
│   └── cli.py
├── skills/
├── datasets/
├── tests/
└── artifacts/
```

Keep modules small and explicit.

## 19. Research Discipline

Do not optimize the implementation to prove the hypothesis.

Design it so the hypothesis can fail.

In particular:

• preserve rejected mutations
• report negative generations
• never silently retry failed experiments
• never expose test data to mutation
• distinguish execution variance from skill improvement
• retain raw trajectories
• report regressions
• do not use an LLM judge where deterministic verification is possible

The experiment should be credible even if evolved skills perform worse than the human seed.

## 20. Deliverables

Produce:

1. working Python repository
2. deterministic synthetic task environment
3. human seed SKILL.md
4. skill mutation engine
5. evaluation harness
6. promotion gate
7. skill lineage/version storage
8. experiment CLI
9. automated report generation
10. tests
11. README explaining methodology
12. one example experiment with generated artifacts

## 21. Implementation Order

Work incrementally:

Milestone 1
Task environment + deterministic tools + verifier.

Milestone 2
Agent runner + skill loading + trajectory capture.
Run the no-skill and human-skill baselines before implementing evolution.

Milestone 3
Mutation generation + version lineage.

Milestone 4
Validation evaluation + promotion gate.

Milestone 5
Multi-generation evolution.

Milestone 6
Naive-vs-verified ablation + held-out evaluation + report.

Do not build later infrastructure until earlier milestones have executable tests.

## 22. Success Criterion

The PoC is successful if it can produce defensible evidence answering:

Given a fixed model and environment, does evaluation-gated skill evolution produce greater held-out task performance than (a) no procedural skill, (b) a static human-authored skill, and (c) unconstrained self-generated skill evolution?

The desired output is an experiment, not a demo.

A negative result is valid.
