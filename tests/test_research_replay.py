"""リプレイの look-ahead bias 対策の検証（RESEARCH_DESIGN §2）。

ここが壊れると検証結果すべてが無意味になるため、最も厳しくテストする。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.conftest import make_stock, uptrend_with_range
from swing_screener.models import PriceSeries
from swing_screener.research.config import DEFAULT, with_position_threshold
from swing_screener.research.replay import replay_stock, resolve_window
from swing_screener.screener import screen_one


@pytest.fixture
def long_series():
    """warmup を満たす十分な長さの系列。"""
    return uptrend_with_range(trend_days=120, range_days=6, touch_days=(1, 4)).build()


def _key(day):
    """比較用に、判定に関わる値だけを取り出す。"""
    r = day.result
    rng = r.range_
    return (
        day.date,
        r.status,
        r.latest_close,
        r.distance_to_lower_pct,
        r.stop_price,
        r.trend.ma if r.trend else None,
        r.trend.ma_direction if r.trend else None,
        r.trend.ma_slope_pct if r.trend else None,
        rng.lower if rng else None,
        rng.upper if rng else None,
        rng.days if rng else None,
        rng.lower_touch_count if rng else None,
        rng.quality if rng else None,
    )


def test_未来足を追加しても過去の判定は変わらない(cfg, exp, long_series):
    """look-ahead bias が無いことの中核テスト。

    同じ系列に未来の足を付け足しても、それ以前の日付の判定は一切変わらない。
    もし screen_one に系列全体が渡っていたら、この不変性は壊れる。
    """
    base = list(replay_stock(make_stock(), long_series, cfg, exp))
    assert len(base) > 20, "テストとして十分な日数がない"

    # 未来に大暴騰を足す
    extended_bars = list(long_series.bars)
    last = extended_bars[-1]
    for k in range(1, 11):
        price = last.close * (1.0 + 0.08 * k)
        extended_bars.append(
            replace(
                last,
                date=_add_days(last.date, k),
                open=price * 0.99,
                high=price * 1.02,
                low=price * 0.98,
                close=price,
                volume=last.volume * 3,
            )
        )
    boomed = PriceSeries(code=long_series.code, bars=tuple(extended_bars))

    # 大暴落版も作る
    crash_bars = list(long_series.bars)
    for k in range(1, 11):
        price = last.close * (1.0 - 0.08 * k)
        crash_bars.append(
            replace(
                last,
                date=_add_days(last.date, k),
                open=price * 1.01,
                high=price * 1.02,
                low=price * 0.98,
                close=price,
                volume=last.volume * 3,
            )
        )
    crashed = PriceSeries(code=long_series.code, bars=tuple(crash_bars))

    end = long_series.bars[-1].date
    boomed_days = [
        d for d in replay_stock(make_stock(), boomed, cfg, exp, end=end)
    ]
    crashed_days = [
        d for d in replay_stock(make_stock(), crashed, cfg, exp, end=end)
    ]

    assert [_key(d) for d in boomed_days] == [_key(d) for d in base]
    assert [_key(d) for d in crashed_days] == [_key(d) for d in base]


def _add_days(d, k):
    from datetime import timedelta

    out = d
    added = 0
    while added < k:
        out = out + timedelta(days=1)
        if out.weekday() < 5:
            added += 1
    return out


def test_リプレイ結果は切り詰めた系列への直接screen_oneと一致する(cfg, exp, long_series):
    """replay が本当にスライスして渡しているかの検証。"""
    days = list(replay_stock(make_stock(), long_series, cfg, exp))
    assert days

    for day in (days[0], days[len(days) // 2], days[-1]):
        sliced = PriceSeries(
            code=long_series.code, bars=long_series.bars[: day.index + 1]
        )
        direct = screen_one(make_stock(), sliced, cfg, exp)
        assert day.result.status == direct.status
        assert day.result.latest_close == direct.latest_close
        assert day.date == sliced.bars[-1].date
        if day.result.range_ and direct.range_:
            assert day.result.range_.lower == direct.range_.lower
            assert day.result.range_.upper == direct.range_.upper


def test_warmup未満の日は判定しない(cfg, exp, long_series):
    """データ不足の日を ENTRY にしないこと。"""
    days = list(replay_stock(make_stock(), long_series, cfg, exp, warmup=80))
    assert days
    # 最初の判定日は 80 本目（index=79）以降
    assert days[0].index >= 79
    assert all(d.index >= 79 for d in days)


def test_判定日は常にスライスの最終足(cfg, exp, long_series):
    """「当日」がずれていないこと。"""
    for day in replay_stock(make_stock(), long_series, cfg, exp):
        assert long_series.bars[day.index].date == day.date
        assert day.result.as_of == day.date


def test_期間指定が両端を含む(cfg, exp, long_series):
    all_days = list(replay_stock(make_stock(), long_series, cfg, exp))
    start = all_days[3].date
    end = all_days[-3].date
    windowed = list(replay_stock(make_stock(), long_series, cfg, exp, start=start, end=end))
    assert windowed[0].date == start
    assert windowed[-1].date == end


def test_resolve_windowはwarmupを確保する(cfg, exp, long_series):
    price_map = {"1234": long_series}
    start, end, warmup = resolve_window(price_map, cfg, exp, months=6, research=DEFAULT)
    assert start is not None and end is not None
    dates = [b.date for b in long_series.bars]
    assert end == dates[-1]
    # start より前に warmup 本以上あること
    assert dates.index(start) + 1 >= warmup


def test_制限なし指定はファイルを書き換えない(exp):
    """with_position_threshold がメモリ上の差し替えであること。"""
    original = exp.as_dict()["near"]["max_position_in_range"]
    loosened = with_position_threshold(exp, None)
    assert loosened.as_dict()["near"]["max_position_in_range"] is None
    # 元の Params は無傷
    assert exp.as_dict()["near"]["max_position_in_range"] == original
