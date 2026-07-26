import { describe, expect, it } from 'vitest';
import {
  GROUP_RANK_CHANGE_FIELDS,
  GROUP_RS_FIELDS,
  formatGroupRs,
} from './groupRankingFields';

describe('groupRankingFields', () => {
  it('keeps live and static overall/1M/3M fields identical', () => {
    expect(GROUP_RS_FIELDS).toEqual([
      { field: 'avg_rs_rating', label: 'RS', staticLabel: 'Avg RS' },
      { field: 'avg_rs_rating_1m', label: '1M RS', staticLabel: '1M RS' },
      { field: 'avg_rs_rating_3m', label: '3M RS', staticLabel: '3M RS' },
    ]);
  });

  it('keeps live and static rank-change fields identical', () => {
    expect(GROUP_RANK_CHANGE_FIELDS).toEqual([
      { field: 'rank_change_1w', label: '1W', staticLabel: '1W' },
      { field: 'rank_change_1m', label: '1M Δ', staticLabel: '1M' },
      { field: 'rank_change_3m', label: '3M Δ', staticLabel: '3M' },
      { field: 'rank_change_6m', label: '6M', staticLabel: '6M' },
    ]);
  });

  it('formats finite ratings and renders missing values safely', () => {
    expect(formatGroupRs(87.25)).toBe('87.3');
    expect(formatGroupRs(null)).toBe('-');
    expect(formatGroupRs(Number.NaN)).toBe('-');
  });
});
