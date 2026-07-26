import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Alert,
  Box,
  CircularProgress,
  Grid,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TableSortLabel,
  Typography,
} from '@mui/material';
import {
  useStaticManifest,
  fetchStaticJson,
  resolveStaticMarketEntry,
  useStaticGroupsRRG,
} from '../dataClient';
import { useStaticChartIndex } from '../chartClient';
import StaticGroupDetailModal from '../StaticGroupDetailModal';
import RRGChart from '../../components/Charts/RRGChart';
import RRGViewToggle from '../../components/Charts/RRGViewToggle';
import { useRRGScopeSelection } from '../../components/Charts/useRRGScopeSelection';
import RankChangeCell from '../../components/shared/RankChangeCell';
import TickerCell from '../../components/common/TickerCell';
import { useStaticMarket } from '../StaticMarketContext';
import { GROUP_RS_FIELDS, formatGroupRs } from '../../features/groups/groupRankingFields';

const GROUP_RANK_CHANGE_COLUMNS = [
  { field: 'rank_change_1w', label: '1W' },
  { field: 'rank_change_1m', label: '1M' },
  { field: 'rank_change_3m', label: '3M' },
  { field: 'rank_change_6m', label: '6M' },
];

function compareGroupRankingValues(aValue, bValue) {
  const aMissing = aValue === null || aValue === undefined || aValue === '';
  const bMissing = bValue === null || bValue === undefined || bValue === '';
  if (aMissing && bMissing) return 0;
  if (aMissing) return 1;
  if (bMissing) return -1;

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
}

function sortGroupRankings(rows, orderBy, order) {
  return [...rows].sort((a, b) => {
    const valueComparison = compareGroupRankingValues(a[orderBy], b[orderBy]);
    const directedComparison = order === 'asc' ? valueComparison : -valueComparison;
    if (directedComparison !== 0) {
      return directedComparison;
    }
    return (
      compareGroupRankingValues(a.rank, b.rank)
      || compareGroupRankingValues(a.industry_group, b.industry_group)
    );
  });
}

function SortableTableCell({ field, label, align = 'left', orderBy, order, onSort }) {
  return (
    <TableCell align={align}>
      <TableSortLabel
        active={orderBy === field}
        direction={orderBy === field ? order : 'asc'}
        onClick={() => onSort(field)}
      >
        {label}
      </TableSortLabel>
    </TableCell>
  );
}

function MoversCard({ title, rows }) {
  return (
    <Paper elevation={0} sx={{ p: 1.5, height: '100%', border: '1px solid', borderColor: 'divider' }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 600, fontSize: '13px', textTransform: 'uppercase', letterSpacing: '0.5px', mb: 0.5 }}>
        {title}
      </Typography>
      <TableContainer>
        <Table size="small" sx={{ minWidth: 360 }}>
          <TableHead>
            <TableRow>
              <TableCell>Group</TableCell>
              <TableCell align="right">Rank</TableCell>
              <TableCell align="right">Change</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {(rows || []).slice(0, 5).map((row) => (
              <TableRow key={`${title}-${row.industry_group}`}>
                <TableCell>{row.industry_group}</TableCell>
                <TableCell align="right">{row.rank}</TableCell>
                <TableCell align="right">
                  <RankChangeCell value={row.rank_change_1w} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </TableContainer>
    </Paper>
  );
}

function GroupsTableView({ movers, moversPeriod, rankings, onSelectGroup }) {
  const [orderBy, setOrderBy] = useState('rank');
  const [order, setOrder] = useState('asc');
  const sortedRankings = useMemo(
    () => sortGroupRankings(rankings, orderBy, order),
    [rankings, orderBy, order],
  );

  const handleSort = (field) => {
    const isAsc = orderBy === field && order === 'asc';
    setOrder(isAsc ? 'desc' : 'asc');
    setOrderBy(field);
  };

  return (
    <>
      <Grid container spacing={1.5} sx={{ mb: 2 }}>
        <Grid item xs={12} md={6}>
          <MoversCard title={`Top Gainers (${moversPeriod.toUpperCase()})`} rows={movers.gainers} />
        </Grid>
        <Grid item xs={12} md={6}>
          <MoversCard title={`Top Losers (${moversPeriod.toUpperCase()})`} rows={movers.losers} />
        </Grid>
      </Grid>

      <Paper elevation={0} sx={{ p: 1.5, border: '1px solid', borderColor: 'divider' }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, fontSize: '13px', textTransform: 'uppercase', letterSpacing: '0.5px', mb: 0.5 }}>
          Current Rankings
        </Typography>
        <TableContainer>
          <Table size="small" sx={{ minWidth: 860 }}>
            <TableHead>
              <TableRow>
                <SortableTableCell
                  field="rank"
                  label="Rank"
                  align="center"
                  orderBy={orderBy}
                  order={order}
                  onSort={handleSort}
                />
                <SortableTableCell
                  field="industry_group"
                  label="Group"
                  orderBy={orderBy}
                  order={order}
                  onSort={handleSort}
                />
                {GROUP_RS_FIELDS.map(({ field, staticLabel }) => (
                  <SortableTableCell
                    key={field}
                    field={field}
                    label={staticLabel}
                    align="center"
                    orderBy={orderBy}
                    order={order}
                    onSort={handleSort}
                  />
                ))}
                <SortableTableCell
                  field="num_stocks"
                  label="Stocks"
                  align="center"
                  orderBy={orderBy}
                  order={order}
                  onSort={handleSort}
                />
                {GROUP_RANK_CHANGE_COLUMNS.map(({ field, label }) => (
                  <SortableTableCell
                    key={field}
                    field={field}
                    label={label}
                    align="right"
                    orderBy={orderBy}
                    order={order}
                    onSort={handleSort}
                  />
                ))}
                <SortableTableCell
                  field="top_symbol"
                  label="Top Stock"
                  orderBy={orderBy}
                  order={order}
                  onSort={handleSort}
                />
              </TableRow>
            </TableHead>
            <TableBody>
              {sortedRankings.map((row) => (
                <TableRow
                  key={row.industry_group}
                  hover
                  onClick={() => onSelectGroup(row.industry_group)}
                  tabIndex={0}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      onSelectGroup(row.industry_group);
                    }
                  }}
                  sx={{ cursor: 'pointer' }}
                >
                  <TableCell align="center" sx={{ fontFamily: 'monospace', fontWeight: 600 }}>{row.rank}</TableCell>
                  <TableCell>{row.industry_group}</TableCell>
                  {GROUP_RS_FIELDS.map(({ field }) => (
                    <TableCell key={field} align="center" sx={{ fontFamily: 'monospace' }}>
                      {formatGroupRs(row[field])}
                    </TableCell>
                  ))}
                  <TableCell align="center" sx={{ fontFamily: 'monospace' }}>{row.num_stocks}</TableCell>
                  <TableCell align="right"><RankChangeCell value={row.rank_change_1w} /></TableCell>
                  <TableCell align="right"><RankChangeCell value={row.rank_change_1m} /></TableCell>
                  <TableCell align="right"><RankChangeCell value={row.rank_change_3m} /></TableCell>
                  <TableCell align="right"><RankChangeCell value={row.rank_change_6m} /></TableCell>
                  <TableCell sx={{ fontSize: '12px' }}>
                    <TickerCell symbol={row.top_symbol} companyName={row.top_symbol_name} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </Paper>
    </>
  );
}

function StaticGroupsPage() {
  const manifestQuery = useStaticManifest();
  const { selectedMarket } = useStaticMarket();
  const marketEntry = useMemo(
    () => resolveStaticMarketEntry(manifestQuery.data, selectedMarket),
    [manifestQuery.data, selectedMarket],
  );
  const groupsQuery = useQuery({
    queryKey: ['staticGroups', marketEntry.pages?.groups?.path],
    queryFn: () => fetchStaticJson(marketEntry.pages.groups.path),
    enabled: Boolean(marketEntry.pages?.groups?.path),
    staleTime: Infinity,
  });
  const chartIndexQuery = useStaticChartIndex(marketEntry.assets?.charts?.path);
  const rrgQuery = useStaticGroupsRRG(marketEntry);
  const rrgAvailable = Boolean(marketEntry.assets?.groups_rrg?.path);
  const [selectedGroup, setSelectedGroup] = useState(null);
  const [view, setView] = useState('table'); // 'table' | 'rrg'
  const [rrgScope, setRrgScope] = useState('groups'); // 'groups' | 'sectors'
  const { availableScopes: availableRrgScopes } = useRRGScopeSelection({
    view,
    scope: rrgScope,
    setView,
    setScope: setRrgScope,
    rrgAvailable,
    bundle: rrgQuery.data,
  });

  if (manifestQuery.isLoading || groupsQuery.isLoading) {
    return (
      <Box display="flex" justifyContent="center" py={8}>
        <CircularProgress />
      </Box>
    );
  }

  if (manifestQuery.isError || groupsQuery.isError) {
    return <Alert severity="error">Failed to load group rankings.</Alert>;
  }

  if (!groupsQuery.data?.available) {
    return <Alert severity="info">{groupsQuery.data?.message || 'No group rankings are available.'}</Alert>;
  }

  const payload = groupsQuery.data.payload || {};
  const rankings = payload.rankings?.rankings || [];
  const movers = payload.movers || {};
  const moversPeriod = payload.movers_period || movers.period || '1w';
  const groupDetails = payload.group_details || {};

  return (
    <Box>
      <Typography variant="h5" sx={{ fontWeight: 700, letterSpacing: '-0.5px', mb: 0.5 }}>
        {marketEntry.display_name} Group Rankings
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2, fontSize: '12px' }}>
        Latest ranking date: {payload.rankings?.date || '-'}.
      </Typography>

      {rrgAvailable && (
        <RRGViewToggle
          view={view}
          onView={setView}
          scope={rrgScope}
          onScope={setRrgScope}
          rrgAvailable={rrgAvailable}
          availableScopes={availableRrgScopes}
          sx={{ mb: 2 }}
        />
      )}

      {view === 'rrg' ? (
        <RRGChart
          data={rrgQuery.data?.payload?.[rrgScope]}
          isLoading={rrgQuery.isLoading}
          error={rrgQuery.isError ? rrgQuery.error : null}
          onSelectGroup={(name) => rrgScope === 'groups' && setSelectedGroup(name)}
        />
      ) : (
        <GroupsTableView
          movers={movers}
          moversPeriod={moversPeriod}
          rankings={rankings}
          onSelectGroup={setSelectedGroup}
        />
      )}

      <StaticGroupDetailModal
        group={selectedGroup}
        detail={selectedGroup ? groupDetails[selectedGroup] : null}
        chartIndex={chartIndexQuery.data}
        open={!!selectedGroup}
        onClose={() => setSelectedGroup(null)}
      />
    </Box>
  );
}

export default StaticGroupsPage;
