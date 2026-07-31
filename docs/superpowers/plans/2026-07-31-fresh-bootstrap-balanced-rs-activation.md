# Fresh Bootstrap Balanced RS Activation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a genuinely empty live database finish runtime bootstrap with `balanced-horizon-percentile-v2` active, while preserving the explicit guarded rollout for every non-empty database.

**Architecture:** Classify whether the installation is pristine once, before bootstrap dispatch, and persist that decision in the bootstrap run manifest. The classifier ignores schema/provisioning metadata but treats any universe, price, fundamental, Scan, Feature, Group-rank, or Market-RS row as existing data. Fresh-install market plans replace the current balanced shadow calculation with a required task that runs bounded backfill, feature publication, static parity validation, and atomic pointer activation; subsequent Group, Scan, and Group-history stages therefore resolve the balanced formula naturally. The fresh marker survives interrupted activation and is consumed once all enabled Market pointers are balanced. Non-empty installations retain the current legacy pointer and manual guarded command.

**Tech Stack:** Python 3.11+, FastAPI, Celery chains, SQLAlchemy, PostgreSQL/SQLite test fixtures, pytest, Docker Compose.

**Tracking:** `stockscreenclaude-z17`

## Global Constraints

- A fresh installation is determined once at bootstrap dispatch by `BootstrapReadinessService.is_pristine_installation()` and must not be reclassified after universe or price data arrives.
- `is_pristine_installation()` must return false for inactive universe rows and derived/user records even when `is_empty_system()` still considers the runtime empty.
- Automatic activation applies only to an unfinished bootstrap run captured as fresh. The marker is consumed after all enabled Markets activate, so non-empty databases, later repair bootstraps, and explicit rollbacks retain operator control.
- Do not initialize a formula pointer directly to balanced before a completed canonical Market RS run, matching Group snapshot, and published Feature run exist.
- A fresh primary market must not become runtime-ready unless its active formula pointer is `balanced-horizon-percentile-v2`.
- Backfill and activation failures must leave both the Market RS and Feature pointers unchanged and fail the owning bootstrap market chain.
- Guarded activation backfills and validates only the 187-calendar-day window needed for 6M Group changes, 12-week RRG tails, and the latest daily Feature/scan publication.
- Reuse the existing six-month Group-history service and its explicit current-universe fallback policy; do not backdate or fabricate universe lifecycle events.
- Preserve legacy Market RS/Group rows for rollback and do not modify prices, watchlists, user scans, themes, or runtime market choices outside existing bootstrap behavior.
- Keep the existing `python -m app.scripts.backfill_market_rs` interface and validation semantics for operators.
- No frontend or public API contract changes are included.

---

## File Structure

### New files

- `backend/app/services/market_rs_rollout_executor.py`
  - Owns the strict guarded activation workflow; shadow backfill remains a direct rollout-service operation.
- `backend/app/services/market_rs_activation_coverage.py`
  - Defines the bounded activation history window shared by backfill and validation.
- `backend/app/services/fresh_balanced_rs_bootstrap_lifecycle.py`
  - Consumes fresh-install identity once every enabled Market formula pointer is balanced.
- `backend/app/wiring/market_rs_activation.py`
  - Hosts task, static-export, and live-publication adapters outside the service layer.
- `backend/tests/unit/test_market_rs_rollout_executor.py`
  - Verifies orchestration order, rejection behavior, pointer safety, and typed outcomes.

### Modified files

- `backend/app/services/bootstrap_run_manifest.py`
  - Persists the immutable `fresh_install` decision for one bootstrap dispatch.
- `backend/app/domain/bootstrap/plan.py`
  - Selects required balanced activation instead of the shadow calculation for fresh runs.
- `backend/app/tasks/runtime_bootstrap_tasks.py`
  - Captures emptiness before dispatch, maps the new operation, and enforces balanced readiness at completion.
- `backend/app/tasks/market_rs_tasks.py`
  - Exposes the strict Celery entry point for fresh-bootstrap activation.
- `backend/app/scripts/backfill_market_rs.py`
  - Becomes a thin argument/directory adapter around the shared rollout executor.
- `backend/app/wiring/bootstrap.py`
  - Constructs and exposes the rollout executor through the existing runtime-services container.
- `backend/app/celery_app.py`
  - Adds the new exact task name to `_MARKET_JOB_TASKS`.
- `backend/app/services/bootstrap_readiness_service.py`
  - Adds strict pristine-installation classification and an optional expected-formula readiness check without changing legacy readiness defaults.
- `backend/tests/unit/test_bootstrap_run_manifest.py`
  - Covers manifest compatibility and fresh-install persistence.
- `backend/tests/unit/domain/test_bootstrap_plan.py`
  - Covers fresh and non-empty operation selection and ordering.
- `backend/tests/unit/test_runtime_bootstrap_tasks.py`
  - Covers dispatch classification, Celery mapping, and completion failure on pointer mismatch.
- `backend/tests/unit/test_market_rs_tasks.py`
  - Covers strict task success, failure, retry re-entry, and activity reporting.
- `backend/tests/unit/test_backfill_market_rs_script.py`
  - Confirms the CLI delegates without losing directory and exit-code safeguards.
- `backend/tests/integration/test_market_rs_activation.py`
  - Adds fresh-bootstrap pointer and exact publication identity coverage.
- `backend/tests/integration/test_live_group_history_bootstrap.py`
  - Proves Group history targets balanced immediately after fresh activation.
- `docs/OPERATIONS.md`
  - Separates automatic fresh activation from explicit existing-database rollout and rollback.

---

### Task 1: Persist the Fresh-Install Dispatch Decision

**Files:**
- Modify: `backend/app/services/bootstrap_run_manifest.py:36-113`
- Modify: `backend/app/services/bootstrap_readiness_service.py:46-56`
- Modify: `backend/app/tasks/runtime_bootstrap_tasks.py:82-158,325-463`
- Test: `backend/tests/unit/test_bootstrap_run_manifest.py`
- Test: `backend/tests/unit/test_bootstrap_readiness_service.py`
- Test: `backend/tests/unit/test_runtime_bootstrap_tasks.py`

**Interfaces:**
- Produces: `BootstrapRunManifest.fresh_install: bool`
- Produces: `BootstrapQueueManifestRecorder.fresh_install: bool`
- Produces: `_is_fresh_install_at_dispatch() -> bool`
- Produces: `BootstrapReadinessService.is_pristine_installation(db: Session) -> bool`

- [ ] **Step 1: Write manifest compatibility tests**

Add tests proving new payloads round-trip the decision and old payloads default safely to `False`:

```python
def test_bootstrap_manifest_round_trips_fresh_install() -> None:
    manifest = BootstrapRunManifest.create(
        primary_market="US",
        enabled_markets=("US", "HK"),
        fresh_install=True,
    )

    assert manifest.to_payload()["fresh_install"] is True
    assert BootstrapRunManifest.from_payload(manifest.to_payload()).fresh_install is True


def test_bootstrap_manifest_treats_legacy_payload_as_non_fresh() -> None:
    manifest = BootstrapRunManifest.from_payload(
        {"primary_market": "US", "enabled_markets": ["US"]}
    )

    assert manifest.fresh_install is False
```

- [ ] **Step 2: Run the manifest tests and verify they fail**

Run:

```bash
cd backend && source venv/bin/activate
pytest tests/unit/test_bootstrap_run_manifest.py -q
```

Expected: FAIL because `fresh_install` is not accepted or serialized.

- [ ] **Step 3: Add the backward-compatible manifest field**

Implement the field and payload conversion:

```python
@dataclass(frozen=True)
class BootstrapRunManifest:
    primary_market: str
    enabled_markets: tuple[str, ...]
    fresh_install: bool = False
    primary_task_id: str | None = None
    market_task_ids: Mapping[str, str | None] = field(default_factory=dict)
    queue_state: BootstrapQueueState | str = BootstrapQueueState.QUEUED
    queued_at: str | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "BootstrapRunManifest":
        return cls(
            primary_market=str(payload["primary_market"]),
            enabled_markets=tuple(payload.get("enabled_markets") or ()),
            fresh_install=payload.get("fresh_install") is True,
            primary_task_id=(
                str(payload["primary_task_id"])
                if payload.get("primary_task_id") is not None
                else None
            ),
            market_task_ids=dict(payload.get("market_task_ids") or {}),
            queue_state=BootstrapQueueState.parse(
                payload.get("queue_state") or BootstrapQueueState.QUEUED
            ),
            queued_at=(
                str(payload["queued_at"])
                if payload.get("queued_at") is not None
                else None
            ),
        )
```

Include `fresh_install` in `create()` and `to_payload()` even when false so current manifests are explicit.

- [ ] **Step 4: Write strict pristine-installation tests**

In `test_bootstrap_readiness_service.py`, prove that schema/provisioning metadata is allowed but any durable data prevents automatic activation:

```python
def test_pristine_installation_ignores_formula_pointer_provisioning(readiness_db):
    readiness_db.add(
        MarketRsFormulaPointer(
            market="US",
            formula_version=LEGACY_RS_FORMULA_VERSION,
        )
    )
    readiness_db.commit()

    assert BootstrapReadinessService().is_pristine_installation(readiness_db) is True


def test_pristine_installation_rejects_inactive_universe_rows(readiness_db):
    readiness_db.add(
        StockUniverse(
            symbol="OLD",
            market="US",
            exchange="NYSE",
            is_active=False,
            status="inactive_manual",
        )
    )
    readiness_db.commit()

    service = BootstrapReadinessService()
    assert service.is_empty_system(readiness_db) is True
    assert service.is_pristine_installation(readiness_db) is False
```

Parameterize equivalent non-pristine cases for one `StockPrice`, `StockFundamental`, `Scan`, `FeatureRun`, `IBDGroupRank`, and `MarketRsRun` row. Use the existing model constructors and the smallest valid row for each table.

- [ ] **Step 5: Run pristine-installation tests and verify they fail**

Run:

```bash
cd backend && source venv/bin/activate
pytest tests/unit/test_bootstrap_readiness_service.py -q
```

Expected: FAIL because `is_pristine_installation()` does not exist.

- [ ] **Step 6: Implement strict pristine classification**

Add a public, side-effect-free method that checks table existence through ORM queries and deliberately ignores AppSettings and formula-pointer provisioning:

```python
def is_pristine_installation(self, db: Session) -> bool:
    persisted_queries = (
        db.query(StockUniverse.id),
        db.query(StockPrice.id),
        db.query(StockFundamental.id),
        db.query(Scan.id),
        db.query(FeatureRun.id),
        db.query(IBDGroupRank.id),
        db.query(MarketRsRun.id),
    )
    return not any(query.limit(1).first() is not None for query in persisted_queries)
```

Import `IBDGroupRank` and `MarketRsRun` from their canonical model modules. Do not implement this in terms of `is_empty_system()`, whose active-core semantics intentionally differ.

- [ ] **Step 7: Write dispatch-classification tests**

In `test_runtime_bootstrap_tasks.py`, prove classification happens before task dispatch and is copied through every manifest state:

```python
def test_queue_bootstrap_captures_pristine_installation_once(monkeypatch):
    monkeypatch.setattr(module, "_is_fresh_install_at_dispatch", lambda: True)
    saved = []
    monkeypatch.setattr(
        module,
        "record_runtime_bootstrap_run",
        lambda **payload: saved.append(payload) or payload,
    )
    monkeypatch.setattr(module, "_queue_market_bootstrap_workflow", _successful_queue)

    module.queue_local_runtime_bootstrap(
        primary_market="US",
        enabled_markets=("US", "HK"),
    )

    assert saved
    assert {record["fresh_install"] for record in saved} == {True}
```

Add the inverse test with `False` and assert no later database read can change the recorder's value.

- [ ] **Step 8: Implement dispatch classification**

Add a focused helper that opens and closes its own session:

```python
def _is_fresh_install_at_dispatch() -> bool:
    from app.services.bootstrap_readiness_service import BootstrapReadinessService

    db = SessionLocal()
    try:
        manifest = BootstrapRunManifestRepository().load(db)
        if manifest is not None and manifest.fresh_install:
            return True
        return BootstrapReadinessService().is_pristine_installation(db)
    finally:
        db.close()
```

An existing `fresh_install=true` manifest is sticky: a retry after universe or price hydration must remain on the guarded activation path even though the database is no longer pristine. Capture the decision before `build_bootstrap_plan()`, store it on `BootstrapQueueManifestRecorder`, and forward it through `record_runtime_bootstrap_run()` and every `BootstrapRunManifest.create()` call.

- [ ] **Step 9: Run focused tests**

Run:

```bash
cd backend && source venv/bin/activate
pytest tests/unit/test_bootstrap_run_manifest.py \
  tests/unit/test_bootstrap_readiness_service.py \
  tests/unit/test_runtime_bootstrap_tasks.py -q
```

Expected: PASS.

- [ ] **Step 10: Commit the dispatch identity**

```bash
git add backend/app/services/bootstrap_run_manifest.py \
  backend/app/services/bootstrap_readiness_service.py \
  backend/app/tasks/runtime_bootstrap_tasks.py \
  backend/tests/unit/test_bootstrap_run_manifest.py \
  backend/tests/unit/test_bootstrap_readiness_service.py \
  backend/tests/unit/test_runtime_bootstrap_tasks.py
git commit -m "feat: persist fresh bootstrap identity"
```

---

### Task 2: Extract the Guarded Rollout Executor

**Files:**
- Create: `backend/app/services/market_rs_rollout_executor.py`
- Modify: `backend/app/scripts/backfill_market_rs.py:1-193`
- Modify: `backend/app/wiring/bootstrap.py`
- Create: `backend/tests/unit/test_market_rs_rollout_executor.py`
- Modify: `backend/tests/unit/test_backfill_market_rs_script.py`

**Interfaces:**
- Consumes: `MarketRsRolloutService.backfill()`, `validate_activation()`, and `activate()`
- Produces: `MarketRsRolloutRequest`
- Produces: `MarketRsRolloutOutcome`
- Produces: `MarketRsRolloutExecutionError`
- Produces: `MarketRsRolloutExecutor.execute(db: Session, request: MarketRsRolloutRequest) -> MarketRsRolloutOutcome`
- Produces: `get_market_rs_rollout_executor() -> MarketRsRolloutExecutor`

- [ ] **Step 1: Write executor orchestration tests**

Create tests for shadow-only execution, successful activation order, and failed backfill:

```python
def test_executor_activates_only_after_all_publication_gates(tmp_path):
    events: list[str] = []
    rollout = MagicMock()
    rollout.backfill.side_effect = lambda *a, **k: events.append("backfill") or _ok_report()
    rollout.validate_activation.side_effect = (
        lambda *a, **k: events.append("validate") or _ok_validation()
    )
    rollout.activate.side_effect = lambda *a, **k: events.append("activate")
    executor = MarketRsRolloutExecutor(
        rollout_service=rollout,
        feature_snapshot_builder=lambda **k: events.append("feature") or 99,
        static_exporter=lambda **k: events.append("static"),
        live_group_publisher=lambda market: events.append("publish_live"),
    )

    outcome = executor.execute(
        MagicMock(),
        request=MarketRsRolloutRequest(
            market="US",
            through_date=date(2026, 7, 29),
            activate=True,
            static_staging_dir=tmp_path,
        ),
    )

    assert outcome.activated is True
    assert events == [
        "backfill",
        "feature",
        "static",
        "validate",
        "activate",
        "publish_live",
    ]
```

Also assert a failed report raises `MarketRsRolloutExecutionError` before Feature/static work and that `activate=False` never calls validation or pointer mutation.

- [ ] **Step 2: Run executor tests and verify they fail**

Run:

```bash
cd backend && source venv/bin/activate
pytest tests/unit/test_market_rs_rollout_executor.py -q
```

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement typed request and outcome contracts**

Use immutable dataclasses and preserve the CLI's existing serialized staging path:

```python
@dataclass(frozen=True)
class MarketRsRolloutRequest:
    market: str
    through_date: date
    start_date: date | None = None
    activate: bool = False
    static_staging_dir: Path | None = None


@dataclass(frozen=True)
class MarketRsRolloutOutcome:
    backfill: dict[str, Any]
    activated: bool
    market: str
    formula_version: str
    feature_run_id: int | None = None
    validation: dict[str, Any] | None = None
    static_staging_dir: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "backfill": dict(self.backfill),
            "activated": self.activated,
        }
        if self.activated:
            payload.update(
                market=self.market,
                formula_version=self.formula_version,
                feature_run_id=self.feature_run_id,
                validation=dict(self.validation or {}),
                static_staging_dir=self.static_staging_dir,
            )
        return payload
```

Normalize `market` through the existing rollout normalization helper and reject activation without an absolute, empty, non-serving staging directory before starting backfill.

- [ ] **Step 4: Move orchestration from the CLI into the executor**

Implement this exact order:

```python
report = self.rollout_service.backfill(
    db,
    market=market,
    through_date=request.through_date,
    start_date=request.start_date,
)
if not request.activate:
    return MarketRsRolloutOutcome(
        backfill=report.to_dict(),
        activated=False,
        market=market,
        formula_version=BALANCED_RS_FORMULA_VERSION,
    )
if not report.ok or report.failed_count:
    raise MarketRsRolloutExecutionError(
        "One or more required backfill dates failed; repair the reported dates before activation"
    )

feature_run_id = self.feature_snapshot_builder(
    market=market,
    through_date=request.through_date,
)
self.static_exporter(
    market=market,
    feature_run_id=feature_run_id,
    static_staging_dir=staging_dir,
)
db.expire_all()
validation = self.rollout_service.validate_activation(
    db,
    market=market,
    through_date=request.through_date,
    feature_run_id=feature_run_id,
    static_staging_dir=staging_dir,
)
if not validation.ok:
    raise MarketRsRolloutExecutionError(
        "Activation validation failed: " + "; ".join(validation.errors)
    )
self.rollout_service.activate(
    db,
    market=market,
    formula_version=BALANCED_RS_FORMULA_VERSION,
    feature_run_id=feature_run_id,
    validation=validation,
    static_staging_dir=staging_dir,
)
self.live_group_publisher(market)
```

Move the existing `_build_balanced_feature_snapshot`, `_export_static_v3`, and `_publish_live_groups` implementations into `market_rs_rollout_executor.py` as module-level production adapters named `build_balanced_feature_snapshot`, `export_static_v3`, and `publish_live_groups`. Inject those three functions when `RuntimeServices` constructs `MarketRsRolloutExecutor`; keep their imports local inside each adapter to avoid adding eager task/static-export imports to bootstrap wiring.

- [ ] **Step 5: Wire one runtime executor instance**

Add a lazy `market_rs_rollout_executor` property and getter to `RuntimeServices`, constructed from the existing `market_rs_rollout_service`. Extend `reset_for_tests()` coverage so no executor leaks between tests.

- [ ] **Step 6: Reduce the CLI to parsing, session ownership, and output**

Move the existing path policy into `validate_static_staging_directory(path: Path | None) -> Path` in `market_rs_rollout_executor.py`. `execute_rollout()` must call that function while constructing `MarketRsRolloutRequest`, delegate once, convert `MarketRsRolloutExecutionError` to `RolloutCommandFailed`, and preserve existing JSON and exit codes. Delete the CLI's private duplicate `_validate_staging_directory()`.

- [ ] **Step 7: Run executor and CLI tests**

Run:

```bash
cd backend && source venv/bin/activate
pytest tests/unit/test_market_rs_rollout_executor.py \
  tests/unit/test_backfill_market_rs_script.py \
  tests/unit/test_market_rs_rollout_service.py \
  tests/unit/test_runtime_services.py -q
```

Expected: PASS with the original CLI activation order and safeguards unchanged.

- [ ] **Step 8: Commit the shared executor**

```bash
git add backend/app/services/market_rs_rollout_executor.py \
  backend/app/scripts/backfill_market_rs.py \
  backend/app/wiring/bootstrap.py \
  backend/tests/unit/test_market_rs_rollout_executor.py \
  backend/tests/unit/test_backfill_market_rs_script.py \
  backend/tests/unit/test_runtime_services.py
git commit -m "refactor: share guarded market rs rollout"
```

---

### Task 3: Add the Required Fresh-Bootstrap Activation Task

**Files:**
- Modify: `backend/app/tasks/market_rs_tasks.py:1-170`
- Modify: `backend/app/celery_app.py`
- Modify: `backend/tests/unit/test_market_rs_tasks.py`
- Modify: `backend/tests/unit/test_celery_config.py`

**Interfaces:**
- Consumes: `get_market_rs_rollout_executor()`
- Produces: `bootstrap_balanced_market_rs(market: str, activity_lifecycle: str | None = None) -> dict[str, object]`
- Produces task name: `app.tasks.market_rs_tasks.bootstrap_balanced_market_rs`

- [ ] **Step 1: Write strict task tests**

Cover successful activation, rollout rejection, unexpected exception, and retry after a transient database error:

```python
def test_bootstrap_balanced_market_rs_requires_activated_outcome(monkeypatch, tmp_path):
    executor = MagicMock()
    executor.execute.return_value = MarketRsRolloutOutcome(
        backfill={"failed_count": 0},
        activated=True,
        market="US",
        formula_version=BALANCED_RS_FORMULA_VERSION,
        feature_run_id=99,
        validation={"ok": True},
    )
    monkeypatch.setattr(module, "get_market_rs_rollout_executor", lambda: executor)
    monkeypatch.setattr(module, "TemporaryDirectory", _temporary_directory(tmp_path))
    monkeypatch.setattr(module, "SessionLocal", lambda: MagicMock())
    monkeypatch.setattr(
        module,
        "get_market_calendar_service",
        lambda: _calendar(date(2026, 7, 29)),
    )

    result = module.bootstrap_balanced_market_rs.run(
        market="US",
        activity_lifecycle="bootstrap",
    )

    assert result["status"] == "activated"
    assert result["formula_version"] == BALANCED_RS_FORMULA_VERSION
```

For a `MarketRsRolloutExecutionError`, assert the task marks `market_rs` activity failed and raises so the Celery chain stops. Do not return a legacy-compatible skipped result.

- [ ] **Step 2: Run the task tests and verify they fail**

Run:

```bash
cd backend && source venv/bin/activate
pytest tests/unit/test_market_rs_tasks.py -q
```

Expected: FAIL because `bootstrap_balanced_market_rs` does not exist.

- [ ] **Step 3: Implement the task with private staging**

Use a market-scoped temporary directory and the market calendar's last completed session:

```python
@celery_app.task(
    bind=True,
    name="app.tasks.market_rs_tasks.bootstrap_balanced_market_rs",
    soft_time_limit=7200,
    max_retries=2,
)
def bootstrap_balanced_market_rs(
    self,
    market: str,
    activity_lifecycle: str | None = None,
) -> dict[str, object]:
    market_code = normalize_market(market)
    lifecycle = activity_lifecycle or "bootstrap"
    through_date = get_market_calendar_service().last_completed_trading_day(market_code)
    db = SessionLocal()
    try:
        mark_market_activity_started(
            db,
            market=market_code,
            stage_key="market_rs",
            lifecycle=lifecycle,
            task_name=self.name,
            task_id=self.request.id,
            message="Preparing balanced Market RS publication",
        )
        with TemporaryDirectory(prefix=f"market-rs-{market_code.lower()}-") as raw_dir:
            outcome = get_market_rs_rollout_executor().execute(
                db,
                request=MarketRsRolloutRequest(
                    market=market_code,
                    through_date=through_date,
                    activate=True,
                    static_staging_dir=Path(raw_dir),
                ),
            )
        if not outcome.activated:
            raise RuntimeError("Balanced Market RS rollout returned without activation")
        mark_market_activity_completed(
            db,
            market=market_code,
            stage_key="market_rs",
            lifecycle=lifecycle,
            task_name=self.name,
            task_id=self.request.id,
            message="Balanced Market RS activated",
        )
        return {"status": "activated", **outcome.to_dict()}
    except (DBAPIError, ConnectionError, TimeoutError, OSError) as exc:
        db.rollback()
        _retry_connection_failure(self, exc)
    except Exception as exc:
        db.rollback()
        mark_market_activity_failed(
            db,
            market=market_code,
            stage_key="market_rs",
            lifecycle=lifecycle,
            task_name=self.name,
            task_id=self.request.id,
            message=f"Balanced Market RS activation failed: {exc}",
        )
        raise
    finally:
        db.close()
```

Reuse the module's existing transient-database retry helper. Keep deterministic rollout failures non-retryable; the whole bootstrap can be explicitly resumed after repairing data.

- [ ] **Step 4: Register and route the task**

`app.tasks.market_rs_tasks` is already in Celery `include`. Add this exact entry to `_MARKET_JOB_TASKS`:

```python
'app.tasks.market_rs_tasks.bootstrap_balanced_market_rs',
```

Assert the default route is the shared market-job queue and that the runtime bootstrap signature overrides it with `market_jobs_<market>`.

- [ ] **Step 5: Run task and Celery tests**

Run:

```bash
cd backend && source venv/bin/activate
pytest tests/unit/test_market_rs_tasks.py tests/unit/test_celery_config.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit the activation task**

```bash
git add backend/app/tasks/market_rs_tasks.py \
  backend/app/celery_app.py \
  backend/tests/unit/test_market_rs_tasks.py \
  backend/tests/unit/test_celery_config.py
git commit -m "feat: add balanced rs bootstrap activation task"
```

---

### Task 4: Select Activation Only for Fresh Bootstrap Plans

**Files:**
- Modify: `backend/app/domain/bootstrap/plan.py:18-199`
- Modify: `backend/app/tasks/runtime_bootstrap_tasks.py:175-221,398-463`
- Modify: `backend/tests/unit/domain/test_bootstrap_plan.py`
- Modify: `backend/tests/unit/test_runtime_bootstrap_tasks.py`

**Interfaces:**
- Produces enum member: `BootstrapOperation.BOOTSTRAP_BALANCED_MARKET_RS`
- Changes: `build_bootstrap_plan(..., fresh_install: bool = False) -> BootstrapPlan`
- Consumes: `bootstrap_balanced_market_rs`

- [ ] **Step 1: Write pure-plan selection tests**

Add explicit fresh and existing assertions:

```python
def test_fresh_bootstrap_requires_balanced_activation_before_groups() -> None:
    plan = build_bootstrap_plan(
        primary_market="US",
        enabled_markets=("US",),
        fresh_install=True,
    )
    stages = plan.market_plans[0].stages
    operations = [stage.operation for stage in stages]

    assert BootstrapOperation.BOOTSTRAP_BALANCED_MARKET_RS in operations
    assert BootstrapOperation.CALCULATE_MARKET_RS_SNAPSHOT not in operations
    assert operations.index(BootstrapOperation.BOOTSTRAP_BALANCED_MARKET_RS) < operations.index(
        BootstrapOperation.CALCULATE_DAILY_GROUP_RANKINGS
    )
    assert operations.index(BootstrapOperation.BOOTSTRAP_BALANCED_MARKET_RS) < operations.index(
        BootstrapOperation.BUILD_DAILY_SNAPSHOT
    )


def test_nonempty_bootstrap_keeps_shadow_market_rs_stage() -> None:
    plan = build_bootstrap_plan(
        primary_market="US",
        enabled_markets=("US",),
        fresh_install=False,
    )

    operations = [stage.operation for stage in plan.market_plans[0].stages]
    assert BootstrapOperation.CALCULATE_MARKET_RS_SNAPSHOT in operations
    assert BootstrapOperation.BOOTSTRAP_BALANCED_MARKET_RS not in operations
```

- [ ] **Step 2: Run plan tests and verify they fail**

Run:

```bash
cd backend && source venv/bin/activate
pytest tests/unit/domain/test_bootstrap_plan.py -q
```

Expected: FAIL because the operation and argument do not exist.

- [ ] **Step 3: Add conditional operation selection**

Keep the stage key `market_rs` for activity/UI compatibility and change only the operation:

```python
_stage(
    key="market_rs",
    operation=(
        BootstrapOperation.BOOTSTRAP_BALANCED_MARKET_RS
        if fresh_install
        else BootstrapOperation.CALCULATE_MARKET_RS_SNAPSHOT
    ),
    queue_kind=BootstrapQueueKind.MARKET_JOBS,
    market=market,
)
```

Pass `fresh_install` from `build_bootstrap_plan()` into every `_build_market_plan()` invocation. Default it to `False` for callers and old tests that represent an existing installation.

- [ ] **Step 4: Map the operation to the strict task**

Import `bootstrap_balanced_market_rs` in `_build_market_bootstrap_signatures()` and add:

```python
BootstrapOperation.BOOTSTRAP_BALANCED_MARKET_RS: bootstrap_balanced_market_rs,
```

Ensure the generated immutable signature contains only `market` and `activity_lifecycle`; the task derives its exact through-date at execution after price hydration.

- [ ] **Step 5: Forward the persisted decision into planning**

In `queue_local_runtime_bootstrap()`:

```python
fresh_install = _is_fresh_install_at_dispatch()
plan = build_bootstrap_plan(
    primary_market=primary_market,
    enabled_markets=enabled_markets,
    fresh_install=fresh_install,
)
manifest_recorder = BootstrapQueueManifestRecorder.create(
    primary_market=plan.primary_market,
    enabled_markets=plan.enabled_markets,
    fresh_install=fresh_install,
)
```

Do not call `is_empty_system()` again after any workflow has been dispatched.

- [ ] **Step 6: Run plan and dispatch tests**

Run:

```bash
cd backend && source venv/bin/activate
pytest tests/unit/domain/test_bootstrap_plan.py tests/unit/test_runtime_bootstrap_tasks.py -q
```

Expected: PASS and the existing non-empty operation lists remain unchanged.

- [ ] **Step 7: Commit conditional orchestration**

```bash
git add backend/app/domain/bootstrap/plan.py \
  backend/app/tasks/runtime_bootstrap_tasks.py \
  backend/tests/unit/domain/test_bootstrap_plan.py \
  backend/tests/unit/test_runtime_bootstrap_tasks.py
git commit -m "feat: activate balanced rs on fresh bootstrap"
```

---

### Task 5: Require Balanced Formula Before Fresh Readiness

**Files:**
- Modify: `backend/app/services/bootstrap_readiness_service.py:17-110`
- Modify: `backend/app/tasks/runtime_bootstrap_tasks.py:47-59,350-540`
- Modify: `backend/tests/unit/test_bootstrap_readiness_service.py`
- Modify: `backend/tests/unit/test_runtime_bootstrap_tasks.py`

**Interfaces:**
- Changes: `BootstrapReadinessService.evaluate(..., expected_formula_versions: Mapping[str, str] | None = None)`
- Changes: `MarketBootstrapReadiness.rs_ready: bool = True`
- Changes: `_evaluate_market_readiness(..., expected_formula_version: str | None = None)`
- Changes completion tasks to accept `expected_formula_version: str | None = None`

- [ ] **Step 1: Write formula-aware readiness tests**

Add service tests with a legacy pointer and an expected balanced formula:

```python
def test_market_readiness_rejects_formula_pointer_mismatch(db_session):
    seed_ready_market_data(db_session, market="US")
    db_session.add(
        MarketRsFormulaPointer(
            market="US",
            formula_version=LEGACY_RS_FORMULA_VERSION,
        )
    )
    db_session.commit()

    result = BootstrapReadinessService().evaluate(
        db_session,
        enabled_markets=["US"],
        expected_formula_versions={"US": BALANCED_RS_FORMULA_VERSION},
    ).market_results["US"]

    assert result.core_ready is True
    assert result.scan_ready is True
    assert result.rs_ready is False
    assert result.ready is False
```

Add a compatibility test proving omitted expectations leave a legacy market ready.

- [ ] **Step 2: Run readiness tests and verify they fail**

Run:

```bash
cd backend && source venv/bin/activate
pytest tests/unit/test_bootstrap_readiness_service.py -q
```

Expected: FAIL because formula readiness is not modeled.

- [ ] **Step 3: Implement optional formula readiness**

Add `rs_ready` without changing default behavior:

```python
@dataclass(frozen=True)
class MarketBootstrapReadiness:
    market: str
    core_ready: bool
    scan_ready: bool
    rs_ready: bool = True

    @property
    def ready(self) -> bool:
        return self.core_ready and self.scan_ready and self.rs_ready
```

Resolve the pointer with `MarketRsRunRepository.active_formula()` only for markets present in `expected_formula_versions`. Missing or mismatched pointers set `rs_ready=False`; they must not raise out of the readiness response.

- [ ] **Step 4: Add a market-RS-specific completion failure**

Change `_readiness_failure()` so `rs_ready=False` produces:

```python
ReadinessFailure(
    stage_key="market_rs",
    activity_message="Balanced Market RS activation incomplete",
    result_reason="balanced market rs formula not active",
)
```

Keep existing `core` and `scan` failure order for non-formula failures.

- [ ] **Step 5: Pass expectations only for fresh chains**

When queueing completion callbacks, set:

```python
expected_formula_version = (
    BALANCED_RS_FORMULA_VERSION if fresh_install else None
)
```

Pass it to both `complete_local_runtime_bootstrap` and `complete_background_market_bootstrap`. Preserve `None` defaults for already queued Celery payload compatibility.

- [ ] **Step 6: Test defensive completion behavior**

In `test_runtime_bootstrap_tasks.py`, simulate a completed chain whose pointer remained legacy and assert:

```python
assert result == {
    "status": "failed",
    "primary_market": "US",
    "market": "US",
    "reason": "balanced market rs formula not active",
}
assert bootstrap_states[-1] == "failed"
```

For a background market, assert the result is failed without changing global primary bootstrap state.

- [ ] **Step 7: Run readiness and completion tests**

Run:

```bash
cd backend && source venv/bin/activate
pytest tests/unit/test_bootstrap_readiness_service.py \
  tests/unit/test_runtime_bootstrap_tasks.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit formula-aware readiness**

```bash
git add backend/app/services/bootstrap_readiness_service.py \
  backend/app/tasks/runtime_bootstrap_tasks.py \
  backend/tests/unit/test_bootstrap_readiness_service.py \
  backend/tests/unit/test_runtime_bootstrap_tasks.py
git commit -m "feat: gate fresh readiness on balanced rs"
```

---

### Task 6: Prove Balanced Groups and History Follow Activation

**Files:**
- Modify: `backend/tests/integration/test_market_rs_activation.py`
- Modify: `backend/tests/integration/test_live_group_history_bootstrap.py`
- Modify: `backend/tests/unit/test_group_history_tasks.py`

**Interfaces:**
- Consumes: `MarketRsRolloutExecutor.execute()`
- Consumes: `_resolve_current_group_history_target(db, market="US")`
- Verifies: exact `MarketRsRun`, `IBDGroupRank`, Feature pointer, cache epoch, and Group-history formula identities

- [ ] **Step 1: Add a fresh activation integration fixture**

Reuse the canonical activation fixtures to seed:

```python
through_date = date(2026, 7, 29)
seed_point_in_time_universe_with_status_events(db_session, market="US")
seed_adjusted_price_anchors(db_session, market="US", through_date=through_date)
seed_ibd_group_memberships(db_session, market="US")
MarketRsRunRepository().provision_formula_pointers(db_session, markets=("US",))
db_session.commit()
```

Assert the initial pointer is legacy, matching production bootstrap provisioning.

- [ ] **Step 2: Write the end-to-end activation identity test**

Execute the shared rollout with a temporary staging directory and assert:

```python
assert outcome.activated is True
assert MarketRsRunRepository().active_formula(db_session, market="US") == (
    BALANCED_RS_FORMULA_VERSION
)

run = MarketRsRunRepository().get_completed_exact(
    db_session,
    market="US",
    as_of_date=through_date,
    formula_version=BALANCED_RS_FORMULA_VERSION,
)
groups = (
    db_session.query(IBDGroupRank)
    .filter(
        IBDGroupRank.market == "US",
        IBDGroupRank.date == through_date,
        IBDGroupRank.rs_formula_version == BALANCED_RS_FORMULA_VERSION,
    )
    .all()
)
assert run is not None
assert groups
assert {row.market_rs_run_id for row in groups} == {run.id}
assert all(row.avg_rs_rating_1m is not None for row in groups)
assert all(row.avg_rs_rating_3m is not None for row in groups)
```

Resolve `latest_published_market:US` and assert its config has the same formula, run ID, RS date, and universe size.

- [ ] **Step 3: Prove Group-history targeting switches immediately**

After activation, assert:

```python
target = _resolve_current_group_history_target(db_session, market="US")
assert target.formula_version == BALANCED_RS_FORMULA_VERSION
assert target.through_date == through_date
```

Run the existing `ensure_group_history` integration path with current-universe fallback and verify all generated rows are balanced; no legacy row may satisfy balanced readiness.

- [ ] **Step 4: Prove cache and US bootstrap publication occur once**

Patch `bump_group_rankings_epoch` at the activator boundary and `safe_publish_groups_bootstrap` at the executor adapter. Assert activation bumps the epoch once and the executor republishes once after the pointer transaction commits.

- [ ] **Step 5: Add the non-empty safety integration case**

Build a non-empty bootstrap plan and assert no call reaches `MarketRsRolloutExecutor`; its market remains legacy and all existing rows and Feature pointers are unchanged. This protects existing deployments from accidental automatic rollout.

- [ ] **Step 6: Run integration coverage**

Run:

```bash
cd backend && source venv/bin/activate
pytest tests/integration/test_market_rs_activation.py \
  tests/integration/test_live_group_history_bootstrap.py \
  tests/unit/test_group_history_tasks.py -q
```

Expected: PASS with balanced current Group rows, formula-isolated history, and unchanged non-empty behavior.

- [ ] **Step 7: Commit integration coverage**

```bash
git add backend/tests/integration/test_market_rs_activation.py \
  backend/tests/integration/test_live_group_history_bootstrap.py \
  backend/tests/unit/test_group_history_tasks.py
git commit -m "test: cover fresh balanced bootstrap publication"
```

---

### Task 7: Document Operations and Run Release Gates

**Files:**
- Modify: `docs/OPERATIONS.md:205-265`

**Interfaces:**
- Documents automatic fresh behavior, explicit existing-database behavior, verification SQL, failure semantics, and rollback.

- [ ] **Step 1: Update the operations contract**

Add a section before the existing manual rollout:

```markdown
### Fresh database activation

Runtime bootstrap captures database emptiness before dispatch. An empty installation
runs balanced Market RS backfill and guarded activation as a required market stage.
The primary market is not marked ready unless its formula pointer is
`balanced-horizon-percentile-v2`. Failure leaves both pointers unchanged and marks
bootstrap failed; correct the reported coverage/input problem and resume bootstrap.

Non-empty databases are never auto-activated. Use the explicit shadow backfill and
activation procedure below.
```

Document that Group history runs after activation and therefore generates 1W/1M/3M/6M deltas and RRG under balanced identity.

- [ ] **Step 2: Add verification SQL**

Document these checks after fresh bootstrap reports ready:

```sql
SELECT market, formula_version
FROM market_rs_formula_pointers
WHERE market = 'US';

SELECT rs_formula_version,
       MAX(date) AS latest_date,
       COUNT(*) FILTER (WHERE avg_rs_rating_1m IS NOT NULL) AS rows_with_1m,
       COUNT(*) FILTER (WHERE avg_rs_rating_3m IS NOT NULL) AS rows_with_3m
FROM ibd_group_ranks
WHERE market = 'US'
GROUP BY rs_formula_version;
```

- [ ] **Step 3: Run the focused backend suite**

Run:

```bash
cd backend && source venv/bin/activate
pytest \
  tests/unit/test_bootstrap_run_manifest.py \
  tests/unit/domain/test_bootstrap_plan.py \
  tests/unit/test_runtime_bootstrap_tasks.py \
  tests/unit/test_bootstrap_readiness_service.py \
  tests/unit/test_market_rs_rollout_executor.py \
  tests/unit/test_market_rs_rollout_service.py \
  tests/unit/test_backfill_market_rs_script.py \
  tests/unit/test_market_rs_tasks.py \
  tests/unit/test_group_history_tasks.py \
  tests/integration/test_market_rs_activation.py \
  tests/integration/test_live_group_history_bootstrap.py -q
```

Expected: PASS.

- [ ] **Step 4: Run backend syntax and broad tests**

Run:

```bash
cd backend && source venv/bin/activate
python -m compileall -q app tests
pytest -q
```

Expected: PASS. Record any environment-only skipped tests in the PR description.

- [ ] **Step 5: Exercise a clean Docker bootstrap**

After preserving the current database, use the documented clean-bootstrap sequence with latest images. Wait until runtime bootstrap reports ready, then verify:

```bash
scripts/docker-compose-enabled-markets.sh exec -T postgres \
  psql -U stockscanner -d stockscanner -c \
  "SELECT market, formula_version FROM market_rs_formula_pointers ORDER BY market;"
```

For every successfully bootstrapped market, expect `balanced-horizon-percentile-v2`. For US, open `/groups` and verify the current ranking order and 1M/3M component values match the exact same-date staged/static identity.

- [ ] **Step 6: Exercise the non-empty upgrade guard**

Restore or start from a database with existing legacy data, queue bootstrap, and verify it does not auto-activate. Run the existing operator command in shadow mode and confirm the pointer remains legacy until `--activate` passes.

- [ ] **Step 7: Commit documentation**

```bash
git add docs/OPERATIONS.md
git commit -m "docs: explain fresh balanced rs activation"
```

- [ ] **Step 8: Complete repository workflow and push**

```bash
bd sync
git pull --rebase
git push -u origin codex/fresh-bootstrap-balanced-rs
git status
```

Expected: clean worktree and branch reported up to date with origin.

---

## Acceptance Criteria

- An empty database records `fresh_install=true` before its first bootstrap task is dispatched.
- Every fresh-install market chain uses the required guarded activation task instead of the legacy-compatible shadow task.
- A fresh primary market cannot reach `runtime.bootstrap_state=ready` while its formula pointer is legacy or absent.
- Successful activation produces an exact completed balanced Market RS run, matching balanced Group rows, and a published Feature pointer for the same market/date/run/universe.
- The subsequent Group-history stage targets balanced identity and provides formula-compatible rank changes, movers, detail history, and RRG readiness.
- Failed backfill, static parity validation, or activation leaves both active pointers unchanged and fails only the owning bootstrap market chain.
- Non-empty databases retain their current formula and require the existing explicit `backfill_market_rs --activate` workflow.
- Legacy payloads for queued bootstrap completion and stored manifests remain readable.
- The operator CLI retains its current flags, JSON shape, staging-directory checks, and rollback semantics.
