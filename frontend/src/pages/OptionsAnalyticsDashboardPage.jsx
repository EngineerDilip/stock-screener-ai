import { useState, useEffect } from 'react';
import {
  Container,
  Typography,
  Box,
  TextField,
  CircularProgress,
  Paper,
  Grid,
  Card,
  CardContent,
  Alert,
  Chip,
  Autocomplete,
} from '@mui/material';
import apiClient from '../api/client';
import SummaryCards from '../components/OptionsMetrics/SummaryCards';
import MetricHistoryChart from '../components/OptionsMetrics/MetricHistoryChart';
import ExpirationSelector from '../components/OptionsMetrics/ExpirationSelector';

export default function OptionsAnalyticsDashboardPage() {
  const [tickerList, setTickerList] = useState([]);
  const [selectedTicker, setSelectedTicker] = useState(null);
  const [tickerInputValue, setTickerInputValue] = useState('');
  const [tickerQuery, setTickerQuery] = useState('');
  const [openTickerList, setOpenTickerList] = useState(false);
  const [defaultTickersLoaded, setDefaultTickersLoaded] = useState(false);
  const [loadingTickers, setLoadingTickers] = useState(false);
  const [loadingData, setLoadingData] = useState(false);
  const [selectedExpiration, setSelectedExpiration] = useState(null);

  const [gexData, setGexData] = useState(null);
  const [maxPainData, setMaxPainData] = useState(null);
  const [optionsMetrics, setOptionsMetrics] = useState(null);
  const [analysisData, setAnalysisData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const controller = new AbortController();
    const fetchSymbols = async () => {
      setLoadingTickers(true);
      try {
        const params = {
          limit: 200,
        };
        if (tickerQuery) {
          params.q = tickerQuery;
        }
        const resp = await apiClient.get('/v1/universe/symbols', {
          params,
          signal: controller.signal,
        });
        setTickerList(resp.data.symbols || []);
        if (!tickerQuery && !defaultTickersLoaded) {
          setDefaultTickersLoaded(true);
        }
      } catch (err) {
        if (err.name !== 'CanceledError' && err.name !== 'AbortError') {
          console.error('Failed to load ticker list:', err);
        }
      } finally {
        setLoadingTickers(false);
      }
    };

    if (tickerQuery || (openTickerList && !defaultTickersLoaded)) {
      const timer = window.setTimeout(fetchSymbols, 250);
      return () => {
        window.clearTimeout(timer);
        controller.abort();
      };
    }

    return undefined;
  }, [tickerQuery, openTickerList, defaultTickersLoaded]);

  // Fetch all three data sources when ticker changes
  useEffect(() => {
    setSelectedExpiration(null); // expiration selection doesn't carry over between tickers

    if (!selectedTicker) {
      setGexData(null);
      setMaxPainData(null);
      setOptionsMetrics(null);
      setAnalysisData(null);
      setError(null);
      return;
    }

    (async () => {
      setLoadingData(true);
      setError(null);
      setGexData(null);
      setMaxPainData(null);
      setOptionsMetrics(null);
      setAnalysisData(null);

      try {
        const [gexResp, maxPainResp, optionsResp, analysisResp] = await Promise.all([
          apiClient.get('/v1/gex/dashboard', { params: { symbol: selectedTicker.symbol } }).catch(() => null),
          apiClient.get('/v1/max-pain/dashboard', { params: { symbol: selectedTicker.symbol } }).catch(() => null),
          apiClient.post('/v1/options/metrics', { symbol: selectedTicker.symbol }).catch(() => null),
          apiClient.get(`/v1/options/analysis/${selectedTicker.symbol}`).catch(() => null),
        ]);

        setGexData(gexResp?.data ?? null);
        setMaxPainData(maxPainResp?.data ?? null);
        setOptionsMetrics(optionsResp?.data ?? null);
        setAnalysisData(analysisResp?.data ?? null);

        if (!gexResp && !maxPainResp && !optionsResp && !analysisResp) {
          setError('No data available for this ticker');
        }
      } catch (err) {
        setError(String(err));
      } finally {
        setLoadingData(false);
      }
    })();
  }, [selectedTicker]);

  const getGexStatus = (value) => {
    if (value == null || Number.isNaN(Number(value))) return null;
    const num = Number(value);
    if (num > 0) {
      return {
        label: 'Long Gamma',
        color: 'success',
        description: 'Positive gamma exposure; option sellers may reduce hedges on large moves.',
      };
    }
    if (num < 0) {
      return {
        label: 'Short Gamma',
        color: 'warning',
        description: 'Negative gamma exposure; market makers may need aggressive re-hedging.',
      };
    }
    return {
      label: 'Neutral Gamma',
      color: 'info',
      description: 'Gamma exposure is balanced and less likely to trigger large hedging flows.',
    };
  };

  const getMaxPainStatus = (distancePct) => {
    if (distancePct == null || Number.isNaN(Number(distancePct))) return null;
    const pct = Number(distancePct);
    if (pct < -0.5) {
      return {
        label: 'Below Max Pain',
        color: 'info',
        description: 'Current price is below max pain; puts may be relatively expensive.',
      };
    }
    if (pct > 0.5) {
      return {
        label: 'Above Max Pain',
        color: 'info',
        description: 'Current price is above max pain; calls may be relatively expensive.',
      };
    }
    return {
      label: 'At Max Pain',
      color: 'success',
      description: 'Price is close to the max pain level, where open interest pain is minimized.',
    };
  };

  const getOpenInterestStatus = (callOi, putOi, side) => {
    if (callOi == null || putOi == null) return null;
    const callValue = Number(callOi);
    const putValue = Number(putOi);
    if (side === 'call') {
      if (callValue > putValue * 1.15) {
        return {
          label: 'Bullish OI',
          color: 'success',
          description: 'Call open interest exceeds put open interest, suggesting stronger bullish or resistance positioning.',
        };
      }
      if (callValue < putValue * 0.85) {
        return {
          label: 'Weak Call OI',
          color: 'warning',
          description: 'Call open interest is lower than put open interest, which may indicate less bullish conviction.',
        };
      }
      return {
        label: 'Balanced OI',
        color: 'info',
        description: 'Call and put open interest are roughly balanced, indicating neutral positioning.',
      };
    }

    if (side === 'put') {
      if (putValue > callValue * 1.15) {
        return {
          label: 'Bearish OI',
          color: 'warning',
          description: 'Put open interest exceeds call open interest, suggesting protective or bearish positioning.',
        };
      }
      if (putValue < callValue * 0.85) {
        return {
          label: 'Weak Put OI',
          color: 'success',
          description: 'Put open interest is lower than call open interest, which may indicate less bearish conviction.',
        };
      }
      return {
        label: 'Balanced OI',
        color: 'info',
        description: 'Call and put open interest are roughly balanced, indicating neutral positioning.',
      };
    }
    return null;
  };

  const gexRow = gexData?.rows?.[0];
  const maxPainRow = maxPainData?.rows?.[0];

  const callGexStatus = getGexStatus(gexRow?.call_gex);
  const putGexStatus = getGexStatus(gexRow?.put_gex);
  const totalGexStatus = getGexStatus(gexRow?.total_gex);
  const maxPainStatus = getMaxPainStatus(maxPainRow?.distance_pct);
  const callOiStatus = getOpenInterestStatus(maxPainRow?.call_oi, maxPainRow?.put_oi, 'call');
  const putOiStatus = getOpenInterestStatus(maxPainRow?.call_oi, maxPainRow?.put_oi, 'put');

  const getOverallConclusion = () => {
    const totalGex = gexRow?.total_gex != null ? Number(gexRow.total_gex) : null;
    const skew = optionsMetrics?.skew != null ? Number(optionsMetrics.skew) : null;
    const maxPain = maxPainRow?.distance_pct != null ? Number(maxPainRow.distance_pct) : null;

    if (totalGex == null && skew == null && maxPain == null) {
      return null;
    }

    const sentences = [];

    if (totalGex != null) {
      if (totalGex > 0) {
        sentences.push('Total GEX is positive, indicating a long-gamma regime that may support upward pressure as option sellers hedge into rising prices.');
      } else if (totalGex < 0) {
        sentences.push('Total GEX is negative, indicating a short-gamma regime that may amplify downside moves as option sellers hedge into falling prices.');
      } else {
        sentences.push('Total GEX is neutral, indicating balanced gamma exposure.');
      }
    }

    if (skew != null) {
      if (skew > 0) {
        sentences.push('Volatility skew is positive, showing put skew and suggesting demand for downside protection.');
      } else if (skew < 0) {
        sentences.push('Volatility skew is negative, showing call skew and suggesting bullish interest in upside risk.');
      } else {
        sentences.push('Volatility skew is neutral, showing no strong call/put bias.');
      }
    }

    if (maxPain != null) {
      if (maxPain < -0.5) {
        sentences.push('Price is below max pain, which may reflect heavier put exposure and a potential support area.');
      } else if (maxPain > 0.5) {
        sentences.push('Price is above max pain, which may reflect heavier call exposure and a potential resistance area.');
      } else {
        sentences.push('Price is close to max pain, suggesting the options market is relatively balanced around current levels.');
      }
    }

    const { advice } = getMarketSignal();

    return `${sentences.join(' ')} ${advice}`;
  };

  // Total GEX reflects the dominant dealer-hedging-flow risk (short gamma
  // amplifies moves, long gamma dampens them) and takes priority over skew.
  // Skew is only used as a secondary confirming/tie-breaking signal — it must
  // never flip a negative-GEX (short-gamma) regime into a "Bullish" badge.
  const getMarketSignal = () => {
    const totalGex = gexRow?.total_gex != null ? Number(gexRow.total_gex) : null;
    const skew = optionsMetrics?.skew != null ? Number(optionsMetrics.skew) : null;
    const maxPain = maxPainRow?.distance_pct != null ? Number(maxPainRow.distance_pct) : null;

    if (totalGex == null && skew == null && maxPain == null) {
      return null;
    }

    if (totalGex > 0 && skew < 0) {
      return {
        label: 'Buy',
        chipColor: 'success',
        textColor: 'success.main',
        advice: 'Buy.',
      };
    }
    if (totalGex < 0 && skew > 0) {
      return {
        label: 'Sell',
        chipColor: 'error',
        textColor: 'error.main',
        advice: 'Sell.',
      };
    }
    if (totalGex < 0) {
      // Short gamma alone is a downside-risk signal — never label it Bullish,
      // even when skew leans bullish (e.g. ACN: negative total GEX with
      // negative/call skew previously showed an incorrect "Bullish" badge).
      return {
        label: 'Bearish',
        chipColor: 'error',
        textColor: 'error.main',
        advice: 'Keep with bearish/cautious bias.',
      };
    }
    if (totalGex > 0) {
      return {
        label: 'Bullish',
        chipColor: 'success',
        textColor: 'success.main',
        advice: 'Keep with bullish bias.',
      };
    }
    // totalGex is zero/unavailable — fall back to skew alone as a secondary signal.
    if (skew > 0) {
      return {
        label: 'Cautious',
        chipColor: 'warning',
        textColor: 'warning.main',
        advice: 'Keep with cautious bias.',
      };
    }
    if (skew < 0) {
      return {
        label: 'Bullish',
        chipColor: 'success',
        textColor: 'success.main',
        advice: 'Keep with bullish bias.',
      };
    }

    return {
      label: 'Neutral',
      chipColor: 'default',
      textColor: 'text.secondary',
      advice: 'Keep position.',
    };
  };

  const marketSignal = getMarketSignal();

  return (
    <Container maxWidth="xl" sx={{ py: 2 }}>
      <Typography variant="h4" sx={{ mb: 3 }}>
        Options Analytics Dashboard
      </Typography>

      {/* Ticker Selector */}
      <Paper sx={{ p: 2, mb: 3 }}>
        <Autocomplete
          open={openTickerList}
          onOpen={() => {
            setOpenTickerList(true);
            if (!defaultTickersLoaded) {
              setTickerQuery('');
            }
          }}
          onClose={() => setOpenTickerList(false)}
          openOnFocus
          options={tickerList}
          getOptionLabel={(opt) => `${opt.symbol} - ${opt.name || ''} (${opt.exchange})`}
          value={selectedTicker}
          onChange={(e, val) => {
            setSelectedTicker(val);
            setTickerInputValue(val ? `${val.symbol} - ${val.name || ''}` : '');
          }}
          inputValue={tickerInputValue}
          onInputChange={(e, val, reason) => {
            setTickerInputValue(val);
            if (reason === 'input') {
              setTickerQuery(val);
            }
          }}
          loading={loadingTickers}
          renderInput={(params) => (
            <TextField
              {...params}
              label="Select Ticker"
              placeholder="Type symbol or company name..."
              InputProps={{
                ...params.InputProps,
                endAdornment: (
                  <>
                    {loadingTickers ? <CircularProgress color="inherit" size={20} /> : null}
                    {params.InputProps.endAdornment}
                  </>
                ),
              }}
            />
          )}
        />
      </Paper>

      {loadingData && (
        <Box sx={{ display: 'flex', justifyContent: 'center', p: 3 }}>
          <CircularProgress />
        </Box>
      )}

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {selectedTicker && !loadingData && (
        <>
          {/* Ticker Header */}
          <Typography variant="h5" sx={{ mb: 2 }}>
            {selectedTicker.symbol} {selectedTicker.name && `- ${selectedTicker.name}`}
          </Typography>

          {/* Term structure: compare positioning across expirations using today's
              data (a cross-section, not a forecast -- see ExpirationSelector's
              doc comment). Selecting one also filters the history charts below
              to that expiration's own series. */}
          <ExpirationSelector
            symbol={selectedTicker.symbol}
            expiration={selectedExpiration}
            onExpirationChange={setSelectedExpiration}
          />

          {/* History trend charts (separate from the point-in-time cards below --
              see MetricHistoryChart's doc comment for why these are never averaged) */}
          <MetricHistoryChart symbol={selectedTicker.symbol} metric="maxPain" expiration={selectedExpiration} />
          <MetricHistoryChart symbol={selectedTicker.symbol} metric="gex" expiration={selectedExpiration} />

          {/* GEX Summary */}
          {gexRow && (
            <Paper sx={{ p: 2, mb: 3 }}>
              <Typography variant="h6" sx={{ mb: 2 }}>
                Gamma Exposure (GEX)
              </Typography>
              <Grid container spacing={2}>
                <Grid item xs={12} sm={6} md={3}>
                  <Card>
                    <CardContent>
                      <Typography color="textSecondary">Call GEX</Typography>
                      <Typography variant="h6">
                        {gexRow.call_gex?.toFixed(2) || 'N/A'}
                      </Typography>
                      {gexRow.call_gex != null && (
                        <Box sx={{ mt: 1 }}>
                          <Chip label={callGexStatus?.label} color={callGexStatus?.color} size="small" />
                          {callGexStatus?.description && (
                            <Typography variant="caption" sx={{ display: 'block', mt: 0.5, color: 'text.secondary' }}>
                              {callGexStatus.description}
                            </Typography>
                          )}
                        </Box>
                      )}
                    </CardContent>
                  </Card>
                </Grid>
                <Grid item xs={12} sm={6} md={3}>
                  <Card>
                    <CardContent>
                      <Typography color="textSecondary">Put GEX</Typography>
                      <Typography variant="h6">
                        {gexRow.put_gex?.toFixed(2) || 'N/A'}
                      </Typography>
                      {gexRow.put_gex != null && (
                        <Box sx={{ mt: 1 }}>
                          <Chip label={putGexStatus?.label} color={putGexStatus?.color} size="small" />
                          {putGexStatus?.description && (
                            <Typography variant="caption" sx={{ display: 'block', mt: 0.5, color: 'text.secondary' }}>
                              {putGexStatus.description}
                            </Typography>
                          )}
                        </Box>
                      )}
                    </CardContent>
                  </Card>
                </Grid>
                <Grid item xs={12} sm={6} md={3}>
                  <Card>
                    <CardContent>
                      <Typography color="textSecondary">Total GEX</Typography>
                      <Typography variant="h6">
                        {gexRow.total_gex?.toFixed(2) || 'N/A'}
                      </Typography>
                      {gexRow.total_gex != null && (
                        <Box sx={{ mt: 1 }}>
                          <Chip label={totalGexStatus?.label} color={totalGexStatus?.color} size="small" />
                          {totalGexStatus?.description && (
                            <Typography variant="caption" sx={{ display: 'block', mt: 0.5, color: 'text.secondary' }}>
                              {totalGexStatus.description}
                            </Typography>
                          )}
                        </Box>
                      )}
                    </CardContent>
                  </Card>
                </Grid>
                <Grid item xs={12} sm={6} md={3}>
                  <Card>
                    <CardContent>
                      <Typography color="textSecondary">Flip Level</Typography>
                      <Typography variant="h6">
                        ${gexRow.flip_level?.toFixed(2) || 'N/A'}
                      </Typography>
                      <Typography variant="caption" sx={{ display: 'block', mt: 1, color: 'text.secondary' }}>
                        The price where dealer hedging flips character. Above it, dealers tend to smooth out
                        price swings; below it, their hedging can amplify moves instead.
                      </Typography>
                    </CardContent>
                  </Card>
                </Grid>
              </Grid>
            </Paper>
          )}

          {/* Max Pain Summary */}
          {maxPainRow && (
            <Paper sx={{ p: 2, mb: 3 }}>
              <Typography variant="h6" sx={{ mb: 2 }}>
                Max Pain Analysis
              </Typography>
              <Grid container spacing={2}>
                <Grid item xs={12} sm={6} md={3}>
                  <Card>
                    <CardContent>
                      <Typography color="textSecondary">Max Pain Level</Typography>
                      <Typography variant="h6">
                        ${maxPainRow.max_pain?.toFixed(2) || 'N/A'}
                      </Typography>
                      <Typography variant="caption" sx={{ display: 'block', mt: 1, color: 'text.secondary' }}>
                        The strike where option buyers collectively lose the most at expiration. Some traders
                        watch it as a possible price magnet into expiry, but it's not a reliable predictor on its own.
                      </Typography>
                    </CardContent>
                  </Card>
                </Grid>
                <Grid item xs={12} sm={6} md={3}>
                  <Card>
                    <CardContent>
                      <Typography color="textSecondary">Distance %</Typography>
                      <Typography variant="h6">
                        {maxPainRow.distance_pct?.toFixed(2) || 'N/A'}%
                      </Typography>
                      {maxPainRow.distance_pct != null && (
                        <Box sx={{ mt: 1 }}>
                          <Chip label={maxPainStatus?.label} color={maxPainStatus?.color} size="small" />
                          {maxPainStatus?.description && (
                            <Typography variant="caption" sx={{ display: 'block', mt: 0.5, color: 'text.secondary' }}>
                              {maxPainStatus.description}
                            </Typography>
                          )}
                        </Box>
                      )}
                    </CardContent>
                  </Card>
                </Grid>
                <Grid item xs={12} sm={6} md={3}>
                  <Card>
                    <CardContent>
                      <Typography color="textSecondary">Call OI</Typography>
                      <Typography variant="h6">
                        {maxPainRow.call_oi?.toLocaleString() || 'N/A'}
                      </Typography>
                      {callOiStatus && (
                        <Box sx={{ mt: 1 }}>
                          <Chip label={callOiStatus.label} size="small" color={callOiStatus.color} />
                          <Typography variant="caption" sx={{ display: 'block', mt: 0.5, color: 'text.secondary' }}>
                            {callOiStatus.description}
                          </Typography>
                        </Box>
                      )}
                      <Typography variant="caption" sx={{ display: 'block', mt: 1, color: 'text.secondary' }}>
                        Call open interest shows the amount of bullish/options resistance flow at strikes above the market.
                      </Typography>
                    </CardContent>
                  </Card>
                </Grid>
                <Grid item xs={12} sm={6} md={3}>
                  <Card>
                    <CardContent>
                      <Typography color="textSecondary">Put OI</Typography>
                      <Typography variant="h6">
                        {maxPainRow.put_oi?.toLocaleString() || 'N/A'}
                      </Typography>
                      {putOiStatus && (
                        <Box sx={{ mt: 1 }}>
                          <Chip label={putOiStatus.label} size="small" color={putOiStatus.color} />
                          <Typography variant="caption" sx={{ display: 'block', mt: 0.5, color: 'text.secondary' }}>
                            {putOiStatus.description}
                          </Typography>
                        </Box>
                      )}
                      <Typography variant="caption" sx={{ display: 'block', mt: 1, color: 'text.secondary' }}>
                        Put open interest shows the amount of downside protection/support flow at strikes below the market.
                      </Typography>
                    </CardContent>
                  </Card>
                </Grid>
              </Grid>
            </Paper>
          )}

          {/* Structural Levels - Call Wall, Put Wall, Flip Level */}
          {analysisData && (
            <Paper sx={{ p: 2, mb: 3 }}>
              <Typography variant="h6" sx={{ mb: 2 }}>
                📊 Structural Levels (Batch Analysis)
              </Typography>
              {(() => {
                const callWallStrike = analysisData.call_wall?.strike;
                const putWallStrike = analysisData.put_wall?.strike;
                const flipLevelStrike = analysisData.flip_level?.strike;
                const invalidStructuralLevels = Boolean(
                  callWallStrike != null &&
                  putWallStrike != null &&
                  flipLevelStrike != null &&
                  callWallStrike === putWallStrike &&
                  callWallStrike === flipLevelStrike &&
                  analysisData.call_wall?.gex === 0 &&
                  analysisData.put_wall?.gex === 0 &&
                  (analysisData.flip_level?.cumulative_gex || 0) === 0
                );

                const formatStrike = (strike) =>
                  invalidStructuralLevels || strike == null ? 'N/A' : `$${strike.toFixed(2)}`;

                const formatGex = (value) =>
                  invalidStructuralLevels ? 'N/A' : (value || 0).toLocaleString('en-US', { maximumFractionDigits: 0 });

                const formatCumGex = (value) =>
                  invalidStructuralLevels ? 'N/A' : (value || 0).toLocaleString('en-US', { maximumFractionDigits: 0 });

                return (
                  <>
                    <Grid container spacing={2}>
                      <Grid item xs={12} sm={6} md={3}>
                        <Card>
                          <CardContent>
                            <Typography color="textSecondary">Call Wall</Typography>
                            <Typography variant="h6">
                              {formatStrike(callWallStrike)}
                            </Typography>
                            <Typography variant="caption" color="textSecondary">
                              GEX: {formatGex(analysisData.call_wall?.gex)}
                            </Typography>
                            <Typography variant="caption" sx={{ display: 'block', mt: 1, color: 'text.secondary' }}>
                              Strike with the heaviest call-side gamma -- tends to act like a ceiling the price
                              struggles to push above.
                            </Typography>
                          </CardContent>
                        </Card>
                      </Grid>
                      <Grid item xs={12} sm={6} md={3}>
                        <Card>
                          <CardContent>
                            <Typography color="textSecondary">Put Wall</Typography>
                            <Typography variant="h6">
                              {formatStrike(putWallStrike)}
                            </Typography>
                            <Typography variant="caption" color="textSecondary">
                              GEX: {formatGex(analysisData.put_wall?.gex)}
                            </Typography>
                            <Typography variant="caption" sx={{ display: 'block', mt: 1, color: 'text.secondary' }}>
                              Strike with the heaviest put-side gamma -- tends to act like a floor that offers support.
                            </Typography>
                          </CardContent>
                        </Card>
                      </Grid>
                      <Grid item xs={12} sm={6} md={3}>
                        <Card>
                          <CardContent>
                            <Typography color="textSecondary">Flip Level</Typography>
                            <Typography variant="h6">
                              {formatStrike(flipLevelStrike)}
                            </Typography>
                            <Typography variant="caption" color="textSecondary">
                              CumGEX: {formatCumGex(analysisData.flip_level?.cumulative_gex)}
                            </Typography>
                            <Typography variant="caption" sx={{ display: 'block', mt: 1, color: 'text.secondary' }}>
                              Where dealer hedging flips character -- calmer above it, choppier below it.
                            </Typography>
                          </CardContent>
                        </Card>
                      </Grid>
                      <Grid item xs={12} sm={6} md={3}>
                        <Card>
                          <CardContent>
                            <Typography color="textSecondary">Spot Price</Typography>
                            <Typography variant="h6">
                              ${analysisData.spot_price?.toFixed(2) || 'N/A'}
                            </Typography>
                            <Typography variant="caption" color="textSecondary">
                              Reference
                            </Typography>
                            <Typography variant="caption" sx={{ display: 'block', mt: 1, color: 'text.secondary' }}>
                              Today's actual stock price -- the point every level above is measured against.
                            </Typography>
                          </CardContent>
                        </Card>
                      </Grid>
                    </Grid>
                    {invalidStructuralLevels && (
                      <Typography variant="caption" sx={{ mt: 2, display: 'block', color: 'warning.main' }}>
                        ℹ️ Analysis data is too sparse to derive reliable key strike levels.
                      </Typography>
                    )}
                  </>
                );
              })()}
              {!analysisData.call_wall?.strike && !analysisData.put_wall?.strike && !analysisData.flip_level?.strike && (
                <Typography variant="caption" sx={{ mt: 2, display: 'block', color: 'text.secondary' }}>
                  ℹ️ Updated nightly at 23:00 UTC via batch analysis job
                </Typography>
              )}
              {analysisData.call_wall?.strike || analysisData.put_wall?.strike || analysisData.flip_level?.strike ? (
                <Typography variant="caption" sx={{ mt: 2, display: 'block', color: 'text.secondary' }}>
                  ℹ️ Updated nightly at 23:00 UTC via batch analysis job
                </Typography>
              ) : null}
            </Paper>
          )}

          {/* Options Metrics (Key Gamma, DEX/VEX/CEX, IVR, Skew) */}
          {optionsMetrics && (
            <>
              <SummaryCards data={optionsMetrics} showGreekExposures />
              <Paper sx={{ p: 2, mt: 3 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', mb: 1 }}>
                  <Typography variant="h6" sx={{ mr: 1 }}>
                    Market Conclusion
                  </Typography>
                  {marketSignal && (
                    <Box
                      sx={{
                        width: 12,
                        height: 12,
                        borderRadius: '50%',
                        bgcolor: marketSignal.textColor,
                        border: '1px solid',
                        borderColor: marketSignal.textColor,
                      }}
                    />
                  )}
                  {marketSignal && (
                    <Typography variant="subtitle2" sx={{ ml: 1, color: marketSignal.textColor }}>
                      {marketSignal.label}
                    </Typography>
                  )}
                </Box>
                <Typography variant="body2" color="text.secondary">
                  {getOverallConclusion() || 'No conclusion available due to missing metric data.'}
                </Typography>
              </Paper>
            </>
          )}
        </>
      )}
    </Container>
  );
}
