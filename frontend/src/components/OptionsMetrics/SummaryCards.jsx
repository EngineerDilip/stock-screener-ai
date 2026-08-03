import { Grid, Paper, Typography, Box, Chip } from '@mui/material';

function MetricCard({ title, value, subtitle, color, status }) {
  const isZero = value === 0 || value === '0';
  return (
    <Paper sx={{ p: 2, height: '100%', backgroundColor: isZero ? 'rgba(255,152,0,0.05)' : 'inherit' }}>
      <Typography variant="caption" color="text.secondary">{title}</Typography>
      <Typography variant="h6" sx={{ color: color || 'inherit' }}>
        {value ?? '—'}
      </Typography>
      {status && (
        <Box sx={{ mt: 1 }}>
          <Chip label={status.label} size="small" color={status.color} />
          {status.description && (
            <Typography variant="caption" sx={{ display: 'block', mt: 0.5, color: 'text.secondary' }}>
              {status.description}
            </Typography>
          )}
        </Box>
      )}
      {subtitle && <Typography variant="caption" color="text.secondary">{subtitle}</Typography>}
      {isZero && <Typography variant="caption" sx={{ display: 'block', mt: 0.5, color: 'warning.main' }}>No data</Typography>}
    </Paper>
  );
}

export default function SummaryCards({ data, showGreekExposures = true }) {
  const {
    key_levels,
    net,
    ivr,
    skew,
    historical_volatility,
    volatility_risk_premium,
    expected_move,
    call_premium_notional,
    put_premium_notional,
  } = data;

  const invalidSameStrikeLevels = Boolean(
    key_levels?.call_wall != null &&
    key_levels?.put_wall != null &&
    key_levels?.zero_gamma != null &&
    key_levels.call_wall === key_levels.put_wall &&
    key_levels.call_wall === key_levels.zero_gamma &&
    (net?.net_dex ?? 0) === 0 &&
    (net?.net_vex ?? 0) === 0 &&
    (net?.net_cex ?? 0) === 0
  );

  const safeKeyLevel = (value) => {
    if (invalidSameStrikeLevels) return null;
    return Number.isFinite(value) ? `$${value.toFixed(2)}` : '—';
  };

  const formatNumber = (n) => {
    if (n === null || n === undefined) return 'Premium Data Required';
    if (n === 0) return '0';
    if (Math.abs(n) > 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (Math.abs(n) > 1e3) return (n / 1e3).toFixed(1) + 'K';
    return Number(n).toLocaleString('en-US', { maximumFractionDigits: 0 });
  };

  const getMetricStatus = (metric, value) => {
    if (value == null || Number.isNaN(Number(value))) return null;
    const num = Number(value);

    if (metric === 'ivr') {
      if (num < 20) {
        return {
          label: 'Low IVR',
          color: 'warning',
          description: 'Implied volatility is low; options are cheaper but may underprice future moves.',
        };
      }
      if (num > 80) {
        return {
          label: 'High IVR',
          color: 'warning',
          description: 'Implied volatility is elevated; premiums are rich and downside protection is expensive.',
        };
      }
      return {
        label: 'Normal IVR',
        color: 'success',
        description: 'IVR is in a balanced range; option prices are neither overly cheap nor rich.',
      };
    }

    if (metric === 'skew') {
      if (num > 0.01) {
        return {
          label: 'Put Skew',
          color: 'warning',
          description: 'Put IV exceeds call IV, indicating demand for downside protection.',
        };
      }
      if (num < -0.01) {
        return {
          label: 'Call Skew',
          color: 'success',
          description: 'Call IV exceeds put IV, suggesting bullish demand for upside exposure.',
        };
      }
      return {
        label: 'Neutral Skew',
        color: 'success',
        description: 'Volatility skew is balanced across calls and puts.',
      };
    }

    if (metric === 'historical_volatility') {
      if (num < 15) {
        return {
          label: 'Low HV',
          color: 'success',
          description: 'Realized volatility is low; the stock has been relatively quiet recently.',
        };
      }
      if (num > 35) {
        return {
          label: 'High HV',
          color: 'warning',
          description: 'Realized volatility is elevated; recent returns have been large relative to history.',
        };
      }
      return {
        label: 'Moderate HV',
        color: 'info',
        description: 'Realized volatility is within a normal market range.',
      };
    }

    if (metric === 'volatility_risk_premium') {
      if (num > 2) {
        return {
          label: 'Rich VRP',
          color: 'warning',
          description: 'Implied volatility is meaningfully richer than realized volatility, making options more expensive.',
        };
      }
      if (num < -2) {
        return {
          label: 'Cheap VRP',
          color: 'success',
          description: 'Implied volatility is lower than realized volatility, making options relatively inexpensive.',
        };
      }
      return {
        label: 'Neutral VRP',
        color: 'info',
        description: 'IV and realized volatility are aligned, indicating balanced option pricing.',
      };
    }

    if (metric === 'expected_move') {
      return {
        label: 'Guidance',
        color: 'info',
        description: 'Expected move is the at-the-money call + put premium, representing one standard move estimate for the expiry.',
      };
    }

    if (metric === 'premium_pcr') {
      if (num > 1.5) {
        return {
          label: 'Put-Biased',
          color: 'warning',
          description: 'Put premium dominates call premium, signaling protective demand or bearish positioning.',
        };
      }
      if (num < 0.7) {
        return {
          label: 'Call-Biased',
          color: 'success',
          description: 'Call premium dominates, suggesting bullish option demand.',
        };
      }
      return {
        label: 'Neutral Premium',
        color: 'info',
        description: 'Call and put premiums are roughly balanced.',
      };
    }

    if (metric === 'net_exposure') {
      if (num > 0) {
        return {
          label: 'Long Gamma',
          color: 'success',
          description: 'Positive gamma exposure; option sellers may hedge into moves and reduce volatility.',
        };
      }
      if (num < 0) {
        return {
          label: 'Short Gamma',
          color: 'warning',
          description: 'Negative gamma exposure; market makers may amplify moves when hedging.',
        };
      }
      return {
        label: 'Neutral Gamma',
        color: 'info',
        description: 'Gamma exposure is balanced and not likely to drive strong hedging flows.',
      };
    }

    return null;
  };

  return (
    <Grid container spacing={2} sx={{ mb: 2 }}>
      {/* Key Gamma Levels */}
      <Grid item xs={12} sm={6} md={4} lg={3}>
        <MetricCard title="Call Wall" value={safeKeyLevel(key_levels?.call_wall)} />
      </Grid>
      <Grid item xs={12} sm={6} md={4} lg={3}>
        <MetricCard title="Put Wall" value={safeKeyLevel(key_levels?.put_wall)} />
      </Grid>

      {showGreekExposures && (
        <>
          <Grid item xs={12} sm={6} md={4} lg={3}>
            <MetricCard 
              title="Net DEX" 
              value={net?.net_dex !== undefined ? formatNumber(net.net_dex) : '—'} 
              subtitle="Delta Exposure"
              color={net?.net_dex > 0 ? 'success.main' : net?.net_dex < 0 ? 'error.main' : undefined}
            />
          </Grid>
          <Grid item xs={12} sm={6} md={4} lg={3}>
            <MetricCard 
              title="Net VEX" 
              value={net?.net_vex !== undefined ? formatNumber(net.net_vex) : '—'} 
              subtitle="Vanna Exposure"
              color={net?.net_vex > 0 ? 'success.main' : net?.net_vex < 0 ? 'error.main' : undefined}
            />
          </Grid>
          <Grid item xs={12} sm={6} md={4} lg={3}>
            <MetricCard 
              title="Net CEX" 
              value={net?.net_cex !== undefined ? formatNumber(net.net_cex) : '—'} 
              subtitle="Charm Exposure"
              color={net?.net_cex > 0 ? 'success.main' : net?.net_cex < 0 ? 'error.main' : undefined}
            />
          </Grid>
        </>
      )}

      {/* Volatility Metrics */}
      <Grid item xs={12} sm={6} md={4} lg={3}>
        <MetricCard
          title="IV Rank (IVR)"
          value={ivr != null ? `${ivr.toFixed(1)}%` : '—'}
          subtitle="52W IV Percentile"
          status={getMetricStatus('ivr', ivr)}
        />
      </Grid>
      <Grid item xs={12} sm={6} md={4} lg={3}>
        <MetricCard
          title="25Δ Volatility Skew"
          value={skew != null ? skew.toFixed(4) : '—'}
          subtitle="Put IV - Call IV"
          status={getMetricStatus('skew', skew)}
        />
      </Grid>
      <Grid item xs={12} sm={6} md={4} lg={3}>
        <MetricCard
          title="Historical Volatility"
          value={historical_volatility != null ? `${historical_volatility.toFixed(1)}%` : '—'}
          subtitle="20-day realized volatility"
          status={getMetricStatus('historical_volatility', historical_volatility)}
        />
      </Grid>
      <Grid item xs={12} sm={6} md={4} lg={3}>
        <MetricCard
          title="Volatility Risk Premium"
          value={volatility_risk_premium != null ? `${volatility_risk_premium.toFixed(1)}%` : '—'}
          subtitle="ATM IV - HV"
          status={getMetricStatus('volatility_risk_premium', volatility_risk_premium)}
        />
      </Grid>
      <Grid item xs={12} sm={6} md={4} lg={3}>
        <MetricCard
          title="Expected Move"
          value={expected_move != null ? `$${expected_move.toFixed(2)}` : '—'}
          subtitle="ATM call + put premium"
          status={getMetricStatus('expected_move', expected_move)}
        />
      </Grid>
      <Grid item xs={12} sm={6} md={4} lg={3}>
        <MetricCard
          title="Premium Put/Call Ratio"
          value={call_premium_notional != null && put_premium_notional != null
            ? (put_premium_notional / (call_premium_notional || 1)).toFixed(2)
            : '—'}
          subtitle="Volume-weighted premium ratio"
          status={getMetricStatus('premium_pcr', call_premium_notional != null && put_premium_notional != null ? (put_premium_notional / (call_premium_notional || 1)) : null)}
        />
      </Grid>
    </Grid>
  );
}
