# Static RRG Bootstrap Universe Design

## Context

The first static-site run after the balanced Market RS formula change cannot reuse
old rolling RRG history. The restored `rrg-history-{market}.json.gz` is scoped by
market and RS formula, so legacy/static artifacts from the previous Group RS
calculation are not enough to render a 12-week RRG tail for
`balanced-horizon-percentile-v2`.

The July 24, 2026 scheduled run hydrated two years of US prices, but the
group-rank history backfill still processed only the newest three sessions. Older
sessions failed with `US current price coverage is 0.0%; 90.0% required` because
the static database's point-in-time universe evidence was seeded at the weekly
reference bundle baseline date, not 100 days back. For older dates, the standard
point-in-time universe resolver has no usable active universe.

## Goal

Unblock static-site RRG on the first successful run after a formula reset without
waiting 12 calendar weeks, while keeping the live app unchanged.

## Non-Goals

- Do not change live app RRG behavior.
- Do not backdate general universe lifecycle events in a way that affects normal
  point-in-time consumers.
- Do not reuse RRG history from a different market, schema, or RS formula.

## Design

The static export workflow will keep the existing rolling RRG restore path. After
restoring the prior `rrg-history-{market}.json.gz`, it will inspect the state for:

- matching market,
- matching RS formula,
- valid schema,
- enough weekly snapshots to satisfy `MIN_TAIL_WEEKS`.

If the restored history is sufficient, the exporter will use the normal path:
build the current group snapshot, merge it into restored history, write
`groups_rrg.json`, and publish the advanced rolling history.

If the restored history is missing or insufficient, static export will enter a
bootstrap-only backfill path for markets with RRG enabled. This path uses the
current active weekly-reference universe as the universe for prior historical
target dates, then builds enough balanced Market RS and group-rank snapshots to
produce at least 12 weekly RRG points. The bootstrap policy must be scoped to
static RRG startup only.

## Data Flow

1. Restore rolling RRG history from the GitHub release asset.
2. Refresh current prices and hydrate two years of startup price history.
3. Run normal recent group-rank history backfill.
4. If RRG history is still insufficient, run static bootstrap group-rank backfill:
   use current active weekly-reference symbols for historical dates.
5. Build a rolling RRG state from stored group-rank snapshots.
6. Publish `groups_rrg.json` only when the payload is available.
7. Persist and upload the updated rolling RRG history.

## Provenance

Bootstrap output should be traceable in diagnostics. The static export report
should include a policy marker such as:

`rrg_bootstrap_universe_policy: current_weekly_reference_static_bootstrap`

The static manifest may continue to use `features.rrg: true` only when
`groups_rrg.json` exists. Warnings should mention bootstrap only if they are
already surfaced through the existing static export warning channel.

## Error Handling

If price hydration is insufficient, benchmark anchors are unavailable, or the
bootstrap still cannot produce at least `MIN_TAIL_WEEKS`, the static market
artifact remains publishable without RRG. The exporter should preserve the
current behavior of omitting `groups_rrg.json` and setting `features.rrg: false`.

The bootstrap path must not mask schema, formula, or market mismatches in a
restored history artifact. Those should remain explicit warnings or diagnostics.

## Testing

Add unit coverage for:

- restored history with fewer than 12 weeks triggers bootstrap,
- bootstrap uses current active weekly-reference symbols for older dates,
- bootstrap produces at least `MIN_TAIL_WEEKS` snapshots when prices exist,
- live/API RRG providers remain unchanged,
- static export omits RRG if bootstrap cannot meet the readiness threshold.

Add a targeted integration-style test that reproduces the first-run condition:
weekly reference baseline is current, older point-in-time universe is unavailable,
and static bootstrap still generates a usable RRG payload.
