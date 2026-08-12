"""状態分類のテスト（DESIGN.md §12）。

OUT / RANGE / NEAR / ENTRY_CANDIDATE の 4 状態がそれぞれ意図通り出ること、
価格フィルタの境界、near.lookback_days の効果を固定する。
"""

from __future__ import annotations

import json

import pytest

from tests.conftest import SeriesBuilder, make_stock, override, uptrend_with_range
from swing_screener.explain import explain_lines, judgement_groups
from swing_screener.models import (
    STATUS_ENTRY,
    STATUS_NEAR,
    STATUS_OUT,
    STATUS_RANGE,
)
from swing_screener.screener import run_screening, run_to_dict, screen_one


def judgement(result, key):
    return next((j for j in result.judgements if j.key == key), None)


# --- 4状態 ------------------------------------------------------------------


def test_ENTRY_CANDIDATE(cfg, exp):
    """上昇トレンド＋レンジ＋下限付近＋反発確認。"""
    series = uptrend_with_range(range_days=6, touch_days=(1, 4)).build()
    result = screen_one(make_stock(), series, cfg, exp)

    assert result.status == STATUS_ENTRY
    assert result.rebound is not None and result.rebound.confirmed is True
    assert result.trend is not None and result.trend.is_uptrend is True
    assert result.range_ is not None
    assert result.stop_price == pytest.approx(result.range_.lower * 0.995)
    assert result.out_reason == ""


def test_NEAR_は反発確認前(cfg, exp):
    """最終日に下限へ反応したが、終値は前日高値を超えていない。"""
    series = uptrend_with_range(range_days=6, touch_days=(1, 5)).build()
    result = screen_one(make_stock(), series, cfg, exp)

    assert result.status == STATUS_NEAR
    assert result.rebound is not None and result.rebound.confirmed is False
    assert result.distance_to_lower_pct is not None
    assert result.distance_to_lower_pct <= float(exp.near.lower_threshold_pct)
    assert result.days_since_lower_touch == 0


def test_RANGE_は下限から遠い(cfg, exp):
    """レンジはあるが下限反応が古く、現在値も下限から離れている。"""
    series = uptrend_with_range(range_days=8, touch_days=(0, 1)).build()
    result = screen_one(make_stock(), series, cfg, exp)

    assert result.status == STATUS_RANGE
    assert result.range_ is not None
    assert result.distance_to_lower_pct > float(exp.near.lower_threshold_pct)
    assert result.touched_lower_recently is False


def test_OUT_価格フィルタ外(cfg, exp):
    series = SeriesBuilder().uptrend_to(80, 1999, 2.0).build()
    result = screen_one(make_stock(), series, cfg, exp)

    assert result.status == STATUS_OUT
    assert result.price_filter_ok is False
    assert "対象レンジ" in result.out_reason
    assert judgement(result, "filter.price").ok is False


def test_OUT_上昇トレンドでない(cfg, exp):
    series = SeriesBuilder().downtrend_to(80, 3000, 15).build()
    result = screen_one(make_stock(), series, cfg, exp)

    assert result.status == STATUS_OUT
    assert "上昇トレンド条件" in result.out_reason
    assert result.price_filter_ok is True
    # OUT でも全判定を残す（なぜ落ちたかを人間が追えること）
    assert judgement(result, "trend.ma_direction").ok is False


def test_OUT_良いレンジがない(cfg, exp):
    """上昇トレンドだが直近の安値が連続で切り下がっており、レンジと呼べない。"""
    builder = SeriesBuilder().uptrend_to(75, 5000, 15)
    for close in (4992, 4984, 4976, 4968, 4960):
        builder.add(close)
    result = screen_one(make_stock(), builder.build(), cfg, exp)

    assert result.status == STATUS_OUT
    assert result.trend is not None and result.trend.is_uptrend is True
    assert "レンジ" in result.out_reason
    # 不採用 window の理由が残っている
    assert result.rejected_ranges
    assert any(c.reject_reasons for c in result.rejected_ranges)


def test_OUT_レンジ崩壊(cfg, exp):
    """前日までのレンジ下限を許容幅を超えて下抜けた。"""
    builder = uptrend_with_range(
        trend_end=5000, trend_step=40, range_lower=4950, range_upper=5150,
        touch_days=(1, 4),
    )
    builder.add(4850, open=4930, high=4935, low=4840)  # 下限を2%下抜け
    result = screen_one(make_stock(), builder.build(), cfg, exp)

    assert result.status == STATUS_OUT
    assert "レンジ崩壊" in result.out_reason
    assert judgement(result, "status.range_break").ok is False


def test_OUT_enabledがfalse(cfg, exp):
    series = uptrend_with_range().build()
    result = screen_one(make_stock(enabled=False), series, cfg, exp)

    assert result.status == STATUS_OUT
    assert "enabled" in result.out_reason


def test_OUT_データ不足(cfg, exp):
    series = SeriesBuilder().uptrend_to(10, 5000, 15).build()
    result = screen_one(make_stock(), series, cfg, exp)

    assert result.status == STATUS_OUT
    assert "データ不足" in result.out_reason


def test_OUT_株価データなし(cfg, exp):
    result = screen_one(make_stock(), None, cfg, exp)

    assert result.status == STATUS_OUT
    assert "株価データ" in result.out_reason
    assert result.latest_close is None


# --- 価格フィルタ境界 -------------------------------------------------------


@pytest.mark.parametrize(
    "price, expected",
    [(1999, False), (2000, True), (7000, True), (7001, False)],
)
def test_価格フィルタは境界を含む(cfg, exp, price, expected):
    series = SeriesBuilder().uptrend_to(80, price, price / 1000).build()
    result = screen_one(make_stock(), series, cfg, exp)

    assert result.latest_close == pytest.approx(price)
    assert judgement(result, "filter.price").ok is expected
    assert result.price_filter_ok is expected


# --- NEAR の lookback -------------------------------------------------------


def test_lookback_daysを0にすると当日判定のみになる(cfg, exp):
    """反発すると価格は下限から離れるため、0 では ENTRY が成立しにくくなる。"""
    series = uptrend_with_range(range_days=6, touch_days=(1, 4)).build()

    with_lookback = screen_one(make_stock(), series, cfg, exp)
    assert with_lookback.status == STATUS_ENTRY
    assert with_lookback.touched_lower_recently is True

    strict = override(exp, {"near.lookback_days": 0})
    without = screen_one(make_stock(), series, cfg, strict)
    assert without.status == STATUS_RANGE
    assert without.touched_lower_recently is False
    # 距離そのものは変わらない（判定基準だけが変わる）
    assert without.distance_to_lower_pct == pytest.approx(
        with_lookback.distance_to_lower_pct
    )


def test_lookback_max_distance_pctで反発しすぎた銘柄を落とせる(cfg, exp):
    """既定は無効。値を入れると「下限から離れすぎた反発」を NEAR から外せる。"""
    series = uptrend_with_range(range_days=6, touch_days=(1, 4)).build()
    assert screen_one(make_stock(), series, cfg, exp).status == STATUS_ENTRY

    capped = override(exp, {"near.lookback_max_distance_pct": 1.0})
    result = screen_one(make_stock(), series, cfg, capped)
    assert result.status == STATUS_RANGE  # 距離 2.5% > 1.0%
    assert result.touched_lower_recently is False
    assert "lookback_max_distance_pct" in judgement(result, "status.near").detail


def test_レンジ上限付近ではNEARにもENTRYにもしない(cfg, exp):
    """CODEX_HANDOFF §21: レンジ上限ブレイクを新規エントリー候補にしない。

    下限zoneに触れたあと上限まで走り抜けた銘柄は、距離(%)だけで見ると
    lookback 経由で NEAR/ENTRY に紛れ込む。実データ（商船三井・日本郵船など）で
    実際に発生し、レンジ内位置 0.87〜0.92 の銘柄が ENTRY_CANDIDATE になっていた。
    最も買ってはいけない位置なので、位置ガードで構造的に防ぐ。
    """
    series = uptrend_with_range(range_days=6, touch_days=(1, 4)).build()
    assert screen_one(make_stock(), series, cfg, exp).status == STATUS_ENTRY

    # 位置ガードを極端に厳しくすると、同じ銘柄が RANGE へ落ちる
    guarded = override(exp, {"near.max_position_in_range": 0.05})
    result = screen_one(make_stock(), series, cfg, guarded)
    assert result.status == STATUS_RANGE
    assert result.touched_lower_recently is False
    detail = judgement(result, "status.near").detail
    assert "レンジ内位置" in detail and "上限寄り" in detail

    # null にすればガード無効（従来挙動へ戻せる）
    disabled = override(exp, {"near.max_position_in_range": None})
    assert screen_one(make_stock(), series, cfg, disabled).status == STATUS_ENTRY


def _range_top_series(lower: float, upper: float):
    """レンジ上限のすぐ下（位置≒0.95）で引けた銘柄を作る。"""
    builder = uptrend_with_range(
        range_days=5, range_lower=lower, range_upper=upper, touch_days=(1, 3)
    )
    span = upper - lower
    builder.add(
        lower + span * 0.90,
        high=lower + span * 0.95,
        low=lower + span * 0.80,
        volume=70_000,
    )
    return builder.build()


def test_位置ガードは距離キャップでは代用できない(cfg, exp):
    """レンジ幅が狭いと、上限付近でも下限からの距離(%)は小さくなる。

    幅3%のレンジで位置0.95 → 下限から +2.7%
    幅9%のレンジで位置0.95 → 下限から +8.2%

    絶対距離のキャップ（5%）では前者を素通しし、レンジ上限のすぐ下で
    ENTRY_CANDIDATE を出してしまう。位置ガードは幅に自動でスケールするため
    どちらも正しく除外する。両方を持つ意味はここにある。
    """
    narrow = _range_top_series(4950, 5100)  # 幅 ≒ 3%
    wide = _range_top_series(4950, 5400)  # 幅 ≒ 9%

    # 距離キャップだけでは、幅の狭いレンジの上限付近を止められない
    distance_only = override(
        exp,
        {"near.max_position_in_range": None, "near.lookback_max_distance_pct": 5.0},
    )
    assert screen_one(make_stock(), narrow, cfg, distance_only).status == STATUS_ENTRY
    assert screen_one(make_stock(), wide, cfg, distance_only).status == STATUS_RANGE

    # 位置ガード（既定 0.65）はレンジ幅によらず両方を除外する
    for series in (narrow, wide):
        result = screen_one(make_stock(), series, cfg, exp)
        assert result.status == STATUS_RANGE
        assert "レンジ内位置" in judgement(result, "status.near").detail


def test_lower_threshold_pctを広げるとNEARが増える(cfg, exp):
    series = uptrend_with_range(range_days=8, touch_days=(0, 1)).build()
    assert screen_one(make_stock(), series, cfg, exp).status == STATUS_RANGE

    loose = override(exp, {"near.lower_threshold_pct": 5.0})
    assert screen_one(make_stock(), series, cfg, loose).status == STATUS_NEAR


# --- 判定理由 ---------------------------------------------------------------


def test_すべての判定に具体的な数値が入る(cfg, exp):
    series = uptrend_with_range().build()
    result = screen_one(make_stock(), series, cfg, exp)

    for j in result.judgements:
        assert j.detail, f"{j.key} に detail がない"
        assert any(ch.isdigit() for ch in j.detail), f"{j.key} の detail に数値がない: {j.detail}"


def test_explain_linesがDESIGN10の項目を含む(cfg, exp):
    series = uptrend_with_range().build()
    result = screen_one(make_stock(), series, cfg, exp)
    text = "\n".join(explain_lines(result))

    for expected in ("上昇トレンド", "25日線", "レンジ", "下限", "上限", "下限反応",
                     "値幅", "下限まで", "出来高", "反発確認", "状態", "損切り候補"):
        assert expected in text, f"'{expected}' が判定理由に出ていない"


def test_judgement_groupsの順序(cfg, exp):
    series = uptrend_with_range().build()
    result = screen_one(make_stock(), series, cfg, exp)
    titles = [t for t, _ in judgement_groups(result)]
    assert titles[:4] == ["上昇トレンド", "レンジ", "出来高", "反発"]


def test_OUT銘柄も判定理由を持つ(cfg, exp):
    series = SeriesBuilder().downtrend_to(80, 3000, 15).build()
    result = screen_one(make_stock(), series, cfg, exp)
    text = "\n".join(explain_lines(result))
    assert "落選理由" in text
    assert result.judgements


# --- 並び順と出力 -----------------------------------------------------------


def test_run_screeningはstatus順に並ぶ(cfg, exp):
    stocks = [
        make_stock(code="0001", name="RANGE銘柄"),
        make_stock(code="0002", name="ENTRY銘柄"),
        make_stock(code="0003", name="OUT銘柄"),
        make_stock(code="0004", name="NEAR銘柄"),
    ]
    price_map = {
        "0001": uptrend_with_range(range_days=8, touch_days=(0, 1), code="0001").build(),
        "0002": uptrend_with_range(range_days=6, touch_days=(1, 4), code="0002").build(),
        "0003": SeriesBuilder(code="0003").downtrend_to(80, 3000, 15).build(),
        "0004": uptrend_with_range(range_days=6, touch_days=(1, 5), code="0004").build(),
    }
    run = run_screening(stocks, price_map, cfg, exp)

    assert [r.status for r in run.results] == [
        STATUS_ENTRY,
        STATUS_NEAR,
        STATUS_RANGE,
        STATUS_OUT,
    ]
    assert run.counts()[STATUS_ENTRY] == 1
    assert run.as_of is not None


def test_run_to_dictはJSON化できてスナップショットを含む(cfg, exp):
    stocks = [make_stock()]
    price_map = {"1234": uptrend_with_range().build()}
    run = run_screening(stocks, price_map, cfg, exp)

    data = run_to_dict(run)
    text = json.dumps(data, ensure_ascii=False)  # 例外が出ないこと
    assert data["config"]["price_filter"]["min"] == 2000
    assert data["experimental"]["near"]["lookback_days"] == 3
    assert data["as_of"] == run.as_of.isoformat()

    first = data["results"][0]
    assert first["stock"]["code"] == "1234"
    assert first["range"]["lower"] == pytest.approx(4950.0)
    assert isinstance(first["range"]["start_date"], str)  # date は ISO 文字列
    assert first["explain"]
    assert "1234" in text


def test_experimental差し替えが結果に反映される(cfg, exp):
    """パラメータ調整が主作業になるので、差し替えで結果が変わることを保証する。"""
    series = uptrend_with_range().build()
    base = screen_one(make_stock(), series, cfg, exp)

    strict = override(exp, {"range_quality.min_quality": 0.99})
    tightened = screen_one(make_stock(), series, cfg, strict)

    assert base.status == STATUS_ENTRY
    assert tightened.status == STATUS_OUT
    assert "品質" in tightened.out_reason
