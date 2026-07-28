# Live Group History Review Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make PR #324 reconciliation single-owner, identity-stable, query-bounded, and covered through production Group snapshot wiring.

**Architecture:** FastAPI enqueues an idempotent Celery discovery task. A portable compare-and-swap marker reserves an immutable `GroupHistoryTarget`, one executor repairs and finalizes that target, and batched snapshot-window reads supply readiness and RRG history.

**Tech Stack:** Python 3.11, SQLAlchemy, Celery, FastAPI lifespan, PostgreSQL/SQLite, pytest.

## Global Constraints

- Preserve raw prices, scans, watchlists, valid Group snapshots, and inactive-formula rows.
- Keep `AppSetting` persistence and avoid a schema migration.
- Fresh bootstrap is strict; upgrade reconciliation is nonblocking.
- Cache invalidation and US snapshot publication happen once per successful execution.
- Add each behavior test first and observe the expected failure before production edits.

---

### Task 1: Immutable Target And Atomic Reservation

**Files:**
- Modify: `backend/app/services/group_history_reconciliation.py`
- Modify: `backend/app/services/group_history_readiness_service.py`
- Modify: `backend/app/services/group_history_bootstrap_service.py`
- Test: `backend/tests/unit/test_group_history_reconciliation.py`
- Test: `backend/tests/unit/test_group_history_readiness_service.py`
- Test: `backend/tests/unit/test_group_history_bootstrap_service.py`

**Interfaces:**
- Produces: `GroupHistoryTarget(market, formula_version, through_date)`.
- Produces: `GroupHistoryReconciliationRepository.reserve(db, target=target) -> bool` using compare-and-swap.
- Changes: `GroupHistoryReadinessService.evaluate(db, target=target)`.
- Changes: `GroupHistoryBootstrapService.ensure(db, target=target)`.

- [ ] **Step 1: Write failing target and two-session reservation tests**

Assert readiness never consults `active_formula` when a target is supplied, bootstrap returns the same target identity, and two sessions attempting to reserve an existing incomplete marker yield exactly one `True` result.

- [ ] **Step 2: Run focused tests and verify the old signatures/race fail**

```bash
/Users/admin/StockScreenClaude/backend/venv/bin/python -m pytest backend/tests/unit/test_group_history_reconciliation.py backend/tests/unit/test_group_history_readiness_service.py backend/tests/unit/test_group_history_bootstrap_service.py -q
```

- [ ] **Step 3: Implement target and compare-and-swap repository**

Use a conditional SQLAlchemy `UPDATE AppSetting ... WHERE key = :key AND value = :old_value`; accept reservation only when `rowcount == 1`. Keep unique-key conflict handling for the missing-row insert race.

- [ ] **Step 4: Thread target through readiness and repair**

Remove independent market/date/formula resolution inside the services. Provide a resolver helper for callers that need the current target.

- [ ] **Step 5: Run focused tests and commit**

```bash
git add backend/app/services/group_history_reconciliation.py backend/app/services/group_history_readiness_service.py backend/app/services/group_history_bootstrap_service.py backend/tests/unit/test_group_history_reconciliation.py backend/tests/unit/test_group_history_readiness_service.py backend/tests/unit/test_group_history_bootstrap_service.py
git commit -m "Make group history targets atomic and immutable"
```

### Task 2: Celery-Owned Discovery And Single Finalizer

**Files:**
- Create: `backend/app/services/group_history_execution_service.py`
- Modify: `backend/app/tasks/group_history_tasks.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/test_group_history_tasks.py`
- Test: `backend/tests/unit/test_group_history_reconciliation.py`
- Test: `backend/tests/unit/test_main_lifespan.py`

**Interfaces:**
- Produces: `GroupHistoryExecutionService.execute(db, target, completion_policy) -> dict`.
- Celery task: `discover_group_history_reconciliation()`.
- Celery task: `repair_group_history_reconciliation(market, formula_version, through_date)`.
- Retains fresh task: `ensure_group_history(market, strict=True)`.

- [ ] **Step 1: Write failing startup, identity, and exactly-once finalization tests**

Assert startup calls `.delay()` and stores no background task, reconciliation uses its captured target after calendar/formula changes, and one successful US repair produces one epoch bump and one snapshot publication.

- [ ] **Step 2: Run tests and verify failures expose the current thread and duplicate finalizer**

```bash
/Users/admin/StockScreenClaude/backend/venv/bin/python -m pytest backend/tests/unit/test_group_history_tasks.py backend/tests/unit/test_group_history_reconciliation.py backend/tests/unit/test_main_lifespan.py -q
```

- [ ] **Step 3: Extract execution/finalization from the Celery module**

The service records activity safely, rolls back before failure reporting, runs repair, invalidates cache, publishes once, and applies either strict-bootstrap or reconciliation-marker completion policy.

- [ ] **Step 4: Replace lifespan threading with Celery discovery dispatch**

Remove `asyncio.create_task`, `asyncio.to_thread`, cancellation state, and the separate reconciliation completion task. Reserve before readiness evaluation in the discovery task.

- [ ] **Step 5: Run tests and commit**

```bash
git add backend/app/services/group_history_execution_service.py backend/app/tasks/group_history_tasks.py backend/app/main.py backend/tests/unit/test_group_history_tasks.py backend/tests/unit/test_group_history_reconciliation.py backend/tests/unit/test_main_lifespan.py
git commit -m "Move group history reconciliation into Celery"
```

### Task 3: Batched Snapshot Window

**Files:**
- Modify: `backend/app/services/group_rank_snapshot_reader.py`
- Modify: `backend/app/services/group_history_readiness_service.py`
- Modify: `backend/app/services/rrg_history_provider.py`
- Test: `backend/tests/unit/services/test_group_rank_snapshot_reader.py`
- Test: `backend/tests/unit/test_group_history_readiness_service.py`
- Test: `backend/tests/unit/test_rrg_service.py`

**Interfaces:**
- Produces: `GroupRankSnapshotReader.load_window(db, market, formula_version, dates) -> dict[date, list[dict]]`.

- [ ] **Step 1: Write failing batched-window integrity and query-count tests**

Cover missing dates, invalid balanced run references, payload ordering, and a bounded SQL query count independent of the number of dates.

- [ ] **Step 2: Run tests and verify `load_window` is absent**

```bash
/Users/admin/StockScreenClaude/backend/venv/bin/python -m pytest backend/tests/unit/services/test_group_rank_snapshot_reader.py backend/tests/unit/test_group_history_readiness_service.py backend/tests/unit/test_rrg_service.py -q
```

- [ ] **Step 3: Implement batched loading and shared validation**

Fetch all Group rows once, fetch referenced Market RS runs once, group rows by date, and reuse one validation implementation for exact and window reads.

- [ ] **Step 4: Switch readiness and stored RRG history to window reads**

Keep existing payload contracts and formula isolation.

- [ ] **Step 5: Run tests and commit**

```bash
git add backend/app/services/group_rank_snapshot_reader.py backend/app/services/group_history_readiness_service.py backend/app/services/rrg_history_provider.py backend/tests/unit/services/test_group_rank_snapshot_reader.py backend/tests/unit/test_group_history_readiness_service.py backend/tests/unit/test_rrg_service.py
git commit -m "Batch group history snapshot reads"
```

### Task 4: Production-Wired Acceptance

**Files:**
- Modify: `backend/tests/integration/test_live_group_history_bootstrap.py`
- Modify: production files only for defects exposed by the real integration path.

**Interfaces:**
- Verifies: production `build_group_history_snapshot_coordinator` and `GroupHistoryUniverseResolver`.

- [ ] **Step 1: Replace fake coordinator with real seeded inputs**

Seed six symbols across two industry groups, active universe rows, SPY and stock adjusted closes for every production anchor, then construct the production coordinator and bootstrap service.

- [ ] **Step 2: Run acceptance test and verify it fails before fixture completion**

```bash
/Users/admin/StockScreenClaude/backend/venv/bin/python -m pytest backend/tests/integration/test_live_group_history_bootstrap.py -q
```

- [ ] **Step 3: Complete fixture and fix only production defects it exposes**

Retain assertions for all rank-change periods, movers, Group RRG, fallback policy, and preservation of raw prices, scans, watchlists, and legacy rows.

- [ ] **Step 4: Run acceptance test and commit**

```bash
git add backend/tests/integration/test_live_group_history_bootstrap.py backend/app
git commit -m "Exercise production group history repair path"
```

### Task 5: Verification And PR Update

**Files:**
- Modify: `.beads/issues.jsonl`
- Modify: `.beads/interactions.jsonl`

- [ ] **Step 1: Run focused Group/RRG suite**

```bash
/Users/admin/StockScreenClaude/backend/venv/bin/python -m pytest backend/tests/unit/domain/test_bootstrap_plan.py backend/tests/unit/test_runtime_bootstrap_tasks.py backend/tests/unit/test_group_history_price_coverage.py backend/tests/unit/test_group_history_universe.py backend/tests/unit/test_group_history_readiness_service.py backend/tests/unit/test_group_history_bootstrap_service.py backend/tests/unit/test_group_history_tasks.py backend/tests/unit/test_group_history_reconciliation.py backend/tests/unit/test_rrg_service.py backend/tests/unit/test_ui_snapshot_service.py backend/tests/integration/test_live_group_history_bootstrap.py -q
```

- [ ] **Step 2: Run full backend unit suite and diff checks**

```bash
/Users/admin/StockScreenClaude/backend/venv/bin/python -m pytest backend/tests/unit -q
git diff --check
```

- [ ] **Step 3: Close and export Beads issue**

```bash
bd close stockscreenclaude-cbe --reason "Addressed PR 324 thermo-nuclear review findings"
bd export --no-memories -o .beads/issues.jsonl
```

- [ ] **Step 4: Commit, rebase, push, and verify PR state**

```bash
git add .beads/issues.jsonl .beads/interactions.jsonl
git commit -m "Close group history review remediation"
git pull --rebase origin main
git push
git status --short --branch
gh pr view 324 --json url,state,headRefName,baseRefName
```
