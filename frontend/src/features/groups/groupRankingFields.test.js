import { describe, expect, it } from 'vitest';
import {
  GROUP_RANK_CHANGE_FIELDS,
  GROUP_RS_FIELDS,
  LIVE_GROUP_RANKING_COLUMNS,
  STATIC_GROUP_RANKING_COLUMNS,
  deriveHistoricalRank,
  derivePctRsAbove80,
  formatGroupRs,
  getLiveGroupRankingSortValue,
} from './groupRankingFields';

describe('groupRankingFields', () => {
  it('keeps live and static overall/1M/3M fields identical', () => {
    expect(
      GROUP_RS_FIELDS.map(({ field, label, staticLabel }) => ({
        field,
        label,
        staticLabel,
      })),
    ).toEqual([
      { field: 'avg_rs_rating', label: 'RS', staticLabel: 'Avg RS' },
      { field: 'avg_rs_rating_1m', label: '1M RS', staticLabel: '1M RS' },
      { field: 'avg_rs_rating_3m', label: '3M RS', staticLabel: '3M RS' },
    ]);
  });

  it('keeps live and static rank-change fields identical', () => {
    expect(
      GROUP_RANK_CHANGE_FIELDS.map(({ field, label, staticLabel }) => ({
        field,
        label,
        staticLabel,
      })),
    ).toEqual([
      { field: 'rank_change_1w', label: '1W', staticLabel: '1W' },
      { field: 'rank_change_1m', label: '1M Δ', staticLabel: '1M' },
      { field: 'rank_change_3m', label: '3M Δ', staticLabel: '3M' },
      { field: 'rank_change_6m', label: '6M', staticLabel: '6M' },
    ]);
  });

  it('keeps live and static top-stock columns sort-addressable', () => {
    expect(
      LIVE_GROUP_RANKING_COLUMNS.find(({ field }) => field === 'top_symbol'),
    ).toEqual(
      expect.objectContaining({ field: 'top_symbol', label: 'Top', kind: 'topStock' }),
    );
    expect(
      STATIC_GROUP_RANKING_COLUMNS.find(({ field }) => field === 'top_symbol'),
    ).toEqual(
      expect.objectContaining({
        field: 'top_symbol',
        staticLabel: 'Top Stock',
        kind: 'topStock',
      }),
    );
  });

  it('derives live sort values for historical ranks and fallback percentages', () => {
    expect(getLiveGroupRankingSortValue(
      { rank: 3, rank_change_1w: 4 },
      'rank_change_1w',
      { showHistoricalRanks: true },
    )).toBe(7);
    expect(getLiveGroupRankingSortValue(
      { num_stocks: 5, num_stocks_rs_above_80: 2, pct_rs_above_80: null },
      'pct_rs_above_80',
    )).toBe(40);
  });

  it('exports derived group ranking display values with missing-data guards', () => {
    expect(deriveHistoricalRank({ rank: 3 }, 4)).toBe(7);
    expect(deriveHistoricalRank({ rank: 3 }, null)).toBeNull();
    expect(deriveHistoricalRank({ rank: null }, 4)).toBeNull();

    expect(derivePctRsAbove80({ pct_rs_above_80: 57.14 })).toBe(57.14);
    expect(derivePctRsAbove80({ num_stocks: 5, num_stocks_rs_above_80: 2 })).toBe(40);
    expect(derivePctRsAbove80({ num_stocks: 0, num_stocks_rs_above_80: 2 })).toBeNull();
    expect(derivePctRsAbove80({ num_stocks: null, num_stocks_rs_above_80: 2 })).toBeNull();
  });

  it('formats finite ratings and renders missing values safely', () => {
    expect(formatGroupRs(87.25)).toBe('87.3');
    expect(formatGroupRs(null)).toBe('-');
    expect(formatGroupRs(Number.NaN)).toBe('-');
  });
});
