from pydantic import BaseModel
from typing import List, Optional


class StrikeExposure(BaseModel):
    strike: float
    call_gex: float
    put_gex: float
    total_gex: float
    dex: float
    vex: float
    cex: float
    oi: int
    iv_avg: Optional[float]


class KeyLevels(BaseModel):
    call_wall: Optional[float]
    put_wall: Optional[float]
    zero_gamma: Optional[float]


class NetExposures(BaseModel):
    net_dex: float
    net_vex: float
    net_cex: float


class OptionsMetricsResponse(BaseModel):
    ticker: Optional[str] = None
    expiration: Optional[str] = None
    key_levels: KeyLevels
    net: NetExposures
    ivr: Optional[float]
    skew: Optional[float]
    strikes: List[StrikeExposure]
    volume_put_call_ratio: Optional[float] = None
    open_interest_put_call_ratio: Optional[float] = None
    call_premium_notional: Optional[float] = None
    put_premium_notional: Optional[float] = None
    underlying_price: Optional[float] = None
    historical_volatility: Optional[float] = None
    current_atm_iv: Optional[float] = None
    volatility_risk_premium: Optional[float] = None
    expected_move: Optional[float] = None
    atm_strike: Optional[float] = None
    total_call_gex: Optional[float] = None
    total_put_gex: Optional[float] = None
    total_gex: Optional[float] = None
    call_wall: Optional[float] = None
    put_wall: Optional[float] = None
