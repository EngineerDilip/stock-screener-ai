const isMissingSortValue = (value) => (
  value === null || value === undefined || value === ''
);

const comparePresentSortValues = (aValue, bValue) => {
  if (typeof aValue === 'string' || typeof bValue === 'string') {
    return String(aValue).localeCompare(String(bValue), undefined, {
      numeric: true,
      sensitivity: 'base',
    });
  }

  const aNumber = Number(aValue);
  const bNumber = Number(bValue);
  if (Number.isFinite(aNumber) && Number.isFinite(bNumber)) {
    return aNumber < bNumber ? -1 : aNumber > bNumber ? 1 : 0;
  }

  return String(aValue).localeCompare(String(bValue), undefined, {
    numeric: true,
    sensitivity: 'base',
  });
};

export const compareGroupRankingValues = (aValue, bValue, order = 'asc') => {
  const aMissing = isMissingSortValue(aValue);
  const bMissing = isMissingSortValue(bValue);
  if (aMissing && bMissing) return 0;
  if (aMissing) return 1;
  if (bMissing) return -1;

  const direction = order === 'desc' ? -1 : 1;
  return direction * comparePresentSortValues(aValue, bValue);
};

export const sortGroupRankings = (
  rows,
  orderBy,
  order,
  getSortValue = (row, column) => row[column],
) => [...rows].sort((a, b) => (
  compareGroupRankingValues(
    getSortValue(a, orderBy),
    getSortValue(b, orderBy),
    order,
  )
  || compareGroupRankingValues(a.rank, b.rank)
  || compareGroupRankingValues(a.industry_group, b.industry_group)
));
