import { describe, expect, it } from 'vitest';

import { sortGroupRankings } from './groupRankingSort';

describe('groupRankingSort', () => {
  const rows = [
    { industry_group: 'Semiconductors', rank: 1, rank_change_6m: null },
    { industry_group: 'Retail', rank: 2, rank_change_6m: 0 },
    { industry_group: 'Software', rank: 3, rank_change_6m: 4 },
  ];

  it('keeps missing values last for descending sorts', () => {
    expect(
      sortGroupRankings(rows, 'rank_change_6m', 'desc')
        .map((row) => row.industry_group),
    ).toEqual(['Software', 'Retail', 'Semiconductors']);
  });

  it('uses rank and group as deterministic tie breakers', () => {
    expect(
      sortGroupRankings(
        [
          { industry_group: 'Beta', rank: 2, avg_rs_rating: 50 },
          { industry_group: 'Alpha', rank: 1, avg_rs_rating: 50 },
        ],
        'avg_rs_rating',
        'desc',
      ).map((row) => row.industry_group),
    ).toEqual(['Alpha', 'Beta']);
  });
});
