"""charting.py の回帰テスト。

株価APIには依存させず、合成 OHLCV から PriceSeries / ScreenResult を組み立てて
PNG が実際に生成されること、result.range_ が None でも落ちないことを確認する。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from swing_screener.charting import render_daily_chart
from swing_screener.config import Params
from swing_screener.models import (
    OHLCVBar,
    PriceSeries,
    RangeCandidate,
    ReboundInfo,
    ScreenResult,
    Stock,
    ThemeTag,
    TrendResult,
    VolumeInfo,
)


def _make_bars(n: int, start_price: float = 3000.0) -> list[OHLCVBar]:
    """緩やかな上昇トレンド＋末尾に小さなレンジを持つ合成日足を作る。"""
    bars: list[OHLCVBar] = []
    d = date(2026, 3, 1)
    price = start_price
    count = 0
    while count < n:
        if d.weekday() < 5:  # 平日のみ
            drift = 5.0 if count < n - 6 else 0.5  # 末尾はレンジっぽく値幅を抑える
            wiggle = (count % 3) - 1  # -1, 0, 1 で細かい上下を作る
            open_ = price
            close = open_ + drift + wiggle
            high = max(open_, close) + 4.0
            low = min(open_, close) - 4.0
            volume = max(200_000, 1_000_000 - count * 4_000)
            bars.append(
                OHLCVBar(date=d, open=open_, high=high, low=low, close=close, volume=volume)
            )
            price = close
            count += 1
        d += timedelta(days=1)
    return bars


def _make_stock() -> Stock:
    return Stock(
        code="1234",
        name="テスト工業",
        sector="機械",
        asset_type="stock",
        enabled=True,
        themes=(ThemeTag(theme="テストテーマ", is_leader=True, watch_priority="A"),),
    )


def _make_trend(bars: list[OHLCVBar]) -> TrendResult:
    latest = bars[-1]
    ma = sum(b.close for b in bars[-25:]) / min(25, len(bars))
    return TrendResult(
        ma=ma,
        ma_deviation_pct=(latest.close - ma) / ma * 100,
        ma_direction="up",
        ma_slope_pct=1.8,
        close_above_ma=latest.close > ma,
        higher_highs=True,
        higher_lows=True,
        swing_highs=(),
        swing_lows=(),
        is_uptrend=True,
        strength=0.7,
        judgements=(),
    )


def _make_range(bars: list[OHLCVBar]) -> RangeCandidate:
    window = bars[-6:]
    upper = max(b.high for b in window)
    lower = min(b.low for b in window)
    return RangeCandidate(
        days=6,
        start_index=len(bars) - 6,
        end_index=len(bars) - 1,
        start_date=window[0].date,
        end_date=window[-1].date,
        upper=upper,
        upper_zone_low=upper * 0.995,
        upper_zone_high=upper * 1.005,
        lower=lower,
        lower_zone_low=lower * 0.993,
        lower_zone_high=lower * 1.007,
        width_pct=(upper - lower) / lower * 100,
        lower_touch_count=2,
        lower_touch_dates=(window[1].date, window[4].date),
        volatility_change=0.9,
        volume_change=0.8,
        quality=0.72,
        accepted=True,
        reject_reasons=(),
        quality_breakdown=(),
    )


def _make_volume() -> VolumeInfo:
    return VolumeInfo(
        latest=350_000,
        avg5=400_000.0,
        avg20=600_000.0,
        range_avg=380_000.0,
        pre_range_avg=520_000.0,
        range_vs_pre_ratio=0.73,
        latest_vs_avg5_ratio=0.88,
        state="contracting",
        state_label="レンジ中減少傾向",
        judgements=(),
    )


def _make_rebound(bars: list[OHLCVBar]) -> ReboundInfo:
    prev_high = bars[-2].high
    latest = bars[-1]
    return ReboundInfo(
        prev_high=prev_high,
        confirmed=latest.close > prev_high,
        bullish_candle=latest.close > latest.open,
        long_lower_wick=False,
        volume_recovered=True,
        judgements=(),
    )


def _cfg() -> Params:
    return Params({"ma": {"period": 25}})


def _exp() -> Params:
    return Params({"near": {"lower_threshold_pct": 2.0}})


def test_render_daily_chart_with_range(tmp_path: Path) -> None:
    bars = _make_bars(90)
    series = PriceSeries(code="1234", bars=tuple(bars))
    stock = _make_stock()
    range_ = _make_range(bars)

    result = ScreenResult(
        stock=stock,
        status="NEAR",
        as_of=bars[-1].date,
        latest_close=bars[-1].close,
        price_filter_ok=True,
        trend=_make_trend(bars),
        range_=range_,
        volume=_make_volume(),
        rebound=_make_rebound(bars),
        distance_to_lower_pct=1.2,
        touched_lower_recently=True,
        days_since_lower_touch=0,
        stop_price=range_.lower * 0.995,
        out_reason="",
        judgements=(),
        rejected_ranges=(),
    )

    output_path = tmp_path / "1234.png"
    result_path = render_daily_chart(series, result, _cfg(), _exp(), output_path, days=120)

    assert result_path == output_path
    assert output_path.exists()
    assert output_path.stat().st_size > 0


def test_render_daily_chart_without_range(tmp_path: Path) -> None:
    """result.range_ が None（レンジ未検出=OUT等）でも描画できること。"""
    bars = _make_bars(70)
    series = PriceSeries(code="5678", bars=tuple(bars))
    stock = Stock(code="5678", name="ノーレンジ商事", sector="卸売業", asset_type="stock")

    result = ScreenResult(
        stock=stock,
        status="OUT",
        as_of=bars[-1].date,
        latest_close=bars[-1].close,
        price_filter_ok=True,
        trend=_make_trend(bars),
        range_=None,
        volume=None,
        rebound=None,
        distance_to_lower_pct=None,
        touched_lower_recently=False,
        days_since_lower_touch=None,
        stop_price=None,
        out_reason="良いレンジなし",
        judgements=(),
        rejected_ranges=(),
    )

    output_path = tmp_path / "5678.png"
    result_path = render_daily_chart(series, result, _cfg(), _exp(), output_path, days=60)

    assert result_path == output_path
    assert output_path.exists()
    assert output_path.stat().st_size > 0


def test_render_daily_chart_short_history(tmp_path: Path) -> None:
    """表示日数よりバー数が少ない（かつMA期間未満）場合でも落ちないこと。"""
    bars = _make_bars(10)
    series = PriceSeries(code="9999", bars=tuple(bars))
    stock = Stock(code="9999", name="短期データ", sector="サービス業", asset_type="stock")

    result = ScreenResult(
        stock=stock,
        status="OUT",
        as_of=bars[-1].date,
        latest_close=bars[-1].close,
        price_filter_ok=True,
        trend=None,
        range_=None,
        volume=None,
        rebound=None,
        out_reason="データ不足",
    )

    output_path = tmp_path / "9999.png"
    result_path = render_daily_chart(series, result, _cfg(), _exp(), output_path, days=120)

    assert result_path == output_path
    assert output_path.exists()
    assert output_path.stat().st_size > 0


# --- 保有銘柄チャート（v1） --------------------------------------------------------
# 描いてよいのは「買ったときに決めた線」と「その後どうなったか」だけ。
# trail stop や利確ラインは v1 で自動化していないので、あるように見せてはいけない。

from swing_screener.charting import render_holding_chart  # noqa: E402
from swing_screener.portfolio import Trade  # noqa: E402


def _make_trade(bars: list[OHLCVBar], **kwargs) -> Trade:
    base = dict(
        code="1234",
        name="テスト銘柄",
        entry_date=bars[-20].date,
        entry_price=bars[-20].close,
        quantity=100,
        original_range_lower=bars[-20].low * 0.99,
        original_range_upper=bars[-20].high * 1.02,
        initial_stop=bars[-20].low * 0.985,
    )
    base.update(kwargs)
    return Trade(**base)


def test_render_holding_chart(tmp_path: Path) -> None:
    bars = _make_bars(60)
    series = PriceSeries(code="1234", bars=tuple(bars))
    out = tmp_path / "holding.png"

    saved = render_holding_chart(series, _make_trade(bars), _cfg(), out, days=60)

    assert saved == out
    assert out.exists() and out.stat().st_size > 5_000


def test_render_holding_chart_closed_trade(tmp_path: Path) -> None:
    bars = _make_bars(60)
    series = PriceSeries(code="1234", bars=tuple(bars))
    trade = _make_trade(bars, exit_date=bars[-5].date, exit_price=bars[-5].close)

    saved = render_holding_chart(series, trade, _cfg(), tmp_path / "closed.png", days=60)
    assert saved.exists()


def test_as_of_truncates_future_bars(tmp_path: Path) -> None:
    """ENTRY 当日の形を後から再現するための機能。未来の足を混ぜないこと。"""
    bars = _make_bars(60)
    series = PriceSeries(code="1234", bars=tuple(bars))
    trade = _make_trade(bars)

    full = render_holding_chart(series, trade, _cfg(), tmp_path / "full.png", days=60)
    at_entry = render_holding_chart(
        series, trade, _cfg(), tmp_path / "entry.png", days=60, as_of=trade.entry_date
    )
    # 同じ日までで切った系列から描いたものと一致する
    truncated = PriceSeries(
        code="1234", bars=tuple(b for b in bars if b.date <= trade.entry_date)
    )
    same = render_holding_chart(truncated, trade, _cfg(), tmp_path / "same.png", days=60)

    assert at_entry.read_bytes() == same.read_bytes()
    assert full.read_bytes() != at_entry.read_bytes()


def test_holding_chart_without_optional_fields(tmp_path: Path) -> None:
    """レンジも STOP も未記入の台帳でも描けること。"""
    bars = _make_bars(40)
    series = PriceSeries(code="1234", bars=tuple(bars))
    trade = Trade(code="1234", entry_date=bars[-10].date, entry_price=bars[-10].close)

    saved = render_holding_chart(series, trade, _cfg(), tmp_path / "sparse.png", days=40)
    assert saved.exists()


def test_holding_chart_raises_when_window_is_empty(tmp_path: Path) -> None:
    bars = _make_bars(40)
    series = PriceSeries(code="1234", bars=tuple(bars))

    with pytest.raises(ValueError, match="株価データがありません"):
        render_holding_chart(
            series, _make_trade(bars), _cfg(), tmp_path / "empty.png",
            as_of=date(2000, 1, 1),
        )
