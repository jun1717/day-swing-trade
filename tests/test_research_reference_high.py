"""`reference_high` の決め方（RH-A/B/C/D/E）のテスト。

このモジュールが守るべき境界をテストで固定する:

    - 5 案で変わるのは **reference_high の決め方だけ**。
      ENTRY / WARNING 開始条件 / warning_low / 押し安値の取り方 /
      trail = 押し安値*0.995 / STOP を下げないこと / 初期STOP は 5 案とも同一。
    - `rh_rule` の既定は `HOLDING_HIGH` で、従来と完全に同じ挙動。
    - どの案も **警戒足当日までの足だけ** から決まる（§18）。
      RH-C は当日の終値を、RH-E は当日の高値を含まない。
    - REHIGH はその営業日の足だけで判定し、押し安値は再突破日に初めて確定、
      引き上げた STOP は翌営業日から有効（§18）。
    - **prefix 不変性が 5 案いずれでも成立する**（§18）。
    - 同日に REHIGH と利確候補が両立したら `AMBIGUOUS_REHIGH_EXIT_ORDER` として
      分離し、どちらを先に採るかは外部パラメータにする（§7）。

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

# --- ケース(i): 警戒足は最高値を作っていない -----------------------------------
# RH-A = 112（D+1 の高値） / RH-B = 111.5 / RH-C = 111（D+1 の終値）
# RH-D = 111.2（警戒足の始値） / RH-E = 112（＝RH-A）
HEAD_PRIOR_HIGH = [
    (104, 108, 103, 107),          # D+0 終値107 < 上限110
    (107, 112, 106, 111),          # D+1 終値111 > 110 → BREAKOUT / 高値112
    (111.2, 111.5, 109, 110.5),    # D+2 陰線 → WARNING（warning_low 109）
]
LEVELS_PRIOR_HIGH = {
    sm.RH_HOLDING: 112.0, sm.RH_WARNING_HIGH: 111.5, sm.RH_PRE_CLOSE: 111.0,
    sm.RH_WARNING_OPEN: 111.2, sm.RH_PRE_HIGH: 112.0,
}

# --- ケース(ii): 警戒足自身が最高値を作った（§4 が問題視した形）----------------
# RH-A = 112.5（＝警戒足の高値） / RH-B = 112.5 / RH-C = 110.6（D+1 の終値）
# RH-D = 111 / RH-E = 110.8（D+1 の高値）
HEAD_WARNING_HIGH = [
    (104, 108, 103, 107),          # D+0
    (107, 110.8, 106, 110.6),      # D+1 終値110.6 > 110 → BREAKOUT / 高値110.8
    (111, 112.5, 109, 110.5),      # D+2 陰線だが高値112.5 は保有中最高値
]
LEVELS_WARNING_HIGH = {
    sm.RH_HOLDING: 112.5, sm.RH_WARNING_HIGH: 112.5, sm.RH_PRE_CLOSE: 110.6,
    sm.RH_WARNING_OPEN: 111.0, sm.RH_PRE_HIGH: 110.8,
}

WARNING_LOW = 109.0


def _track(rows, *, rh_rule=sm.RH_HOLDING, ambiguous_order=sm.AMB_REHIGH,
           break_rule=sm.BREAK_CLOSE, exp=None, days=60) -> sm.SMEvent:
    """研究側と同じ固定条件（VARIANT A / CLOSE_BREAK）で 1 件追跡する。"""
    ev = sm.track_event(
        _signal(1, close=104.0, lower=100.0, upper=110.0),
        _series(BASE_HEAD + rows), exp, max_track_days=days,
        variant=sm.VARIANT_A, break_rule=break_rule,
        rh_rule=rh_rule, ambiguous_order=ambiguous_order,
    )
    sm.apply_classification([ev])
    return ev


def _all(rows, **kw) -> dict[str, sm.SMEvent]:
    return {r: _track(rows, rh_rule=r, **kw) for r in sm.RH_RULES}


def _snap(ev: sm.SMEvent) -> sm.RefHighSnapshot:
    assert ev.ref_highs, "警戒足が 1 件も出ていない"
    return ev.ref_highs[0]


# --- 既定は従来どおりであること ------------------------------------------------


def test_rh_ruleを省略すると現行の保有中最高値になる(exp):
    rows = HEAD_PRIOR_HIGH + [
        (110.5, 110.8, 108, 108.5),
        (108.5, 109, 107, 108),
    ]
    default = sm.track_event(
        _signal(1, close=104.0, lower=100.0, upper=110.0),
        _series(BASE_HEAD + rows), None, max_track_days=60,
    )
    explicit = sm.track_event(
        _signal(1, close=104.0, lower=100.0, upper=110.0),
        _series(BASE_HEAD + rows), None, max_track_days=60,
        rh_rule=sm.RH_HOLDING,
    )
    assert default.rh_rule == sm.RH_HOLDING
    assert default.ambiguous_order == sm.AMB_REHIGH
    assert asdict(default.cases[sm.CASE3]) == asdict(explicit.cases[sm.CASE3])
    assert default.warnings[0].reference_high == LEVELS_PRIOR_HIGH[sm.RH_HOLDING]


def test_未知のrh_ruleは弾く(exp):
    with pytest.raises(ValueError, match="未知の rh_rule"):
        _track(HEAD_PRIOR_HIGH, rh_rule="SOMETHING_ELSE")


def test_未知のambiguous_orderは弾く(exp):
    with pytest.raises(ValueError, match="未知の ambiguous_order"):
        _track(HEAD_PRIOR_HIGH, ambiguous_order="COIN_FLIP")


# --- §18 各案が使う足の範囲 ----------------------------------------------------


@pytest.mark.parametrize("head,levels", [
    (HEAD_PRIOR_HIGH, LEVELS_PRIOR_HIGH),
    (HEAD_WARNING_HIGH, LEVELS_WARNING_HIGH),
])
def test_5案のreference_highは警戒足当日までの足だけで決まる(head, levels, exp):
    evs = _all(head + [(110.5, 110.8, 110, 110.4)])
    for rule, expected in levels.items():
        assert _snap(evs[rule]).reference_high == pytest.approx(expected), rule
        # 実際に状態機械が使った値も同じであること
        assert evs[rule].warnings[0].reference_high == pytest.approx(expected), rule


def test_RH_Aは警戒足当日までの高値だけを使う(exp):
    """当日より後に高値を更新しても reference_high は動かない。"""
    later = [(110.5, 130, 110, 129)]      # 警戒足の翌日に大きく上放れ
    ev = _track(HEAD_PRIOR_HIGH + later, rh_rule=sm.RH_HOLDING)
    assert _snap(ev).reference_high == pytest.approx(112.0)


def test_RH_Bは警戒足自身の高値になる(exp):
    for head, levels in ((HEAD_PRIOR_HIGH, LEVELS_PRIOR_HIGH),
                         (HEAD_WARNING_HIGH, LEVELS_WARNING_HIGH)):
        ev = _track(head + [(110.5, 110.8, 110, 110.4)], rh_rule=sm.RH_WARNING_HIGH)
        s = _snap(ev)
        assert s.reference_high == pytest.approx(s.warning_high)
        assert s.reference_high == pytest.approx(levels[sm.RH_WARNING_HIGH])


def test_RH_Cは警戒足当日の終値を含まない(exp):
    """警戒足の終値がそれまでの最高終値を上回っていても採用しない。

    この足を running max に入れるタイミングを日次処理の先頭へ動かすと、
    reference_high が 110.5 になってこのテストが落ちる（変異チェック）。
    """
    head = [
        (104, 108, 103, 107),          # D+0 終値107
        (107, 110.5, 106, 110.2),      # D+1 終値110.2 > 110 → BREAKOUT
        (111, 112.5, 109, 110.5),      # D+2 陰線。終値110.5 はそれまでの最高終値より上
    ]
    ev = _track(head + [(110.5, 110.8, 110, 110.4)], rh_rule=sm.RH_PRE_CLOSE)
    s = _snap(ev)
    assert s.warning_close == pytest.approx(110.5)
    assert s.reference_high == pytest.approx(110.2), "当日の終値が混ざっている"
    assert s.pre_warning_close_high == pytest.approx(110.2)


def test_RH_Dは警戒足の始値になる(exp):
    ev = _track(HEAD_WARNING_HIGH + [(110.5, 110.8, 110, 110.4)],
                rh_rule=sm.RH_WARNING_OPEN)
    s = _snap(ev)
    assert s.reference_high == pytest.approx(111.0)
    assert s.reference_high == pytest.approx(s.warning_open)


def test_RH_Eは警戒足当日の高値を含まない(exp):
    """警戒足自身が最高値を作った日でも、その上ヒゲを条件にしない。

    当日の足を入れた後の holding_high を渡すよう変えると RH-A と同じ 112.5 になり、
    このテストが落ちる（変異チェック）。
    """
    ev = _track(HEAD_WARNING_HIGH + [(110.5, 110.8, 110, 110.4)],
                rh_rule=sm.RH_PRE_HIGH)
    s = _snap(ev)
    assert s.warning_high == pytest.approx(112.5)
    assert s.reference_high == pytest.approx(110.8), "警戒足の高値が混ざっている"


def test_RH_AはRH_BかRH_Eのどちらか一方と必ず一致する(exp):
    """定義上 RH-A = max(RH-E, RH-B) なので、必ずどちらかと同値になる。

    §4 が問題視した「警戒陰線自身の天井が再上昇の条件になる」形は
    a_equals_b が真の場合そのもので、その時 RH-B では緩められない。
    """
    for head in (HEAD_PRIOR_HIGH, HEAD_WARNING_HIGH):
        s = _snap(_track(head + [(110.5, 110.8, 110, 110.4)]))
        assert s.holding_high == pytest.approx(max(s.warning_high, s.pre_warning_high))
        assert (
            s.holding_high == pytest.approx(s.warning_high)
            or s.holding_high == pytest.approx(s.pre_warning_high)
        )
        assert s.a_equals_b == (s.holding_high == pytest.approx(s.warning_high))


# --- §18 REHIGH / 押し安値 / trail --------------------------------------------


# D+3 の高値 111.5 は RH-C(110.6)/RH-D(111)/RH-E(110.8) を超えるが
# RH-A/RH-B(112.5) には届かない。
REHIGH_ROWS = HEAD_WARNING_HIGH + [
    (110.5, 111.5, 110.0, 111.2),      # D+3 陽線。C/D/E だけ再突破
    (111.2, 111.5, 111.0, 111.3),      # D+4
    (111.0, 111.2, 108.0, 108.2),      # D+5 終値108.2 < warning_low 109
    (108.2, 108.5, 107.0, 107.5),      # D+6
    (107.5, 107.8, 106.0, 106.5),      # D+7
]


def test_REHIGHはその営業日の足だけで判定する(exp):
    """再突破は当日の高値だけで決まり、翌日の高値では決まらない。

    判定を bars[d+1].high に変えると RH-A/RH-B も D+3 で再突破したことになり、
    このテストが落ちる（変異チェック）。
    """
    evs = _all(REHIGH_ROWS)
    for rule in (sm.RH_PRE_CLOSE, sm.RH_WARNING_OPEN, sm.RH_PRE_HIGH):
        s = _snap(evs[rule])
        assert s.rehigh_date is not None, rule
        assert s.rehigh_day_offset == 3, rule
    for rule in (sm.RH_HOLDING, sm.RH_WARNING_HIGH):
        assert _snap(evs[rule]).rehigh_date is None, rule
        assert evs[rule].rehigh_count == 0, rule


def test_押し安値は再突破日までの安値だけで決まる(exp):
    """警戒足の日から再突破日までの安値の最小値。以後の安値は入らない。"""
    ev = _track(REHIGH_ROWS, rh_rule=sm.RH_PRE_CLOSE)
    s = _snap(ev)
    # D+2 の 109 と D+3 の 110.0 の小さい方
    assert s.new_swing_low_candidate == pytest.approx(109.0)
    assert s.trail_stop_candidate == pytest.approx(109.0 * sm.TRAIL_BUFFER)
    # D+5 の 108.0 は再突破より後なので入らない
    assert s.new_swing_low_candidate > 108.0


def test_trail_stopは翌営業日から有効(exp):
    ev = _track(REHIGH_ROWS, rh_rule=sm.RH_PRE_CLOSE)
    assert ev.stop_updates, "STOP が引き上がっていない"
    su = ev.stop_updates[0]
    assert su.day_offset == 3
    assert su.effective_from_day_offset == 4
    by_off = {d.day_offset: d for d in ev.daily}
    # 確定した当日はまだ旧 STOP（初期STOP）のまま
    assert by_off[3].active_stop == pytest.approx(ev.initial_stop)
    assert by_off[4].active_stop == pytest.approx(su.new_stop)
    assert su.new_stop > su.old_stop


def test_EXIT後の値動きは状態遷移に影響しない_RH(exp):
    """EXIT より後の足を差し替えても、EXIT の判定と価格は変わらない。"""
    tail_a = [(107.5, 107.8, 106.0, 106.5)]
    tail_b = [(107.5, 140.0, 106.0, 139.0)]
    for rule in sm.RH_RULES:
        a = _track(REHIGH_ROWS[:-1] + tail_a, rh_rule=rule)
        b = _track(REHIGH_ROWS[:-1] + tail_b, rh_rule=rule)
        assert a.path_result.exit_type == b.path_result.exit_type, rule
        assert a.path_result.exit_date == b.path_result.exit_date, rule
        assert a.path_result.exit_reference_price == pytest.approx(
            b.path_result.exit_reference_price
        ), rule


# --- §7 同日に REHIGH と利確候補が両立 -----------------------------------------


# D+3 は 高値 111.5（RH-C/D/E を超える）かつ 終値 108.8 < warning_low 109
AMBIGUOUS_ROWS = HEAD_WARNING_HIGH + [
    (110.0, 111.5, 108.5, 108.8),      # D+3 同日に両方成立
    (108.8, 110.0, 108.0, 109.5),      # D+4
    (109.5, 110.0, 108.0, 108.2),      # D+5
    (108.2, 108.5, 107.0, 107.5),      # D+6
]


def test_同日にREHIGHと終値割れが成立したら順序不明として分離する(exp):
    evs = _all(AMBIGUOUS_ROWS)
    for rule in (sm.RH_PRE_CLOSE, sm.RH_WARNING_OPEN, sm.RH_PRE_HIGH):
        ev = evs[rule]
        s = _snap(ev)
        assert s.order_ambiguous is True, rule
        assert s.order_class == "ambiguous_same_day", rule
        assert ev.ambiguous_rehigh_exit_count == 1, rule
        kinds = [t.kind for t in ev.timeline]
        assert sm.E_AMBIGUOUS_REHIGH_EXIT in kinds, rule
    # RH-A / RH-B は再突破していないので曖昧にならない（純粋な利確候補）
    for rule in (sm.RH_HOLDING, sm.RH_WARNING_HIGH):
        s = _snap(evs[rule])
        assert s.order_ambiguous is False, rule
        assert s.order_class == "close_break_first", rule


def test_ambiguous_orderで同日の扱いを切り替えられる(exp):
    """どちらを先に採るかは外部パラメータで、実装が勝手に決めない（§7）。"""
    rule = sm.RH_WARNING_OPEN
    first_rehigh = _track(AMBIGUOUS_ROWS, rh_rule=rule, ambiguous_order=sm.AMB_REHIGH)
    first_exit = _track(AMBIGUOUS_ROWS, rh_rule=rule, ambiguous_order=sm.AMB_EXIT)

    assert _snap(first_rehigh).rehigh_day_offset == 3
    assert _snap(first_rehigh).ambiguous_resolved_as == "rehigh"
    assert first_rehigh.stop_raise_count >= 1

    assert _snap(first_exit).rehigh_date is None
    assert _snap(first_exit).ambiguous_resolved_as == "exit"
    assert first_exit.path_result.exit_type == sm.X_BREAK_EXIT[sm.BREAK_CLOSE]
    assert first_exit.path_result.trigger_day_offset == 3
    assert first_exit.path_result.exit_day_offset == 4      # 約定は翌営業日始値

    # どちらも「順序不明だった」という事実は同じように残る
    for ev in (first_rehigh, first_exit):
        assert ev.ambiguous_rehigh_exit_count == 1
        assert _snap(ev).order_ambiguous is True


def test_ambiguous_orderの既定は従来の挙動と同じ(exp):
    rule = sm.RH_WARNING_OPEN
    default = _track(AMBIGUOUS_ROWS, rh_rule=rule)
    explicit = _track(AMBIGUOUS_ROWS, rh_rule=rule, ambiguous_order=sm.AMB_REHIGH)
    assert asdict(default.cases[sm.CASE3]) == asdict(explicit.cases[sm.CASE3])


# --- 5 案で変えていないもの ----------------------------------------------------


def test_警戒足とwarning_lowは5案とも同じ(exp):
    evs = _all(REHIGH_ROWS)
    firsts = {r: e.warnings[0] for r, e in evs.items()}
    assert len({w.date for w in firsts.values()}) == 1
    assert len({w.low for w in firsts.values()}) == 1
    assert all(w.low == WARNING_LOW for w in firsts.values())


def test_初期STOPと上限突破は5案とも同じ(exp):
    evs = _all(REHIGH_ROWS)
    assert len({e.initial_stop for e in evs.values()}) == 1
    assert len({e.upper_close_break_date for e in evs.values()}) == 1
    assert all(e.initial_stop == pytest.approx(100.0 * 0.995) for e in evs.values())


def test_trailは押し安値の0995倍で5案とも同じ(exp):
    for rule in sm.RH_RULES:
        ev = _track(REHIGH_ROWS, rh_rule=rule)
        for s in ev.ref_highs:
            if s.new_swing_low_candidate is None:
                continue
            assert s.trail_stop_candidate == pytest.approx(
                s.new_swing_low_candidate * sm.TRAIL_BUFFER
            ), rule


def test_STOPは5案とも下がらない(exp):
    for rule in sm.RH_RULES:
        ev = _track(REHIGH_ROWS, rh_rule=rule)
        stops = [d.active_stop for d in ev.daily]
        assert stops == sorted(stops), rule
        for su in ev.stop_updates:
            assert su.new_stop >= su.old_stop, rule


def test_固定利確は5案とも導入されていない(exp):
    """+3/+5/+10% に到達しても、それ自体では降りない。"""
    rows = HEAD_PRIOR_HIGH + [
        (110.5, 125, 110, 124),        # +19% まで上昇
        (124, 126, 123, 125),
        (125, 126, 124, 125.5),
    ]
    for rule in sm.RH_RULES:
        ev = _track(rows, rh_rule=rule)
        assert ev.reached_gain[10.0] is True, rule
        assert ev.path_result.exit_type not in sm.BREAK_EXIT_TYPES, rule
        assert ev.path_result.exit_type != sm.X_TRAIL_STOP, rule


def test_WARNING開始条件は5案とも同じ(exp):
    """VARIANT A 固定なので、警戒足の出方は reference_high に依存しない。"""
    evs = _all(HEAD_PRIOR_HIGH + [(110.5, 110.8, 110, 110.4)])
    offsets = {r: [w.day_offset for w in e.warnings] for r, e in evs.items()}
    assert len({tuple(v) for v in offsets.values()}) == 1


# --- §18 prefix 不変性 ---------------------------------------------------------

LOOKAHEAD_ROWS = HEAD_WARNING_HIGH + [
    (110.5, 111.5, 110.0, 111.2),
    (111.2, 111.5, 111.0, 111.3),
    (111.3, 113.0, 110.5, 112.8),
    (112.8, 113.5, 111.0, 111.5),
    (111.5, 112.0, 108.0, 108.2),
    (108.2, 108.5, 107.0, 107.5),
    (107.5, 109.0, 107.0, 108.8),
]


def test_prefix不変性_5案いずれでも成立する(exp):
    """途中まで走らせた結果が、全長で走らせた結果の先頭と一致すること。

    未来の足を 1 本でも先読みしていれば、打ち切り位置のどこかで必ず壊れる。
    """
    for rule in sm.RH_RULES:
        full = _track(LOOKAHEAD_ROWS, rh_rule=rule)
        full_daily = [asdict(d) for d in full.daily]

        for k in range(3, len(LOOKAHEAD_ROWS) + 1):
            part = _track(LOOKAHEAD_ROWS[:k], rh_rule=rule)
            assert [asdict(d) for d in part.daily] == full_daily[: len(part.daily)], (
                f"rh_rule={rule} k={k} で日次ログが一致しない"
            )

            def _core(updates):
                return [
                    {kk: vv for kk, vv in asdict(s).items() if kk != "effective_from_date"}
                    for s in updates
                ]
            assert _core(part.stop_updates) == _core(full.stop_updates)[: len(part.stop_updates)]


# 時間とともに情報が「増える」列。増えるのは正しいので prefix 比較から除く。
_ACCUMULATING = {
    "observed_days", "close_break_date", "close_break_day_offset",
    "close_break_close", "order_class",
}


def test_prefix不変性_reference_highは後から書き換わらない(exp):
    """一度決めた reference_high と押し安値が、後の足で別の値に変わらないこと。"""
    for rule in sm.RH_RULES:
        full = _track(LOOKAHEAD_ROWS, rh_rule=rule)
        full_by_date = {s.warning_date: asdict(s) for s in full.ref_highs}

        for k in range(3, len(LOOKAHEAD_ROWS) + 1):
            part = _track(LOOKAHEAD_ROWS[:k], rh_rule=rule)
            for s in part.ref_highs:
                assert s.warning_date in full_by_date, (
                    f"rh_rule={rule} 打ち切り {k} 本目に無い警戒足 {s.warning_date}"
                )
                ref = full_by_date[s.warning_date]
                for key, value in asdict(s).items():
                    if key in _ACCUMULATING or value is None:
                        continue
                    if isinstance(value, bool):
                        assert not (value and not ref[key]), (
                            f"rh_rule={rule} k={k}: {s.warning_date} の {key} が取り消された"
                        )
                        continue
                    assert value == ref[key], (
                        f"rh_rule={rule} k={k}: {s.warning_date} の {key} が "
                        f"{value} → {ref[key]} に変わった"
                    )


def test_LOOKAHEAD_ROWSが検証として空でない(exp):
    """レポートの look-ahead 表が、実在するテスト名を指していること。"""
    from swing_screener.research.reference_high_report import LOOKAHEAD_ROWS as ROWS

    names = {n for _a, _b, n in ROWS}
    assert len(ROWS) >= 9
    here = set(globals())
    missing = {n for n in names if n not in here}
    assert not missing, f"レポートが存在しないテストを指している: {sorted(missing)}"


# --- 研究モジュールの集計 ------------------------------------------------------


def test_study_moduleが5案を走らせられる(exp):
    from swing_screener.research import reference_high_study as rhs

    prepared = [(
        _signal(1, close=104.0, lower=100.0, upper=110.0),
        _series(BASE_HEAD + LOOKAHEAD_ROWS),
    )]
    runs = rhs.run_rules(prepared, None)
    assert set(runs) == set(sm.RH_RULES)
    frames = rhs.build_frames(prepared)
    assert len(frames) == 1
    early = rhs.extract_early_trail(runs, frames)
    rows = rhs.all_metrics(runs, frames, early)
    assert rows and all(set(r.values) <= set(sm.RH_RULES) for r in rows)
    pos = rhs.position_rows(runs)
    assert pos and pos[0].metric.startswith("母集団")


def test_研究側の固定条件がVARIANT_AとCLOSE_BREAKであること(exp):
    from swing_screener.research import reference_high_study as rhs

    assert rhs.FIXED_VARIANT == sm.VARIANT_A
    assert rhs.FIXED_BREAK_RULE == sm.BREAK_CLOSE
    assert rhs.RULES == sm.RH_RULES
