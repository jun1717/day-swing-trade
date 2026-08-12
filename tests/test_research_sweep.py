"""閾値スイープの等価性検証（RESEARCH_DESIGN §3）。

「max_position_in_range は最終分類段階にしか影響しない」を**仮定しない**。
事後導出が実際の再計算と一致することを、合成データで直接確かめる。
"""

from __future__ import annotations

import pytest

from tests.conftest import make_stock, override, uptrend_with_range
from swing_screener.models import STATUS_ENTRY, STATUS_NEAR, STATUS_RANGE, PriceSeries
from swing_screener.research.config import (
    SWEEP_THRESHOLDS,
    threshold_label,
    with_position_threshold,
)
from swing_screener.research.replay import replay_stock
from swing_screener.research.sweep import derive_status, verify_derivation
from swing_screener.screener import screen_one


def _series_set():
    """レンジ内位置がばらけるように複数の系列を作る。"""
    out = []
    for width, touch in ((150, (1, 3)), (300, (1, 4)), (450, (0, 2, 4))):
        builder = uptrend_with_range(
            trend_days=100,
            range_days=6,
            range_lower=4950,
            range_upper=4950 + width,
            touch_days=touch,
        )
        out.append(builder.build())
    # 上限付近で引けるケースも足す
    top = uptrend_with_range(
        trend_days=100, range_days=5, range_lower=4950, range_upper=5100, touch_days=(1, 3)
    )
    top.add(5085, high=5090, low=5060, volume=70_000)
    out.append(top.build())
    return out


def test_事後導出は実際の再計算と一致する(cfg, exp):
    """スイープ高速化の前提が本当に成り立つかを全サンプルで検証する。"""
    unlimited = with_position_threshold(exp, None)
    checked = 0

    for series in _series_set():
        for day in replay_stock(make_stock(), series, cfg, unlimited):
            rng = day.result.range_
            if rng is None or day.result.latest_close is None:
                continue
            span = rng.upper - rng.lower
            position = (day.result.latest_close - rng.lower) / span if span > 0 else None
            sliced = PriceSeries(code=series.code, bars=series.bars[: day.index + 1])

            for threshold in SWEEP_THRESHOLDS:
                actual = screen_one(
                    make_stock(), sliced, cfg, with_position_threshold(exp, threshold)
                ).status
                derived = derive_status(day.result.status, position, threshold)
                assert actual == derived, (
                    f"{day.date} threshold={threshold_label(threshold)} "
                    f"position={position}: 導出={derived} 実際={actual}"
                )
                checked += 1

    assert checked > 200, f"検証サンプルが少なすぎる: {checked}"


def test_verify_derivationは不一致を検出できる(cfg, exp):
    """検証関数自体が機能していること（壊れた導出を渡すと落ちること）。"""
    unlimited = with_position_threshold(exp, None)
    series = _series_set()[0]
    days = list(replay_stock(make_stock(), series, cfg, unlimited))
    stocks = {"1234": make_stock()}
    prices = {"1234": series}

    ok, mismatches = verify_derivation(days, stocks, prices, cfg, exp)
    assert ok, mismatches


def test_閾値を緩めるとENTRYは減らない(cfg, exp):
    """単調性。厳しい閾値の ENTRY は必ず緩い閾値にも含まれる。"""
    unlimited = with_position_threshold(exp, None)
    counts: dict[str, set] = {}

    for series in _series_set():
        for day in replay_stock(make_stock(), series, cfg, unlimited):
            rng = day.result.range_
            if rng is None or day.result.status != STATUS_ENTRY:
                continue
            span = rng.upper - rng.lower
            position = (day.result.latest_close - rng.lower) / span if span > 0 else None
            for threshold in SWEEP_THRESHOLDS:
                label = threshold_label(threshold)
                if derive_status(STATUS_ENTRY, position, threshold) == STATUS_ENTRY:
                    counts.setdefault(label, set()).add((series.code, day.date))

    ordered = [threshold_label(t) for t in SWEEP_THRESHOLDS]
    for stricter, looser in zip(ordered, ordered[1:]):
        a = counts.get(stricter, set())
        b = counts.get(looser, set())
        assert a <= b, f"{stricter} の ENTRY が {looser} に含まれていない"


@pytest.mark.parametrize(
    "base,position,threshold,expected",
    [
        (STATUS_ENTRY, 0.90, 0.65, STATUS_RANGE),
        (STATUS_ENTRY, 0.50, 0.65, STATUS_ENTRY),
        (STATUS_NEAR, 0.70, 0.65, STATUS_RANGE),
        (STATUS_ENTRY, 0.90, None, STATUS_ENTRY),  # 制限なし
        (STATUS_RANGE, 0.90, 0.65, STATUS_RANGE),  # RANGE は変化しない
        (STATUS_ENTRY, 0.65, 0.65, STATUS_ENTRY),  # 境界は通す（> で判定）
    ],
)
def test_derive_statusの規則(base, position, threshold, expected):
    assert derive_status(base, position, threshold) == expected


def test_本番experimentalは書き換えられない(exp):
    """スイープが experimental.yaml の値を汚染しないこと。"""
    before = exp.as_dict()
    for threshold in SWEEP_THRESHOLDS:
        with_position_threshold(exp, threshold)
    assert exp.as_dict() == before
    # 本番既定値が保持されている
    assert override(exp, {}).near.max_position_in_range == 0.65
