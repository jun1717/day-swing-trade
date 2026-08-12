"""レンジ検出のテスト（DESIGN.md §12）。

「理想的なレンジ / 幅が広すぎる / 値幅拡大中 / 大陰線+出来高急増 / 2日しかない」
の各ケースと、lower_touch_count が連続日を1回にまとめることを固定する。
"""

from __future__ import annotations

import pytest

from tests.conftest import SeriesBuilder, override, uptrend_with_range
from swing_screener.rules.range_detect import (
    count_lower_touches,
    detect_range,
    evaluate_window,
    range_judgements,
)


# --- 理想的なレンジ ---------------------------------------------------------


def test_理想的なレンジは採用される(cfg, exp):
    bars = uptrend_with_range(
        range_days=6, range_lower=4950, range_upper=5150, touch_days=(1, 4)
    ).build().bars

    win = evaluate_window(bars, 6, cfg, exp)

    assert win.accepted is True
    assert win.reject_reasons == ()
    assert win.lower == pytest.approx(4950.0)
    assert win.upper == pytest.approx(5150.0)
    assert win.width_pct == pytest.approx((5150 - 4950) / 4950 * 100, abs=0.01)
    assert win.lower_touch_count == 2
    assert len(win.lower_touch_dates) == 2
    assert win.quality >= float(exp.range_quality.min_quality)
    # zone は許容幅ぶん広がる
    assert win.lower_zone_high == pytest.approx(4950 * 1.007)
    assert win.lower_zone_low == pytest.approx(4950 * 0.993)
    assert win.upper_zone_high == pytest.approx(5150 * 1.007)


def test_品質スコアの内訳がすべて残る(cfg, exp):
    bars = uptrend_with_range().build().bars
    win = evaluate_window(bars, 6, cfg, exp)
    keys = [j.key for j in win.quality_breakdown]
    assert keys == [
        "range.quality.narrowness",
        "range.quality.lower_touches",
        "range.quality.volatility_contraction",
        "range.quality.volume_contraction",
        "range.quality.duration",
    ]
    # 内訳には必ず具体的な数値が入る
    assert all("重み" in j.detail for j in win.quality_breakdown)


def test_detect_rangeは品質最大のwindowを採用する(cfg, exp):
    bars = uptrend_with_range().build().bars
    best, candidates = detect_range(bars, cfg, exp)

    assert best is not None
    assert [c.days for c in candidates] == list(range(3, 11))
    accepted = [c for c in candidates if c.accepted]
    assert best.quality == max(c.quality for c in accepted)


def test_不採用windowの理由が残る(cfg, exp):
    """OUT の原因を人間が追えることが v0.1 の成功条件。"""
    bars = uptrend_with_range().build().bars
    _, candidates = detect_range(bars, cfg, exp)
    for c in candidates:
        if not c.accepted:
            assert c.reject_reasons, f"{c.days}日 window に不採用理由がない"


# --- lower_touch_count ------------------------------------------------------


def test_連続した下限接触は1回にまとめる(cfg, exp):
    """2日連続で下限を這うのは 1 反応。素朴に日数を数えると水増しになる。"""
    consecutive = uptrend_with_range(
        range_days=6, touch_days=(2, 3)
    ).build().bars
    separated = uptrend_with_range(
        range_days=6, touch_days=(1, 4)
    ).build().bars

    assert evaluate_window(consecutive, 6, cfg, exp).lower_touch_count == 1
    assert evaluate_window(separated, 6, cfg, exp).lower_touch_count == 2


def test_count_lower_touchesの代表日はグループ内の最安値の日(cfg, exp):
    bars = (
        SeriesBuilder()
        .add(100, high=101, low=99)  # 接触
        .add(100, high=101, low=98)  # 接触（こちらが安い）
        .add(105, high=106, low=104)
        .add(100, high=101, low=99)  # 接触
        .build()
        .bars
    )
    count, dates = count_lower_touches(bars, lower_zone_high=99.5)
    assert count == 2
    assert dates == (bars[1].date, bars[3].date)


def test_下限反応が0回でも理由付きで残る(cfg, exp):
    bars = uptrend_with_range(range_days=6, touch_days=()).build().bars
    win = evaluate_window(bars, 6, cfg, exp)
    # 全日が同じ安値になるので「1回の反応」にまとまる（連続グループのため）
    assert win.lower_touch_count <= 1
    js = range_judgements(win, [win], cfg, exp)
    touch_j = next(j for j in js if j.key == "range.lower_touches")
    assert touch_j.ok is False
    assert "連続日は1回に集約" in touch_j.detail


# --- 除外条件 ---------------------------------------------------------------


def test_値幅が広すぎるレンジは除外される(cfg, exp):
    bars = uptrend_with_range(
        trend_end=5000, range_days=6, range_lower=4500, range_upper=5500,
        touch_days=(1, 4),
    ).build().bars
    win = evaluate_window(bars, 6, cfg, exp)

    assert win.accepted is False
    assert any("値幅が広すぎる" in r for r in win.reject_reasons)


def test_値幅が狭すぎるレンジは除外される(cfg, exp):
    builder = SeriesBuilder().uptrend_to(40, 5000, 10)
    for _ in range(6):
        builder.add(5000, open=5000, high=5005, low=4995)
    bars = builder.build().bars
    win = evaluate_window(bars, 6, cfg, exp)

    assert win.accepted is False
    assert any("狭すぎ" in r for r in win.reject_reasons)


def test_値幅が拡大中のレンジは除外される(cfg, exp):
    builder = SeriesBuilder().uptrend_to(40, 5000, 10)
    # 前半は静か、後半は日中値幅が3倍以上
    for _ in range(3):
        builder.add(5000, open=5000, high=5010, low=4990)
    for _ in range(3):
        builder.add(5000, open=5000, high=5090, low=4910)
    bars = builder.build().bars
    win = evaluate_window(bars, 6, cfg, exp)

    assert win.accepted is False
    assert any("値幅が拡大" in r for r in win.reject_reasons)
    assert win.volatility_change > float(exp.range_quality.max_volatility_change)


def test_安値の連続切り下がりは除外される(cfg, exp):
    builder = SeriesBuilder().uptrend_to(40, 5000, 10)
    for low in (5000, 4980, 4960, 4940, 4930, 4925):
        builder.add(low + 20, open=low + 25, high=low + 35, low=low)
    bars = builder.build().bars
    win = evaluate_window(bars, 6, cfg, exp)

    assert win.accepted is False
    assert any("安値が" in r and "切り下がり" in r for r in win.reject_reasons)


def test_大陰線と出来高急増があるレンジは除外される(cfg, exp):
    builder = SeriesBuilder(default_volume=100_000).uptrend_to(40, 5000, 10)
    builder.add(5000, open=5000, high=5030, low=4970)
    builder.add(5010, open=5000, high=5040, low=4980)
    # 実体 -4%、出来高は20日平均の2倍以上
    builder.add(4800, open=5000, high=5010, low=4780, volume=400_000)
    builder.add(4820, open=4800, high=4850, low=4790)
    builder.add(4830, open=4820, high=4860, low=4800)
    builder.add(4840, open=4830, high=4870, low=4810)
    bars = builder.build().bars
    win = evaluate_window(bars, 6, cfg, exp)

    assert win.accepted is False
    assert any("大陰線" in r for r in win.reject_reasons)


def test_除外条件の閾値はexperimentalで変えられる(cfg, exp):
    """未確定パラメータをコードに固定していないことの確認。"""
    bars = uptrend_with_range(
        range_days=6, range_lower=4500, range_upper=5500, touch_days=(1, 4)
    ).build().bars
    assert evaluate_window(bars, 6, cfg, exp).accepted is False

    loose = override(exp, {"range_quality.max_width_pct": 40.0})
    win = evaluate_window(bars, 6, cfg, loose)
    assert not any("値幅が広すぎる" in r for r in win.reject_reasons)


# --- 日数 -------------------------------------------------------------------


def test_2日しかない持ち合いはレンジにならない(cfg, exp):
    """min_days=3 未満の window は評価対象にしない。"""
    builder = SeriesBuilder().uptrend_to(40, 5000, 100)  # 1日100円上がる急騰
    builder.add(5000, open=5000, high=5020, low=4980)
    builder.add(5005, open=5000, high=5020, low=4985)
    bars = builder.build().bars

    best, candidates = detect_range(bars, cfg, exp)
    assert min(c.days for c in candidates) == int(cfg.range.min_days) == 3
    assert best is None or best.days >= 3


def test_max_daysを超えるwindowは評価しない(cfg, exp):
    bars = uptrend_with_range().build().bars
    _, candidates = detect_range(bars, cfg, exp)
    assert max(c.days for c in candidates) == int(cfg.range.max_days) == 10


def test_レンジ品質が閾値未満なら不採用(cfg, exp):
    bars = uptrend_with_range().build().bars
    strict = override(exp, {"range_quality.min_quality": 0.99})
    best, candidates = detect_range(bars, cfg, strict)
    assert best is None
    assert any("品質が不足" in r for c in candidates for r in c.reject_reasons)
