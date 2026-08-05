/**
 * ExpirationSelector
 *
 * Lets a user compare options positioning across different expiration
 * dates using TODAY's data -- a cross-section ("term structure"), not a
 * forecast. Selecting an expiration triggers a live compute (Max Pain + GEX
 * for that specific expiration) via GET /v1/options/term-structure/{symbol},
 * which also persists the result so /v1/max-pain/history and
 * /v1/gex/history (filtered to that expiration) start accumulating real
 * history for it going forward.
 *
 * "Nearest expiration (default)" clears the expiration filter, returning to
 * the daily batch's own series -- the same history that existed before this
 * feature.
 */
import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Box,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  ListSubheader,
  Typography,
  CircularProgress,
  Alert,
} from '@mui/material';
import apiClient from '../../api/client';

const BUCKET_LABELS = {
  this_week: 'This Week',
  this_month: 'This Month',
  this_quarter: 'This Quarter',
  later: 'Later',
};
const BUCKET_ORDER = ['this_week', 'this_month', 'this_quarter', 'later'];

function fmtCompact(value) {
  if (value == null || Number.isNaN(Number(value))) return '—';
  const num = Number(value);
  const abs = Math.abs(num);
  if (abs >= 1e9) return `${(num / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(num / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${(num / 1e3).toFixed(1)}K`;
  return num.toFixed(2);
}

/**
 * @param {Object} props
 * @param {string} props.symbol
 * @param {string|null} props.expiration - Currently selected expiration (YYYY-MM-DD) or null for default
 * @param {(expiration: string|null) => void} props.onExpirationChange
 */
export default function ExpirationSelector({ symbol, expiration, onExpirationChange }) {
  const queryClient = useQueryClient();

  const { data: expirationsData, isLoading: loadingExpirations, isError: expirationsError } = useQuery({
    queryKey: ['options-expirations', symbol],
    queryFn: async () => {
      const resp = await apiClient.get(`/v1/options/expirations/${symbol}`);
      return resp.data;
    },
    enabled: Boolean(symbol),
    staleTime: 60 * 60 * 1000,
  });

  const termStructureMutation = useMutation({
    mutationFn: async (exp) => {
      const resp = await apiClient.get(`/v1/options/term-structure/${symbol}`, {
        params: { expiration: exp },
      });
      return resp.data;
    },
    onSuccess: () => {
      // The term-structure call just persisted a fresh snapshot for this
      // expiration -- invalidate so the history charts pick it up.
      queryClient.invalidateQueries({ queryKey: ['metric-history'] });
    },
  });

  const handleChange = (event) => {
    const value = event.target.value;
    const nextExpiration = value === '' ? null : value;
    onExpirationChange(nextExpiration);
    if (nextExpiration) {
      termStructureMutation.mutate(nextExpiration);
    }
  };

  if (!symbol) return null;

  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2, flexWrap: 'wrap' }}>
      <FormControl size="small" sx={{ minWidth: 260 }}>
        <InputLabel id="expiration-select-label">Expiration</InputLabel>
        <Select
          labelId="expiration-select-label"
          label="Expiration"
          value={expiration ?? ''}
          onChange={handleChange}
          disabled={loadingExpirations}
        >
          <MenuItem value="">Nearest expiration (default)</MenuItem>
          {expirationsData &&
            BUCKET_ORDER.flatMap((bucketKey) => {
              const dates = expirationsData.buckets?.[bucketKey] || [];
              if (dates.length === 0) return [];
              return [
                <ListSubheader key={`header-${bucketKey}`}>{BUCKET_LABELS[bucketKey]}</ListSubheader>,
                ...dates.map((date) => (
                  <MenuItem key={date} value={date}>
                    {date}
                  </MenuItem>
                )),
              ];
            })}
        </Select>
      </FormControl>

      {loadingExpirations && <CircularProgress size={20} />}
      {expirationsError && (
        <Alert severity="warning" sx={{ py: 0 }}>
          Could not load expirations for {symbol}
        </Alert>
      )}

      {termStructureMutation.isPending && (
        <Typography variant="caption" color="text.secondary">
          Computing {expiration} term structure…
        </Typography>
      )}
      {termStructureMutation.isError && (
        <Alert severity="error" sx={{ py: 0 }}>
          Failed to compute term structure for {expiration}
        </Alert>
      )}
      {termStructureMutation.isSuccess && expiration && (
        <Typography variant="caption" color="text.secondary">
          {expiration}: Max Pain ${termStructureMutation.data?.max_pain_strike?.toFixed(2) ?? '—'} · Total GEX{' '}
          {fmtCompact(termStructureMutation.data?.total_gex)} · Spot $
          {termStructureMutation.data?.underlying_price?.toFixed(2) ?? '—'}
        </Typography>
      )}
    </Box>
  );
}
