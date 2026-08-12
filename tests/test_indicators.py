"""指標のテスト（DESIGN.md §12）。

合成OHLCVだけで完結させる。株価APIには依存させない。
"""

from __future__ import annotations

import pytest

from tests.conftest import SeriesBuilder, override
from swing_screener.indicators.ma import calc_ma_series, ma_slope
from swing_screener.indicators.swing import detect_swings
from swing_screener.indicators.volume import summarize_volume
from swing_screener.rules.trend import evaluate_trend


# --- MA ---------------------------------------------------------------------


def test_calc_ma_series_は確定前をNoneにする():
    bars = SeriesBuilder().add(100).add(110).add(120).add(130).build().bars
    ma = calc_ma_series(bars, 3)
    assert ma[0] is None
    assert ma[1] is None
    assert ma[2] == pytest.approx(110.0)
    assert ma[3] == pytest.approx(120.0)


def test_ma_slope_の3方向(exp):
    up = [100 + i for i in range(10)]
    down = [100 - i for i in range(10)]
    flat = [100.0] * 10

    assert ma_slope(up, exp)[0] == "up"
    assert ma_slope(down, exp)[0] == "down"
    assert ma_slope(flat, exp)[0] == "flat"


def test_ma_slope_のdetailに具体的な数値が入る(exp):
    values = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
    direction, slope_pct, detail = ma_slope(values, exp, period=25)
    assert direction == "up"
    assert slope_pct == pytest.approx(5.0)
    assert "MA25" in detail
    assert "5日前" in detail
    assert "+5.0%" in detail


def test_min_slope_pctを上げるとflatになる(exp):
    values = [100.0 + i * 0.1 for i in range(10)]  # 5日で約 +0.5%
    assert ma_slope(values, exp)[0] == "up"
    strict = override(exp, {"ma_slope.min_slope_pct": 1.0})
    assert ma_slope(values, strict)[0] == "flat"


def test_linreg_methodでも判定できる(exp):
    linreg = override(exp, {"ma_slope.method": "linreg"})
    up = [100 + i for i in range(10)]
    direction, slope_pct, detail = ma_slope(up, linreg)
    assert direction == "up"
    assert slope_pct > 0
    assert "回帰" in detail


def test_未知のmethodはエラーになる(exp):
    broken = override(exp, {"ma_slope.method": "unknown_method"})
    with pytest.raises(ValueError):
        ma_slope([100.0] * 10, broken)


def test_ma履歴が足りなければ判定不能でflat(exp):
    direction, slope_pct, detail = ma_slope([100.0, 101.0], exp)
    assert direction == "flat"
    assert slope_pct is None
    assert "不足" in detail or "比較できない" in detail


# --- swing ------------------------------------------------------------------

# 手作りの山谷。pivot_window=2 で swing high が i2/i5、swing low が i4/i7 になる。
_SWING_ROWS = [
    #  open,  high,   low,  close
    (99.0, 100.0, 98.0, 99.0),
    (100.0, 102.0, 100.0, 101.0),
    (102.0, 104.0, 102.0, 103.0),  # swing high (104)
    (103.0, 103.0, 101.0, 102.0),
    (102.0, 102.0, 99.0, 100.0),  # swing low (99)
    (105.0, 108.0, 105.0, 107.0),  # swing high (108)
    (106.0, 106.0, 103.0, 104.0),
    (104.0, 105.0, 102.0, 103.0),  # swing low (102)
    (104.0, 107.0, 104.0, 106.0),
    (107.0, 110.0, 107.0, 109.0),
    (108.0, 108.0, 105.0, 106.0),
]


def test_fractalでswing_highとswing_lowを検出する(exp):
    bars = SeriesBuilder().add_rows(_SWING_ROWS).build().bars
    highs, lows = detect_swings(bars, exp)

    assert [p.price for p in highs] == [104.0, 108.0]
    assert [p.index for p in highs] == [2, 5]
    assert [p.price for p in lows] == [99.0, 102.0]
    assert [p.index for p in lows] == [4, 7]


def test_pivot_windowを広げるとpivotが減る(exp):
    bars = SeriesBuilder().add_rows(_SWING_ROWS).build().bars
    wide = override(exp, {"swing.pivot_window": 4})
    highs, lows = detect_swings(bars, wide)
    assert len(highs) <= 1
    assert len(lows) <= 1


def test_zigzag_methodでも検出できる(exp):
    bars = SeriesBuilder().add_rows(_SWING_ROWS).build().bars
    zig = override(exp, {"swing.method": "zigzag", "swing.zigzag_pct": 3.0})
    highs, lows = detect_swings(bars, zig)
    # 山谷があるので少なくとも1つずつは確定する
    assert highs or lows


# --- トレンド ---------------------------------------------------------------


def test_上昇トレンドはclose_above_maとma_upがOKになる(cfg, exp):
    bars = SeriesBuilder().uptrend_to(80, 5000, 15).build().bars
    trend = evaluate_trend(bars, cfg, exp)

    assert trend.close_above_ma is True
    assert trend.ma_direction == "up"
    assert trend.is_uptrend is True
    assert trend.strength > 0
    detail = {j.key: j.detail for j in trend.judgements}
    assert "MA25" in detail["trend.close_above_ma"]
    assert "5,000円" in detail["trend.close_above_ma"]


def test_下降トレンドはOUT扱いになる(cfg, exp):
    bars = SeriesBuilder().downtrend_to(80, 3000, 15).build().bars
    trend = evaluate_trend(bars, cfg, exp)

    assert trend.close_above_ma is False
    assert trend.ma_direction == "down"
    assert trend.is_uptrend is False


def test_高値安値切り上げは初期設定では必須ではない(cfg, exp):
    """swing 検出が未確定なので、初期値では参考表示にとどめる。"""
    bars = SeriesBuilder().uptrend_to(80, 5000, 15).build().bars
    trend = evaluate_trend(bars, cfg, exp)
    required = {j.key for j in trend.judgements if j.required}
    assert required == {"trend.close_above_ma", "trend.ma_direction"}
    assert trend.is_uptrend is True

    strict = override(
        exp, {"trend.require_higher_highs": True, "trend.require_higher_lows": True}
    )
    strict_trend = evaluate_trend(bars, cfg, strict)
    # 直線的な上昇では swing が検出されないため、必須にすると落ちる
    assert strict_trend.is_uptrend is False


# --- 出来高 -----------------------------------------------------------------


def _volume_bars(pre_volume: int, range_volume: int, range_days: int = 6):
    builder = SeriesBuilder(default_volume=pre_volume)
    builder.uptrend_to(40, 5000, 10, volume=pre_volume)
    builder.flat_range(range_days, 4950, 5150, touch_days=(1, 4), volume=range_volume)
    return builder.build().bars


def test_出来高が減っていればcontracting(exp):
    bars = _volume_bars(120_000, 60_000)
    info = summarize_volume(bars, len(bars) - 6, exp)
    assert info.state == "contracting"
    assert info.state_label == "レンジ中減少傾向"
    assert info.range_vs_pre_ratio == pytest.approx(0.5)
    assert info.avg5 == pytest.approx(60_000)
    assert info.avg20 is not None


def test_出来高が増えていればexpanding(exp):
    bars = _volume_bars(60_000, 120_000)
    info = summarize_volume(bars, len(bars) - 6, exp)
    assert info.state == "expanding"


def test_レンジ未検出なら出来高はunknown(exp):
    bars = _volume_bars(100_000, 100_000)
    info = summarize_volume(bars, None, exp)
    assert info.state == "unknown"
    assert info.range_avg is None
    # レンジがなくても当日/5日/20日は出す
    assert info.latest > 0
    assert info.avg5 is not None


def test_出来高判定は売買条件にしない(exp):
    """出来高 Judgement は ok=None（単独で売買判定に使わない）。"""
    bars = _volume_bars(120_000, 60_000)
    info = summarize_volume(bars, len(bars) - 6, exp)
    assert all(j.ok is None for j in info.judgements)
