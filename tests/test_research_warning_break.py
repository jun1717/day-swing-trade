"""warning_low を割ったあとの扱い（LOW / CLOSE / STRUCTURAL）のテスト。

このモジュールが守るべき境界をテストで固定する:

    - 4 案で変わるのは **warning_low を割ったあとの処理だけ**。
      WARNING 開始条件 / reference_high の定義 / 押し安値 / トレーリング /
      初期STOP は 4 案とも同一。
    - `break_rule` の既定は `HOLD_UNTIL_STOP` で、前回の CASE3 と完全に同じ挙動。
    - トリガーは **その営業日の足だけ** で判定する（§17）。
    - close 型の EXIT は翌営業日始値でのみ約定でき、その始値は判定に使わない。
    - **prefix 不変性が 4 案いずれでも成立する**（§17）。

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

# D+0 未突破 / D+1 で終値突破 / D+2 が警戒陰線（warning_low = 109）
BREAK_HEAD = [
    (104, 108, 103, 107),        # D+0 終値107 < 上限110
    (107, 112, 106, 111),        # D+1 終値111 > 110 → BREAKOUT
    (111, 111.5, 109, 110.5),    # D+2 陰線 → WARNING（warning_low 109）
]

WARNING_LOW = 109.0
RANGE_UPPER = 110.0


def _track(rows, *, break_rule=sm.BREAK_HOLD, exp=None, days=60):
    ev = sm.track_event(
        _signal(1, close=104.0, lower=100.0, upper=110.0),
        _series(BASE_HEAD + rows), exp, max_track_days=days, break_rule=break_rule,
    )
    sm.apply_classification([ev])   # flags を埋める（研究側と同じ後段パス）
    return ev


def _all(rows, *, exp=None, days=60) -> dict[str, sm.SMEvent]:
    return {r: _track(rows, break_rule=r, exp=exp, days=days) for r in sm.BREAK_RULES}


def _first_break(ev: sm.SMEvent) -> sm.WarningBreak:
    return ev.warning_breaks[0]


# --- 既定は従来どおりであること ------------------------------------------------


def test_break_ruleを省略すると降りない解釈になる(exp):
    rows = BREAK_HEAD + [
        (110.5, 110.8, 108, 108.5),    # D+3 日中も終値も warning_low 割れ
        (108.5, 109, 107, 108),        # D+4
    ]
    default = sm.track_event(
        _signal(1, close=104.0, lower=100.0, upper=110.0),
        _series(BASE_HEAD + rows), None, max_track_days=60,
    )
    explicit = _track(rows, break_rule=sm.BREAK_HOLD)

    assert default.break_rule == sm.BREAK_HOLD
    assert asdict(default.cases[sm.CASE3]) == asdict(explicit.cases[sm.CASE3])
    # 割っても降りない = 状態機械は WARNING に留まる
    assert default.cases[sm.CASE3].exit_type not in sm.BREAK_EXIT_TYPES


def test_未知のbreak_ruleは弾く(exp):
    with pytest.raises(ValueError, match="未知の break_rule"):
        _track(BREAK_HEAD, break_rule="SOMETHING_ELSE")


# --- V1 LOW_BREAK -------------------------------------------------------------


def test_V1は日中に割った日に降りる(exp):
    rows = BREAK_HEAD + [
        (110.5, 111, 108.5, 110.8),    # D+3 安値108.5 < 109 だが終値は回復
        (110.8, 111, 110, 110.5),      # D+4 約定日
    ]
    ev = _track(rows, break_rule=sm.BREAK_LOW)
    r = ev.path_result

    assert r.exit_type == sm.X_BREAK_EXIT[sm.BREAK_LOW]
    assert r.trigger_day_offset == 3          # 判定は D+3
    assert r.exit_day_offset == 4             # 約定は翌営業日
    assert r.fill_rule == "next_open"
    assert r.exit_reference_price == 110.8    # D+4 の始値


def test_V1のSTOP注文参考価格は主分析と別に持つ(exp):
    rows = BREAK_HEAD + [
        (110.5, 111, 108.5, 110.8),
        (110.8, 111, 110, 110.5),
    ]
    ev = _track(rows, break_rule=sm.BREAK_LOW)

    # 主分析（path_result）は翌営業日始値、参考の CASE2 は warning_low
    assert ev.path_result.exit_reference_price == 110.8
    assert ev.cases[sm.CASE2].exit_reference_price == WARNING_LOW
    assert ev.cases[sm.CASE2].fill_rule == "same_day"


# --- V2 CLOSE_BREAK -----------------------------------------------------------


def test_V2は日中割れだけでは降りない(exp):
    rows = BREAK_HEAD + [
        (110.5, 111, 108.5, 110.8),    # D+3 日中割れだが終値は回復
        (110.8, 113, 110, 112.5),      # D+4 上昇（reference_high 112 を更新）
        (112.5, 113, 111, 112),        # D+5
    ]
    ev = _track(rows, break_rule=sm.BREAK_CLOSE)

    assert ev.path_result.exit_type not in sm.BREAK_EXIT_TYPES
    b = _first_break(ev)
    assert b.intraday_break_day_offset == 3
    assert b.intraday_break_close_recovered is True
    assert b.close_break_date is None
    # 割れを無視したので、既存の再高値更新 → 押し安値確定はそのまま働く
    assert ev.rehigh_count == 1
    assert ev.stop_raise_count == 1


def test_V2は終値で割った日に降りる(exp):
    rows = BREAK_HEAD + [
        (110.5, 111, 108.5, 110.8),    # D+3 日中割れ・終値回復 → 降りない
        (110.8, 111, 108, 108.5),      # D+4 終値108.5 < 109 → トリガー
        (108.2, 109, 107, 108),        # D+5 約定日
    ]
    ev = _track(rows, break_rule=sm.BREAK_CLOSE)
    r = ev.path_result

    assert r.exit_type == sm.X_BREAK_EXIT[sm.BREAK_CLOSE]
    assert r.trigger_day_offset == 4
    assert r.exit_day_offset == 5
    assert r.exit_reference_price == 108.2      # D+5 の始値
    assert r.fill_gap_pct == pytest.approx((108.2 - 108.5) / 108.5 * 100.0)


# --- V3 STRUCTURAL_BREAK ------------------------------------------------------


def test_V3は元レンジ上限より上なら終値割れでも降りない(exp):
    # warning_low 109 を終値で割るが、元レンジ上限 110 は…下回ってしまうので
    # 上限を 108 に下げたケースを別に作る必要がある。ここでは上限 108 の系列を使う。
    sig = _signal(1, close=104.0, lower=100.0, upper=108.0)
    rows = [
        (104, 107, 103, 106),          # D+0
        (106, 112, 105, 111),          # D+1 終値111 > 108 → BREAKOUT
        (111, 111.5, 109, 110.5),      # D+2 陰線 → warning_low 109
        (110.5, 111, 108.5, 108.8),    # D+3 終値108.8 < 109 だが 108 は維持
        (108.8, 110, 108.2, 109.5),    # D+4
    ]
    v2 = sm.track_event(sig, _series(BASE_HEAD + rows), None,
                        max_track_days=60, break_rule=sm.BREAK_CLOSE)
    v3 = sm.track_event(sig, _series(BASE_HEAD + rows), None,
                        max_track_days=60, break_rule=sm.BREAK_STRUCT)

    assert v2.path_result.exit_type == sm.X_BREAK_EXIT[sm.BREAK_CLOSE]
    assert v2.path_result.trigger_day_offset == 3
    assert v3.path_result.exit_type not in sm.BREAK_EXIT_TYPES      # V3 は保有継続
    b = v3.warning_breaks[0]
    assert b.close_break_date is not None
    assert b.close_break_above_range_upper is True
    assert b.struct_break_date is None


def test_V3は上限も終値で割った日に降りる(exp):
    rows = BREAK_HEAD + [
        (110.5, 111, 108.5, 109.5),    # D+3 終値109.5 は warning_low 109 より上
        (109.5, 110, 107, 107.5),      # D+4 終値107.5 < 109 かつ < 110 → トリガー
        (107.2, 108, 106, 107),        # D+5 約定日
    ]
    ev = _track(rows, break_rule=sm.BREAK_STRUCT)
    r = ev.path_result

    assert r.exit_type == sm.X_BREAK_EXIT[sm.BREAK_STRUCT]
    assert r.trigger_day_offset == 4
    assert r.exit_reference_price == 107.2


def test_3案のトリガーは入れ子になる(exp):
    """close 割れは必ず日中割れでもあるので V1 ⊇ V2 ⊇ V3。"""
    rows = BREAK_HEAD + [
        (110.5, 111, 108.5, 110.8),
        (110.8, 111, 108, 108.5),
        (108.2, 109, 106, 106.5),
        (106.5, 107, 105, 106),
    ]
    evs = _all(rows)
    t1 = evs[sm.BREAK_LOW].path_result.trigger_day_offset
    t2 = evs[sm.BREAK_CLOSE].path_result.trigger_day_offset
    t3 = evs[sm.BREAK_STRUCT].path_result.trigger_day_offset
    assert t1 is not None and t2 is not None and t3 is not None
    assert t1 <= t2 <= t3


# --- 固定するもの（4 案で同一）------------------------------------------------


def test_警戒足とreference_highは4案とも同じ(exp):
    rows = BREAK_HEAD + [
        (110.5, 111, 108.5, 110.8),
        (110.8, 111, 108, 108.5),
        (108.2, 109, 107, 108),
    ]
    evs = _all(rows)
    base = evs[sm.BREAK_HOLD]
    assert base.warnings, "前提: 警戒足が出ていること"
    for rule, ev in evs.items():
        assert ev.warnings[0].date == base.warnings[0].date, rule
        assert ev.warnings[0].low == base.warnings[0].low, rule
        assert ev.warnings[0].reference_high == base.warnings[0].reference_high, rule
        assert ev.upper_close_break_date == base.upper_close_break_date, rule
        assert ev.initial_stop == base.initial_stop == 99.5


def test_押し安値とtrailは4案とも同じ計算(exp):
    """割れずに再高値更新へ進む形では、4 案の結果が完全に一致する。"""
    rows = BREAK_HEAD + [
        (110.5, 111, 109.5, 110.2),    # 警戒足の安値 109 は割らない
        (110.2, 113, 110, 112.5),      # reference_high 112 を更新 → 押し安値確定
        (112.5, 113, 111, 112),
    ]
    evs = _all(rows)
    base = evs[sm.BREAK_HOLD]
    assert base.stop_raise_count == 1
    for rule, ev in evs.items():
        assert ev.stop_raise_count == base.stop_raise_count, rule
        assert [asdict(s) for s in ev.stop_updates] == [
            asdict(s) for s in base.stop_updates
        ], rule
        assert ev.max_active_stop == base.max_active_stop, rule


def test_STOPは4案とも上方向にしか動かない(exp):
    rows = BREAK_HEAD + [
        (110.5, 111, 109.5, 110.2),
        (110.2, 113, 110, 112.5),
        (112.5, 113, 111, 112),
        (112, 112.5, 110.5, 111),
        (111, 115, 110.8, 114),
        (114, 114.5, 100, 101),
    ]
    for rule, ev in _all(rows).items():
        levels = [d.active_stop for d in ev.daily]
        assert levels == sorted(levels), rule


def test_固定利確は4案とも入れていない(exp):
    rows = BREAK_HEAD + [
        (110.5, 116, 110, 115.5),
        (115.5, 125, 115, 124),        # +19% でも降りない
        (124, 124.5, 122, 123),
    ]
    for rule, ev in _all(rows).items():
        assert ev.reached_gain[10.0] is True, rule
        assert ev.path_result.exit_type != "TAKE_PROFIT", rule
        assert ev.path_result.exit_type in (sm.X_DATA_END,) + sm.BREAK_EXIT_TYPES, rule


def test_割れの観測はbreak_ruleに関係なく同じ規則(exp):
    """観測できる範囲は案ごとに違うが、記録の規則そのものは同じ。"""
    rows = BREAK_HEAD + [
        (110.5, 111, 108.5, 110.8),    # D+3 日中割れ・終値回復
        (110.8, 111, 108, 108.5),      # D+4 終値割れ（上限110 も割れ）
        (108.2, 109, 107, 108),
    ]
    evs = _all(rows)
    for rule in (sm.BREAK_HOLD, sm.BREAK_CLOSE, sm.BREAK_STRUCT):
        b = _first_break(evs[rule])
        assert b.intraday_break_day_offset == 3, rule
        assert b.intraday_break_close_recovered is True, rule
    # V1 は D+3 で降りるので、その後の終値割れは観測できない
    b1 = _first_break(evs[sm.BREAK_LOW])
    assert b1.intraday_break_day_offset == 3
    assert b1.close_break_date is None


# --- STUCK_IN_WARNING（§11）----------------------------------------------------


def test_V1はSTUCK_IN_WARNINGにならない(exp):
    rows = BREAK_HEAD + [
        (110.5, 111, 108.5, 110.8),
        (110.8, 111, 108.6, 110),
        (110, 110.5, 108.7, 109.8),
        (109.8, 110, 108.8, 109.5),
    ]
    evs = _all(rows)
    assert "STUCK_IN_WARNING" in evs[sm.BREAK_HOLD].flags
    assert "STUCK_IN_WARNING" not in evs[sm.BREAK_LOW].flags
    assert all(
        (w.days_held_in_warning_after_low_break or 0) == 0
        for w in evs[sm.BREAK_LOW].warnings
    )


def test_割ったのに降りない解釈だけ滞留が長くなる(exp):
    rows = BREAK_HEAD + [
        (110.5, 111, 108.5, 110.8),    # D+3 日中割れ・終値回復（V2/V3 は降りない）
        (110.8, 111, 108.6, 110),      # D+4 同上
        (110, 110.5, 108.7, 109.8),    # D+5 同上
        (109.8, 110, 108, 108.2),      # D+6 終値割れ → V2/V3 トリガー
        (108, 108.5, 107, 107.5),
    ]
    evs = _all(rows)
    stuck = {
        rule: max((w.days_held_in_warning_after_low_break or 0) for w in ev.warnings)
        for rule, ev in evs.items()
    }
    assert stuck[sm.BREAK_LOW] == 0
    assert stuck[sm.BREAK_CLOSE] == 3        # D+3 → D+6
    assert stuck[sm.BREAK_HOLD] >= stuck[sm.BREAK_CLOSE]


# --- §4 割れてもEXIT条件を満たさない場合、既存ロジックを続ける -------------------


def test_割れても降りない場合は警戒足を置き換えない(exp):
    rows = BREAK_HEAD + [
        (110.5, 111, 108.5, 110.8),    # D+3 日中割れ（V2/V3 は保有継続）
        (110.8, 111, 110, 110.2),      # D+4 陰線だが警戒足は置き換えない
        (110.2, 113, 110, 112.5),      # D+5 reference_high 112 更新
    ]
    for rule in (sm.BREAK_CLOSE, sm.BREAK_STRUCT):
        ev = _track(rows, break_rule=rule)
        assert len(ev.warnings) == 1, rule
        assert ev.warnings[0].day_offset == 2, rule          # 警戒足は D+2 のまま
        assert ev.warnings[0].low == WARNING_LOW, rule       # warning_low も維持
        assert ev.warnings[0].extra_bearish_count == 1, rule
        assert ev.rehigh_count == 1, rule                    # 既存ロジックは継続
        assert ev.stop_raise_count == 1, rule


def test_同日に終値割れと再高値更新なら再高値更新を優先する(exp):
    rows = BREAK_HEAD + [
        (110.5, 113, 108, 108.5),      # D+3 高値113 > 112 かつ 終値108.5 < 109
        (108.5, 109, 107, 108),
    ]
    for rule in (sm.BREAK_CLOSE, sm.BREAK_STRUCT):
        ev = _track(rows, break_rule=rule)
        assert ev.rehigh_count == 1, rule
        assert ev.close_break_with_same_day_rehigh == 1, rule
        assert ev.path_result.exit_type not in sm.BREAK_EXIT_TYPES, rule
        assert ev.warning_breaks[0].same_day_rehigh_on_close_break is True, rule


# --- §17 look-ahead bias ------------------------------------------------------


LOOKAHEAD_ROWS = BREAK_HEAD + [
    (110.5, 111, 108.5, 110.8),    # D+3 日中割れ・終値回復
    (110.8, 113, 110, 112.5),      # D+4 再高値更新 → 押し安値確定
    (112.5, 112.8, 111, 111.5),    # D+5 陰線 → 2 本目の警戒足
    (111.5, 112, 110.5, 110.8),    # D+6 日中割れ・終値も割れ
    (110.5, 111, 109, 110),        # D+7
    (110, 110.5, 100, 101),        # D+8 STOP へ
]


def test_LOOKAHEAD_ROWSが検証として空でない(exp):
    """打ち切りテストが「何も起きない系列」で通ってしまわないこと。"""
    evs = _all(LOOKAHEAD_ROWS)
    assert evs[sm.BREAK_HOLD].warning_count >= 2
    assert evs[sm.BREAK_HOLD].stop_raise_count >= 1
    triggers = {
        rule: evs[rule].path_result.trigger_day_offset
        for rule in (sm.BREAK_LOW, sm.BREAK_CLOSE, sm.BREAK_STRUCT)
    }
    assert triggers[sm.BREAK_LOW] is not None
    assert triggers[sm.BREAK_CLOSE] is not None
    assert triggers[sm.BREAK_LOW] < triggers[sm.BREAK_CLOSE]


def test_トリガーはその営業日の足だけで判定する(exp):
    """トリガー日で系列を打ち切っても、同じ日に同じ判定になる。

    `_track` は BASE_HEAD の 2 本を前置し、仮想ENTRY は rows[0]（= D+0）なので
    「D+off までの足」は `rows[: off + 1]` に等しい。
    """
    for rule in (sm.BREAK_LOW, sm.BREAK_CLOSE, sm.BREAK_STRUCT):
        full = _track(LOOKAHEAD_ROWS, break_rule=rule)
        off = full.path_result.trigger_day_offset
        if off is None:
            continue
        part = _track(LOOKAHEAD_ROWS[: off + 1], break_rule=rule)
        assert part.path_result.trigger_day_offset == off, rule
        assert part.path_result.trigger_date == full.path_result.trigger_date, rule
        assert part.path_result.exit_type == full.path_result.exit_type, rule


def test_トリガー日より前の足だけでは降りない(exp):
    for rule in (sm.BREAK_LOW, sm.BREAK_CLOSE, sm.BREAK_STRUCT):
        full = _track(LOOKAHEAD_ROWS, break_rule=rule)
        off = full.path_result.trigger_day_offset
        if off is None:
            continue
        part = _track(LOOKAHEAD_ROWS[:off], break_rule=rule)   # トリガー日を含めない
        assert part.path_result.exit_type not in sm.BREAK_EXIT_TYPES, rule


def test_close型のEXITは翌営業日始値で約定する(exp):
    for rule in (sm.BREAK_CLOSE, sm.BREAK_STRUCT):
        ev = _track(LOOKAHEAD_ROWS, break_rule=rule)
        r = ev.path_result
        if r.exit_type not in sm.BREAK_EXIT_TYPES:
            continue
        assert r.fill_rule == "next_open", rule
        assert r.exit_day_offset == r.trigger_day_offset + 1, rule
        # 実際にその翌営業日の始値になっていること
        expected = LOOKAHEAD_ROWS[r.trigger_day_offset + 1][0]
        assert r.exit_reference_price == expected, rule


def test_翌営業日始値は判定に使われない(exp):
    """トリガー日で打ち切ると約定だけが保留になり、判定は変わらない。

    判定に翌日の始値を使っていれば、翌日が存在しない打ち切りで
    トリガー自体が消えるか別の日に動くはずである。
    """
    for rule in (sm.BREAK_LOW, sm.BREAK_CLOSE, sm.BREAK_STRUCT):
        full = _track(LOOKAHEAD_ROWS, break_rule=rule)
        off = full.path_result.trigger_day_offset
        if off is None:
            continue
        part = _track(LOOKAHEAD_ROWS[: off + 1], break_rule=rule)
        assert part.path_result.trigger_day_offset == off, rule
        assert part.path_result.fill_pending is True, rule
        assert full.path_result.fill_pending is False, rule


def test_EXIT後の値動きは状態遷移に影響しない(exp):
    """EXIT 後の足を差し替えても、EXIT の日付・価格・警戒足が変わらないこと。"""
    for rule in (sm.BREAK_LOW, sm.BREAK_CLOSE, sm.BREAK_STRUCT):
        full = _track(LOOKAHEAD_ROWS, break_rule=rule)
        off = full.path_result.exit_day_offset
        if off is None or off + 1 >= len(LOOKAHEAD_ROWS):
            continue
        # EXIT の翌日以降を暴騰に差し替える
        mutated = LOOKAHEAD_ROWS[: off + 1] + [
            (200, 260, 199, 255) for _ in LOOKAHEAD_ROWS[off + 1 :]
        ]
        other = _track(mutated, break_rule=rule)
        assert other.path_result.exit_date == full.path_result.exit_date, rule
        assert (other.path_result.exit_reference_price
                == full.path_result.exit_reference_price), rule
        assert [w.date for w in other.warnings] == [w.date for w in full.warnings], rule


def test_reference_highは4案とも同じで未来を見ない(exp):
    full = _all(LOOKAHEAD_ROWS)
    base = full[sm.BREAK_HOLD]
    for k in range(3, len(LOOKAHEAD_ROWS) + 1):
        part = _track(LOOKAHEAD_ROWS[:k], break_rule=sm.BREAK_HOLD)
        for w in part.warnings:
            ref = next((x for x in base.warnings if x.date == w.date), None)
            assert ref is not None, f"k={k} 打ち切りにしか無い警戒足 {w.date}"
            assert w.reference_high == ref.reference_high, f"k={k} {w.date}"


def test_prefix不変性_4案いずれでも成立する(exp):
    """途中まで走らせた結果が、全長で走らせた結果の先頭と一致すること。

    未来の足を 1 本でも先読みしていれば、打ち切り位置のどこかで必ず壊れる。
    """
    for rule in sm.BREAK_RULES:
        full = _track(LOOKAHEAD_ROWS, break_rule=rule)
        full_daily = [asdict(d) for d in full.daily]

        for k in range(3, len(LOOKAHEAD_ROWS) + 1):
            part = _track(LOOKAHEAD_ROWS[:k], break_rule=rule)
            assert [asdict(d) for d in part.daily] == full_daily[: len(part.daily)], (
                f"break_rule={rule} k={k} で日次ログが一致しない"
            )

            def _core(updates):
                return [
                    {kk: vv for kk, vv in asdict(s).items() if kk != "effective_from_date"}
                    for s in updates
                ]
            assert _core(part.stop_updates) == _core(full.stop_updates)[: len(part.stop_updates)]

            # トリガーは打ち切っても動かない。約定だけが「まだ来ていない」で保留になる。
            pt, ft = part.path_result.trigger_day_offset, full.path_result.trigger_day_offset
            if pt is not None and ft is not None:
                assert pt == ft, f"break_rule={rule} k={k} でトリガー日が変わった"
                if not part.path_result.fill_pending:
                    assert (part.path_result.exit_reference_price
                            == full.path_result.exit_reference_price), (
                        f"break_rule={rule} k={k} で約定価格が変わった"
                    )


# 時間とともに情報が「増える」列。増えるのは正しいので prefix 比較から除く。
_ACCUMULATING = {
    "observed_days", "intraday_break_days", "close_break_days", "struct_break_days",
    "close_break_date", "close_break_day_offset", "close_break_close",
    "close_break_above_range_upper", "close_break_next_open_date",
    "close_break_next_open", "close_break_next_open_gap_pct",
    "days_from_intraday_to_close_break",
    "struct_break_date", "struct_break_day_offset", "struct_break_close",
    "struct_break_next_open_date", "struct_break_next_open",
    "struct_break_next_open_gap_pct", "days_from_close_to_struct_break",
    "intraday_break_next_open_date", "intraday_break_next_open",
    "intraday_break_next_open_gap_pct",
    "resolution", "left_warning_day_offset", "rehigh_date",
    "same_day_rehigh_on_close_break",
}


def test_prefix不変性_割れの記録は積み増すだけで書き換わらない(exp):
    """一度埋まった値が、後の足で別の値に変わらないこと。

    変わるなら、その時点で未来を見て決めていたことになる。
    """
    for rule in sm.BREAK_RULES:
        full = _track(LOOKAHEAD_ROWS, break_rule=rule)
        full_by_date = {b.warning_date: asdict(b) for b in full.warning_breaks}

        for k in range(3, len(LOOKAHEAD_ROWS) + 1):
            part = _track(LOOKAHEAD_ROWS[:k], break_rule=rule)
            for b in part.warning_breaks:
                assert b.warning_date in full_by_date, (
                    f"break_rule={rule} 打ち切り {k} 本目に無い警戒足 {b.warning_date}"
                )
                ref = full_by_date[b.warning_date]
                for key, value in asdict(b).items():
                    if key in _ACCUMULATING or value is None:
                        continue
                    if isinstance(value, bool):
                        assert not (value and not ref[key]), (
                            f"break_rule={rule} k={k}: "
                            f"{b.warning_date} の {key} が取り消された"
                        )
                        continue
                    assert value == ref[key], (
                        f"break_rule={rule} k={k}: {b.warning_date} の {key} が "
                        f"{value} → {ref[key]} に変わった"
                    )
