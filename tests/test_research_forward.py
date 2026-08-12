"""シグナル後の値動き観察の検証（RESEARCH_DESIGN §6）。

forward は「観察」であって「判定」ではない。判定に未来を使っていないことは
test_research_replay.py が担保する。ここでは計算そのものの正しさを見る。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from swing_screener.models import OHLCVBar
from swing_screener.research import classify, forward
from swing_screener.research.config import ShapeParams


def _bars(rows: list[tuple[float, float, float, float]]) -> tuple[OHLCVBar, ...]:
    """(open, high, low, close) の並びから足を作る。"""
    out = []
    d = date(2026, 1, 5)
    for o, h, low, c in rows:
        out.append(OHLCVBar(date=d, open=o, high=h, low=low, close=c, volume=1000))
        d += timedelta(days=1)
    return tuple(out)


def test_シグナル日の足はforwardに含めない():
    """二重計上の防止。シグナル日に大きな高値があっても最大上昇率に入らない。"""
    bars = _bars(
        [
            (100, 100, 100, 100),  # index 0
            (100, 999, 100, 100),  # index 1 = シグナル日。高値999は無視されるべき
            (100, 110, 95, 105),  # index 2
            (105, 108, 100, 106),  # index 3
        ]
    )
    obs = forward.observe(
        bars, 1, signal_close=100.0, range_upper=120.0, stop_price=90.0, horizons=(2,)
    )
    stats = obs.from_close[2]
    assert stats.max_gain_pct == pytest.approx(10.0)  # 110 が最大、999 ではない
    assert stats.max_loss_pct == pytest.approx(-5.0)  # 95 が最安


def test_基準価格が終値と翌日始値で切り替わる():
    bars = _bars(
        [
            (100, 100, 100, 100),
            (100, 100, 100, 100),  # signal, close=100
            (110, 120, 105, 115),  # 翌日: 始値110
        ]
    )
    obs = forward.observe(
        bars, 1, signal_close=100.0, range_upper=200.0, stop_price=80.0, horizons=(1,)
    )
    assert obs.next_open == 110.0
    assert obs.gap_pct == pytest.approx(10.0)
    # 終値100基準: 高値120 → +20%
    assert obs.from_close[1].max_gain_pct == pytest.approx(20.0)
    # 翌日始値110基準: 高値120 → +9.09%
    assert obs.from_next_open[1].max_gain_pct == pytest.approx(9.0909, abs=1e-3)


def test_損切り到達と到達日数():
    bars = _bars(
        [
            (100, 100, 100, 100),
            (100, 100, 100, 100),  # signal
            (100, 102, 96, 99),  # 1日目: 安値96 > stop 95 → 未到達
            (99, 100, 94, 95),  # 2日目: 安値94 <= stop 95 → 到達
            (95, 96, 90, 92),
        ]
    )
    obs = forward.observe(
        bars, 1, signal_close=100.0, range_upper=120.0, stop_price=95.0, horizons=(5,)
    )
    stats = obs.from_close[5]
    assert stats.hit_stop is True
    assert stats.days_to_stop == 2  # 終値基準ではシグナル日翌日が1日目


def test_レンジ上限の到達と突破を区別する():
    # 高値は上限に届くが終値は超えない
    bars = _bars(
        [
            (100, 100, 100, 100),
            (100, 100, 100, 100),  # signal
            (100, 120, 99, 110),  # high=120 到達、close=110 は未突破
        ]
    )
    obs = forward.observe(
        bars, 1, signal_close=100.0, range_upper=120.0, stop_price=90.0, horizons=(1,)
    )
    assert obs.from_close[1].reached_range_upper is True
    assert obs.from_close[1].broke_range_upper is False


def test_forward不足はcompleteがFalse():
    bars = _bars([(100, 100, 100, 100)] * 4)
    obs = forward.observe(
        bars, 1, signal_close=100.0, range_upper=120.0, stop_price=90.0, horizons=(5, 10)
    )
    assert obs.bars_available == 2
    assert obs.complete is False
    assert obs.from_close[5].complete is False


def test_翌足が無い場合():
    bars = _bars([(100, 100, 100, 100), (100, 100, 100, 100)])
    obs = forward.observe(
        bars, 1, signal_close=100.0, range_upper=120.0, stop_price=90.0, horizons=(5,)
    )
    assert obs.next_open is None
    assert obs.gap_pct is None
    assert obs.from_next_open == {}


def test_レンジ崩壊の判定は終値で行う():
    bars = _bars(
        [
            (100, 100, 100, 100),
            (100, 100, 100, 100),  # signal
            (100, 101, 94, 96),  # 安値は下限95を割るが終値96は割らない
        ]
    )
    assert not forward.closed_below_range_lower(bars, 1, range_lower=95.0, horizon=5)

    bars2 = _bars(
        [
            (100, 100, 100, 100),
            (100, 100, 100, 100),
            (100, 101, 90, 93),  # 終値93 < 95
        ]
    )
    assert forward.closed_below_range_lower(bars2, 1, range_lower=95.0, horizon=5)


# --- 分類 -------------------------------------------------------------------


@pytest.mark.parametrize(
    "position,days,expected",
    [
        (0.30, 1, classify.SHAPE_IDEAL),
        (0.30, 8, classify.SHAPE_SLOW_TOUCH),
        (0.70, 1, classify.SHAPE_LATE),
        (0.85, 1, classify.SHAPE_NEAR_UPPER),
        (0.97, 1, classify.SHAPE_UPPER_ZONE),
    ],
)
def test_形状分類(position, days, expected):
    # upper_zone_low を十分高くして position だけで決まるようにする
    assert (
        classify.classify_shape(position, days, close=100.0, upper_zone_low=999.0,
                                params=ShapeParams())
        == expected
    )


def test_上限zoneに入ったらD判定():
    """position が低くても終値が上限zoneなら実質的な上限買い。"""
    assert (
        classify.classify_shape(0.50, 1, close=100.0, upper_zone_low=99.0,
                                params=ShapeParams())
        == classify.SHAPE_UPPER_ZONE
    )


@pytest.mark.parametrize(
    "complete,stop,breakdown,upper,expected",
    [
        (False, False, False, False, classify.OUTCOME_INCOMPLETE),
        (True, True, False, True, classify.OUTCOME_STOPPED),  # 損切り優先
        (True, False, True, True, classify.OUTCOME_BREAKDOWN),
        (True, False, False, True, classify.OUTCOME_REACHED_UPPER),
        (True, False, False, False, classify.OUTCOME_NEUTRAL),
    ],
)
def test_転帰分類(complete, stop, breakdown, upper, expected):
    assert (
        classify.classify_outcome(
            complete=complete,
            hit_stop=stop,
            closed_below_lower=breakdown,
            reached_upper=upper,
        )
        == expected
    )
