"""警戒陰線の有効化タイミング比較（VARIANT A/B/C）のテスト。

このモジュールが守るべき境界をテストで固定する:

    - A/B/C で変わるのは **WARNING へ入る条件だけ**。
      reference_high の定義 / warning_low 割れ後の CASE3 の扱い /
      押し安値の取り方 / トレーリングは 3 案とも同一。
    - VARIANT A は前回の状態機械と完全に同じ挙動（確認ゲートを使わない）。
    - UPTREND_CONFIRMED はその営業日の足だけで判定し、未来を見ない。
    - **prefix 不変性が A/B/C いずれでも成立する**（§14）。

合成OHLCVを使い、株価APIには依存させない。
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta

import pytest

from swing_screener.models import OHLCVBar, PriceSeries
from swing_screener.research import exit_state_machine as sm


def _series(rows: list[tuple[float, float, float, float]], code: str = "0000") -> PriceSeries:
    bars = []
    d = date(2026, 1, 5)
    for o, h, low, c in rows:
        bars.append(OHLCVBar(date=d, open=o, high=h, low=low, close=c, volume=1000))
        d += timedelta(days=1)
    return PriceSeries(code=code, bars=tuple(bars))


def _signal(index: int, *, close: float, lower: float, upper: float) -> dict:
    return {
        "date": (date(2026, 1, 5) + timedelta(days=index)).isoformat(),
        "code": "0000", "name": "テスト", "sector": "テスト",
        "signal_index": str(index), "signal_close": str(close),
        "range_lower": str(lower), "range_upper": str(upper),
        "initial_stop": str(lower * 0.995),
    }


# レンジ 100〜110、初期STOP 99.5。D+0 = index2 で始値 104 の仮想ENTRY。
BASE_HEAD = [
    (100, 105, 99, 104),   # 0
    (100, 105, 99, 104),   # 1 = シグナル日
]

# D+0 未突破 / D+1 で終値突破（breakout_day_high=112, breakout_day_close=111）
BREAK_HEAD = [
    (104, 108, 103, 107),   # D+0 終値107 < 上限110
    (107, 112, 106, 111),   # D+1 終値111 > 110 → BREAKOUT
]


def _track(rows, *, variant=sm.VARIANT_A, exp=None, days=60):
    return sm.track_event(
        _signal(1, close=104.0, lower=100.0, upper=110.0),
        _series(BASE_HEAD + rows), exp, max_track_days=days, variant=variant,
    )


def _all(rows, *, exp=None, days=60) -> dict[str, sm.SMEvent]:
    return {v: _track(rows, variant=v, exp=exp, days=days) for v in sm.VARIANTS}


# --- VARIANT A は前回のままであること -----------------------------------------


def test_A_は確認ゲートを使わず突破翌日から警戒足を拾う(exp):
    ev = _track(BREAK_HEAD + [
        (111, 111.5, 109, 110.5),   # D+2 陰線
    ])

    assert ev.variant == sm.VARIANT_A
    assert ev.uptrend_confirmed_date is None          # A は確認を挟まない
    assert ev.breakout_day_high == 112                # 記録はする
    assert ev.breakout_day_close == 111
    assert [w.day_offset for w in ev.warnings] == [2]


def test_variantを省略するとAになる(exp):
    rows = BREAK_HEAD + [(111, 111.5, 109, 110.5)]
    default = sm.track_event(
        _signal(1, close=104.0, lower=100.0, upper=110.0),
        _series(BASE_HEAD + rows), None, max_track_days=60,
    )
    explicit = _track(rows, variant=sm.VARIANT_A)

    assert [asdict(d) for d in default.daily] == [asdict(d) for d in explicit.daily]
    assert [asdict(w) for w in default.warnings] == [asdict(w) for w in explicit.warnings]


def test_未知のvariantは受け付けない(exp):
    with pytest.raises(ValueError):
        _track(BREAK_HEAD, variant="D")


# --- VARIANT B: 高値更新確認後 ------------------------------------------------


def test_B_確認前の陰線は警戒足にしない(exp):
    """突破翌日の陰線は、まだ高値を更新していないので警戒足にならない。"""
    ev = _track(BREAK_HEAD + [
        (111, 111.5, 109, 110.5),   # D+2 陰線。高値111.5 < 112 なので確認未成立
        (110.5, 111.8, 110, 111),   # D+3 高値111.8 < 112
    ], variant=sm.VARIANT_B)

    assert ev.reached_trend_hold is True
    assert ev.uptrend_confirmed_date is None
    assert ev.warnings == []
    assert ev.warning_gate_pending is True


def test_B_ブレイクアウト日の高値を更新した翌営業日から警戒足を拾う(exp):
    ev = _track(BREAK_HEAD + [
        (111, 111.5, 109, 110.5),     # D+2 陰線（Aならここが警戒足）
        (111, 113, 110, 112.5),       # D+3 高値113 > 112 → UPTREND_CONFIRMED
        (112.5, 112.8, 111, 111.5),   # D+4 陰線 → 警戒足
    ], variant=sm.VARIANT_B)

    assert ev.uptrend_confirmed_day_offset == 3
    assert ev.uptrend_confirmed_price == 113
    assert [w.day_offset for w in ev.warnings] == [4]
    assert sm.E_UPTREND_CONFIRMED in {t.kind for t in ev.timeline}


def test_B_確認日そのものが陰線でも警戒足にしない(exp):
    """§11。確認日は警戒足に使わず、翌営業日以降の最初の陰線を使う。"""
    ev = _track(BREAK_HEAD + [
        (113, 114, 111, 112),         # D+2 高値114 > 112 で確認、かつ陰線
        (112, 113.5, 111, 113),       # D+3 陽線
        (113, 113.2, 111, 112),       # D+4 陰線 → ここが警戒足
    ], variant=sm.VARIANT_B)

    assert ev.uptrend_confirmed_day_offset == 2
    assert ev.uptrend_confirm_day_bearish is True
    assert [w.day_offset for w in ev.warnings] == [4]

    sm.apply_classification([ev])
    assert "CONFIRM_DAY_BEARISH" in ev.flags


def test_B_高値を更新しないまま終わると警戒足が一度も出ない(exp):
    ev = _track(BREAK_HEAD + [
        (111, 111.5, 109, 110.5),
        (110.5, 111, 109.5, 110),
        (110, 110.5, 109, 109.5),
    ], variant=sm.VARIANT_B)

    assert ev.warnings == []
    sm.apply_classification([ev])
    assert "NO_UPTREND_CONFIRM" in ev.flags


# --- VARIANT C: 終値上昇確認後 ------------------------------------------------


def test_C_終値がbreakout_day_closeを超えるまで警戒足にしない(exp):
    ev = _track(BREAK_HEAD + [
        (111, 111.5, 109, 110.5),     # D+2 陰線。終値110.5 < 111
        (110.5, 113, 110, 111),       # D+3 終値111 は 111 を「超えて」いない
        (111, 112, 110, 111.5),       # D+4 終値111.5 > 111 → UPTREND_CONFIRMED
        (111.5, 111.8, 110, 110.5),   # D+5 陰線 → 警戒足
    ], variant=sm.VARIANT_C)

    assert ev.uptrend_confirmed_day_offset == 4
    assert ev.uptrend_confirmed_price == 111.5
    assert [w.day_offset for w in ev.warnings] == [5]


def test_C_高値だけ更新しても確認にならない(exp):
    """同じ足で B は確認成立、C は不成立になることを固定する。"""
    rows = BREAK_HEAD + [
        (110, 113, 109, 110.5),       # 高値113 > 112 だが終値110.5 < 111
        (110.5, 111, 109, 110),       # 陰線
    ]
    b = _track(rows, variant=sm.VARIANT_B)
    c = _track(rows, variant=sm.VARIANT_C)

    assert b.uptrend_confirmed_day_offset == 2
    assert [w.day_offset for w in b.warnings] == [3]
    assert c.uptrend_confirmed_date is None
    assert c.warnings == []


def test_C_終値だけ更新すればBが不成立でも確認になる(exp):
    rows = BREAK_HEAD + [
        (110.5, 111.8, 110, 111.5),   # 高値111.8 < 112 だが終値111.5 > 111
        (111.5, 111.6, 110, 111),     # 陰線
    ]
    b = _track(rows, variant=sm.VARIANT_B)
    c = _track(rows, variant=sm.VARIANT_C)

    assert c.uptrend_confirmed_day_offset == 2
    assert [w.day_offset for w in c.warnings] == [3]
    assert b.uptrend_confirmed_date is None
    assert b.warnings == []


# --- 変えていないことの確認 ---------------------------------------------------


# 3案とも同じ日に警戒足が出る系列（突破翌日に高値も終値も更新する）
SAME_WARNING_ROWS = BREAK_HEAD + [
    (111, 114, 110, 113.5),       # D+2 高値114>112 かつ終値113.5>111 → 3案とも確認
    (113.5, 113.8, 111, 112),     # D+3 陰線 → 3案とも警戒足（reference_high=114）
    (112, 112.5, 110, 112.2),     # D+4
    (112.2, 115, 111, 114.5),     # D+5 高値115 > 114 → REHIGH
    (114.5, 115.5, 113, 114),     # D+6 陰線 → 次の警戒足
]


def test_3案の違いはWARNINGへ入る条件だけで他は完全に同じ(exp):
    """同じ日に警戒足が出る系列なら、3 案の結果は完全一致する。"""
    evs = _all(SAME_WARNING_ROWS)

    ref = evs[sm.VARIANT_A]
    for v in (sm.VARIANT_B, sm.VARIANT_C):
        assert [asdict(d) for d in evs[v].daily] == [asdict(d) for d in ref.daily]
        assert [asdict(w) for w in evs[v].warnings] == [asdict(w) for w in ref.warnings]
        assert [asdict(s) for s in evs[v].stop_updates] == [
            asdict(s) for s in ref.stop_updates
        ]
        for case in sm.CASES:
            assert asdict(evs[v].cases[case]) == asdict(ref.cases[case])


def test_reference_highの定義は3案とも保有中最高値のまま(exp):
    """警戒足自身の高値ではなく、その時点までの保有中最高値であること。"""
    evs = _all(BREAK_HEAD + [
        (111, 111.5, 109, 110.5),     # D+2 陰線（Aの警戒足）
        (111, 116, 110, 115),         # D+3 高値116 = 保有中最高値
        (115, 115.5, 113, 114),       # D+4 陰線（B/Cの警戒足）
    ])

    a = evs[sm.VARIANT_A].warnings[0]
    assert a.day_offset == 2
    assert a.reference_high == 112     # D+1 の高値。警戒足自身の高値 111.5 ではない

    for v in (sm.VARIANT_B, sm.VARIANT_C):
        w = evs[v].warnings[0]
        assert w.day_offset == 4
        assert w.reference_high == 116  # D+3 の高値。自身の高値 115.5 ではない


def test_押し安値とトレーリングのロジックは3案で同一(exp):
    evs = _all(SAME_WARNING_ROWS)

    for v in sm.VARIANTS:
        ev = evs[v]
        assert ev.rehigh_count == 1
        su = ev.stop_updates[0]
        # 警戒足（D+3）から REHIGH（D+5）までの最安値 110 の 0.5% 下
        assert su.new_swing_low_candidate == 110
        assert su.new_stop == pytest.approx(110 * sm.TRAIL_BUFFER)
        assert su.effective_from_day_offset == su.day_offset + 1


def test_STOPは3案とも上方向にしか動かない(exp):
    for v in sm.VARIANTS:
        ev = _track(SAME_WARNING_ROWS, variant=v)
        stops = [d.active_stop for d in ev.daily]
        assert stops == sorted(stops)
        for su in ev.stop_updates:
            assert su.new_stop >= su.old_stop


def test_warning_low割れ後のCASE3の扱いは3案とも変えていない(exp):
    """warning_low を割っても CASE3 は降りず WARNING に留まる（解釈(b)）。"""
    rows = BREAK_HEAD + [
        (111, 114, 110, 113.5),       # D+2 3案とも確認
        (113.5, 113.8, 111, 112),     # D+3 陰線 → 警戒足（warning_low=111）
        (112, 112.5, 110.5, 111),     # D+4 安値110.5 < 111 → 割れ
        (111, 111.5, 110.8, 111.2),   # D+5 まだ WARNING のまま
    ]
    for v in sm.VARIANTS:
        ev = _track(rows, variant=v)
        w = ev.warnings[0] if ev.warnings else None
        assert w is not None and w.low_break_day_offset == 4
        assert ev.cases[sm.CASE2].exit_type == sm.X_WARNING_LOW
        assert ev.cases[sm.CASE3].exit_type != sm.X_WARNING_LOW
        assert (w.days_held_in_warning_after_low_break or 0) >= 1


def test_確認ゲートは突破直後の1回だけで再高値更新後は課さない(exp):
    """解釈(d)。REHIGH で TREND_HOLD に戻ったあとは A と同じく翌営業日から拾う。"""
    for v in sm.VARIANTS:
        ev = _track(SAME_WARNING_ROWS, variant=v)
        # D+5 が REHIGH、その翌営業日 D+6 の陰線が 2 本目の警戒足
        assert [w.day_offset for w in ev.warnings] == [3, 6]


def test_固定利確は3案とも追加していない(exp):
    """+3/+5/+10% に到達しても機械的に降りないこと。"""
    rows = BREAK_HEAD + [
        (111, 114, 110, 113.5),
        (113.5, 125, 113, 124),       # +19% でも降りない
        (124, 124.5, 122, 123),       # 陰線
    ]
    for v in sm.VARIANTS:
        ev = _track(rows, variant=v)
        assert ev.reached_gain[10.0] is True
        for case in sm.CASES:
            assert ev.cases[case].exit_type != "TAKE_PROFIT"
        assert ev.cases[sm.CASE3].exit_type in (sm.X_DATA_END,)


# --- §14 look-ahead bias ------------------------------------------------------


LOOKAHEAD_ROWS = BREAK_HEAD + [
    (111, 111.5, 109, 110.5),
    (111, 113, 110, 112.5),
    (112.5, 112.8, 111, 111.5),
    (111.5, 112, 110, 111.8),
    (111.8, 114, 110.5, 113.5),
    (113.5, 113.8, 112, 112.5),
    (112.5, 113, 100, 101),
]


def test_UPTREND_CONFIRMEDはその営業日の足だけで判定する(exp):
    """確認成立日で系列を打ち切っても、同じ日に同じ判定になる。

    `_track` は BASE_HEAD の 2 本を前置し、仮想ENTRY は rows[0]（= D+0）なので
    「D+off までの足」は `rows[: off + 1]` に等しい。
    """
    for v in (sm.VARIANT_B, sm.VARIANT_C):
        full = _track(LOOKAHEAD_ROWS, variant=v)
        off = full.uptrend_confirmed_day_offset
        assert off is not None
        part = _track(LOOKAHEAD_ROWS[: off + 1], variant=v)
        assert part.uptrend_confirmed_date == full.uptrend_confirmed_date
        assert part.uptrend_confirmed_day_offset == off


def test_確認成立日より前の足だけでは確認しない(exp):
    for v in (sm.VARIANT_B, sm.VARIANT_C):
        full = _track(LOOKAHEAD_ROWS, variant=v)
        off = full.uptrend_confirmed_day_offset
        part = _track(LOOKAHEAD_ROWS[:off], variant=v)   # 確認日を含めない
        assert part.uptrend_confirmed_date is None
        assert part.warnings == []


def test_prefix不変性_ABCいずれの案でも成立する(exp):
    """途中まで走らせた結果が、全長で走らせた結果の先頭と一致すること。

    未来の足を 1 本でも先読みしていれば、打ち切り位置のどこかで必ず壊れる。
    """
    for v in sm.VARIANTS:
        full = _track(LOOKAHEAD_ROWS, variant=v)
        full_daily = [asdict(d) for d in full.daily]

        for k in range(3, len(LOOKAHEAD_ROWS) + 1):
            part = _track(LOOKAHEAD_ROWS[:k], variant=v)
            assert [asdict(d) for d in part.daily] == full_daily[: len(part.daily)], (
                f"variant={v} k={k} で日次ログが一致しない"
            )
            # effective_from_date は除外（確定時点では翌営業日がまだ存在しない）
            def _core(updates):
                return [
                    {kk: vv for kk, vv in asdict(s).items() if kk != "effective_from_date"}
                    for s in updates
                ]
            assert _core(part.stop_updates) == _core(full.stop_updates)[: len(part.stop_updates)]
            if part.uptrend_confirmed_date is not None:
                assert part.uptrend_confirmed_date == full.uptrend_confirmed_date


# 時間とともに情報が「増える」列。増えるのは正しいので prefix 比較から除く。
_ACCUMULATING = {
    "fractal_confirm_day_offset", "fractal_is_same_low",
    "days_held_in_warning_after_low_break", "resolved_date",
    "resolved_day_offset", "days_to_resolve", "resolution", "extra_bearish_count",
}


def test_prefix不変性_確認と警戒足の情報は積み増すだけで書き換わらない(exp):
    """一度埋まった値が、後の足で別の値に変わらないこと。

    変わるなら、その時点で未来を見て決めていたことになる。
    """
    for v in sm.VARIANTS:
        full = _track(LOOKAHEAD_ROWS, variant=v)
        full_by_date = {w.date: asdict(w) for w in full.warnings}

        for k in range(3, len(LOOKAHEAD_ROWS) + 1):
            part = _track(LOOKAHEAD_ROWS[:k], variant=v)
            if part.uptrend_confirmed_date is not None:
                assert part.uptrend_confirmed_date == full.uptrend_confirmed_date, (
                    f"variant={v} k={k} で UPTREND_CONFIRMED の日付が変わった"
                )
            for w in part.warnings:
                assert w.date in full_by_date, (
                    f"variant={v} 打ち切り {k} 本目に無い警戒足 {w.date}"
                )
                ref = full_by_date[w.date]
                for key, value in asdict(w).items():
                    if key in _ACCUMULATING or value is None:
                        continue
                    if isinstance(value, bool):
                        # bool は False が「まだ起きていない」と区別できないので、
                        # 「一度 True になった事実が取り消されない」ことだけを見る
                        assert not (value and not ref[key]), (
                            f"variant={v} k={k}: {w.date} の {key} が取り消された"
                        )
                        continue
                    assert value == ref[key], (
                        f"variant={v} k={k}: {w.date} の {key} が "
                        f"{value} → {ref[key]} に変わった"
                    )
