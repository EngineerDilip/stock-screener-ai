# Live Group History Review Remediation Design

## Goal

Remove the concurrency, identity, lifecycle, query-volume, and test-boundary
risks identified in the thermo-nuclear review of PR #324 without changing the
live Group API contracts or deleting persisted data.

## Constraints

- Preserve raw prices, scans, watchlists, valid Group snapshots, and rows from
  inactive RS formulas.
- Support PostgreSQL in live deployments and SQLite in desktop/test runtimes.
- Keep existing `AppSetting` marker storage; no schema migration is needed.
- Fresh bootstrap remains strict. Existing-database reconciliation remains
  nonblocking.
- Cache invalidation and US Group snapshot publication occur exactly once after
  database-verified readiness.

## Reconciliation Ownership

FastAPI startup will enqueue a lightweight Celery reconciliation-discovery task
and retain no background thread or task handle. All database inspection and
workflow dispatch move to Celery.

The discovery task resolves one immutable `GroupHistoryTarget` per enabled
Group market:

```python
@dataclass(frozen=True)
class GroupHistoryTarget:
    market: str
    formula_version: str
    through_date: date
```

Every readiness check, repair, marker transition, and completion result uses
that target. The active formula and latest completed session are resolved once,
before reservation, and are never silently replaced later in the chain.

## Atomic Reservation

`AppSetting.value` remains the serialized marker. Reservation uses an atomic
compare-and-swap:

1. Read the current row and retain its exact serialized value.
2. Decide whether the target is already active and fresh.
3. Execute `UPDATE app_settings SET value = :queued WHERE key = :key AND
   value = :previous`.
4. Dispatch only when the update affects exactly one row.
5. For a missing row, insert and treat a unique-key conflict as a lost race.

This is portable across PostgreSQL and SQLite and prevents four Uvicorn workers
from dispatching duplicate chains. Reservation occurs before expensive
readiness evaluation; the winning discovery task either marks the target ready
or dispatches its repair chain.

## Execution And Finalization

Fresh bootstrap and existing-database reconciliation use separate thin Celery
entry points backed by one application executor.

- Fresh bootstrap resolves a target and executes it with strict failure
  semantics.
- Reconciliation receives the reserved target, marks it repairing, executes
  repair, and finalizes the marker without raising for incomplete history.

The executor owns the sequence:

1. Evaluate and repair the exact target.
2. If database readiness is incomplete, record failure/incomplete status.
3. If ready, bump the Group cache epoch.
4. Publish the US Group UI snapshot once.
5. Mark activity and reconciliation state from the final outcome.

The separate reconciliation completion task is removed. There is no paired
optional formula/date contract and no duplicated publication.

## Batched Readiness

`GroupRankSnapshotReader` gains a window read that fetches all Group rows for
one market/formula/date set in one query and all referenced Market RS runs in a
second query. It validates each grouped identity with the same invariants as
`load_exact` and returns a date-keyed snapshot mapping.

`GroupHistoryReadinessService` classifies desired dates from this mapping.
`StoredGroupRankHistoryProvider` uses the same window reader, reducing RRG input
loading from one query per date to bounded batch queries. Readiness may perform
one batch for classification and one through the provider, but no longer scales
database round trips linearly with the number of dates.

## Acceptance Coverage

The integration test will seed:

- an active balanced RS formula;
- current active universe symbols;
- real industry-group memberships;
- adjusted-close prices for every exact Market RS anchor;
- representative raw price, scan, watchlist, and legacy-formula rows.

It will build the production `GroupRankSnapshotCoordinator` with the real
fallback resolver and execute `GroupHistoryBootstrapService`. Existing API
assertions remain: all four rank-change columns are populated, every mover
period has gainers and losers, Group RRG is nonempty, and unrelated data counts
are preserved.

## Testing

- A two-session compare-and-swap test proves only one existing-marker
  reservation wins.
- Startup tests prove FastAPI only enqueues Celery work and retains no task to
  cancel.
- Target tests prove a captured formula/date reaches readiness and completion
  unchanged even if the active formula or wall-clock date changes.
- Finalization tests prove one cache invalidation and one US publication.
- Query-count tests prove snapshot-window loading is bounded.
- The integration acceptance test crosses the production coordinator boundary.

