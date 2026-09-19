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

