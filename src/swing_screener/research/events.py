"""ENTRY イベントの記録（RESEARCH_DESIGN §6）。

閾値非依存の生データとして全 ENTRY を1度だけ記録し、閾値別のファイルは
これをフィルタして生成する。

基準価格の扱いに注意:
    `*_from_close` はシグナル日終値が基準。日足確定後に判定する運用では
    **その終値で買うことは実際には不可能**であり、比較用の基準にすぎない。
    `*_from_next_open` は翌営業日始値が基準。実運用との乖離を見る参考値であって、
    「翌日始値で必ず買う」という売買ルールではない。
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, fields
from datetime import date
from pathlib import Path
from typing import Any

from swing_screener.models import PriceSeries, ScreenResult, Stock
from swing_screener.research import classify, forward
from swing_screener.research.config import ResearchConfig, ShapeParams
from swing_screener.research.replay import DayResult

CSV_NOTE = (
    "# 注記: *_from_close はシグナル日終値が基準。日足確定後に判定する運用では"
    "その終値で買うことは実際には不可能であり、比較用の基準にすぎない。"
    " *_from_next_open は翌営業日始値が基準の参考値であって売買ルールではない。"
    " 本ファイルはイベントスタディの観察結果であり、収益バックテストではない。"
)


@dataclass(frozen=True)
class EntryEvent:
    # --- シグナル情報 ---
    date: date
    code: str
    name: str
    sector: str
    asset_type: str
    themes: str
    watch_priority: str
    is_leader: bool
    entry_reason: str

    range_start_date: date | None
    range_end_date: date | None
    range_days: int
    range_lower: float
    range_upper: float
    range_width_pct: float

    signal_close: float
    position_in_range: float | None

    last_lower_touch_date: date | None
    days_from_touch_to_signal: int | None
    lower_touch_count: int

    ma25: float | None
    ma_direction: str
    ma_slope_pct: float | None
    ma_deviation_pct: float | None
    higher_highs: str  # true / false / unknown
    higher_lows: str

    volume_state: str
    volume_range_vs_pre_ratio: float | None

    prev_high: float | None
    breakout_pct_vs_prev_high: float | None
    initial_stop: float
    stop_distance_pct_from_close: float

    # --- 分類 ---
    shape: str
    outcome: str

    # --- forward: シグナル日終値基準（実際には約定不可能）---
    fwd5_max_gain_pct_from_close: float | None
    fwd5_max_loss_pct_from_close: float | None
    fwd10_max_gain_pct_from_close: float | None
    fwd10_max_loss_pct_from_close: float | None
    fwd5_reached_range_upper: bool
    fwd10_reached_range_upper: bool
    fwd5_broke_range_upper: bool
    fwd10_broke_range_upper: bool
    fwd5_hit_stop: bool
    fwd10_hit_stop: bool
    days_to_stop: int | None
    fwd5_days_to_max_gain: int | None
    fwd10_days_to_max_gain: int | None
    forward_complete: bool
    forward_bars_available: int

    # --- forward: 翌営業日始値基準（参考データ）---
    next_open: float | None
    gap_pct: float | None
    fwd5_max_gain_pct_from_next_open: float | None
    fwd5_max_loss_pct_from_next_open: float | None
    fwd10_max_gain_pct_from_next_open: float | None
    fwd10_max_loss_pct_from_next_open: float | None
    stop_distance_pct_from_next_open: float | None
    fwd5_hit_stop_from_next_open: bool
    fwd10_hit_stop_from_next_open: bool

    # --- 内部用（CSV には出すが分析の主役ではない）---
    signal_index: int


def _tri(value: bool | None) -> str:
    if value is None:
        return "unknown"
    return "true" if value else "false"


def _status_reason(result: ScreenResult) -> str:
    for j in result.judgements:
        if j.key == "status.result":
            return j.detail
    return ""


def build_event(
    day: DayResult,
    stock: Stock,
    series: PriceSeries,
    research: ResearchConfig,
    shape_params: ShapeParams,
) -> EntryEvent | None:
    """ENTRY_CANDIDATE の DayResult から観察レコードを作る。

    range_ が無い結果は対象外（ENTRY は必ずレンジを持つ）。
    """
    result = day.result
    rng = result.range_
    if rng is None or result.latest_close is None or result.stop_price is None:
        return None

    bars = series.bars
    close = result.latest_close
    span = rng.upper - rng.lower
    position = (close - rng.lower) / span if span > 0 else None

    obs = forward.observe(
        bars,
        day.index,
        signal_close=close,
        range_upper=rng.upper,
        stop_price=result.stop_price,
        horizons=research.horizons,
    )
    max_h = research.max_horizon
    c5 = obs.from_close.get(5)
    c10 = obs.from_close.get(10)
    o5 = obs.from_next_open.get(5)
    o10 = obs.from_next_open.get(10)
    long_close = obs.from_close.get(max_h)

    breakdown = forward.closed_below_range_lower(
        bars, day.index, range_lower=rng.lower, horizon=max_h
    )
    outcome = classify.classify_outcome(
        complete=obs.complete,
        hit_stop=bool(long_close and long_close.hit_stop),
        closed_below_lower=breakdown,
        reached_upper=bool(long_close and long_close.reached_range_upper),
    )
    shape = classify.classify_shape(
        position,
        result.days_since_lower_touch,
        close,
        rng.upper_zone_low,
        shape_params,
    )

    trend = result.trend
    vol = result.volume
    reb = result.rebound
    prev_high = reb.prev_high if reb else None

    touch_dates = rng.lower_touch_dates
    last_touch = max(touch_dates) if touch_dates else None

    return EntryEvent(
        date=day.date,
        code=stock.code,
        name=stock.name,
        sector=stock.sector,
        asset_type=stock.asset_type,
        themes=";".join(stock.theme_names),
        watch_priority=stock.display_priority,
        is_leader=stock.is_leader_any,
        entry_reason=_status_reason(result),
        range_start_date=rng.start_date,
        range_end_date=rng.end_date,
        range_days=rng.days,
        range_lower=rng.lower,
        range_upper=rng.upper,
        range_width_pct=rng.width_pct,
        signal_close=close,
        position_in_range=position,
        last_lower_touch_date=last_touch,
        days_from_touch_to_signal=result.days_since_lower_touch,
        lower_touch_count=rng.lower_touch_count,
        ma25=trend.ma if trend else None,
        ma_direction=trend.ma_direction if trend else "",
        ma_slope_pct=trend.ma_slope_pct if trend else None,
        ma_deviation_pct=trend.ma_deviation_pct if trend else None,
        higher_highs=_tri(trend.higher_highs if trend else None),
        higher_lows=_tri(trend.higher_lows if trend else None),
        volume_state=vol.state if vol else "",
        volume_range_vs_pre_ratio=vol.range_vs_pre_ratio if vol else None,
        prev_high=prev_high,
        breakout_pct_vs_prev_high=(
            (close - prev_high) / prev_high * 100.0
            if prev_high not in (None, 0)
            else None
        ),
        initial_stop=result.stop_price,
        stop_distance_pct_from_close=(close - result.stop_price) / close * 100.0,
        shape=shape,
        outcome=outcome,
        fwd5_max_gain_pct_from_close=c5.max_gain_pct if c5 else None,
        fwd5_max_loss_pct_from_close=c5.max_loss_pct if c5 else None,
        fwd10_max_gain_pct_from_close=c10.max_gain_pct if c10 else None,
        fwd10_max_loss_pct_from_close=c10.max_loss_pct if c10 else None,
        fwd5_reached_range_upper=bool(c5 and c5.reached_range_upper),
        fwd10_reached_range_upper=bool(c10 and c10.reached_range_upper),
        fwd5_broke_range_upper=bool(c5 and c5.broke_range_upper),
        fwd10_broke_range_upper=bool(c10 and c10.broke_range_upper),
        fwd5_hit_stop=bool(c5 and c5.hit_stop),
        fwd10_hit_stop=bool(c10 and c10.hit_stop),
        days_to_stop=c10.days_to_stop if c10 else None,
        fwd5_days_to_max_gain=c5.days_to_max_gain if c5 else None,
        fwd10_days_to_max_gain=c10.days_to_max_gain if c10 else None,
        forward_complete=obs.complete,
        forward_bars_available=obs.bars_available,
        next_open=obs.next_open,
        gap_pct=obs.gap_pct,
        fwd5_max_gain_pct_from_next_open=o5.max_gain_pct if o5 else None,
        fwd5_max_loss_pct_from_next_open=o5.max_loss_pct if o5 else None,
        fwd10_max_gain_pct_from_next_open=o10.max_gain_pct if o10 else None,
        fwd10_max_loss_pct_from_next_open=o10.max_loss_pct if o10 else None,
        stop_distance_pct_from_next_open=(
            (obs.next_open - result.stop_price) / obs.next_open * 100.0
            if obs.next_open
            else None
        ),
        fwd5_hit_stop_from_next_open=bool(o5 and o5.hit_stop),
        fwd10_hit_stop_from_next_open=bool(o10 and o10.hit_stop),
        signal_index=day.index,
    )


FIELD_NAMES = [f.name for f in fields(EntryEvent)]


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def write_events_csv(events: list[EntryEvent], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(CSV_NOTE + "\n")
        writer = csv.DictWriter(f, fieldnames=FIELD_NAMES)
        writer.writeheader()
        for ev in events:
            writer.writerow({k: _cell(v) for k, v in asdict(ev).items()})
    return path


def read_events_csv(path: Path) -> list[dict[str, Any]]:
    """レポート側から読み直すための緩いローダ（型は文字列のまま）。"""
    with path.open(encoding="utf-8") as f:
        lines = [ln for ln in f if not ln.startswith("#")]
    return list(csv.DictReader(lines))
