# Live Group History Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate live Group rank changes, movers, detail history, and RRG during fresh bootstrap and automatically repair existing databases without destructive migration.

**Architecture:** A shared readiness service validates six months of formula-scoped Group snapshots and RRG usability. A shared bootstrap service repairs only missing or invalid identities using a point-in-time-first/current-universe-fallback coordinator, while price planning treats exact RS-anchor gaps as two-year refresh work. Fresh bootstrap runs this as a required post-snapshot stage; startup reconciliation queues the same idempotent work in the background and records status in a versioned `AppSetting` marker.

**Tech Stack:** Python 3.11, SQLAlchemy, Celery, Redis workload serialization, FastAPI lifespan, pytest.

## Global Constraints

- Preserve raw prices, user data, valid historical snapshots, and legacy-formula rows.
- Use the 187-calendar-day Group history window and existing seven-day rank lookup tolerance.
- Prefer valid point-in-time membership; fall back to the current active universe when lifecycle evidence is unavailable or empty.
- Fresh bootstrap must fail when Group history remains incomplete; upgrade reconciliation must remain nonblocking.
- Do not add a frontend or API contract change.

---

### Task 1: Exact Group-History Price Coverage

**Files:**
- Create: `backend/app/services/group_history_price_coverage.py`
- Modify: `backend/app/services/static_daily_price_refresh_service.py`
- Modify: `backend/app/services/price_refresh_plan_builder.py`
- Modify: `backend/app/services/price_refresh_workflow.py`
- Modify: `backend/app/tasks/cache_tasks.py`
- Test: `backend/tests/unit/test_group_history_price_coverage.py`
- Test: `backend/tests/unit/test_price_refresh_workflow.py`
- Test: `backend/tests/unit/test_static_daily_price_refresh_service.py`

**Interfaces:**
- Produces: `GroupHistoryPriceCoverageService.required_anchor_dates(market, through_date) -> frozenset[date]`.
- Produces: `GroupHistoryPriceCoverageService.classify(db, market, through_date, symbols) -> GroupHistoryPriceCoverage` with `complete_symbols`, `incomplete_symbols`, `required_anchor_count`, and diagnostic samples.
- Extends: `smart_refresh_cache(..., ensure_group_history: bool = False)` so incomplete exact-anchor symbols become `NO_HISTORY` jobs with period `2y`.

- [ ] **Step 1: Write failing exact-anchor and planner tests**

```python
coverage = service.classify(db, market="US", through_date=day, symbols=("AAA", "SPY"))
assert coverage.incomplete_symbols == ("AAA",)
assert coverage.complete_symbols == ("SPY",)

plan = build_market_price_refresh_plan(..., ensure_group_history=True)
assert next(job for job in plan.jobs if job.kind.value == "no_history").period == "2y"
assert "AAA" in next(job.symbols for job in plan.jobs if job.kind.value == "no_history")
```

- [ ] **Step 2: Run tests and verify failures identify the missing service/argument**

Run: `/Users/admin/StockScreenClaude/backend/venv/bin/python -m pytest backend/tests/unit/test_group_history_price_coverage.py backend/tests/unit/test_price_refresh_workflow.py backend/tests/unit/test_static_daily_price_refresh_service.py -q`

- [ ] **Step 3: Implement shared exact-anchor coverage and thread the opt-in through live refresh**

```python
@dataclass(frozen=True)
class GroupHistoryPriceCoverage:
    complete_symbols: tuple[str, ...]
    incomplete_symbols: tuple[str, ...]
    required_anchor_count: int
    available_anchor_counts: Mapping[str, int]
```

Merge incomplete symbols into `PriceHistoryCoverage.no_history`, remove them from `fresh`/`stale`, and keep the default planner behavior unchanged when `ensure_group_history=False`. Replace static service private anchor calculations with this service.

- [ ] **Step 4: Run focused tests and commit**

```bash
git add backend/app/services/group_history_price_coverage.py backend/app/services/static_daily_price_refresh_service.py backend/app/services/price_refresh_plan_builder.py backend/app/services/price_refresh_workflow.py backend/app/tasks/cache_tasks.py backend/tests/unit/test_group_history_price_coverage.py backend/tests/unit/test_price_refresh_workflow.py backend/tests/unit/test_static_daily_price_refresh_service.py
git commit -m "Add exact group history price coverage"
```

### Task 2: Historical Universe Fallback

**Files:**
- Create: `backend/app/services/group_history_universe.py`
- Create: `backend/app/services/group_history_snapshot_coordinator.py`
- Test: `backend/tests/unit/test_group_history_universe.py`
- Test: `backend/tests/unit/test_group_history_snapshot_coordinator.py`

**Interfaces:**
- Produces: `GroupHistoryUniverseResolver.resolve(db, market, as_of_date) -> PointInTimeUniverse`.
- Produces: `GroupHistoryUniverseResolver.policy_for(market, as_of_date) -> str`.
- Produces: `build_group_history_snapshot_coordinator(...) -> GroupRankSnapshotCoordinator` wired to the fallback resolver and real legacy service.

- [ ] **Step 1: Write failing precedence/fallback tests**

```python
assert resolver.resolve(db, market="US", as_of_date=day).symbols == ("PIT",)
assert resolver.policy_for("US", day) == "point_in_time"

pit.resolve.side_effect = PointInTimeUniverseUnavailable("missing lifecycle")
assert resolver.resolve(db, market="US", as_of_date=day).symbols == ("CURRENT",)
assert resolver.policy_for("US", day) == "current_active_fallback_v1"
```

- [ ] **Step 2: Run tests and verify the service is absent**

Run: `/Users/admin/StockScreenClaude/backend/venv/bin/python -m pytest backend/tests/unit/test_group_history_universe.py backend/tests/unit/test_group_history_snapshot_coordinator.py -q`

- [ ] **Step 3: Implement the resolver and coordinator factory**

Use `PointInTimeUniverseService` first. Fall back on `PointInTimeUniverseUnavailable` or an empty historical result, query `StockUniverse.active_filter()`, and record policy per market/date without mutating lifecycle rows.

- [ ] **Step 4: Run focused tests and commit**

```bash
git add backend/app/services/group_history_universe.py backend/app/services/group_history_snapshot_coordinator.py backend/tests/unit/test_group_history_universe.py backend/tests/unit/test_group_history_snapshot_coordinator.py
git commit -m "Add group history universe fallback"
```

### Task 3: Database-Verified Readiness

**Files:**
- Create: `backend/app/services/group_history_readiness_service.py`
- Test: `backend/tests/unit/test_group_history_readiness_service.py`

**Interfaces:**
- Produces: `GroupHistoryReadinessService.evaluate(db, market, through_date=None) -> GroupHistoryReadinessReport`.
- Report fields include `formula_version`, `desired_dates`, `valid_dates`, `missing_dates`, `invalid_dates`, `rank_change_ready`, `rrg_required`, `rrg_usable_weeks`, `rrg_plottable_series`, and `ready`.

- [ ] **Step 1: Write failing readiness tests**

```python
report = service.evaluate(db, market="US", through_date=current_day)
assert report.missing_dates == (missing_day,)
assert report.invalid_dates == (invalid_day,)
assert report.rank_change_ready == {"1w": True, "1m": True, "3m": False, "6m": False}
assert report.ready is False
```

Also assert a Group-capable market with no RRG scopes can be ready without RRG, while an RRG market requires at least `MIN_TAIL_WEEKS` weekly observations and one nonempty scope.

- [ ] **Step 2: Run the readiness test and verify it fails**

Run: `/Users/admin/StockScreenClaude/backend/venv/bin/python -m pytest backend/tests/unit/test_group_history_readiness_service.py -q`

- [ ] **Step 3: Implement read-only classification**

Call `GroupRankSnapshotReader.load_exact` for every desired trading date, catch `GroupSnapshotIntegrityError`, evaluate rank target proximity with `CALENDAR_DAY_GROUP_RANK_LOOKUP_TOLERANCE_DAYS`, and use the injected RRG provider/service for weekly/plottable checks. Never commit or mutate in `evaluate`.

- [ ] **Step 4: Run focused tests and commit**

```bash
git add backend/app/services/group_history_readiness_service.py backend/tests/unit/test_group_history_readiness_service.py
git commit -m "Add group history readiness checks"
```

### Task 4: Idempotent Group-History Repair

**Files:**
- Create: `backend/app/services/group_history_bootstrap_service.py`
- Modify: `backend/app/services/group_rank_snapshot_coordinator.py`
- Test: `backend/tests/unit/test_group_history_bootstrap_service.py`
- Test: `backend/tests/unit/services/test_group_rank_snapshot_coordinator.py`

**Interfaces:**
- Produces: `GroupHistoryBootstrapService.ensure(db, market, through_date) -> GroupHistoryBootstrapResult`.
- Extends: `GroupRankSnapshotCoordinator.repair_snapshot(db, identity) -> GroupSnapshotResult` for exact-identity integrity repair.

- [ ] **Step 1: Write failing skip/repair/formula tests**

```python
result = service.ensure(db, market="US", through_date=day)
assert result.processed_dates == (missing_day, invalid_day)
assert result.skipped_valid == len(valid_dates)
assert result.after.ready is True
assert result.policy_counts["current_active_fallback_v1"] > 0
```

Assert each failed date rolls back independently, valid identities are untouched, balanced uses the canonical coordinator, and legacy calls the real legacy service.

- [ ] **Step 2: Run tests and verify the repair API is absent**

Run: `/Users/admin/StockScreenClaude/backend/venv/bin/python -m pytest backend/tests/unit/test_group_history_bootstrap_service.py backend/tests/unit/services/test_group_rank_snapshot_coordinator.py -q`

- [ ] **Step 3: Implement oldest-first exact-identity repair**

For missing dates call `ensure_snapshot`; for invalid dates call `repair_snapshot`. Re-evaluate readiness after all dates and derive success only from the second report. Do not delete ranges or other formulas.

- [ ] **Step 4: Run focused tests and commit**

```bash
git add backend/app/services/group_history_bootstrap_service.py backend/app/services/group_rank_snapshot_coordinator.py backend/tests/unit/test_group_history_bootstrap_service.py backend/tests/unit/services/test_group_rank_snapshot_coordinator.py
git commit -m "Add idempotent group history repair"
```

### Task 5: Fresh Runtime Bootstrap Stage

**Files:**
- Modify: `backend/app/domain/bootstrap/plan.py`
- Create: `backend/app/tasks/group_history_tasks.py`
- Modify: `backend/app/tasks/runtime_bootstrap_tasks.py`
- Test: `backend/tests/unit/domain/test_bootstrap_plan.py`
- Test: `backend/tests/unit/test_runtime_bootstrap_tasks.py`
- Test: `backend/tests/unit/test_group_history_tasks.py`

**Interfaces:**
- Adds: `BootstrapOperation.CALCULATE_DAILY_GROUP_RANKINGS` for current-only Group calculation.
- Adds: `BootstrapOperation.ENSURE_GROUP_HISTORY` and stage key `group_history` after `snapshot` for Group-capable markets.
- Produces Celery task: `ensure_group_history(market, activity_lifecycle="bootstrap", strict=True)`.

- [ ] **Step 1: Write failing stage-order and strict-task tests**

```python
keys = [stage.key for stage in market_plan.stages]
assert keys.index("group_history") == keys.index("snapshot") + 1
assert market_plan.stages[keys.index("groups")].operation is BootstrapOperation.CALCULATE_DAILY_GROUP_RANKINGS

with pytest.raises(RuntimeError, match="group history remains incomplete"):
    ensure_group_history.run(market="US", strict=True)
```

- [ ] **Step 2: Run tests and verify missing operation/task failures**

Run: `/Users/admin/StockScreenClaude/backend/venv/bin/python -m pytest backend/tests/unit/domain/test_bootstrap_plan.py backend/tests/unit/test_runtime_bootstrap_tasks.py backend/tests/unit/test_group_history_tasks.py -q`

- [ ] **Step 3: Implement stage mapping and task orchestration**

Pass `ensure_group_history=True` to the bootstrap price stage. Run current-only Group calculation before snapshot. In the new task, mark `group_history` activity, execute repair, fail strict chains when `after.ready` is false, then bump the Group cache epoch and republish the US Group UI snapshot.

- [ ] **Step 4: Run focused tests and commit**

```bash
git add backend/app/domain/bootstrap/plan.py backend/app/tasks/group_history_tasks.py backend/app/tasks/runtime_bootstrap_tasks.py backend/tests/unit/domain/test_bootstrap_plan.py backend/tests/unit/test_runtime_bootstrap_tasks.py backend/tests/unit/test_group_history_tasks.py
git commit -m "Wire live group history bootstrap stage"
```

### Task 6: Existing-Database Reconciliation Marker

**Files:**
- Create: `backend/app/services/group_history_reconciliation.py`
- Modify: `backend/app/tasks/group_history_tasks.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/test_group_history_reconciliation.py`
- Test: `backend/tests/unit/test_main_lifespan.py`

**Interfaces:**
- Produces: `GroupHistoryReconciliationRepository` with marker key `runtime.group_history.v1.<MARKET>`.
- Produces: `queue_group_history_reconciliation() -> dict[str, str]`.
- Adds startup hook: `trigger_group_history_reconciliation_on_startup()`.

- [ ] **Step 1: Write failing marker/idempotency/startup tests**

```python
first = reconciler.reserve(db, market="US", formula_version="balanced-v1", through_date=day)
second = reconciler.reserve(db, market="US", formula_version="balanced-v1", through_date=day)
assert first is True
assert second is False

assert queue_group_history_reconciliation()["US"] == "queued"
assert queue_group_history_reconciliation()["US"] == "ready"
```

Assert runtime bootstrap state `running` skips dispatch, dispatch failure returns the marker to `incomplete`, formula changes create new work, and a failed background chain leaves the live app available.

- [ ] **Step 2: Run tests and verify reconciliation interfaces are absent**

Run: `/Users/admin/StockScreenClaude/backend/venv/bin/python -m pytest backend/tests/unit/test_group_history_reconciliation.py backend/tests/unit/test_main_lifespan.py -q`

- [ ] **Step 3: Implement versioned marker and cross-queue chain**

Queue `smart_refresh_cache.si(mode="bootstrap", ensure_group_history=True)` on the market data-fetch queue, `ensure_group_history.si(strict=False)` on market-jobs, and a completion task on `celery`. Persist `queued`, `repairing`, `ready`, `incomplete`, or `failed` plus counts/error. Always verify actual readiness before honoring a ready marker.

- [ ] **Step 4: Run focused tests and commit**

```bash
git add backend/app/services/group_history_reconciliation.py backend/app/tasks/group_history_tasks.py backend/app/main.py backend/tests/unit/test_group_history_reconciliation.py backend/tests/unit/test_main_lifespan.py
git commit -m "Repair existing group history in background"
```

### Task 7: Live API Acceptance and Data Safety

**Files:**
- Create: `backend/tests/integration/test_live_group_history_bootstrap.py`
- Modify: focused unit tests as required by discovered behavior.

**Interfaces:**
- Verifies existing `/v1/groups/rankings`, `/v1/groups/rankings/movers`, and `/v1/groups/rrg/scopes` contracts without frontend changes.

- [ ] **Step 1: Add sparse-database integration test**

```python
repair = bootstrap_service.ensure(db, market="US", through_date=day)
assert repair.after.ready
assert all(row["rank_change_1w"] is not None for row in rankings_payload["rankings"])
assert movers_payload["gainers"] or movers_payload["losers"]
assert rrg_payload["groups"]["groups"]
```

Snapshot row counts for raw prices, watchlists/scans, and legacy formula Group rows before repair and assert they are preserved afterward.

- [ ] **Step 2: Run integration test and fix only defects exposed by it**

Run: `/Users/admin/StockScreenClaude/backend/venv/bin/python -m pytest backend/tests/integration/test_live_group_history_bootstrap.py -q`

- [ ] **Step 3: Commit integration coverage**

```bash
git add backend/tests/integration/test_live_group_history_bootstrap.py backend/app backend/tests/unit
git commit -m "Verify live group history bootstrap APIs"
```

### Task 8: Full Verification and Delivery

**Files:**
- Modify: `.beads/issues.jsonl` through `bd close stockscreenclaude-cbe`.

- [ ] **Step 1: Run focused backend regression suite**

Run: `/Users/admin/StockScreenClaude/backend/venv/bin/python -m pytest backend/tests/unit/domain/test_bootstrap_plan.py backend/tests/unit/test_runtime_bootstrap_tasks.py backend/tests/unit/test_group_history_price_coverage.py backend/tests/unit/test_group_history_universe.py backend/tests/unit/test_group_history_readiness_service.py backend/tests/unit/test_group_history_bootstrap_service.py backend/tests/unit/test_group_history_tasks.py backend/tests/unit/test_group_history_reconciliation.py backend/tests/unit/test_rrg_service.py backend/tests/unit/test_ui_snapshot_service.py -q`

- [ ] **Step 2: Run backend lint/type-adjacent project gates available in the repository**

Run the configured backend formatter/linter command discovered from `Makefile`, `pyproject.toml`, or CI, then run `git diff --check`.

- [ ] **Step 3: Run the full backend unit suite**

Run: `/Users/admin/StockScreenClaude/backend/venv/bin/python -m pytest backend/tests/unit -q`

- [ ] **Step 4: Close the Beads issue, sync metadata, commit, rebase, and push**

```bash
bd close stockscreenclaude-cbe --reason "Implemented live Group history and RRG bootstrap parity"
bd export --no-memories -o .beads/issues.jsonl
git add .beads/issues.jsonl
git commit -m "Close live group history bootstrap issue"
git pull --rebase
git push -u origin codex/live-group-history-bootstrap-implementation
git status --short --branch
```
