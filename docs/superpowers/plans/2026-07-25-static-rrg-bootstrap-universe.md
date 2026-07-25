# Static RRG Bootstrap Universe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a static-only RRG bootstrap path that uses the current weekly-reference universe for historical group snapshots when restored rolling RRG history is insufficient.

**Architecture:** Keep live app RRG untouched. Add a static bootstrap universe source and a small bootstrap backfill service that materializes balanced Market RS and group-rank snapshots for enough recent trading sessions. Wire it into `export_static_site.py` only when `--refresh-daily --market --rrg-history-dir` is used and the restored history has fewer than `MIN_TAIL_WEEKS`.

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
- Create `backend/app/services/static_rrg_bootstrap_backfill_service.py`: orchestrates date selection, bootstrap Market RS snapshot calculation, group-rank persistence, and readiness diagnostics.
- Modify `backend/app/services/static_rrg_history_bundle.py`: expose a focused readiness helper that counts weekly snapshots against `MIN_TAIL_WEEKS`.
- Modify `backend/app/scripts/export_static_site.py`: pass `rrg_history_dir` into daily refresh and invoke bootstrap after normal group-rank backfill when restored history is insufficient.
- Test `backend/tests/unit/test_static_rrg_bootstrap_backfill_service.py`: isolated bootstrap service behavior.
- Test `backend/tests/unit/test_export_static_site_script.py`: exporter invokes bootstrap only under the intended static conditions.

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
- Consumes: `MarketRsInputLoader`, `MarketRsSnapshotService`, `CanonicalGroupRankingService`, `MarketCalendarService`, `MarketRsRunRepository`

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

Use fakes for calendar, snapshot service, repository, and group service. Seed no
existing group dates. Assert `backfill()` selects the latest trading session from
each week and uses the last `MIN_TAIL_WEEKS` weekly targets, calls
snapshot/group calculation for each missing date, and returns:

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
existing_dates = query IBDGroupRank.date for market/formula/selected range
for calculation_date in selected_dates:
    if calculation_date in existing_dates:
        existing += 1
        continue
    run = market_rs_snapshot_service.calculate(...)
    rows = canonical_group_service.calculate_and_store(...)
    processed += 1
```

Use a bootstrap-specific `MarketRsInputLoader(point_in_time_universe=StaticRRGBootstrapUniverse())` in the default constructor so historical dates use the current active universe only inside this service.

- [x] **Step 5: Run the bootstrap service tests**

Run: `cd backend && pytest tests/unit/test_static_rrg_bootstrap_backfill_service.py -q`

Expected: pass.

---

### Task 3: Static Export Integration

**Files:**
- Modify: `backend/app/scripts/export_static_site.py`
- Test: `backend/tests/unit/test_export_static_site_script.py`

**Interfaces:**
- Consumes: `StaticRRGHistoryBundleService.prepare()` and `.has_minimum_history()`
- Consumes: `StaticRRGBootstrapBackfillService.backfill()`
- Produces daily-refresh result key: `rrg_bootstrap_backfill`

- [x] **Step 1: Write the exporter tests**

Add tests that monkeypatch:

```python
export_script.StaticRRGHistoryBundleService
export_script.StaticRRGBootstrapBackfillService
```

Assert that `_run_daily_refresh(market="US", rrg_history_dir=Path("/tmp/rrg"))`
invokes bootstrap when `prepare().state` has fewer than `MIN_TAIL_WEEKS`, and
does not invoke bootstrap when the prepared state is sufficient.

Run: `cd backend && pytest tests/unit/test_export_static_site_script.py -k rrg_bootstrap -q`

Expected: fail because `_run_daily_refresh` has no `rrg_history_dir` argument.

- [x] **Step 2: Integrate into `_run_daily_refresh`**

Add parameter:

```python
rrg_history_dir: Path | None = None,
```

After normal `group_rank_history_backfill`, add:

```python
rrg_bootstrap_backfill = {}
if rrg_history_dir is not None:
    for selected_market in selected_markets:
        bootstrap_result = _bootstrap_static_rrg_history_if_needed(
            db_session_factory=SessionLocal,
            rrg_history_dir=rrg_history_dir,
            market=selected_market,
            as_of_date=as_of_by_market[selected_market],
            formula_version=formula_by_market[selected_market],
        )
        rrg_bootstrap_backfill[selected_market] = bootstrap_result
results["rrg_bootstrap_backfill"] = rrg_bootstrap_backfill
```

Implement `_bootstrap_static_rrg_history_if_needed()` to load/prepare current
history, skip when RRG is not enabled or history is already sufficient, and run
`StaticRRGBootstrapBackfillService().backfill()` otherwise.

- [x] **Step 3: Pass CLI history directory into refresh**

Change the `main()` call:

```python
rrg_history_dir=Path(args.rrg_history_dir) if args.rrg_history_dir else None,
```

- [x] **Step 4: Run exporter tests**

Run: `cd backend && pytest tests/unit/test_export_static_site_script.py -k rrg_bootstrap -q`

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
       tests/unit/test_export_static_site_script.py -k "rrg_bootstrap or static_rrg_history_readiness" -q
```

Expected: pass.

- [x] **Step 2: Run broader static/export tests**

Run:

```bash
cd backend
pytest tests/unit/test_export_static_site_script.py tests/unit/test_static_rrg_history_bundle.py -q
```

Expected: pass.

- [x] **Step 3: Commit implementation**

Run:

```bash
git add backend/app/services/static_rrg_bootstrap_universe.py \
        backend/app/services/static_rrg_bootstrap_backfill_service.py \
        backend/app/services/static_rrg_history_bundle.py \
        backend/app/scripts/export_static_site.py \
        backend/tests/unit/test_static_rrg_bootstrap_backfill_service.py \
        backend/tests/unit/test_static_rrg_history_bundle.py \
        backend/tests/unit/test_export_static_site_script.py
git commit -m "fix: bootstrap static rrg history with current universe"
```

Expected: commit succeeds.
