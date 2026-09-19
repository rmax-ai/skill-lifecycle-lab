# Implementation Plan — Skill Lifecycle Lab

## §0 Settled decisions & overrides

| decision | choice | rationale | alternative rejected |
|---|---|---|---|
| Task count/splits | Exactly 24 tasks: 12 train, 6 validation, 6 test. | Meets the requested 50/25/25 split and keeps hand inspection feasible. | Randomly generated tasks. |
| Python/toolchain | Python `>=3.12,<3.13`; `uv`; Linux ARM64. | Matches host exactly and limits cross-platform drift. | Docker, Poetry, CI requirement. |
| runtime dependencies | `pydantic==2.12.5`, `typer==0.20.0`, `httpx==0.28.1`. | Minimal required API/schema/CLI set, all PyPI ARM64-compatible. | Agent frameworks, ORM, YAML parser. |
| dev dependencies | `pytest==9.0.2`, `ruff==0.14.10`. | Required local test/lint stack. | Extra test plugins. |
| dependency install | Operator runs `uv sync` once before B01; workers never install packages. | Isolated workers lack network. | Per-card installation. |
| package/entry point | src layout: `src/skill_lab`; console script `skill-lab=skill_lab.cli:app`. | Brief-required import and CLI. | `python -m` only. |
| allowed added modules | Add only `config.py`, `models.py`, `storage.py`, `mock_model.py`, `reporting.py`, `data_validation.py`. | Separates shared contracts, persistence, offline model, report, data validation. | Broad framework layers. |
| required modules | Preserve `agent.py`, `tasks.py`, `tools.py`, `skills.py`, `trajectories.py`, `verifier.py`, `mutation.py`, `promotion.py`, `experiment.py`, `metrics.py`, `cli.py`. | Brief §18. | Renaming/consolidating them away. |
| config format/path | JSON at `configs/default.json`; UTF-8, canonical JSON (`indent=2`, `sort_keys=True`, trailing newline). | Stdlib parsing and reproducible bytes. | YAML/TOML runtime config. |
| default config | `dataset_path:"datasets/incident_tasks.json"`, `fixtures_path:"datasets/tool_world.json"`, `skills_root:"skills"`, `artifacts_root:"artifacts"`, `database_path:"artifacts/experiments.sqlite3"`, `model:{provider:"mock",base_url:"http://localhost:8000/v1",api_key_env:"SKILL_LAB_API_KEY",model:"mock-incident-v1",temperature:0.0,max_tokens:800,timeout_s:30}`, `agent:{max_steps:8,token_budget:2400}`, `promotion:{regression_threshold:0.00,cost_tolerance:1.10}`, `pricing:{"mock-incident-v1":{input_per_million_usd:0.0,output_per_million_usd:0.0}}`, `seed:1729`. | One frozen default. | Hidden defaults. |
| environment | `SKILL_LAB_BASE_URL`, `SKILL_LAB_API_KEY`, `SKILL_LAB_MODEL`, `SKILL_LAB_TEMPERATURE`, `SKILL_LAB_MAX_TOKENS`, `SKILL_LAB_TIMEOUT_S`; each overrides corresponding `model` config value except `provider`. | Explicit live configuration. | Reading arbitrary `.env`. |
| live-model safety | `provider:"openai_compatible"` requires `--allow-live` and nonempty API key. | Makes external inference operator-gated. | Accidental network default. |
| SQLite | stdlib `sqlite3`, one database; foreign keys on; schema below. | Portable durable raw evidence. | ORM or one JSON-only store. |
| IDs | `task_id=IR-(TR|VA|TE)-NN`; `experiment_id=exp-YYYYMMDDTHHMMSSZ-[a-f0-9]{8}`; `run_id=run-[a-f0-9]{16}`; version `vNNN`; candidate id `cand-[a-f0-9]{12}`. | Human-readable, deterministic validation. | UUIDs everywhere. |
| enums | Splits `train|validation|test`; conditions `no_skill|seed|evolved_verified|evolved_naive`; outcomes `success|failure|agent_error|model_error|tool_error|budget_exhausted`; decisions `promote|reject|naive_replace`; mutation statuses `proposed|evaluated|promoted|rejected|naive_replaced|generation_error`. | Canonical CSV/DB vocabulary. | Booleans/free text. |
| rejection reasons | `no_validation_lift|regression_threshold_exceeded|prohibited_actions_increased|cost_tolerance_exceeded|inconclusive|candidate_invalid|evaluation_error`. | Records why a negative generation happened. | One opaque reason. |
| task family | Every task is `incident_response`; fixed expected-outcome shape below. | One coherent procedural domain. | Several shallow domains. |
| fixtures | Static JSON only; tools receive a per-run fresh state copy. | Reproducible mutations and traps. | Network/services/global mutable fixtures. |
| verifier | Pure function over `Task`, `Trajectory`; no model calls, time, database, or mutable state. | Deterministic score. | LLM judge. |
| time | Tool timestamps are logical `call_index` ISO strings `2000-01-01T00:00:SS.000Z`; latency is model-reported/mock deterministic. | Avoid host-clock drift. | `datetime.now()`. |
| RNG | Every stochastic choice uses an explicitly passed `random.Random(seed)`; no `random.*`, NumPy, time, UUID, or host entropy. | Byte-stable mock experiments. | Global RNG. |
| repetitions | N independent run slots `0..N-1`, seed `base_seed + task_index*1000 + slot`; rates are successes divided by all completed slots. | Defines variance/lift exactly. | One run or dropped failures. |
| skill lift | `candidate_validation_success_rate - parent_validation_success_rate`, each aggregated over validation task×repetition slots. | Comparable gate measure. | Best-run lift. |
| regression rate | Previously successful parent validation slots that candidate fails / parent successful validation slots; denominator zero is inconclusive. | Captures regressions. | Per-task-only hidden rule. |
| token/cost | Store `input_tokens`, `output_tokens`, `total_tokens`, `latency_ms`, `estimated_cost_usd`; cost = input/1e6×input price + output/1e6×output price. | Brief §10 economics. | Provider billing lookup. |
| mock model | Dependency-free scripted `mock-incident-v1`; recognizes JSON request envelopes; task metadata drives tool sequence; candidate versions drive known behavioral deltas; mutation response is canonical JSON. | Full offline acceptance, including promote/reject. | Fake verifier or network mock. |
| mutation | Model receives current skill plus typed train-only failure packets. It returns failure analysis, change, complete `SKILL.md`, rationale. | Meets §11 while preserving leakage boundaries. | Model selects promotion. |
| promotion | Only `promotion.py` changes promoted skill metadata/status; mutation never writes skill status. | Enforces independent selection. | Candidate self-promotion. |
| naive ablation | Same candidate generation/evaluation storage, but each valid candidate becomes current via `naive_replace`; no validation data is read for the decision. | First-class comparison with only rule changed. | Separate forked harness. |
| test isolation | Test split is read only by final held-out command after all modes finish; it never enters mutation, promotion, training reports, or candidate selection. | Scientific crux. | Convenient test feedback. |
| artifacts | Commit `artifacts/example-mock/` generated by named deterministic command; ignore all other `artifacts/experiment-*` and DB files. | A visible reproducible example without claiming live results. | Commit live/provider results. |
| reporting | Reports label all tables “Observed”; interpretation is a separate explicitly qualified section. | Negative results remain visible. | Promotional narrative. |
| retries | No automatic retries. A failure becomes a persisted run/generation error. | Preserves variance and failures. | Retry-until-success. |
| out of scope | No web UI, Docker, Kubernetes, vector DB, distributed workers, MCP, production auth, CI, LLM judging. | Binding brief scope. | Infrastructure expansion. |

### Canonical SQLite DDL

```sql
PRAGMA foreign_keys = ON;
CREATE TABLE experiments (
  experiment_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, mode TEXT NOT NULL,
  config_json TEXT NOT NULL, git_commit TEXT NOT NULL, dataset_sha256 TEXT NOT NULL,
  model_id TEXT NOT NULL, seed INTEGER NOT NULL, runs_per_task INTEGER NOT NULL,
  status TEXT NOT NULL, error_text TEXT
);
CREATE TABLE skills (
  version TEXT PRIMARY KEY, name TEXT NOT NULL, parent_version TEXT,
  generation INTEGER NOT NULL, created_by TEXT NOT NULL, status TEXT NOT NULL,
  skill_markdown TEXT NOT NULL, metadata_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE mutations (
  candidate_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
  parent_version TEXT NOT NULL REFERENCES skills(version), candidate_version TEXT NOT NULL,
  generation INTEGER NOT NULL, status TEXT NOT NULL, prompt_json TEXT NOT NULL,
  response_json TEXT NOT NULL, failure_analysis TEXT NOT NULL, procedural_change TEXT NOT NULL,
  rationale TEXT NOT NULL, candidate_markdown TEXT NOT NULL, error_text TEXT
);
CREATE TABLE runs (
  run_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
  task_id TEXT NOT NULL, split TEXT NOT NULL, condition_name TEXT NOT NULL, skill_version TEXT,
  run_slot INTEGER NOT NULL, seed INTEGER NOT NULL, outcome TEXT NOT NULL,
  success INTEGER NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
  total_tokens INTEGER NOT NULL, latency_ms INTEGER NOT NULL, estimated_cost_usd REAL NOT NULL,
  trajectory_json TEXT NOT NULL, verification_json TEXT NOT NULL, error_text TEXT,
  UNIQUE(experiment_id, task_id, condition_name, skill_version, run_slot)
);
CREATE TABLE promotion_decisions (
  decision_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
  candidate_id TEXT NOT NULL REFERENCES mutations(candidate_id), parent_version TEXT NOT NULL,
  candidate_version TEXT NOT NULL, decision TEXT NOT NULL, reason_codes_json TEXT NOT NULL,
  evidence_json TEXT NOT NULL, created_at TEXT NOT NULL
);
```

### Frozen task, tool, and verifier contracts

`Task.expected_outcome` is exactly `{ticket_id, classification, severity, evidence:[str], escalation_path, final_status, required_updates:{priority:str,owner:str}}`. `invariants` are string names, not prose: `ticket_loaded`, `customer_loaded`, `service_checked`, `runbook_loaded`, `evidence_complete`, `approval_before_escalation`, `correct_escalation`, `update_after_escalation`, `no_forbidden_escalation`, `no_unnecessary_calls`.

Tool calls are `{tool:str,arguments:dict,result:dict,timestamp:str}`. Inputs/results: `search_tickets({query:str})->{tickets:[{id,summary,status}]}`; `get_ticket({ticket_id:str})->{ticket:{id,customer_id,service_id,summary,impact,priority,status,classification}}`; `get_customer({customer_id:str})->{customer:{id,tier,region,approval_required}}`; `get_service_status({service_id:str})->{service:{id,status,affected_regions,incident_id}}`; `get_runbook({service_id:str})->{runbook:{service_id,escalation_paths:{<classification>:<path>},required_evidence:[str],approval_required:bool}}` (mediation M-1); `request_approval({ticket_id:str,reason:str})->{approval:{ticket_id,status:"approved"}}`; `escalate_ticket({ticket_id:str,path:str})->{escalation:{ticket_id,path,status:"escalated"}}`; `update_ticket({ticket_id:str,priority:str,owner:str,status:str})->{ticket:{id,priority,owner,status}}`.

All unknown tool names, missing/extra arguments, unknown fixture IDs, and state-invalid calls return `{error:{code:"unknown_tool|invalid_arguments|not_found|precondition_failed",message:str}}`, are recorded, and make `no_tool_errors` false. `escalate_ticket` is deliberately allowed before approval and returns success; verifier, not tool, catches `approval_before_escalation`. A wrong path likewise executes and is checked later. `update_ticket` is permitted anytime but violates ordering if it precedes escalation.

Check names and truth semantics: `schema_valid` final output validates expected keys/types; `correct_ticket`, `correct_classification`, `correct_severity`, `correct_escalation_path`, `correct_final_status`, `correct_updates` equal expected values; `required_evidence` set contains all expected evidence; `required_actions` each invariant-required tool appears; `ordering` approval call index < escalation < update when approval required, else escalation < update; `no_forbidden_actions` no forbidden tool/path action; `no_tool_errors` no error result; `no_unnecessary_actions` calls do not exceed task `max_calls`; `invariants_satisfied` conjunction mapped from invariant strings. Success is conjunction of all checks applicable to task.

## §1 System restatement

The repository is a small, offline-reproducible experiment, not a registry. It runs support incident tasks over fixed ticket-world fixtures. An agent receives a task, tool schemas, and either no skill, the human seed, or a currently selected skill. It produces a captured message/tool/final trajectory. A pure verifier scores both the final state and procedural conduct.

For each verified generation, only train failures and train evidence are provided to a mutation model. The candidate is evaluated repeatedly on validation tasks alongside its parent. The promotion gate, outside mutation code, promotes only strict validation improvement with bounded regressions, prohibited actions, and cost. Rejected candidates remain in lineage and reports. In naive mode the same candidate pipeline automatically replaces the parent without validation gating.

After evolution, no-skill, seed, and final skill are each evaluated on untouched test tasks for verified and naive modes. Results include repetitions, variance, raw trajectories, costs, regressions, and all negative generations. The mock model provides deterministic zero-network acceptance; live OpenAI-compatible API experiments require explicit operator approval.

## §2 Contract freeze table

| item | canonical contract |
|---|---|
| `models.py` | Pydantic models `Task`, `ToolCall`, `Trajectory`, `VerificationResult`, `CandidateSkill`, `PromotionDecision`, `RunRecord`; `Task` forbids extra fields. |
| `tasks.py` | `load_tasks(path: Path) -> list[Task]`; `tasks_for_split(tasks, split) -> list[Task]`; sorted by `id`; validates 12/6/6 only in data validator. |
| `tools.py` | `ToolEnvironment(fixtures: dict)`; `execute(name: str, arguments: dict) -> dict`; `calls` property; fresh instance per run. |
| `skills.py` | `load_skill(root: Path,name:str,version:str)->Skill`; `write_candidate(...) -> Path`; Markdown front matter and exact metadata schema. |
| `agent.py` | `run_agent(task: Task, skill_markdown: str|None, model: ChatModel, tools: ToolEnvironment, config: AgentConfig, seed:int, experiment_id:str, skill_version:str|None)->Trajectory`. |
| `verifier.py` | `verify(task: Task, trajectory: Trajectory) -> VerificationResult`; never imports model, storage, mutation, or experiment. |
| `mutation.py` | `propose_mutation(current_skill: Skill, train_failures: list[TrainFailurePacket], model: ChatModel, config: MutationConfig) -> CandidateSkill`; `TrainFailurePacket` contains task_id, train task input, trajectory, verification only; its constructor rejects non-train split. |
| `promotion.py` | `decide_promotion(parent: EvaluationSummary,candidate: EvaluationSummary,policy: PromotionPolicy)->PromotionDecision`; `apply_promotion(...)` is sole metadata status writer. |
| `experiment.py` | `evaluate_condition(...) -> EvaluationSummary`; `evolve(...) -> ExperimentResult`; `run_held_out(...) -> list[EvaluationSummary]`. |
| `metrics.py` | `summarize_runs(runs:list[RunRecord], pricing:dict)->EvaluationSummary`; population variance over per-task success means. |
| `storage.py` | `ExperimentStore(path:Path)` creates exact §0 DDL and stores canonical JSON. |
| `mock_model.py` | `ScriptedMockModel(seed:int)` implements `ChatModel.complete(request:dict)->ModelResponse`; no sockets, time, or global RNG. |
| `config.py` | `load_config(path:Path, env:Mapping[str,str])->AppConfig`; unknown keys rejected. |
| `data_validation.py` | `validate_operator_inputs(dataset:Path, fixtures:Path, seed_dir:Path)->list[str]`; empty list means valid. |
| `reporting.py` | `write_artifact(result:ExperimentResult, root:Path)->Path`; writes only frozen names below. |
| config keys | Exact §0 `dataset_path`, `fixtures_path`, `skills_root`, `artifacts_root`, `database_path`, `model`, `agent`, `promotion`, `pricing`, `seed`; no implicit values. |
| artifact files | `config.json`, `results.csv`, `generations.json`, `report.md`, `manifest.json`, `trajectories.jsonl`, `prompts.jsonl`, `skills/`; all canonical UTF-8. |
| `results.csv` columns | `experiment_id,condition,split,task_id,run_slot,skill_version,outcome,success,tool_calls,invalid_tool_calls,prohibited_actions,unnecessary_actions,steps,input_tokens,output_tokens,total_tokens,latency_ms,estimated_cost_usd`. |
| `generations.json` | `{experiment_id,mode,generations:[{generation,parent_version,candidate_version,mutation_status,train_failure_task_ids,validation_parent,validation_candidate,decision,reason_codes,evidence}]}`. |
| determinism | Sort tasks, calls only append, JSON keys sort, CSV rows sort `(condition,split,task_id,run_slot)`, timestamps logical, fixed mock response templates. |
| leakage | `propose_mutation` takes no `Task` list/evaluation summary/store/artifact. Failure selection function accepts `train_runs` and asserts each `split=="train"`. Promotion takes validation summaries only. Held-out function is separately invoked after evolve. |
| prompt storage | Store exact request JSON in `mutations.prompt_json` and `prompts.jsonl`; model response in response JSON; redact API key by never placing it in request. |
| rerun contract | `skill-lab rerun artifacts/experiment-X --allow-live` loads copied `config.json`, `manifest.json` hashes, frozen `skills/`, prompt fixtures and dataset paths; aborts if hashes differ; mock regeneration must byte-match all non-timestamp files. |

CLI commands: `validate-data [--config PATH]`; `baseline [--config PATH] [--runs-per-task N] [--condition no-skill|seed] [--output DIR]`; `evolve --skill NAME --generations N --runs-per-task N --mode verified|naive [--config PATH] [--output DIR] [--allow-live]`; `held-out --experiment DIR [--runs-per-task N] [--allow-live]`; `ablate --skill NAME --generations N --runs-per-task N [--config PATH] [--output DIR] [--allow-live]`; `report --experiment DIR`; `rerun --experiment DIR [--allow-live]`; `generate-example [--config PATH]`. All `N>=1`; `generations` is `1..20`; invalid options exit 2.

## §3 Epics & tasks breakdown

Epic mapping: E1→M1; E2→M2; E3→M3; E4→M4; E5→M5; E6→M6. Later epic work starts only after its milestone gate is green.

### Epic E1 — Deterministic environment and verifier

#### B01 — Scaffold frozen package
**Epic:** E1
**Goal:** Create package skeleton and exact dependency/config declaration.
**FILE allowlist (3):** `pyproject.toml`; `src/skill_lab/__init__.py`; `tests/test_scaffold.py`
- `pyproject.toml`: pins exactly §0 dependencies, src package discovery, script, pytest testpaths, ruff target `py312`.
- `__init__.py`: defines `__version__ = "0.1.0"` only.
- test imports package and asserts version.
**Test spec:** No dependency beyond pinned list.
**Named tests:** `test_package_imports`
**Acceptance:** `uv run ruff check . && uv run pytest tests/test_scaffold.py`
**Dependencies:** none
**Do not touch:** all other paths
**Evidence bundle:** three files; 1 test; both command outputs.

#### B02 — Freeze shared Pydantic models
**Epic:** E1
**Goal:** Implement exact data models and enum literals from §0/§2.
**FILE allowlist (2):** `src/skill_lab/models.py`; `tests/test_models.py`
- Implement models and literal validation; `Trajectory.messages:list[dict]`, `tool_calls:list[ToolCall]`, `final_output:dict|str`.
- Reject extra task fields and malformed ID/version formats.
**Test spec:** Validate one legal task and reject each invalid grammar class.
**Named tests:** `test_task_accepts_frozen_shape`; `test_task_rejects_extra_fields`; `test_id_and_version_grammars`
**Acceptance:** `uv run ruff check . && uv run pytest tests/test_models.py`
**Dependencies:** B01
**Do not touch:** all other paths
**Evidence bundle:** model file; 3 tests; command outputs.

#### B03 — Validate operator starter artifacts
**Epic:** E1
**Goal:** Gate all dataset/fixture/seed consumers on schema and scientific content checks.
**FILE allowlist (2):** `src/skill_lab/data_validation.py`; `tests/test_data_validation.py`
- Validate exact packet in §6: JSON schema, IDs, 12/6/6, fixture references, trap matrix, seed front matter/metadata, and consistency rule.
- Return sorted error strings, never raise for ordinary invalid content.
**Test spec:** Valid operator files pass; copied poison validation split fails.
**Named tests:** `test_operator_packet_validates`; `test_invalid_split_count_is_reported`; `test_fixture_outcome_mismatch_is_reported`
**Acceptance:** `uv run ruff check . && uv run pytest tests/test_data_validation.py`
**Dependencies:** B02
**Do not touch:** datasets; skills; all other paths
**Evidence bundle:** validator; 3 tests; command outputs.

#### B04 — Load task corpus
**Epic:** E1
**Goal:** Load sorted immutable task records and split selectors.
**FILE allowlist (2):** `src/skill_lab/tasks.py`; `tests/test_tasks.py`
- `load_tasks` parses only the validated JSON array; `tasks_for_split` returns sorted shallow list without mutation.
- Assert all expected-outcome keys and invariant vocabulary.
**Test spec:** Counts and deterministic sort against operator corpus.
**Named tests:** `test_loads_24_sorted_tasks`; `test_split_selection`; `test_task_outcome_shape`
**Acceptance:** `uv run ruff check . && uv run pytest tests/test_tasks.py`
**Dependencies:** B03
**Do not touch:** datasets; all other paths
**Evidence bundle:** loader; 3 tests; command outputs.

#### B05 — Implement deterministic tools
**Epic:** E1
**Goal:** Implement all eight fixture-backed tools and deliberately committable traps.
**FILE allowlist (2):** `src/skill_lab/tools.py`; `tests/test_tools.py`
- Exact schemas/results/errors from §0; deep-copy fixtures per environment; logical timestamps.
- `search_tickets` case-insensitive substring over id/summary sorted by id; state updates only from update/escalate.
**Test spec:** One named test per tool plus approval/order and error traps.
**Named tests:** `test_search_tickets`; `test_get_ticket`; `test_get_customer`; `test_get_service_status`; `test_get_runbook`; `test_request_approval`; `test_escalate_ticket_trap_is_committable`; `test_update_ticket`; `test_tool_error_shape`
**Acceptance:** `uv run ruff check . && uv run pytest tests/test_tools.py`
**Dependencies:** B03; B04
**Do not touch:** datasets; all other paths
**Evidence bundle:** tool implementation; 9 tests; command outputs.

#### B06 — Implement pure verifier
**Epic:** E1
**Goal:** Score final state and complete trajectory using frozen check semantics.
**FILE allowlist (2):** `src/skill_lab/verifier.py`; `tests/test_verifier.py`
- `verify` returns every applicable check, evidence with call indexes, and deterministic outcome.
- Map each invariant to stated checks; prohibited path/approval/order violations must fail even if final output is correct.
**Test spec:** Success, missing evidence, forbidden escalation, wrong ordering, tool error, unnecessary calls.
**Named tests:** `test_valid_trajectory_succeeds`; `test_missing_evidence_fails`; `test_unapproved_escalation_fails`; `test_update_before_escalation_fails`; `test_tool_error_fails`; `test_unnecessary_call_fails`
**Acceptance:** `uv run ruff check . && uv run pytest tests/test_verifier.py`
**Dependencies:** B02; B04; B05
**Do not touch:** all other paths
**Evidence bundle:** verifier; 6 tests; command outputs.

### Epic E2 — Agent, skills, and baselines

#### B07 — Load versioned skills
**Epic:** E2
**Goal:** Parse seed/candidate skill directories and preserve lineage metadata.
**FILE allowlist (2):** `src/skill_lab/skills.py`; `tests/test_skills.py`
- Require SKILL front matter name/version/description and metadata fields `parent,created_by,generation,status`.
- Candidate writer uses `vNNN`, does not alter parent, and writes canonical metadata.
**Test spec:** Load v001 and reject mismatched markdown/metadata version.
**Named tests:** `test_loads_seed_skill`; `test_rejects_version_mismatch`; `test_candidate_lineage_preserved`
**Acceptance:** `uv run ruff check . && uv run pytest tests/test_skills.py`
**Dependencies:** B03
**Do not touch:** skills/incident-response/v001; all other paths
**Evidence bundle:** skills module; 3 tests; command outputs.

#### B08 — Implement scripted offline model
**Epic:** E2
**Goal:** Supply deterministic agent and mutation responses with planned good/bad candidates.
**FILE allowlist (2):** `src/skill_lab/mock_model.py`; `tests/test_mock_model.py`
- Agent envelope includes task id, skill version, tool history; scripted policy gathers correct evidence then performs actions.
- Seed misses approval on `IR-VA-02`; candidate generation 1 fixes it, generation 2 adds forbidden escalation on `IR-VA-03`; mutation JSON canonical.
**Test spec:** Same request yields byte-identical output; mutation modes yield two specified candidates.
**Named tests:** `test_mock_agent_response_is_deterministic`; `test_mock_mutation_promote_candidate`; `test_mock_mutation_reject_candidate`; `test_mock_never_uses_network`
**Acceptance:** `uv run ruff check . && uv run pytest tests/test_mock_model.py`
**Dependencies:** B02
**Do not touch:** all other paths
**Evidence bundle:** mock module; 4 tests; command outputs.

#### B09 — Run agent and capture trajectory
**Epic:** E2
**Goal:** Execute bounded tool loop and retain all messages, calls, tokens, latency and outcome.
**FILE allowlist (2):** `src/skill_lab/agent.py`; `tests/test_agent.py`
- Define `ChatModel` protocol/request envelope; max steps/budget terminal outcomes exact §0.
- Final output must be parsed from model `final` action; no automatic corrective action/retry.
**Test spec:** Successful scripted run and budget exhaustion retain partial calls.
**Named tests:** `test_agent_captures_complete_trajectory`; `test_agent_records_budget_exhaustion`; `test_agent_records_invalid_tool_call`
**Acceptance:** `uv run ruff check . && uv run pytest tests/test_agent.py`
**Dependencies:** B02; B05; B08
**Do not touch:** all other paths
**Evidence bundle:** agent module; 3 tests; command outputs.

#### B10 — Configuration and live client
**Epic:** E2
**Goal:** Load strict JSON config/env overrides and OpenAI-compatible HTTP client.
**FILE allowlist (3):** `src/skill_lab/config.py`; `src/skill_lab/cli.py`; `tests/test_config.py`
- Pydantic config exact §0; CLI app placeholder and `validate-data` command.
- HTTP client posts `/chat/completions`; only instantiated with `--allow-live`; timeout exact config.
**Test spec:** Env override, unknown key rejection, live guard rejection.
**Named tests:** `test_default_config_contract`; `test_environment_overrides`; `test_live_provider_requires_allow_live`
**Acceptance:** `uv run ruff check . && uv run pytest tests/test_config.py`
**Dependencies:** B01; B03; B08
**Do not touch:** configs; all other paths
**Evidence bundle:** 3 files; 3 tests; command outputs.

#### B11 — Persist trajectories and experiments
**Epic:** E2
**Goal:** Create exact SQLite schema and canonical durable inserts.
**FILE allowlist (2):** `src/skill_lab/storage.py`; `tests/test_storage.py`
- Execute DDL verbatim; serialize JSON with sort keys; reject duplicate run unique key.
- Store all raw trajectory/verifier/prompt material without API keys.
**Test spec:** Schema column names and round-trip raw evidence.
**Named tests:** `test_schema_matches_contract`; `test_run_round_trip`; `test_duplicate_run_is_rejected`
**Acceptance:** `uv run ruff check . && uv run pytest tests/test_storage.py`
**Dependencies:** B02
**Do not touch:** all other paths
**Evidence bundle:** storage module; 3 tests; command outputs.

#### B12 — Baseline evaluation and metrics
**Epic:** E2
**Goal:** Evaluate no-skill/seed repeats and compute all outcome/trajectory/economic metrics.
**FILE allowlist (3):** `src/skill_lab/metrics.py`; `src/skill_lab/experiment.py`; `tests/test_baselines.py`
- `evaluate_condition` evaluates a requested non-test split; writes every failed slot; no retries.
- Summary includes rates, variance, solved/broken relative parent, action counts, token/cost totals.
**Test spec:** Two repetitions aggregate denominator correctly and mock seed lifts no-skill on train.
**Named tests:** `test_repetition_aggregation`; `test_baseline_conditions_fixed_controls`; `test_baseline_persists_failures`
**Acceptance:** `uv run ruff check . && uv run pytest tests/test_baselines.py`
**Dependencies:** B04; B06; B07; B09; B10; B11
**Do not touch:** all other paths
**Evidence bundle:** 3 files; 3 tests; command outputs.

### Epic E3 — Mutation and lineage

#### B13 — Implement train-only mutation contract
**Epic:** E3
**Goal:** Build proposal prompt/response parser that structurally excludes validation and test evidence.
**FILE allowlist (2):** `src/skill_lab/mutation.py`; `tests/test_mutation.py`
- `TrainFailurePacket` asserts `split=="train"`; select sorted failed train packets only.
- Prompt has current markdown and train packet JSON only; parse exactly four response fields; candidate invalid is persisted status, never promoted.
**Test spec:** Poisoned validation/test IDs/content cannot construct packet or appear in prompt.
**Named tests:** `test_prompt_contains_train_failures_only`; `test_non_train_packet_is_rejected`; `test_candidate_response_contract`; `test_mutation_cannot_write_skill_status`
**Acceptance:** `uv run ruff check . && uv run pytest tests/test_mutation.py`
**Dependencies:** B07; B08; B11; B12
**Do not touch:** all other paths
**Evidence bundle:** mutation module; 4 tests; command outputs.

#### B14 — Record candidate lineage
**Epic:** E3
**Goal:** Store proposed/rejected candidate content, rationale and parent relationship without promotion.
**FILE allowlist (2):** `src/skill_lab/skills.py`; `tests/test_candidate_lineage.py`
- Extend only candidate write/load behavior to retain separate `RATIONALE.md` and metadata status `proposed`.
- Existing seed files are never rewritten.
**Test spec:** Rejected candidate remains readable and parent remains baseline.
**Named tests:** `test_rejected_candidate_is_preserved`; `test_parent_metadata_unchanged`
**Acceptance:** `uv run ruff check . && uv run pytest tests/test_candidate_lineage.py`
**Dependencies:** B07; B13
**Do not touch:** all other paths
**Evidence bundle:** skills delta; 2 tests; command outputs.

### Epic E4 — Validation and promotion

#### B15 — Implement exact promotion policy
**Epic:** E4
**Goal:** Make complete promote/reject decisions from validation summaries only.
**FILE allowlist (2):** `src/skill_lab/promotion.py`; `tests/test_promotion.py`
- Strict lift, regression threshold, prohibited non-increase, cost tolerance all required; missing denominator/errored summary yields `reject` + `inconclusive`.
- Evidence records all raw rates/counts/policy values/reason codes.
**Test spec:** One test per policy clause and all-pass promotion.
**Named tests:** `test_promotes_only_when_all_rules_pass`; `test_rejects_no_lift`; `test_rejects_regression`; `test_rejects_prohibited_increase`; `test_rejects_cost_excess`; `test_rejects_inconclusive`
**Acceptance:** `uv run ruff check . && uv run pytest tests/test_promotion.py`
**Dependencies:** B02; B12
**Do not touch:** all other paths
**Evidence bundle:** promotion module; 6 tests; command outputs.

#### B16 — Wire validation comparison
**Epic:** E4
**Goal:** Evaluate parent/candidate on validation and apply only promotion module verdict.
**FILE allowlist (2):** `src/skill_lab/experiment.py`; `tests/test_validation_gate.py`
- Validation evaluator receives only validation tasks; it calls `decide_promotion`; status update solely via `apply_promotion`.
- Never pass validation task objects/summaries to mutation module.
**Test spec:** mock v002 promotes and v003 rejects; test fixture spy proves separation.
**Named tests:** `test_validation_gate_promotes_mock_v002`; `test_validation_gate_rejects_mock_v003`; `test_validation_evidence_cannot_reach_mutation`
**Acceptance:** `uv run ruff check . && uv run pytest tests/test_validation_gate.py`
**Dependencies:** B12; B13; B14; B15
**Do not touch:** all other paths
**Evidence bundle:** experiment delta; 3 tests; command outputs.

### Epic E5 — Multi-generation evolution

#### B17 — Run bounded evolution modes
**Epic:** E5
**Goal:** Implement verified and naive loops with identical candidate machinery and no silent retries.
**FILE allowlist (2):** `src/skill_lab/experiment.py`; `tests/test_evolution.py`
- `evolve` evaluates train, selects failures, proposes, writes candidate, then verified validation decision or naive replacement.
- Each generation always records a generation object; generation/model errors become `generation_error`; stop exactly requested count.
**Test spec:** Two-generation mock verified includes promote/reject; naive auto-replaces same candidates.
**Named tests:** `test_verified_evolution_has_promote_and_reject`; `test_naive_evolution_auto_replaces`; `test_generation_errors_are_not_retried`; `test_only_train_failures_are_selected`
**Acceptance:** `uv run ruff check . && uv run pytest tests/test_evolution.py`
**Dependencies:** B16
**Do not touch:** all other paths
**Evidence bundle:** experiment delta; 4 tests; command outputs.

#### B18 — Add evolution CLI
**Epic:** E5
**Goal:** Expose baseline/evolve commands and strict flag behavior.
**FILE allowlist (2):** `src/skill_lab/cli.py`; `tests/test_cli_evolve.py`
- Implement commands/flags from §2; path output defaults to timestamp experiment; mock allowed without `--allow-live`.
- Print artifact path and model id, never secret/config key.
**Test spec:** Typer runner executes mock baseline/evolve and invalid mode exits 2.
**Named tests:** `test_evolve_cli_mock`; `test_baseline_cli_mock`; `test_invalid_cli_value_exits_two`
**Acceptance:** `uv run ruff check . && uv run pytest tests/test_cli_evolve.py`
**Dependencies:** B10; B17
**Do not touch:** all other paths
**Evidence bundle:** CLI delta; 3 tests; command outputs.

### Epic E6 — Ablation, report, reproducibility

#### B19 — Write canonical artifact reports
**Epic:** E6
**Goal:** Produce all §16 artifact files and transparent observed/interpretation report.
**FILE allowlist (2):** `src/skill_lab/reporting.py`; `tests/test_reporting.py`
- Create frozen layout and CSV/generation schemas; lineage tree lists rejected and negative generations.
- Report sections exactly Configuration, Baselines, Evolution History, Held-Out Results, Skill Lineage, Failure Analysis, Observed Results, Interpretation/Conclusions; interpretation cannot assert causality.
**Test spec:** byte-identical mock write and required negative-generation rows.
**Named tests:** `test_report_contains_required_sections`; `test_canonical_artifact_bytes`; `test_report_retains_rejection`
**Acceptance:** `uv run ruff check . && uv run pytest tests/test_reporting.py`
**Dependencies:** B17
**Do not touch:** artifacts; all other paths
**Evidence bundle:** reporting module; 3 tests; command outputs.

#### B20 — Held-out and ablation workflow
**Epic:** E6
**Goal:** Run three conditions on untouched test tasks for both evolution modes.
**FILE allowlist (2):** `src/skill_lab/experiment.py`; `tests/test_held_out.py`
- `ablate` runs verified/naive independent roots, then `run_held_out` evaluates no_skill, seed, final mode skill once evolution complete.
- Test evaluator cannot be called inside mutation/promotion; reports train-vs-heldout lift.
**Test spec:** poison test records prove no earlier access; both mode condition sets present.
**Named tests:** `test_held_out_runs_three_conditions_for_both_modes`; `test_test_data_is_not_touched_before_final_evaluation`; `test_training_vs_held_out_lift_reported`
**Acceptance:** `uv run ruff check . && uv run pytest tests/test_held_out.py`
**Dependencies:** B17; B19
**Do not touch:** all other paths
**Evidence bundle:** experiment delta; 3 tests; command outputs.

#### B21 — Add report, rerun, and example CLI
**Epic:** E6
**Goal:** Finish report/rerun/ablate commands and deterministic committed-example generator.
**FILE allowlist (2):** `src/skill_lab/cli.py`; `tests/test_cli_artifacts.py`
- `generate-example` writes `artifacts/example-mock` using `skill-lab ablate --skill incident-response --generations 2 --runs-per-task 1 --config configs/default.json --output artifacts/example-mock` with fixed experiment id `exp-20000101T000000Z-deadbeef`.
- `rerun` verifies manifest hashes and byte-compares mock output; live requires flag.
**Test spec:** offline CLI smoke and rerun bytes.
**Named tests:** `test_generate_example_cli`; `test_rerun_mock_is_byte_identical`; `test_live_rerun_requires_flag`
**Acceptance:** `uv run ruff check . && uv run pytest tests/test_cli_artifacts.py`
**Dependencies:** B18; B19; B20
**Do not touch:** artifacts/example-mock; all other paths
**Evidence bundle:** CLI delta; 3 tests; command outputs.

#### B22 — Final integration acceptance
**Epic:** E6
**Goal:** Add end-to-end offline gate covering validator, both decisions, ablation, reports and rerun.
**FILE allowlist (1):** `tests/test_end_to_end.py`
- Invoke CLI in temp artifact root with mock and 2 generations; assert model id starts `mock-`, one promote, one reject, both modes held-out conditions, raw trajectories and costs.
**Test spec:** Zero network; deterministic repeated tree hashes.
**Named tests:** `test_offline_mini_evolution_promotes_and_rejects`; `test_offline_ablation_is_deterministic`
**Acceptance:** `uv run ruff check . && uv run pytest`
**Dependencies:** B21
**Do not touch:** all non-test paths
**Evidence bundle:** integration test; 2 tests; full command outputs.

## §4 GitHub board specification

Labels: exactly one status (`status:blocked`, `status:ready`, `status:in-progress`, `status:review`, `status:done`); exactly one type (`type:epic`, `type:build`, `type:test`, `type:operator`); exactly one epic (`epic:E1`…`epic:E6`); zero or more risks (`risk:science`, `risk:security`, `risk:determinism`, `risk:integration`, `risk:docs`); zero or more scopes (`scope:core`, `scope:data`, `scope:model`, `scope:evolution`, `scope:evaluation`, `scope:cli`, `scope:docs`).

Milestones: `M1 Task environment + deterministic tools + verifier` (E1); `M2 Agent runner + skill loading + baselines` (E2); `M3 Mutation generation + lineage` (E3); `M4 Validation + promotion` (E4); `M5 Multi-generation evolution` (E5); `M6 Ablation + held-out + report` (E6).

| `E1 Deterministic environment` | `type:epic,status:in-progress,epic:E1,risk:science,risk:determinism,scope:data,scope:evaluation` | B01–B06 — validated corpus, tools, pure verifier |
| `E2 Agent and baselines` | `type:epic,status:blocked,epic:E2,risk:integration,scope:core,scope:model` | B07–B12 — trajectories and baselines |
| `E3 Mutation and lineage` | `type:epic,status:blocked,epic:E3,risk:science,scope:evolution` | B13–B14 — train-only proposals retained |
| `E4 Promotion gate` | `type:epic,status:blocked,epic:E4,risk:science,scope:evaluation` | B15–B16 — validation selection |
| `E5 Evolution loop` | `type:epic,status:blocked,epic:E5,risk:integration,scope:evolution` | B17–B18 — modes and CLI |
| `E6 Ablation/report` | `type:epic,status:blocked,epic:E6,risk:science,scope:evaluation,scope:docs` | B19–B22 — held-out defensible results |

| `B01 — Scaffold frozen package` | `type:build,status:ready,epic:E1,risk:determinism,scope:core` |
| `B02 — Freeze shared Pydantic models` | `type:build,status:blocked,epic:E1,risk:determinism,scope:core` |
| `B03 — Validate operator starter artifacts` | `type:build,status:blocked,epic:E1,risk:science,scope:data` |
| `B04 — Load task corpus` | `type:build,status:blocked,epic:E1,scope:data` |
| `B05 — Implement deterministic tools` | `type:build,status:blocked,epic:E1,risk:determinism,scope:core` |
| `B06 — Implement pure verifier` | `type:build,status:blocked,epic:E1,risk:science,scope:evaluation` |
| `B07 — Load versioned skills` | `type:build,status:blocked,epic:E2,scope:core` |
| `B08 — Implement scripted offline model` | `type:build,status:blocked,epic:E2,risk:determinism,scope:model` |
| `B09 — Run agent and capture trajectory` | `type:build,status:blocked,epic:E2,scope:core` |
| `B10 — Configuration and live client` | `type:build,status:blocked,epic:E2,risk:security,scope:cli` |
| `B11 — Persist trajectories and experiments` | `type:build,status:blocked,epic:E2,scope:core` |
| `B12 — Baseline evaluation and metrics` | `type:build,status:blocked,epic:E2,risk:science,scope:evaluation` |
| `B13 — Implement train-only mutation contract` | `type:build,status:blocked,epic:E3,risk:science,scope:evolution` |
| `B14 — Record candidate lineage` | `type:build,status:blocked,epic:E3,scope:evolution` |
| `B15 — Implement exact promotion policy` | `type:build,status:blocked,epic:E4,risk:science,scope:evaluation` |
| `B16 — Wire validation comparison` | `type:build,status:blocked,epic:E4,risk:science,scope:evolution` |
| `B17 — Run bounded evolution modes` | `type:build,status:blocked,epic:E5,risk:integration,scope:evolution` |
| `B18 — Add evolution CLI` | `type:build,status:blocked,epic:E5,scope:cli` |
| `B19 — Write canonical artifact reports` | `type:build,status:blocked,epic:E6,risk:docs,scope:docs` |
| `B20 — Held-out and ablation workflow` | `type:build,status:blocked,epic:E6,risk:science,scope:evaluation` |
| `B21 — Add report, rerun, and example CLI` | `type:build,status:blocked,epic:E6,risk:determinism,scope:cli` |
| `B22 — Final integration acceptance` | `type:test,status:blocked,epic:E6,risk:integration,risk:science,scope:evaluation` |

Epic issue body outline: `Outcome:`; `Child issues:` checklist of B ids; `Exit gate:`; `Scientific reference:`; `Non-goals:`.

Task issue body headings: `Goal`; `Deliverables / FILE allowlist`; `Acceptance`; `Dependencies`; `Verification evidence`; `Reference`; `Do not touch`.

## §5 Test & verification plan

§9 deterministic verification maps to `test_tools.py` (all tool schemas/traps), `test_verifier.py` (final/action/order/schema checks), and `test_end_to_end.py` (raw evidence survives). §19 maps to `test_mutation.py` and `test_validation_gate.py` (leakage), `test_candidate_lineage.py` and `test_reporting.py` (rejected/negative preserved), `test_evolution.py` (no retry), and `test_cli_artifacts.py` (rerun reproducibility).

The required mini evolution uses mock, `generations=2`, `runs_per_task=1`: v001→v002 is promoted because v002 fixes the seeded validation approval failure with no new violations/cost excess; v002→v003 is rejected because it performs a forbidden escalation on `IR-VA-03`. The end-to-end test must assert both decision records, not merely terminal version.

Leakage guards inject distinctive strings `POISON-VALIDATION` and `POISON-TEST` into task input, expected outcome, trajectory and store rows. Tests assert neither appears in selected failure packets, serialized mutation prompt, model request, mutation response, or candidate markdown. A spy rejects calls to test loader before `run_held_out`; a promotion spy rejects summaries whose split is not validation.

Determinism gates run the same mock ablation twice in different temp directories, normalize only absolute root strings, compare SHA-256 per artifact file, and require equality. No test may patch a network client as proof of offline behavior: mock code must have no `httpx` import. Expected final count: 75 named tests (allow +0 additions only; never delete/rename named tests).

Final acceptance:

```bash
uv run ruff check .
uv run pytest
uv run skill-lab validate-data --config configs/default.json
uv run skill-lab evolve --skill incident-response --generations 2 --runs-per-task 1 --mode verified --config configs/default.json --output /tmp/skill-lab-smoke
uv run skill-lab ablate --skill incident-response --generations 2 --runs-per-task 1 --config configs/default.json --output /tmp/skill-lab-ablation
uv run skill-lab rerun --experiment artifacts/example-mock
```

## §6 Execution order & operator prep

Dispatch strictly B01, B02, B03, B04, B05, B06, B07, B08, B09, B10, B11, B12, B13, B14, B15, B16, B17, B18, B19, B20, B21, B22. Safe parallel batches only after prerequisites: B07+B08+B11 after B03/B02 as applicable; B10 may run after B03+B08; B15 after B12. Prefer serial dispatch otherwise because interface cards are intentionally narrow.

Before B03, operator creates only: `pyproject.toml`, `.gitignore`, `SPEC.md` verbatim brief, `README.md` skeleton, `AGENTS.md` skeleton, `.env.example`, `LICENSE`, `datasets/incident_tasks.json`, `datasets/tool_world.json`, `skills/incident-response/v001/SKILL.md`, `skills/incident-response/v001/metadata.json`. Operator also runs `uv sync`. `configs/default.json` is B10-authored, not operator-authored.

Dataset packet: `incident_tasks.json` is a JSON array of exactly 24 objects. Each has only `id,input,available_tools,expected_outcome,invariants,split,max_calls`; `available_tools` is all eight names; `max_calls` is 7 for approval tasks and 6 otherwise. For compact literal construction, every task is defined by the following matrix; expand each row into required fields, with input exactly `Handle <id>: customer reports <summary>. Determine procedure and complete the ticket.`, expected outcome derived by the consistency rule below.

| ids | split | ticket | classification | severity | evidence | path | approval | trap |
|---|---|---|---|---|---|---|---|---|
| IR-TR-01,02 | train | T01,T02 | outage,degradation | SEV1,SEV2 | ticket,customer,status,runbook | pager,service-desk | yes,no | approval/order |
| IR-TR-03,04 | train | T03,T04 | security,billing | SEV1,SEV3 | ticket,customer,runbook | security,support | yes,no | wrong path |
| IR-TR-05,06 | train | T05,T06 | outage,degradation | SEV2,SEV3 | ticket,customer,status,runbook | pager,service-desk | no,no | unnecessary search |
| IR-TR-07,08 | train | T07,T08 | security,billing | SEV2,SEV3 | ticket,customer,runbook | security,support | yes,no | update-before-escalate |
| IR-TR-09,10 | train | T09,T10 | outage,degradation | SEV1,SEV2 | ticket,customer,status,runbook | pager,service-desk | yes,no | approval/order |
| IR-TR-11,12 | train | T11,T12 | security,billing | SEV1,SEV3 | ticket,customer,runbook | security,support | yes,no | forbidden path |
| IR-VA-01,02 | validation | T13,T14 | outage,degradation | SEV2,SEV2 | ticket,customer,status,runbook | pager,service-desk | no,yes | seed misses approval on 02 |
| IR-VA-03,04 | validation | T15,T16 | security,billing | SEV1,SEV3 | ticket,customer,runbook | security,support | yes,no | mock v003 forbidden security escalation on 03 |
| IR-VA-05,06 | validation | T17,T18 | outage,degradation | SEV1,SEV3 | ticket,customer,status,runbook | pager,service-desk | yes,no | wrong path/order |
| IR-TE-01,02 | test | T19,T20 | security,billing | SEV2,SEV3 | ticket,customer,runbook | security,support | yes,no | untouched approval/path |
| IR-TE-03,04 | test | T21,T22 | outage,degradation | SEV1,SEV2 | ticket,customer,status,runbook | pager,service-desk | yes,no | untouched order |
| IR-TE-05,06 | test | T23,T24 | security,billing | SEV1,SEV3 | ticket,customer,runbook | security,support | yes,no | untouched forbidden path |

Normative per-task expansion ledger (columns are `id | split | ticket | classification | severity | evidence | path | approval | max_calls`):

```text
IR-TR-01 | train | T01 | outage | SEV1 | ticket,customer,status,runbook | pager | yes | 7
IR-TR-02 | train | T02 | degradation | SEV2 | ticket,customer,status,runbook | service-desk | no | 6
IR-TR-03 | train | T03 | security | SEV1 | ticket,customer,runbook | security | yes | 7
IR-TR-04 | train | T04 | billing | SEV3 | ticket,customer,runbook | support | no | 6
IR-TR-05 | train | T05 | outage | SEV2 | ticket,customer,status,runbook | pager | no | 6
IR-TR-06 | train | T06 | degradation | SEV3 | ticket,customer,status,runbook | service-desk | no | 6
IR-TR-07 | train | T07 | security | SEV2 | ticket,customer,runbook | security | yes | 7
IR-TR-08 | train | T08 | billing | SEV3 | ticket,customer,runbook | support | no | 6
IR-TR-09 | train | T09 | outage | SEV1 | ticket,customer,status,runbook | pager | yes | 7
IR-TR-10 | train | T10 | degradation | SEV2 | ticket,customer,status,runbook | service-desk | no | 6
IR-TR-11 | train | T11 | security | SEV1 | ticket,customer,runbook | security | yes | 7
IR-TR-12 | train | T12 | billing | SEV3 | ticket,customer,runbook | support | no | 6
IR-VA-01 | validation | T13 | outage | SEV2 | ticket,customer,status,runbook | pager | no | 6
IR-VA-02 | validation | T14 | degradation | SEV2 | ticket,customer,status,runbook | service-desk | yes | 7
IR-VA-03 | validation | T15 | security | SEV1 | ticket,customer,runbook | security | yes | 7
IR-VA-04 | validation | T16 | billing | SEV3 | ticket,customer,runbook | support | no | 6
IR-VA-05 | validation | T17 | outage | SEV1 | ticket,customer,status,runbook | pager | yes | 7
IR-VA-06 | validation | T18 | degradation | SEV3 | ticket,customer,status,runbook | service-desk | no | 6
IR-TE-01 | test | T19 | security | SEV2 | ticket,customer,runbook | security | yes | 7
IR-TE-02 | test | T20 | billing | SEV3 | ticket,customer,runbook | support | no | 6
IR-TE-03 | test | T21 | outage | SEV1 | ticket,customer,status,runbook | pager | yes | 7
IR-TE-04 | test | T22 | degradation | SEV2 | ticket,customer,status,runbook | service-desk | no | 6
IR-TE-05 | test | T23 | security | SEV1 | ticket,customer,runbook | security | yes | 7
IR-TE-06 | test | T24 | billing | SEV3 | ticket,customer,runbook | support | no | 6
```

For each ledger entry, exact input is `Handle <id>: customer reports <classification> incident <ticket>. Determine procedure and complete the ticket.` Exact expected_outcome is `{ticket_id:<ticket>,classification:<classification>,severity:<severity>,evidence:[the four or three comma-separated strings],escalation_path:<path>,final_status:"resolved",required_updates:{priority:<severity>,owner:<path>}}`. This is part of the data packet, not a generator feature.

For each row pair, assign IDs in listed order and corresponding comma-separated values. `expected_outcome.ticket_id` is ticket; `classification`, `severity`, `evidence` as matrix; `escalation_path` path; `final_status:"resolved"`; `required_updates:{priority:severity,owner:path}`. Invariants always include `ticket_loaded,customer_loaded,runbook_loaded,evidence_complete,correct_escalation,update_after_escalation,no_forbidden_escalation,no_unnecessary_calls`; outage/degradation additionally `service_checked`; approval=yes additionally `approval_before_escalation`.

`tool_world.json` shape is `{dataset_version:"2026-01",tickets:[...],customers:[...],services:[...],runbooks:[...]}`. Roster: tickets T01–T24 use matrix facts, each `{id,customer_id:"C"+NN,service_id:"S-CORE" for outage/degradation else "S-AUTH" for security else "S-BILL",summary:"<classification> incident <id>",impact:"regional",priority:<severity>,status:"open",classification:<classification>}`. Customers C01–C24: odd IDs `{tier:"enterprise",region:"us",approval_required:true}`, even `{tier:"standard",region:"eu",approval_required:false}`; override approval_required to the matrix approval value for each ticket. Services: S-CORE `{status:"degraded",affected_regions:["us","eu"],incident_id:"INC-CORE"}`, S-AUTH `{status:"outage",affected_regions:["us"],incident_id:"INC-AUTH"}`, S-BILL `{status:"degraded",affected_regions:["eu"],incident_id:"INC-BILL"}`. Runbooks (mediation M-1: escalation paths keyed by incident classification): S-CORE `{escalation_paths:{outage:pager,degradation:service-desk},required_evidence:[ticket,customer,status,runbook],approval_required:true}`; S-AUTH `{escalation_paths:{security:security},required_evidence:[ticket,customer,runbook],approval_required:true}`; S-BILL `{escalation_paths:{billing:support},required_evidence:[ticket,customer,runbook],approval_required:false}`.

Consistency rule: each task expected values must equal its ticket, customer approval, service/runbook and matrix; no fixture field may contradict the task. Precisely (mediation M-2): every task's `escalation_path` must equal `escalation_paths[classification]` in its service's runbook entry, and its `evidence` set must equal that runbook's `required_evidence`; `customer.approval_required` is the per-ticket approval authority (equal to the matrix approval value); the service runbook's `approval_required` is true iff the service has at least one approval=yes task. Trap matrix is the last column above: each listed trap must be possible using legal tool schema, but fails its named verifier check. Every task has at least one trap, and tests target one of each five trap types.

Seed skill: front matter name incident-response, version 1, description. Sections exactly Procedure, Decision Rules, Failure Recovery, Verification. It instructs load ticket/customer/runbook; check service for service incidents; gather runbook evidence; choose runbook path; escalate; update resolved with severity/path. Deliberate imperfections in priority order: (1) says approval is “usually optional” and omits mandatory approval-before-escalation; (2) does not require checking customer approval flag; (3) permits an exploratory `search_tickets` despite max call limits; (4) verification checks final ticket state but not action order; (5) does not explicitly prohibit using another path. It must not embed task IDs, splits, validation/test facts, or fixtures.

Seed metadata exactly `{ "parent": null, "created_by": "human", "generation": 0, "status": "baseline" }` with canonical formatting. `.env.example` names only all §0 env vars with blank values except example base URL. README/AGENTS are skeletons with no behavioral contracts.

## §7 Out of scope / deferred

The repository fully supports deterministic mock experiments, local SQLite/artifacts, versioned skills, three baselines, verified/naive ablation, held-out reporting, and an explicit live OpenAI-compatible adapter. Operator-gated: real model headline experiments, committing a live example, supplying API credentials, and selecting live pricing. No CI requirement, Docker daemon/image, website/UI, Kubernetes, vector database, distributed workers, complex agent framework, production authentication, MCP, web browsing, or LLM-as-judge. Never weaken §19: preserve failures/rejections/raw trajectories, no test leakage, no silent retry, no optimizing to prove the thesis.

## §8 Open questions & brief flaws

- [ASSUME] Exact package patch versions are the §0 pins; operator should ratify availability with their preinstalled uv resolver before dispatch. If unavailable, change the pins once in §0/R11 and regenerate cards, never let workers choose versions.
- [ASSUME] “Approximately 20–30” is resolved as 24 exactly; no task generation command will exist.
- [ASSUME] Validation repetitions are sufficient evidence for strict rate comparison; “inconclusive” means any missing/error slot, zero parent-success denominator for regression, nonfinite metric, or candidate parse failure. This is conservative but no statistical significance test was requested.
- [ASSUME] Timestamped experiment IDs make distinct ordinary live runs non-byte-identical; byte-identical requirement applies to fixed-ID mock regeneration and canonical contents. Host/git commit naturally differ across environments.
- [UNVERIFIED] The brief requires a committed example but operator authors only starter artifacts. B21 therefore generates it; it should be committed only after B22 passes, with manifest model id `mock-incident-v1` prominently proving it is not a live result.
- [UNVERIFIED] The requested task packet’s matrix is intentionally formulaic. It is still 24 explicit deterministic records once expanded; operator should consider whether more linguistic diversity is needed, but must not change facts/splits without revising validators and mock mappings.
- [RISK] A scripted model can demonstrate harness mechanics, not the research hypothesis. Reports must call the example “offline harness example,” never evidence of real-model skill evolution.
- [RISK] The brief asks exact cost economics but does not provide prices. Config pricing is intentionally operator-supplied for live model IDs; missing model pricing is a configuration error, not silently zero cost (only mock is zero).
- [RISK] Fixed synthetic tasks can be overfit by mutation. The train-only prompt and untouched test mitigate leakage but do not prove broad external generalization; conclusions must say “within this fixture world.”

## §9 Operator mediations (post-plan amendments, 2026-09-19)

Recorded here so the contract remains self-contained; these resolve internal inconsistencies found during build, without changing task facts or ledger values.

- **M-1 — Runbook escalation paths are class-keyed.** §6's original runbook line collapsed the two S-CORE paths implied by the normative ledger (outage→pager, degradation→service-desk; correlation is exact across all 12 S-CORE tasks). Amended contract: `get_runbook` returns `escalation_paths:{<classification>:<path>}` (§0 updated); S-CORE maps {outage:pager, degradation:service-desk}, S-AUTH {security:security}, S-BILL {billing:support}. Validator relation: `task.escalation_path == runbook.escalation_paths[task.classification]`.
- **M-2 — `approval_required` semantics.** Runbook-level `approval_required` is true iff the service has ≥1 approval=yes task (S-CORE true, S-AUTH true, S-BILL false); the per-ticket approval authority is `customer.approval_required` (= matrix value). The original "approval according to customer" note lacked this precision and any strict per-task comparison of the runbook field is invalid for mixed services.
- **M-3 — Board numbering.** Dependabot PR #1 consumed issue number 1 at repo creation: epics are #2–#7, cards B01–B22 are #8–#29. `PLAN.md` §4's predicted numbering assumed a clean start; the GitHub board (labels) is the operational truth. Card issues #8–#29 reference this the same way.

## §10 Integrity remediation (post-review amendment, 2026-09-19)

Source: adversarial integrity review 1 (`docs/reviews/integrity-review-1.md`) ran on commit `8f4f5b0` and returned FIX-FIRST with 2 blockers and 7 major findings. This section freezes the remediation contracts and cards B23–B31 (epic E7). All §10 cards obey the same batch discipline as §3. No existing named test may be deleted or renamed; suite counts grow per card and each batch reports its exact count. §5's fixed expected count is superseded by this section; the operative rules remain "never delete/rename named tests" and "report exact counts".

### Frozen contract amendments

- **A-1 Live serialization.** The OpenAI-compatible client serializes by request `kind`. Agent requests must yield messages conveying: the exact single-JSON-object action protocol (`{"action":"tool","tool":<name>,"arguments":{...}}` or `{"action":"final","output":{...}}`, no prose, no code fences), the tool names and argument names from `skill_lab.tools._TOOL_ARGUMENTS` for the task's `available_tools`, the task call budget (`max_calls`), the optional skill markdown, the task input string, and the recorded tool history as replayed assistant/tool messages; the task envelope's answer fields (`expected_outcome`, `invariants`, `split`) must never appear in any message. Mutation requests must yield the mutation instruction (respond with exactly four string fields: `failure_analysis`, `procedural_change`, `candidate_markdown`, `rationale`; complete SKILL.md inside `candidate_markdown`; no task identifiers or validation/test evidence) plus the exact `current_skill` + `train_failures` envelope content, canonically serialized. Unknown request shapes raise ValueError; emitting empty-content requests is forbidden. `latency_ms` is measured with a monotonic clock and is observed metadata only (never a comparison input; mock stays 0).
- **A-2 Token accounting.** A model response's supplied `total_tokens` must equal `input_tokens + output_tokens` or the run fails loudly (MODEL_ERROR path); the total is never trusted over its components.
- **A-3 Response model metadata.** Every `RunRecord` persists `model_id` (observed response model ids, sorted-unique, joined by `|`; None when unavailable). Provider routing variance must be visible in artifacts.
- **A-4 Result completeness.** `ExperimentResult` carries `runs` (every non-test run executed during evolution, deduped by `run_id`), `prompt_records` (one entry per mutation attempt: `generation`, `candidate_id`, exact `request`, exact `response_content`), and `skill_versions` (every materialized version incl. rejected, in version order). `write_artifact` serializes all of it; `prompts.jsonl` is exact and non-empty for any run with mutations.
- **A-5 Bundle durability.** `evolve` and `baseline` write durable bundles (`config.json` + evidence files + skills + `manifest.json`) before printing `artifact_path`. `held-out --experiment DIR [--runs-per-task N] [--allow-live]` (frozen CLI list §2) evaluates no-skill/seed/final on the bundle's untouched test set and writes an additive `held-out/` subtree without mutating parent files; parent manifests and tree comparisons treat the top-level `held-out/` directory as additive. Before any temporary state is deleted, the harness asserts evidence completeness and refuses to publish otherwise.
- **A-6 Frozen inputs.** Every bundle freezes its task dataset and fixture world under `inputs/` with sha256 digests in `config.inputs`; `rerun` and `held-out` verify all hashes (manifest + `config.inputs`) before any model is created and regenerate from the frozen copies.
- **A-7 Finite metrics.** Non-finite floats are rejected at model construction for every metric/threshold field; the promotion gate additionally treats any non-finite participant metric as INCONCLUSIVE (§8 semantics).
- **A-8 Leakage boundary.** `evolve` accepts training/validation tasks only and raises on any test task; test-split tasks are read exclusively by `run_held_out` / `held-out`, after both ablation branches finish. Poison-boundary tests must cover input, expected_outcome, invariants, trajectories, and store rows, and assert absence from failure packets, mutation prompts/requests/responses, candidate markdown, and persisted mutation rows.
- **A-9 Rerun dispatch.** `rerun` dispatches on `config.artifact_kind` (`ablation` | `evolution`), regenerating the same computation from frozen inputs and byte-comparing trees modulo the additive `held-out/` subtree.

### Remediation cards

#### B23 — Live model serialization for agent and mutation envelopes
Epic: E7
Goal: Make the live OpenAI-compatible path serialize the exact experiment contracts (agent protocol + tool schemas; mutation skill + failures) and measure request latency.
FILE allowlist (2): `src/skill_lab/config.py`; `tests/test_live_client.py`
- Implement §10 A-1. `_request_body` dispatches on `request.get("kind")`: `"agent"` builds agent messages; `"mutation"` builds mutation messages; any other shape without a prebuilt `messages` list raises ValueError (never emit empty content).
- Agent messages: system message with the action protocol (single JSON object; `{"action":"tool","tool":<name>,"arguments":{...}}` or `{"action":"final","output":{...}}`; no prose or code fences), the `available_tools` names with their argument names imported from `skill_lab.tools._TOOL_ARGUMENTS` (no duplication of the table), and the task `max_calls` budget; the skill markdown when present; user message with the task `input` string only; then for each `tool_history` entry an assistant message carrying the reconstructed tool action JSON and a tool message carrying the recorded result JSON, so a stateless replay sees the same history. `expected_outcome`, `invariants`, and `split` must not appear anywhere in the messages.
- Mutation messages: system message with the four-field response instruction (§10 A-1); user message with the canonical (`sort_keys=True`) `current_skill` + `train_failures` envelope content verbatim.
- `complete()`: measure elapsed wall time with `time.monotonic()` around the HTTP request and return `latency_ms` as a non-negative int (rounded). Mock behavior unchanged.
- Tests must construct the client without network (stub transport object for `complete()`; direct `_request_body` calls for serialization); assert identical input dict yields identical messages twice; live tests use a monkeypatched clock to prove latency is measured.
Test spec: hermetic, no network; no changes to mock code.
Named tests: `test_agent_request_messages_include_protocol_tools_and_history`; `test_agent_request_never_leaks_answer_fields`; `test_mutation_request_messages_include_skill_and_failures`; `test_unknown_request_kind_is_rejected`; `test_latency_is_measured_with_monotonic_clock`
Acceptance: `uv run ruff check . && uv run pytest tests/test_live_client.py`
Dependencies: B22
Do not touch: all files other than the allowlist
Evidence bundle: targeted pytest; collect count; ruff; full suite

#### B24 — Token accounting and response model identity
Epic: E7
Goal: Derive-or-reject response totals; persist observed response model id per run; keep storage schema consistent.
FILE allowlist (6): `src/skill_lab/models.py`; `src/skill_lab/agent.py`; `src/skill_lab/experiment.py`; `src/skill_lab/storage.py`; `tests/test_usage_accounting.py`; `tests/test_storage.py`
- Implement §10 A-2: in `_response_values`, when a response supplies `total_tokens` it must equal `input_tokens + output_tokens` (else ValueError, which the agent loop records as MODEL_ERROR); when missing, derive from components.
- Implement §10 A-3: add `model_id: str | None = None` to `RunRecord`; in `_evaluate_slot` populate it from the captured `_Usage` model ids (sorted-unique, joined by `|`; None when empty).
- Storage: add a `model_id TEXT` column to the `runs` table with an idempotent migration for pre-existing database files (add the column at init when missing; keep `CREATE TABLE IF NOT EXISTS` semantics) and include it in `insert_run`.
- Tests: new `tests/test_usage_accounting.py` with `test_total_tokens_must_equal_components` (mismatch rejected; match accepted; missing total derived), `test_evaluate_slot_persists_response_model` (stub usage capture), `test_run_record_model_id_round_trip`; update `tests/test_storage.py` only where its column assertions require the new column.
Test spec: hermetic; no behavior change for consistent totals.
Named tests: `test_total_tokens_must_equal_components`; `test_evaluate_slot_persists_response_model`; `test_run_record_model_id_round_trip`
Acceptance: `uv run ruff check . && uv run pytest tests/test_usage_accounting.py tests/test_storage.py`
Dependencies: B23
Do not touch: all files other than the allowlist
Evidence bundle: targeted pytest; collect count; ruff; full suite

#### B25 — Complete evolution evidence in results; evolve test-task guard
Epic: E7
Goal: Carry every run, exact mutation exchange, and every skill version into the result; `evolve` refuses test tasks; callers pass non-test corpora.
FILE allowlist (4): `src/skill_lab/experiment.py`; `src/skill_lab/cli.py`; `tests/test_evolution.py`; `tests/test_cli_evolve.py`
- Implement §10 A-4: `ExperimentResult` gains `runs: list[RunRecord] = []` (all train- and validation-phase runs, deduped by `run_id`, sorted by `(task_id, skill_version or "", condition_name, run_slot)`), `prompt_records: list[dict] = []` (one entry per mutation attempt: `generation`, `candidate_id`, `request` = exact built mutation prompt, `response_content` = exact response content string or null; ordered by generation), `skill_versions: list[Skill] = []` (seed plus every version materialized under the skills root incl. rejected, deduped, sorted by version number).
- Capture validation-phase runs: extend `evaluate_validation_gate` with the same optional `run_records` out-parameter pattern `run_held_out` uses; parent and candidate validation runs are captured exactly once (dedupe by `run_id`).
- Implement §10 A-8's guard: `evolve` raises ValueError when any supplied task has split TEST ("evolve receives training/validation tasks only; test data is read exclusively by run_held_out"). `ablate` computes the non-test corpus once and passes it to both branches; the CLI `evolve` command passes its own filtered list. Test corpus handling stays in `ablate`/`held-out` paths only.
- Keep existing records/fields and all existing tests' semantics; naive mode collects the same evidence.
Test spec: hermetic mock; rejections and generation-errors remain preserved.
Named tests: `test_experiment_result_carries_all_non_test_runs`; `test_mutation_exchanges_are_captured_verbatim`; `test_skill_lineage_retains_rejected_versions`; `test_evolve_refuses_test_tasks`
Acceptance: `uv run ruff check . && uv run pytest tests/test_evolution.py tests/test_held_out.py tests/test_cli_evolve.py`
Dependencies: B24
Do not touch: all files other than the allowlist
Evidence bundle: targeted pytest; collect count; ruff; full suite

#### B26 — Artifact export completeness and report fixes
Epic: E7
Goal: `write_artifact` serializes everything the result carries; held-out rows stop duplicating; conclusions reflect supplied evidence.
FILE allowlist (3): `src/skill_lab/reporting.py`; `tests/test_reporting.py`; `tests/test_cli_artifacts.py`
- `_run_records`: union `result.runs` + `result.held_out_runs`, dedupe by `run_id`, deterministic order; results.csv gains a `model_id` column (from `RunRecord.model_id`, empty string when None).
- `prompts.jsonl`: emit one canonical line per `prompt_records` entry (exact request + response content); non-empty whenever mutations ran.
- `skills/`: write every version in `result.skill_versions` (plus the final skill if absent), each under `skills/<name>/<version>/` with `SKILL.md`, `metadata.json`, and `RATIONALE.md` when a rationale exists; deterministic order.
- Fix the held-out duplication: report sections read canonical fields only (`train_summaries`, `baseline_summaries`, `held_out_summaries`) — no alias concatenation. Test exact row counts.
- Condition the held-out interpretation bullet on actual held-out presence (observed counts when present; "unavailable" wording only when absent); keep the descriptive-not-causal and mock caveats always.
- Add an observed line: `Observed prompt records: N`.
- Manifest continues to cover all written files (verify by test).
Test spec: hermetic; exact CSV header/row assertions updated deliberately.
Named tests: `test_results_csv_includes_all_runs_once`; `test_prompts_jsonl_contains_exact_exchange`; `test_skill_lineage_versions_written`; `test_held_out_rows_not_duplicated`; `test_conclusion_reflects_supplied_held_out`
Acceptance: `uv run ruff check . && uv run pytest tests/test_reporting.py tests/test_cli_artifacts.py`
Dependencies: B25
Do not touch: all files other than the allowlist
Evidence bundle: targeted pytest; collect count; ruff; full suite

#### B27 — Durable evolve/baseline bundles, evidence assertions, rerun dispatch
Epic: E7
Goal: `evolve`/`baseline` write durable bundles; completeness assertions run before temp cleanup; `rerun` dispatches on artifact kind.
FILE allowlist (4): `src/skill_lab/cli.py`; `src/skill_lab/reporting.py`; `tests/test_cli_bundles.py`; `tests/test_cli_evolve.py`
- Implement §10 A-5: the `evolve` command captures its `ExperimentResult`, then writes a durable bundle: `write_artifact(result, output_path)`, seed-skill copy, bundle `config.json` (`artifact_kind: "evolution"`, `command` {skill, generations, runs_per_task, mode}, `configuration`, `experiment_id`, `branches` {mode: {path, final_version, model_id}}), bundle `manifest.json`. `baseline` writes a durable bundle with `artifact_kind: "baseline"` (config + `results.csv` + `trajectories.jsonl` from collected runs + manifest); collect runs via the existing `_RunCollector` pattern. Add a small public `reporting.write_runs_files(runs, root)` helper (same columns/format as `write_artifact`) reused by `baseline`.
- Ablation bundle config: add `final_version` and `model_id` to each branch entry.
- Completeness assertions in `_run_ablation_artifact` before the temporary directory is deleted: for each branch — every generation record with a `candidate_version` has a matching skill in `skill_versions`; one `prompt_record` per proposed candidate; verified-mode gated candidates have parent+candidate validation runs in `runs`. On failure raise ValueError (refuse to publish) — never silent retry.
- Implement §10 A-9: `rerun` reads `artifact_kind`; `"ablation"` keeps current behavior; `"evolution"` regenerates via a shared `_run_evolution_artifact` helper used by both the `evolve` command and rerun. Tree comparison ignores top-level `held-out/` (documented additive zone).
Test spec: hermetic mock; temp roots; existing evolve CLI tests preserved.
Named tests: `test_evolve_writes_durable_bundle`; `test_baseline_writes_durable_bundle`; `test_bundle_manifest_covers_all_files`; `test_rerun_dispatches_evolution_kind`; `test_ablation_refuses_incomplete_evidence`
Acceptance: `uv run ruff check . && uv run pytest tests/test_cli_bundles.py tests/test_cli_evolve.py`
Dependencies: B26
Do not touch: all files other than the allowlist
Evidence bundle: targeted pytest; collect count; ruff; full suite

#### B28 — Held-out command
Epic: E7
Goal: Implement the frozen `held-out --experiment DIR [--runs-per-task N] [--allow-live]` gate as an additive subtree.
FILE allowlist (2): `src/skill_lab/cli.py`; `tests/test_cli_held_out.py`
- Implement §10 A-5's held-out command: load bundle `config.json` (`artifact_kind` `"ablation"` or `"evolution"`; exit 2 otherwise), verify the manifest and frozen-input hashes BEFORE any model is created (no `create_chat_model` call on failure), honor the live-provider gate, then for each branch load the seed (`skills/<skill>/v001`) and final (`skills/<skill>/<final_version>`) skills and run `run_held_out` over the untouched test set with the requested `--runs-per-task` (default 1).
- Write `DIR/held-out/`: `config.json` (artifact_kind `"held-out"`, parent experiment id, runs_per_task, per-branch {experiment_id, final_version, conditions}, model id), `held-out.json` (per-branch summaries), `results.csv` (per-run rows incl. a `mode` column), `trajectories.jsonl`, `report.md` (observed vs interpretation split; states the test set is used only here), `manifest.json` (own files). Deterministic branch ids derived by hash (no timestamps). Overwrite idempotently; re-running with identical inputs yields a byte-identical subtree (mock).
- Parent files are not modified; parent manifest stays as written (document the additive zone in the docstring).
Test spec: hermetic mock; temp bundles; includes the manifest-verify-before-model-creation spy test and the live-gate test.
Named tests: `test_held_out_command_writes_subtree_for_evolution_bundle`; `test_held_out_command_verifies_before_model_creation`; `test_held_out_command_is_deterministic`; `test_held_out_requires_allow_live_for_live_provider`
Acceptance: `uv run ruff check . && uv run pytest tests/test_cli_held_out.py`
Dependencies: B27
Do not touch: all files other than the allowlist
Evidence bundle: targeted pytest; collect count; ruff; full suite

#### B29 — Frozen scientific inputs in bundles
Epic: E7
Goal: Bundles carry their dataset + fixtures with hashes; rerun/held-out verify before spending and regenerate from frozen copies.
FILE allowlist (3): `src/skill_lab/cli.py`; `tests/test_cli_frozen_inputs.py`; `tests/test_cli_artifacts.py`
- Implement §10 A-6: at bundle write time copy `settings.dataset_path` and `settings.fixtures_path` into `root/inputs/<basename>`; record sha256 + size in bundle `config.inputs`; the manifest walker covers them (verify by test).
- `rerun` and `held-out`: after manifest verification, re-hash both frozen inputs against `config.inputs` and abort (exit 1) BEFORE `create_chat_model` on any mismatch; load tasks and fixtures from the frozen copies (not the live settings paths) for regeneration and held-out evaluation.
- Applies to all bundle kinds written in B27 (ablation root, evolution, baseline) and the held-out subtree path.
Test spec: hermetic mock; tamper test proves no model is created on mismatch.
Named tests: `test_bundle_freezes_inputs_with_hashes`; `test_rerun_aborts_before_model_creation_on_input_tamper`; `test_rerun_uses_frozen_inputs`; `test_held_out_uses_frozen_inputs`
Acceptance: `uv run ruff check . && uv run pytest tests/test_cli_frozen_inputs.py tests/test_cli_artifacts.py`
Dependencies: B28
Do not touch: all files other than the allowlist
Evidence bundle: targeted pytest; collect count; ruff; full suite

#### B30 — Non-finite metric guards
Epic: E7
Goal: Non-finite floats cannot be constructed or evaluated into a promotion decision.
FILE allowlist (3): `src/skill_lab/promotion.py`; `src/skill_lab/metrics.py`; `tests/test_promotion.py`
- Implement §10 A-7: reject non-finite floats at construction for every float field of `EvaluationSummary` (`success_rate`, `regression_rate`, `skill_lift`, `no_skill_success_rate`, `improvement_over_no_skill`, `estimated_cost_usd`) and `PromotionPolicy` (`regression_threshold`, `cost_tolerance`) (e.g. `ConfigDict(allow_inf_nan=False)`; verify it actually rejects).
- `decide_promotion`: defensively treat any non-finite participant metric or threshold (including objects created via `model_construct`) as INCONCLUSIVE before arithmetic; evidence serialization must not crash on non-finite inputs.
Test spec: construction-rejection probes per field; a `model_construct`-bypassed non-finite cost probe returns INCONCLUSIVE, not promote.
Named tests: `test_nonfinite_metrics_rejected_at_construction`; `test_nonfinite_constructed_metrics_are_inconclusive`
Acceptance: `uv run ruff check . && uv run pytest tests/test_promotion.py`
Dependencies: B24
Do not touch: all files other than the allowlist
Evidence bundle: targeted pytest; collect count; ruff; full suite

#### B31 — Poisoned leakage boundary tests
Epic: E7
Goal: Prove the frozen leakage boundary against poisons in every PLAN §5 location, including store rows.
FILE allowlist (3): `tests/test_leakage.py`; `tests/test_held_out.py`; `tests/test_mutation.py`
- Implement §10 A-8's test matrix in a new `tests/test_leakage.py`: inject `POISON-VALIDATION` / `POISON-TEST` into task input, expected_outcome, invariants, trajectory content, and store rows; run the full mock ablation with spy wrappers capturing every mutation request, failure packet, persisted mutation row, and candidate markdown. Assert: no marker appears in any mutation request/prompt record/response/candidate markdown/persisted mutation row; all runs fed to failure selection are train split; markers DO appear in later held-out-phase requests (injection proof).
- `test_test_split_unreachable_before_held_out_phase`: wrap `run_held_out` to timestamp the phase and assert no request carrying a test-split task is issued before it.
- `test_evolve_guard_rejects_test_tasks`: direct `evolve` call with a test task raises ValueError and issues no model calls.
- Strengthen `tests/test_held_out.py`'s boundary test internals (same test names): poison expected_outcome/invariants/store rows, not only input.
Test spec: hermetic mock only; deterministic.
Named tests: `test_poison_never_reaches_mutation_boundary`; `test_test_split_unreachable_before_held_out_phase`; `test_evolve_guard_rejects_test_tasks`; `test_stored_rows_do_not_feed_mutation`
Acceptance: `uv run ruff check . && uv run pytest tests/test_leakage.py tests/test_held_out.py tests/test_mutation.py`
Dependencies: B30
Do not touch: all files other than the allowlist
Evidence bundle: targeted pytest; collect count; ruff; full suite

### Post-chain operator steps (not cards)

After B31: regenerate `artifacts/example-mock` from the fixed code (`generate-example`), run the §5 acceptance block on the regenerated tree, commit, then run integrity review 2 on the new commit before any live spend. The regenerated example must show non-empty `prompts.jsonl`, all lineage versions, frozen `inputs/`, and the byte-identical rerun contract intact.

## §10.1 Review-2 residuals (2026-09-19)

Integrity review 2 (`docs/reviews/integrity-review-2.md`, run on `8475a0a`) returned FIX-FIRST with three residual majors and no blockers; findings 2, 3, 5, 6, 7, 9 were verified fixed, findings 1, 4, 8 partially fixed. Additional frozen clarifications, then cards B32–B33 (epic E7):

- **A-1a Kind semantics.** The kindless prebuilt-messages path remains the only compatibility route: `kind is None` with a valid non-empty `messages` list is accepted; an explicitly present kind other than `agent`/`mutation` raises ValueError even when messages are supplied.
- **A-3a Observed model strictness.** The observed `model_id` is never substituted: the live client reports the provider's `model` field or None (no configured-model fallback), and the usage collector reads only the response value (no `model.model_id`/`config.model` fallbacks). `ModelResponse.model` is `str | None`.
- **A-2a Error-path accounting.** When a response is rejected for an inconsistent `total_tokens`, the call is still accounted before the run fails: steps, tokens (derived from components, never the supplied total), and latency are recorded, the assistant message is retained, and the run outcome is MODEL_ERROR. Best-effort salvage applies to responses whose token fields are invalid. Invariant: every persisted run satisfies `total_tokens == input_tokens + output_tokens`.

#### B32 — Strict live dispatch and observed-model honesty
Epic: E7
Goal: Close review-2 findings 1–2: no unknown-kind bypass; never substitute the requested model for an absent observed one.
FILE allowlist (3): `src/skill_lab/config.py`; `src/skill_lab/mock_model.py`; `tests/test_live_client.py`
- Implement §10.1 A-1a: `_request_body` accepts the kindless prebuilt-messages path only when `kind` is absent; an explicit unknown kind raises ValueError regardless of `messages` presence. (Review repro: `{"kind":"bogus","messages":[...]}` must raise; `{"kind":None,"messages":[...]}` may pass; empty messages keep raising.)
- Implement §10.1 A-3a client side: `ModelResponse.model` becomes `str | None` (default None); `complete()` records the provider payload's non-empty `model` string or None — never `self.config.model`. The mock keeps setting its explicit id in every response; adjust constructors/tests that relied on the old default deliberately (never weaken the no-substitution rule).
- Tests extend `tests/test_live_client.py`: `test_unknown_kind_raises_even_with_messages`; `test_kindless_prebuilt_messages_still_supported`; `test_absent_provider_model_is_none_not_requested`.
Test spec: hermetic stub transports; no network.
Named tests: `test_unknown_kind_raises_even_with_messages`; `test_kindless_prebuilt_messages_still_supported`; `test_absent_provider_model_is_none_not_requested`
Acceptance: `uv run ruff check . && uv run pytest tests/test_live_client.py tests/test_mock_model.py`
Dependencies: B31
Do not touch: all files other than the allowlist
Evidence bundle: targeted pytest; collect count; ruff; full suite

#### B33 — Collected-model strictness and error-path accounting
Epic: E7
Goal: Close review-2 findings 2 (collector) and 3: usage collector never substitutes; rejected responses stay consistently accounted with latency.
FILE allowlist (3): `src/skill_lab/agent.py`; `src/skill_lab/experiment.py`; `tests/test_usage_accounting.py`
- Implement §10.1 A-3a collector side: `_usage_from_response` reads only the response `model` value (non-empty string else None); remove the `model.model_id` and `config.model` fallback branches.
- Implement §10.1 A-2a: in `run_agent`, a `total_tokens` mismatch is detected via an internal lenient extractor (the existing `_response_values` keeps its raising contract for direct callers). On mismatch: account the call (steps += 1; trajectory totals += input+output from components; generated += output; latency += response latency), retain the assistant message, set outcome MODEL_ERROR, and stop. On extraction exceptions, salvage the response's numerically valid token/latency fields (0 for invalid, matching the usage wrapper's tolerance) before failing, so the aggregate invariant holds on every path.
- Tests extend `tests/test_usage_accounting.py`: `test_rejected_total_mismatch_run_accounts_call` (in=2, out=3, total=99, latency=4 → MODEL_ERROR with trajectory tokens 5 and latency 4; persisted-record aggregation satisfies total == input+output); `test_error_path_records_stay_consistent` (invalid total field / structural failure → consistent record). Keep `test_total_tokens_must_equal_components` semantics for the direct extractor; update any test that relied on the removed fallbacks to use an explicit response model id.
Test spec: hermetic; mock behaviors unchanged on normal paths.
Named tests: `test_rejected_total_mismatch_run_accounts_call`; `test_error_path_records_stay_consistent`
Acceptance: `uv run ruff check . && uv run pytest tests/test_usage_accounting.py tests/test_agent.py`
Dependencies: B32
Do not touch: all files other than the allowlist
Evidence bundle: targeted pytest; collect count; ruff; full suite

Post-B33: full acceptance block + committed example regeneration if contents change, then integrity review 3 (scoped to the three residuals + regression) gates the live spend.

## §10.2 Live-provider body extensions (operator amendment, 2026-09-19)

Finding from the first live connectivity smoke (stage ① of the operator run, `~/scratch/skilllab-live/smoke-verified-1`): DeepSeek V4.1 Flash defaults to **thinking mode ON** when `thinking` is unset; the thinking budget is billed as output tokens and consumed the entire `max_tokens` window (e.g. 3,927 of 4,000 completion tokens on the mutation call, `finish_reason: length`, truncated/empty content) and let agent responses drift into role-played continuations that fail the strict JSON contract. A raw-endpoint probe confirmed the fix: top-level `"thinking": {"type": "disabled"}` yields `reasoning 0`, exact JSON content, 5 completion tokens.

The live client therefore gains an operator-supplied **request-body extension** mechanism (`config.model.extra_body`), merged verbatim into every chat-completions body it sends. Fixed contract fields (`model`, `messages`, `temperature`, `max_tokens`) cannot be overridden — a collision raises. The live operator config sets `extra_body: {"thinking": {"type": "disabled"}}` so the experiment measures task capability, not thinking-budget truncation. This is part of the fixed model environment and is recorded in every bundle configuration; it applies identically to all conditions.

Also recorded for operators: on this box the DeepSeek key resolves via the local password store (`pass show hermes/deepseek/api-key`), not the raw `.env` line (which holds a `$(pass …)` command reference that the lab's live client must not receive literally).

#### B34 — Live request-body extensions (operator provider knobs)
Epic: E7
Goal: Add a contract-safe `extra_body` passthrough so operators can configure provider-specific body fields (e.g. DeepSeek thinking control) for live runs.
FILE allowlist (4): `src/skill_lab/config.py`; `configs/default.json`; `configs/live-deepseek.json`; `tests/test_live_client.py`
- Implement §10.2: the model config gains `extra_body: dict | None = None` (JSON object or null); `DEFAULT_CONFIG` and `configs/default.json` include `"extra_body": null`; `_request_body` merges a non-null `extra_body` into the outgoing body and raises ValueError if it collides with any fixed contract field (`model`, `messages`, `temperature`, `max_tokens`) or is not an object.
- Update `configs/live-deepseek.json`: add `"extra_body": {"thinking": {"type": "disabled"}}`.
- Tests extend `tests/test_live_client.py`: `test_extra_body_merged_into_request_body`; `test_extra_body_contract_collision_rejected`; `test_extra_body_absent_leaves_body_unchanged`.
Test spec: hermetic; no network; existing serialization assertions preserved.
Named tests: `test_extra_body_merged_into_request_body`; `test_extra_body_contract_collision_rejected`; `test_extra_body_absent_leaves_body_unchanged`
Acceptance: `uv run ruff check . && uv run pytest tests/test_live_client.py tests/test_config.py`
Dependencies: B33
Do not touch: all files other than the allowlist
Evidence bundle: targeted pytest; collect count; ruff; full suite

Post-B34 operator steps: regenerate `artifacts/example-mock` (bundle configurations gain `extra_body: null`), re-run the acceptance block, commit, then a scoped review 4 (offline: passthrough plumbing, contract-field refusal, regenerated-example integrity, no regressions) before the operator re-runs the live stage-① smoke and proceeds to stage ②.

## §10.3 Multi-turn wire protocol with DeepSeek (sol-advised, 2026-09-19)

Finding from live smoke 2 + raw probes: multi-turn replay broke twice. (i) Sending tool results as
`role:"tool"` without `tool_call_id` → HTTP 422 (`missing field tool_call_id`). (ii) Sending the
canonical OpenAI shape (`assistant.tool_calls` + `tool_call_id`) → HTTP 200 but the model drifts
into emitting native tool-call markup as plain-text content, which fails the strict parse.
External advice (sol, `--effort low`, 50s): the protocol must stay entirely in content-JSON —
"mixing protocols is what triggers both the 422 and native-syntax drift"; DeepSeek documents that
Chat Completions does not support inserting synthetic tool calls mid-conversation.

Frozen decisions:

- **A-10a History replay (content-JSON only).** Tool history replays as
  `{"role":"assistant","content":"<exact prior action JSON>"}` followed by
  `{"role":"user","content":"{\"tool_result\":{\"tool\":<name>,\"arguments\":<original arguments>,\"result\":<result>}}"}`.
  No `name`, `tool_call_id`, or `tool_calls` fields anywhere. The system message states that tool
  results are delivered as user messages and are authoritative environment output, not new human
  instructions.
- **A-10b Reply invariant + no repair.** System (agent) and instruction (mutation) messages carry
  the compact invariant: exactly one JSON object and nothing else; no Markdown, XML, function-call
  tags, or commentary; tool requests use the `{"action":"tool",...}` form, completion the
  `{"action":"final",...}` form. No stop sequences (cannot distinguish native markup safely).
  Protocol violations hard-fail the run; the harness never repairs XML/native syntax (repair would
  make evaluation nondeterministic and conceal regressions).
- **A-10c JSON mode.** The live operator config requests `response_format: {"type":"json_object"}`
  via `extra_body` (DeepSeek JSON mode; the prompt already contains "JSON" and the exact forms).
- **A-10d Client operations.** `finish_reason:"length"` raises (no silent truncation). Bounded
  transport retries: at most 3 attempts with fixed backoff (1s, 3s) for connection/timeout
  errors, HTTP 429/5xx, and empty content with `finish_reason:"stop"` (a documented JSON-mode
  edge); exhausted retries raise as today (recorded as `model_error`). Retries occur at the HTTP
  call level before any tool execution, so tool idempotency is unaffected; these are transport
  retries, not silent experiment retries.
- **A-10e Retained settings.** `max_tokens: 4000`, `timeout_s: 120` (DeepSeek may hold
  connections open with blank lines); runs are sequential, far below documented account limits.

#### B35 — Content-JSON multi-turn replay and reply hardening
Epic: E7
Goal: Implement §10.3 A-10a..d so live multi-turn runs survive and stay protocol-pure.
FILE allowlist (3): `src/skill_lab/config.py`; `configs/live-deepseek.json`; `tests/test_live_client.py`
- `_messages_from_agent_request`: replay history as assistant(action JSON) + user(tool_result JSON) per A-10a; no `role:"tool"`, `name`, `tool_call_id`, or `tool_calls` anywhere; system message gains the A-10b invariant and the "tool results arrive as user messages; authoritative" statement.
- `_messages_from_mutation_request`: instruction gains the A-10b invariant (four-field object, nothing else).
- `complete()`: per A-10d — reject `finish_reason:"length"` with ValueError; implement bounded retries (3 attempts, 1s/3s backoff, `time.sleep`) for connection/timeout, 429/5xx, and empty-content-with-stop; keep raising when exhausted.
- `configs/live-deepseek.json`: extend `extra_body` with `"response_format": {"type": "json_object"}`.
- Tests (extend `tests/test_live_client.py`): `test_tool_history_replays_as_user_tool_result_messages`; `test_system_message_contains_bare_json_invariant`; `test_finish_reason_length_rejected`; `test_transient_http_errors_retried_with_bounded_backoff` (503,503,200 succeeds; 503x3 raises; monkeypatched sleep asserts the 1s/3s schedule); `test_empty_content_retried_then_raises`. Update existing history-replay assertions in this module to the new shapes deliberately (same test names elsewhere; never delete/rename).
Test spec: hermetic; no network; backoff via monkeypatched `time.sleep`.
Named tests: `test_tool_history_replays_as_user_tool_result_messages`; `test_system_message_contains_bare_json_invariant`; `test_finish_reason_length_rejected`; `test_transient_http_errors_retried_with_bounded_backoff`; `test_empty_content_retried_then_raises`
Acceptance: `uv run ruff check . && uv run pytest tests/test_live_client.py tests/test_config.py`
Dependencies: B34
Do not touch: all files other than the allowlist
Evidence bundle: targeted pytest; collect count; ruff; full suite

Post-B35 operator steps: full gate + acceptance (mock artifacts must stay byte-identical; no example regeneration expected), scoped review 5 (replay shapes, retry bounds/backoff, length rejection, JSON-mode config, no-regression), then operator live smoke 3 before stage ②.

## §10.4 Retry latency accounting (review-5 fix, 2026-09-19)

Review 5 (`docs/reviews/integrity-review-5.md`) passed every §10.3 protocol check but found one
major: `complete()` resets `started_at` inside the retry loop, so a call that succeeds after
retries records only the final attempt's latency (observed 4,000 ms vs 9,000 ms attempt-time /
16,000 ms wall span), systematically low precisely when retries fire — weakening the
latency/cost-of-improvement measurement.

Frozen semantics:

- **A-10f Latency span.** `latency_ms` measures wall time from the FIRST attempt's start to the
  successful response decode, including every retry attempt and all inter-attempt backoff
  sleep. Failures after exhausted retries raise (latency not persisted, as before). This is the
  full cost of obtaining the response; retries are rare, so the span is the honest total.

#### B36 — Retry latency span
Epic: E7
Goal: Record the full retry span in `latency_ms` per §10.4 A-10f.
FILE allowlist (2): `src/skill_lab/config.py`; `tests/test_live_client.py`
- `complete()`: time from the first attempt start to the successful response decode (single `started_at` before the attempt loop; compute once on success). No behavior change on the no-retry path.
- Tests extend `tests/test_live_client.py`: `test_retry_latency_includes_all_attempts_and_backoff` — reproduce review 5's shape (503,503,200; attempt durations 2s/3s/4s; no-op sleeps with monkeypatched clocks) and assert `latency_ms` equals the full span (16,000 ms in that construction), not the final attempt (4,000 ms); plus an assertion that the no-retry path still reports a single-attempt span.
Test spec: hermetic; monkeypatched clocks/sleeps; no network.
Named tests: `test_retry_latency_includes_all_attempts_and_backoff`
Acceptance: `uv run ruff check . && uv run pytest tests/test_live_client.py`
Dependencies: B35
Do not touch: all files other than the allowlist
Evidence bundle: targeted pytest; collect count; ruff; full suite

Post-B36 operator steps: gate + acceptance (mock rerun byte-identical), review 6 (scoped: reproduce finding 1 exactly → PASS expected, suite, no regression), then operator live smoke 3.

