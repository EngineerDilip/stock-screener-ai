# Live Group History Bootstrap Design

## Context

PRs #317 through #323 repaired static-site Group ranking and RRG publication.
The static fixes established four relevant facts:

1. A symbol can have a fresh latest price while still missing exact historical
   adjusted-close anchors required by balanced Market RS.
2. A fresh database has current universe membership but no reliable historical
   lifecycle evidence, so strict point-in-time resolution can return an empty
   historical universe.
3. Group-rank history must be generated after broad price and feature-snapshot
   hydration.
4. Six months of formula-compatible Group snapshots supplies 1W, 1M, 3M, and
   6M rank changes, movers, Group detail history, and the weekly series required
   by RRG.

The live frontend already requests `/v1/groups/rrg/scopes`, pins requests to a
fresh Group bootstrap date, displays all four rank-change columns, and supports
sortable rankings. The remaining work is in live backend orchestration,
readiness, persistence refresh, and upgrade handling.

The existing live Group gap-fill is not sufficient. It uses the strict runtime
point-in-time universe, runs before the feature snapshot, and reports historical
date failures inside a result that does not necessarily abort its Celery chain.
The persisted US Group bootstrap snapshot is also published before repaired
history exists, so database repair and Redis invalidation alone would still let
clients receive stale null deltas and empty movers.

## Goals

- Make Group rankings, movers, Group detail history, and RRG ready on a fresh
  live bootstrap.
- Repair existing databases automatically in the background without blocking
  the rest of the live app.
- Reuse one six-month Group-history repair for ranking deltas and RRG instead of
  running separate overlapping backfills.
- Preserve raw prices, user data, valid historical snapshots, and legacy-formula
  rollback support.
- Make readiness formula-aware, market-aware, observable, resumable, and
  independently verifiable against the database.

## Non-Goals

- No live frontend redesign or API contract change.
- No reconstruction of historical universe lifecycle events.
- No claim that fallback snapshots are point-in-time constituent history.
- No synchronous repair inside an HTTP request.
- No destructive replacement of valid raw price history or valid Group
  snapshots.

## Decisions

### One shared Group-history bootstrap

A shared Group-history bootstrap will own the 187-calendar-day window defined by
`DEFAULT_CALENDAR_DAY_GROUP_RANK_HISTORY_LOOKBACK_DAYS`. It will materialize the
market's desired trading dates for the active RS formula. This daily history is
a superset of the 12 weekly observations required by RRG.

Static export, fresh runtime bootstrap, and existing-database maintenance will
use the same readiness and backfill core through thin orchestration adapters.
Static-specific artifact and publication behavior remains outside the shared
core.

### Universe policy

For each historical target date:

1. Use valid point-in-time membership when the database has usable lifecycle
   evidence for that date.
2. Otherwise use the current active weekly-reference universe for the market.

The result must record which policy supplied each date. Current-universe
fallback creates survivorship bias, but it is preferable to withholding RRG and
rank history for at least 12 weeks. It must not backdate or mutate universe
lifecycle records.

Static export may retain a current-only adapter where deterministic static
bootstrap behavior requires it; the shared interface must make the policy
explicit rather than embedding it in the calculation service.

### Existing-database upgrade

Deployment queues an idempotent background maintenance workflow for enabled
Group-ranking markets whose history is not ready. The app remains available.
RRG or rank-history fields may remain incomplete until maintenance succeeds.

A versioned AppSetting marker records market, active formula, policy version,
through date, status, counts, and the last error. The marker is an optimization
and observability record, not the source of truth. Every reconciliation verifies
actual database readiness before skipping work.

Fresh runtime bootstrap executes the same repair as a required stage and does
not mark the market ready if required Group history remains incomplete.

## Components

### GroupHistoryReadinessService

Responsibilities:

- Resolve the active formula for a market.
- Validate the current Group snapshot through `GroupRankSnapshotReader`.
- Resolve desired trading dates in the six-month window.
- Classify dates as valid, missing, or integrity-invalid for the active formula.
- Verify that the four calendar rank-change targets have a valid nearby
  snapshot within the existing seven-day tolerance.
- For RRG-enabled markets, use the live history provider and weekly bucketing to
  verify at least `MIN_TAIL_WEEKS` provider-usable observations and at least one
  plottable Group series.
- Return a structured report with no write side effects.

The service must not equate "a row exists on this date" with snapshot validity.
It must enforce contiguous ranks, formula identity, Market RS run identity, and
the balanced price-basis checks already implemented by the snapshot reader.

### GroupHistoryPriceCoverageService

This service extracts and generalizes the exact-anchor logic currently located
in `StaticDailyPriceRefreshService`.

Responsibilities:

- Resolve all Market RS horizon anchor sessions needed by missing Group target
  dates.
- Include current active price symbols and market benchmark/key-market symbols.
- Count only usable adjusted closes.
- Classify fresh-but-anchor-incomplete symbols for a two-year refresh.
- Return structured totals and symbol samples for diagnostics.

The live bootstrap price planner uses this classification in bootstrap and
maintenance modes. Symbols with sufficient latest coverage but incomplete
historical anchors must be treated like no-history symbols for period selection,
not like seven-day stale top-ups.

Static price refresh will consume the same requirement service so static and
live cannot drift on anchor semantics.

### GroupHistoryBootstrapService

Responsibilities:

- Accept a market, through date, formula, universe policy, and readiness report.
- Process only missing or explicitly repairable invalid snapshot identities,
  oldest to newest.
- Use the canonical coordinator for balanced history and the real runtime legacy
  service for legacy history.
- Commit each date independently and roll back only the failed date.
- Re-run readiness after processing and return a typed result.
- Never mark success from processed counts alone.

Valid existing identities are skipped. Invalid identities may be rebuilt only
within the exact market/date/formula identity after the replacement can be
calculated. No broad delete is permitted.

### Runtime orchestration

Add a semantic `ENSURE_GROUP_HISTORY` bootstrap operation and a `group_history`
stage after `snapshot` for markets with Group-ranking capability. Map it to a
serialized market-job Celery task.

During fresh runtime bootstrap, the existing `groups` stage must calculate the
current Group snapshot without running its historical gap-fill. The new
post-snapshot `group_history` stage is the sole owner of bootstrap history. This
avoids spending time on the strict point-in-time path before price and feature
snapshot hydration is complete. Normal daily pipelines may retain their existing
point-in-time gap-fill for ordinary recent gaps after initial history readiness.

The task must:

1. evaluate readiness;
2. run the shared backfill when needed;
3. fail the fresh bootstrap chain if readiness remains incomplete;
4. bump `bump_group_rankings_epoch(market)` after database readiness;
5. republish the persisted US Group bootstrap snapshot;
6. verify publication and persist the versioned readiness marker.

Non-US Group bootstrap responses are built dynamically, so only US requires the
persisted UI snapshot publication. All markets require the cache epoch bump.

### Upgrade reconciliation

After runtime services initialize, queue a lightweight reconciliation task. It
must skip while a full runtime bootstrap owns the market and must avoid duplicate
market work through the existing workload-serialization boundary plus an atomic
queued/running marker transition.

Use an AppSetting key scoped by marker schema version and market. Its JSON value
contains the active formula and through date, so a formula change cannot inherit
an earlier success marker. The atomic transition is committed before dispatch;
dispatch failure returns the marker to an incomplete state.

For each enabled Group-ranking market that is not ready, reconciliation queues a
cross-queue chain:

1. data-fetch queue: hydrate exact historical stock and benchmark anchors;
2. market-jobs queue: build and validate Group history;
3. Celery/default queue: record completion and publish diagnostics.

Interrupted or failed workflows leave the marker incomplete. A later startup or
scheduled reconciliation may resume them. Repeated reconciliation after success
is a database-verified no-op.

## Data Flow

### Fresh database

1. Load the current universe and Group taxonomy.
2. Refresh bootstrap prices.
3. Hydrate symbols and benchmarks missing exact historical RS anchors.
4. Calculate current Market RS and Group rankings.
5. Build and publish the daily feature snapshot.
6. Run `ensure_group_history` for the active formula.
7. Backfill missing dates using point-in-time membership or current-universe
   fallback.
8. Re-evaluate rank-change and RRG readiness.
9. Invalidate Group caches and republish the US Group bootstrap snapshot.
10. Record the marker and allow runtime bootstrap completion.

### Existing database

1. Startup reconciliation checks the marker and actual readiness.
2. Ready markets do not queue work.
3. Incomplete markets queue background price-coverage and history-repair work.
4. The live app remains available during repair.
5. Successful repair refreshes caches and UI snapshots and records readiness.
6. Failed repair preserves prior data and is eligible for a later retry.

Formula changes and newly enabled markets naturally create a distinct readiness
identity and require repair. Normal daily processing continues adding current
snapshots; reconciliation returns only when the recent usable window becomes
incomplete.

## Failure Semantics

- Historical price hydration reports missing-anchor totals and symbol samples.
- A historical date succeeds only when its Market RS and Group snapshots pass
  integrity validation.
- Fresh bootstrap fails the `group_history` stage if a Group-capable market is
  not ready after repair.
- Existing-database maintenance never changes global runtime bootstrap state and
  never blocks application routes.
- Cache invalidation and UI publication happen only after database readiness.
- UI publication failure does not roll back valid history, but maintenance stays
  incomplete so publication can be retried.
- A marker alone never makes a market ready.
- Unsupported markets return an explicit skipped result.

## Data Safety

- Stock prices are inserted by symbol/date. Existing valid historical adjusted
  closes are preserved; the established persistence policy may repair invalid
  adjusted closes or update the latest row.
- Group and Market RS records remain scoped by market, date, and formula.
- Legacy rows remain available when balanced rows are added.
- Valid existing snapshots are not recalculated.
- A repair may replace only an integrity-invalid derived snapshot for its exact
  identity; it cannot delete unrelated dates or formulas.
- Watchlists, scans, fundamentals, themes, runtime choices, and other user data
  are outside the write set.

## Caching and Publication

Empty RRG responses are not currently cached, but nonempty ranking payloads with
null change fields can be cached. Therefore every successful repair bumps the
per-market Group cache epoch.

The US frontend bootstrap fast path seeds ranking and mover query caches from the
persisted Group UI snapshot. That snapshot must be republished after history is
ready. Its new source revision must expose populated ranking deltas and movers.

## Observability

Publish a dedicated `group_history` maintenance/bootstrap activity containing:

- market, active formula, through date, and universe-policy version;
- required, valid, missing, invalid, processed, and failed dates;
- historical price-anchor coverage and refreshed-symbol counts;
- rank-change window readiness;
- RRG usable-week and plottable-series counts;
- cache invalidation and UI publication outcomes;
- elapsed time and resumable failure reason.

Statuses are `ready`, `repairing`, `incomplete`, `failed`, and `skipped`.

## Performance Expectations

The successful US static repair generated 129 missing Group dates. With price
history already present, equivalent live repair is expected to take roughly
10-20 minutes on typical hardware. Exact-anchor hydration may add 5-30 minutes,
with a 30-60 minute normal upgrade envelope and a possible one-to-two-hour tail
under provider throttling. A full fresh bootstrap remains longer because feature
snapshot generation dominates it.

Readiness checks and successful no-op reconciliation must avoid provider calls.

## Testing

Add focused unit coverage for:

- fresh history falling back to current active membership;
- existing point-in-time membership taking precedence;
- valid snapshots being skipped;
- partial and integrity-invalid snapshots being detected and safely rebuilt;
- formula-specific balanced and legacy coordinator selection;
- exact stock and benchmark anchor coverage;
- unusable adjusted closes counting as absent;
- fresh-but-history-incomplete symbols receiving a two-year refresh;
- all four rank-change references becoming available;
- movers being populated from repaired history;
- at least 12 provider-usable RRG weeks and a nonempty scopes response;
- Group-ranking markets without RRG not requiring RRG readiness;
- bootstrap stage ordering after `snapshot`;
- fresh bootstrap failure when history remains incomplete;
- non-blocking existing-database failure and retry;
- formula change and newly enabled market repair;
- cache invalidation only after database readiness;
- US UI snapshot republication after repair;
- repeated reconciliation becoming a no-op;
- preservation of raw prices, legacy rows, scans, and user data.

Add one integration-style test from sparse existing data through live API
responses. It must verify populated rank changes and movers from the rankings
API and nonempty data from `/v1/groups/rrg/scopes`.

## Deployment and Acceptance

No frontend deployment change or destructive data migration is required. The
release introduces the shared services, runtime tasks, marker handling, and
automatic reconciliation.

Acceptance requires:

- a new live bootstrap reaches ready with Group history and RRG available;
- an existing database remains usable while repair runs;
- successful repair populates 1W, 1M, 3M, and 6M fields plus movers;
- RRG is available without waiting 12 calendar weeks;
- a second reconciliation queues no repair work;
- interrupted repair resumes without loss of valid data.
