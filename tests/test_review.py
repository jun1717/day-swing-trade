"""保有銘柄の日次レビュー（review.py）のテスト。

このモジュールの契約は 2 つある。

    1. **「売れ」と言わないこと。** 出すのは「チャートを見るべき理由」だけ
    2. **EXIT 用の新しい閾値を増やさないこと。** 使ってよいのは
       experimental.yaml の既存値（大陰線 3.0% / 出来高急増 1.8倍）だけ

数値そのもの（損益率・保有後最高値など）は表示のためのものなので、
定義が意図どおりであることを固定する。
"""

from __future__ import annotations

from datetime import date

import pytest
from tests.conftest import SeriesBuilder, override, uptrend_with_range

from swing_screener import review
from swing_screener.portfolio import Trade
from swing_screener.review import (
    LEVEL_CAUTION,
    LEVEL_NONE,
    LEVEL_REVIEW,
    LEVEL_SCENARIO_RISK,
)


def make_trade(**kwargs) -> Trade:
    base = dict(
        code="1234",
        name="テスト銘柄",
        entry_date=date(2025, 7, 8),
        entry_price=5000.0,
        quantity=100,
        original_range_lower=4950.0,
        original_range_upper=5150.0,
        initial_stop=4925.25,
    )
    base.update(kwargs)
    return Trade(**base)


@pytest.fixture
def series():
    """上昇トレンド + レンジ。最終足の日付が ENTRY 日より後になるよう組む。"""
    return uptrend_with_range().build()


def entry_on(series, offset_from_end: int) -> date:
    return series.bars[-offset_from_end].date


# --- 基本 -----------------------------------------------------------------------


def test_missing_price_cache_still_returns_a_row(cfg, exp):
    """株価が無いからといって保有が画面から消えないこと（見落としになる）。"""
    view = review.build_view(make_trade(), None, cfg, exp)
    assert view.trade.code == "1234"
    assert view.close is None
    assert "キャッシュ" in view.note
    assert view.signs == []


def test_pnl_uses_latest_close(cfg, exp, series):
    trade = make_trade(entry_date=entry_on(series, 5), entry_price=1000.0)
    view = review.build_view(trade, series, cfg, exp)
    expected = (series.bars[-1].close - 1000.0) / 1000.0 * 100.0
    assert view.pnl_pct == pytest.approx(expected)
    assert view.pnl_yen == pytest.approx((series.bars[-1].close - 1000.0) * 100)


def test_holding_high_starts_at_entry_not_earlier(cfg, exp, series):
    """ENTRY 前の高値を「保有後最高値」に混ぜないこと。"""
    trade = make_trade(entry_date=entry_on(series, 3))
    view = review.build_view(trade, series, cfg, exp)
    expected = max(b.high for b in series.bars[-3:])
    assert view.holding_high == pytest.approx(expected)
    assert view.bars_held == 3


def test_holding_high_stops_at_exit_for_closed_trade(cfg, exp, series):
    """決済後の上昇を「取れた値」に混ぜないこと。"""
    trade = make_trade(
        entry_date=entry_on(series, 5),
        exit_date=entry_on(series, 3),
        exit_price=series.bars[-3].close,
    )
    view = review.build_view(trade, series, cfg, exp)
    expected = max(b.high for b in series.bars[-5:-2])
    assert view.holding_high == pytest.approx(expected)
    assert view.bars_held == 3


def test_closed_trade_gets_no_signs(cfg, exp, series):
    """決済済みに「チャートを見るべき理由」を出しても行動につながらない。"""
    trade = make_trade(
        entry_date=entry_on(series, 5),
        exit_date=series.bars[-1].date,
        exit_price=1.0,          # 初期STOP以下だが、決済済みなので出さない
        exit_reason="discretionary",
    )
    view = review.build_view(trade, series, cfg, exp)
    assert view.signs == []
    assert view.level == LEVEL_NONE
    assert "決済済み" in view.note
    assert view.scenario  # 振り返り用の数値は残る


# --- SCENARIO_RISK ---------------------------------------------------------------


def test_below_initial_stop_is_scenario_risk(cfg, exp, series):
    close = series.bars[-1].close
    view = review.build_view(
        make_trade(entry_date=entry_on(series, 3), initial_stop=close + 100),
        series, cfg, exp,
    )
    assert view.below_initial_stop
    assert view.level == LEVEL_SCENARIO_RISK
    assert "below_initial_stop" in {s.key for s in view.signs}


def test_below_range_lower_is_scenario_risk(cfg, exp, series):
    close = series.bars[-1].close
    view = review.build_view(
        make_trade(
            entry_date=entry_on(series, 3),
            original_range_lower=close + 100,
            initial_stop=1.0,
        ),
        series, cfg, exp,
    )
    assert view.below_range_lower
    assert view.level == LEVEL_SCENARIO_RISK
    assert "below_range_lower" in {s.key for s in view.signs}


def test_below_ma25_is_scenario_risk(cfg, exp):
    """上昇後に下げてMA25を割った系列。"""
    builder = SeriesBuilder().uptrend_to(60, 5000, 15)
    for _ in range(12):
        builder.add(builder.bars[-1].close * 0.97)
    series = builder.build()

    view = review.build_view(
        make_trade(entry_date=series.bars[-6].date, original_range_lower=1.0, initial_stop=1.0),
        series, cfg, exp,
    )
    assert view.above_ma25 is False
    assert view.level == LEVEL_SCENARIO_RISK
    assert "below_ma25" in {s.key for s in view.signs}


# --- CAUTION ----------------------------------------------------------------------


def test_big_bearish_candle_is_caution(cfg, exp):
    builder = uptrend_with_range()
    prev = builder.bars[-1].close
    builder.add(prev * 0.95, open=prev)  # 実体 -5% の大陰線
    series = builder.build()

    view = review.build_view(
        make_trade(entry_date=series.bars[-3].date, original_range_lower=1.0, initial_stop=1.0),
        series, cfg, exp,
    )
    assert view.latest_body_pct < -3.0
    sign = next(s for s in view.signs if s.key == "big_bearish")
    assert sign.level == LEVEL_CAUTION


def test_bearish_with_volume_spike_is_caution(cfg, exp):
    builder = uptrend_with_range()
    prev = builder.bars[-1].close
    builder.add(prev * 0.99, open=prev, volume=1_000_000)  # 小さい陰線 + 出来高急増
    series = builder.build()

    view = review.build_view(
        make_trade(entry_date=series.bars[-3].date, original_range_lower=1.0, initial_stop=1.0),
        series, cfg, exp,
    )
    keys = {s.key for s in view.signs}
    assert "volume_spike" in keys
    assert "big_bearish" not in keys  # 大陰線ではない


def test_big_bearish_threshold_comes_from_experimental(cfg, exp):
    """新しい閾値を持たず experimental.yaml を見ていること。"""
    builder = uptrend_with_range()
    prev = builder.bars[-1].close
    builder.add(prev * 0.98, open=prev)  # 実体 -2%
    series = builder.build()
    trade = make_trade(entry_date=series.bars[-3].date, original_range_lower=1.0, initial_stop=1.0)

    default_keys = {s.key for s in review.build_view(trade, series, cfg, exp).signs}
    assert "big_bearish" not in default_keys

    strict = override(exp, {"range_quality.big_bearish_body_pct": 1.0})
    strict_keys = {s.key for s in review.build_view(trade, series, cfg, strict).signs}
    assert "big_bearish" in strict_keys


# --- REVIEW ------------------------------------------------------------------------


def test_range_upper_breakout_is_only_review(cfg, exp, series):
    """上限突破は「見ておく」であって、売り判定でも危険信号でもない。"""
    view = review.build_view(
        make_trade(
            entry_date=entry_on(series, 4),
            original_range_upper=1.0,   # 必ず突破済みになる
            original_range_lower=1.0,
            initial_stop=1.0,
        ),
        series, cfg, exp,
    )
    assert view.closed_above_range_upper
    assert view.level == LEVEL_REVIEW
    assert "above_range_upper" in {s.key for s in view.signs}


def test_reached_upper_but_not_closed_above(cfg, exp, series):
    highest_high = max(b.high for b in series.bars[-4:])
    highest_close = max(b.close for b in series.bars[-4:])
    upper = (highest_high + highest_close) / 2  # 高値は超える / 終値は超えない

    view = review.build_view(
        make_trade(
            entry_date=entry_on(series, 4),
            original_range_upper=upper,
            original_range_lower=1.0,
            initial_stop=1.0,
        ),
        series, cfg, exp,
    )
    assert view.reached_range_upper
    assert not view.closed_above_range_upper
    assert "reached_range_upper" in {s.key for s in view.signs}


def test_last_bearish_low_is_information_only(cfg, exp):
    """CODEX_HANDOFF §30 の「警戒足」。v1 では REVIEW どまり。"""
    builder = uptrend_with_range()
    prev = builder.bars[-1].close
    builder.add(prev * 0.995, open=prev, low=prev * 0.99)  # 陰線
    builder.add(prev * 0.985, open=prev * 0.99)            # その安値を割った終値
    series = builder.build()

    view = review.build_view(
        make_trade(entry_date=series.bars[-4].date, original_range_lower=1.0, initial_stop=1.0,
                   original_range_upper=10 ** 9),
        series, cfg, exp,
    )
    assert view.below_last_bearish_low
    sign = next(s for s in view.signs if s.key == "below_bearish_low")
    assert sign.level == LEVEL_REVIEW
    assert "自動の売り判定ではありません" in sign.detail


# --- 「売れ」と言わない --------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden", ["SELL", "TAKE_PROFIT", "TAKE PROFIT", "利確しろ", "売却してください", "BUY"]
)
def test_no_trade_instruction_words_anywhere(forbidden):
    """段階名・ラベル・説明文のどこにも売買指示を出さないこと。"""
    text = " ".join(
        list(review.LEVEL_LABELS_JA.values())
        + list(review.LEVEL_DESCRIPTIONS_JA.values())
        + list(review.LEVEL_ORDER)
    )
    assert forbidden not in text


def test_levels_are_only_the_three_plus_ok():
    assert set(review.LEVEL_ORDER) == {
        LEVEL_NONE, LEVEL_REVIEW, LEVEL_CAUTION, LEVEL_SCENARIO_RISK
    }
    assert review.LEVEL_ORDER[LEVEL_SCENARIO_RISK] > review.LEVEL_ORDER[LEVEL_CAUTION]
    assert review.LEVEL_ORDER[LEVEL_CAUTION] > review.LEVEL_ORDER[LEVEL_REVIEW]


def test_level_is_the_strongest_sign(cfg, exp, series):
    """複数該当したら最も強い段階を見出しにする。"""
    close = series.bars[-1].close
    view = review.build_view(
        make_trade(
            entry_date=entry_on(series, 4),
            initial_stop=close + 100,     # SCENARIO_RISK
            original_range_upper=1.0,     # REVIEW
            original_range_lower=1.0,
        ),
        series, cfg, exp,
    )
    assert len(view.signs) >= 2
    assert view.level == LEVEL_SCENARIO_RISK


# --- シナリオ確認欄 -------------------------------------------------------------------


def test_scenario_items_are_never_required(cfg, exp, series):
    """未確定の条件を売却ルールに格上げしないこと。"""
    view = review.build_view(make_trade(entry_date=entry_on(series, 3)), series, cfg, exp)
    assert [j.key for j in view.scenario] == [
        "scenario.uptrend",
        "scenario.above_ma25",
        "scenario.range_upper",
        "scenario.support",
        "scenario.big_bearish",
        "scenario.volume",
        "scenario.swing_low",
    ]
    assert all(j.required is False for j in view.scenario)
    assert all(j.detail for j in view.scenario)


def test_scenario_is_none_when_data_missing(cfg, exp, series):
    trade = make_trade(
        entry_date=entry_on(series, 3), original_range_lower=None, original_range_upper=None
    )
    view = review.build_view(trade, series, cfg, exp)
    by_key = {j.key: j for j in view.scenario}
    assert by_key["scenario.range_upper"].ok is None
    assert by_key["scenario.support"].ok is None


# --- 並び順とまとめ ---------------------------------------------------------------------


def test_views_sorted_by_level_then_loss(cfg, exp, series):
    close = series.bars[-1].close
    trades = [
        make_trade(code="AAAA", entry_date=entry_on(series, 3),
                   original_range_lower=1.0, initial_stop=1.0, original_range_upper=10 ** 9),
        make_trade(code="BBBB", entry_date=entry_on(series, 3),
                   original_range_lower=1.0, initial_stop=close + 100,
                   original_range_upper=10 ** 9),
    ]
    views = review.build_views(trades, {"AAAA": series, "BBBB": series}, cfg, exp)
    assert [v.trade.code for v in views] == ["BBBB", "AAAA"]

    counts = review.summarize_levels(views)
    assert counts[LEVEL_SCENARIO_RISK] == 1
    assert sum(counts.values()) == 2
