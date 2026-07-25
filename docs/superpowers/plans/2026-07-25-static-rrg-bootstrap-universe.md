# Static RRG Bootstrap Universe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a static-only RRG bootstrap path that uses the current weekly-reference universe for historical group snapshots when restored rolling RRG history is insufficient.

**Architecture:** Keep live app RRG untouched. Add a static bootstrap universe source and a small bootstrap backfill service that delegates snapshot materialization to `GroupRankSnapshotCoordinator` for enough recent trading sessions. Keep bootstrap orchestration inside `StaticGroupsRRGRollingHistoryExportSession`, so the CLI only wires the rolling history source and prints typed diagnostics.

**Tech Stack:** Python 3.11, SQLAlchemy ORM, pytest, existing Market RS services, existing static RRG history contracts.

## Global Constraints

- The bootstrap is static-site only.
- The live app and live API RRG providers must not change.
- Do not backdate general `stock_universe` lifecycle events.
- Do not reuse RRG history from a different market, schema, or RS formula.
- If bootstrap cannot produce enough history, the market artifact remains publishable without RRG.

---

## File Structure

- Create `backend/app/services/static_rrg_bootstrap_universe.py`: current-active universe resolver that implements the `PointInTimeUniverseService.resolve()` shape for static bootstrap.
- Create `backend/app/services/static_rrg_bootstrap_backfill_service.py`: selects weekly target dates, delegates Market RS and group-rank snapshot materialization to `GroupRankSnapshotCoordinator`, and reports readiness diagnostics.
- Modify `backend/app/services/static_rrg_history_bundle.py`: expose a focused readiness helper that counts weekly snapshots against `MIN_TAIL_WEEKS`.
- Modify `backend/app/services/static_groups_rrg_export.py`: invoke bootstrap from the rolling RRG export session when restored history is missing or insufficient, then rebuild the prepared payload state.
- Modify `backend/app/scripts/export_static_site.py`: keep CLI responsibility to source wiring, persistence, and status output.
- Test `backend/tests/unit/test_static_rrg_bootstrap_backfill_service.py`: isolated bootstrap service behavior.
- Test `backend/tests/unit/test_static_groups_rrg_sources.py`: rolling source invokes bootstrap only under the intended static conditions.

---

### Task 1: RRG History Readiness Helper

**Files:**
- Modify: `backend/app/services/static_rrg_history_bundle.py`
- Test: `backend/tests/unit/test_static_rrg_history_bundle.py`

**Interfaces:**
- Produces: `StaticRRGHistoryBundleService.has_minimum_history(state: StaticRRGHistoryState | None) -> bool`
- Consumes: `MIN_TAIL_WEEKS` from `app.services.rrg_service`

- [x] **Step 1: Write the failing tests**

Add tests that validate `None`, short state, and exactly-enough state:

```python
def test_static_rrg_history_readiness_requires_min_tail_weeks():
    service = StaticRRGHistoryBundleService()
    assert service.has_minimum_history(None) is False

    short = StaticRRGHistoryState(
        schema_version=STATIC_RRG_HISTORY_SCHEMA_VERSION,
        market="US",
        rs_formula_version=BALANCED_RS_FORMULA_VERSION,
        weeks=tuple(
            StaticRRGWeek(
                source_date=date(2026, 1, day + 1),
                groups=(
                    StaticRRGGroupPoint(
                        industry_group="Software",
                        rank=1,
                        avg_rs_rating=80.0,
                        num_stocks=5,
                    ),
                ),
            )
            for day in range(MIN_TAIL_WEEKS - 1)
        ),
    )
    assert service.has_minimum_history(short) is False

    enough = short.model_copy(
        update={
            "weeks": short.weeks
            + (
                StaticRRGWeek(
                    source_date=date(2026, 1, MIN_TAIL_WEEKS),
                    groups=short.weeks[-1].groups,
                ),
            )
        }
    )
    assert service.has_minimum_history(enough) is True
```

- [x] **Step 2: Run the focused test**

Run: `cd backend && pytest tests/unit/test_static_rrg_history_bundle.py::test_static_rrg_history_readiness_requires_min_tail_weeks -q`

Expected: fails because `has_minimum_history` does not exist.

- [x] **Step 3: Implement the helper**

Add the import and method:

```python
from app.services.rrg_service import MIN_TAIL_WEEKS, RRGService

def has_minimum_history(self, state: StaticRRGHistoryState | None) -> bool:
    return state is not None and len(state.weeks) >= MIN_TAIL_WEEKS
```

- [x] **Step 4: Run the focused test again**

Run: `cd backend && pytest tests/unit/test_static_rrg_history_bundle.py::test_static_rrg_history_readiness_requires_min_tail_weeks -q`

Expected: pass.

---

### Task 2: Static Bootstrap Backfill Service

**Files:**
- Create: `backend/app/services/static_rrg_bootstrap_universe.py`
- Create: `backend/app/services/static_rrg_bootstrap_backfill_service.py`
- Test: `backend/tests/unit/test_static_rrg_bootstrap_backfill_service.py`

**Interfaces:**
- Produces: `StaticRRGBootstrapUniversePolicy = "current_weekly_reference_static_bootstrap"`
- Produces: `StaticRRGBootstrapUniverse.resolve(db: Session, market: str, as_of_date: date) -> PointInTimeUniverse`
- Produces: `StaticRRGBootstrapBackfillService.backfill(db: Session, market: str, through_date: date, formula_version: str) -> StaticRRGBootstrapBackfillResult`
- Consumes: `GroupRankSnapshotCoordinator`, `MarketCalendarService`, `MarketRsRunRepository`

- [x] **Step 1: Write the universe resolver test**

Create a test that inserts two active US rows and one inactive row, calls
`StaticRRGBootstrapUniverse.resolve()` for an older date, and asserts only active
US symbols are returned with `as_of_date` equal to the requested historical date.

Run: `cd backend && pytest tests/unit/test_static_rrg_bootstrap_backfill_service.py::test_bootstrap_universe_uses_current_active_market_rows_for_historical_date -q`

Expected: fail because the module does not exist.

- [x] **Step 2: Implement the universe resolver**

Create `StaticRRGBootstrapUniverse`:

```python
StaticRRGBootstrapUniversePolicy = "current_weekly_reference_static_bootstrap"

class StaticRRGBootstrapUniverse:
    def resolve(self, db: Session, *, market: str, as_of_date: date) -> PointInTimeUniverse:
        normalized = str(market or "").strip().upper()
        symbols = tuple(
            row[0]
            for row in db.query(StockUniverse.symbol)
            .filter(StockUniverse.market == normalized, StockUniverse.active_filter())
            .order_by(StockUniverse.symbol.asc())
            .all()
        )
        payload = "".join(f"{symbol}\n" for symbol in symbols).encode("utf-8")
        return PointInTimeUniverse(
            market=normalized,
            as_of_date=as_of_date,
            symbols=symbols,
            universe_hash=hashlib.sha256(payload).hexdigest(),
        )
```

- [x] **Step 3: Write the backfill orchestration test**

Use fakes for calendar and `GroupRankSnapshotCoordinator`. Assert `backfill()`
selects the latest trading session from each week, uses the last
`MIN_TAIL_WEEKS` weekly targets, calls the coordinator once with typed
`GroupSnapshotIdentity` values, and returns:

```python
{
    "status": "completed",
    "policy": "current_weekly_reference_static_bootstrap",
    "processed": MIN_TAIL_WEEKS,
    "errors": 0,
}
```

Run: `cd backend && pytest tests/unit/test_static_rrg_bootstrap_backfill_service.py::test_bootstrap_backfill_materializes_min_tail_weeks -q`

Expected: fail because `StaticRRGBootstrapBackfillService` does not exist.

- [x] **Step 4: Implement the backfill service**

Implement a dataclass result with `as_dict()` and a service that:

```python
target_start = through_date - timedelta(days=DEFAULT_GROUP_RANK_HISTORY_LOOKBACK_DAYS)
target_dates = calendar_service.trading_days(market, target_start, through_date)
latest_by_week = {rrg_week_start(day): day for day in target_dates}
selected_dates = tuple(latest_by_week[key] for key in sorted(latest_by_week)[-MIN_TAIL_WEEKS:])
report = group_snapshot_coordinator.backfill(
    db,
    identities=tuple(GroupSnapshotIdentity(market, day, formula_version) for day in selected_dates),
    continue_on_error=True,
)
```

Use a bootstrap-specific `MarketRsInputLoader(point_in_time_universe=StaticRRGBootstrapUniverse())` in the default constructor so historical dates use the current active universe only inside this service.

- [x] **Step 5: Run the bootstrap service tests**

Run: `cd backend && pytest tests/unit/test_static_rrg_bootstrap_backfill_service.py -q`

Expected: pass.

---

### Task 3: Static Rolling RRG Integration

**Files:**
- Modify: `backend/app/services/static_groups_rrg_export.py`
- Modify: `backend/app/scripts/export_static_site.py`
- Test: `backend/tests/unit/test_static_groups_rrg_sources.py`

**Interfaces:**
- Consumes: `StaticRRGHistoryBundleService.prepare()` and `.has_minimum_history()`
- Consumes: `StaticRRGBootstrapBackfillService.backfill()`
- Produces session property: `StaticGroupsRRGRollingHistoryExportSession.bootstrap_backfill`

- [x] **Step 1: Write the exporter tests**

Add tests that inject:

```python
StaticGroupsRRGRollingHistoryExportSession(
    history_service=FakeHistoryService(),
    bootstrap_service=FakeBootstrapService(),
)
```

Assert the rolling source invokes bootstrap when `prepare().state` has fewer
than `MIN_TAIL_WEEKS` or reports a missing current snapshot, prepares again
after bootstrap, and does not invoke bootstrap when the prepared state is
sufficient.

Run: `cd backend && pytest tests/unit/test_static_groups_rrg_sources.py -q`

Expected: fail before integration.

- [x] **Step 2: Integrate into `StaticGroupsRRGRollingHistoryExportSession`**

After the first `history_service.prepare()`, add:

```python
if self._should_bootstrap(preparation):
    bootstrap_result = self.bootstrap_service.backfill(...)
    preparation = self.history_service.prepare(...)
```

Expose `bootstrap_backfill` from the session so the CLI can print diagnostics
without branching on daily-refresh result dictionaries.

- [x] **Step 3: Keep CLI integration narrow**

Construct `StaticGroupsRRGRollingHistoryExportSession` from `--rrg-history-dir`,
pass it to `StaticSiteExportService`, print `session.bootstrap_backfill` when
present, and persist after a successful export.

- [x] **Step 4: Run rolling source and CLI tests**

Run: `cd backend && pytest tests/unit/test_static_groups_rrg_sources.py tests/unit/test_export_static_rrg_history.py tests/unit/test_export_static_site_script.py -q`

Expected: pass.

---

### Task 4: Verification and Commit

**Files:**
- Verify all modified files.

**Interfaces:**
- Produces: committed implementation branch.

- [x] **Step 1: Run focused RRG tests**

Run:

```bash
cd backend
pytest tests/unit/test_static_rrg_history_bundle.py::test_static_rrg_history_readiness_requires_min_tail_weeks \
       tests/unit/test_static_rrg_bootstrap_backfill_service.py \
       tests/unit/test_static_groups_rrg_sources.py \
       tests/unit/test_export_static_rrg_history.py -q
```

Expected: pass.

- [x] **Step 2: Run broader static/export tests**

Run:

```bash
cd backend
pytest tests/unit/test_export_static_site_script.py \
       tests/unit/test_static_rrg_history_bundle.py \
       tests/unit/test_static_groups_rrg_sources.py \
       tests/unit/test_export_static_rrg_history.py -q
```

Expected: pass.

- [x] **Step 3: Commit implementation**

Run:

```bash
git add backend/app/services/static_rrg_bootstrap_universe.py \
        backend/app/services/static_rrg_bootstrap_backfill_service.py \
        backend/app/services/static_rrg_history_bundle.py \
        backend/app/services/static_groups_rrg_export.py \
        backend/app/scripts/export_static_site.py \
        backend/tests/unit/test_static_rrg_bootstrap_backfill_service.py \
        backend/tests/unit/test_static_rrg_history_bundle.py \
        backend/tests/unit/test_static_groups_rrg_sources.py \
        backend/tests/unit/test_export_static_rrg_history.py \
        backend/tests/unit/test_export_static_site_script.py
git commit -m "fix: keep static rrg bootstrap in export session"
```

Expected: commit succeeds.
