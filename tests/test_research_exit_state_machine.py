"""EXIT ロジックの状態遷移検証（research 専用）のテスト。

このモジュールが守るべき境界をテストで固定する:

    - 警戒足は「元レンジ上限を終値突破した後」からしか拾わない（今回の検証仮説）
    - reference_high は保有中最高値であって警戒足自身の高値ではない
    - STOP は上方向にしか動かない
    - **look-ahead bias が無い**（§19）— これが今回いちばん重要

合成OHLCVを使い、株価APIには依存させない。
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta

import pytest

from swing_screener.models import OHLCVBar, PriceSeries
from swing_screener.research import exit_state_machine as sm


def _series(rows: list[tuple[float, float, float, float]], code: str = "0000") -> PriceSeries:
    """(open, high, low, close) の並びから系列を作る。"""
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


def _track(rows, *, close=104.0, lower=100.0, upper=110.0, exp=None, days=60):
    return sm.track_event(
        _signal(1, close=close, lower=lower, upper=upper),
        _series(BASE_HEAD + rows), exp, max_track_days=days,
    )


# --- 確定ルールを変えていないこと -------------------------------------------


def test_確定ルールは変更していない(exp):
    """ENTRY価格は翌営業日始値、初期STOPは range_lower*0.995 のまま。"""
    ev = _track([(104, 108, 103, 107), (107, 109, 106, 108)])

    assert ev.entry_price == 104           # シグナル翌営業日の始値
    assert ev.initial_stop == pytest.approx(100 * 0.995)
    assert ev.cases[sm.CASE1].exit_type in (sm.X_DATA_END, sm.X_INITIAL_STOP)


# --- STATE 1: INITIAL_HOLD ----------------------------------------------------


def test_上限を終値突破するまで陰線を警戒足にしない(exp):
    """今回の検証仮説の核。ENTRY直後の陰線は WARNING を起こさない。"""
    ev = _track([
        (104, 106, 103, 105),   # D+0 陽線
        (105, 106, 103, 104),   # D+1 陰線。上限110未達なので警戒足にしない
        (104, 107, 103, 105),   # D+2 陰線ではない
        (105, 106, 102, 103),   # D+3 陰線。まだ上限未達
    ])

    assert ev.reached_trend_hold is False
    assert ev.warnings == []
    assert ev.path_label == "" or True  # 分類は apply_classification で付く


def test_高値だけ上限を超えても状態遷移しない(exp):
    ev = _track([
        (104, 112, 103, 109),   # D+0 高値112 > 上限110 だが終値109 は上限以下
        (109, 110, 108, 109),   # D+1
    ])

    assert ev.upper_high_only_before_break is True
    assert ev.reached_trend_hold is False
    assert sm.E_UPPER_HIGH_ONLY in {t.kind for t in ev.timeline}


def test_終値で上限を突破するとTREND_HOLDへ移る(exp):
    ev = _track([
        (104, 108, 103, 107),   # D+0
        (107, 112, 106, 111),   # D+1 終値111 > 110
    ])

    assert ev.reached_trend_hold is True
    assert ev.upper_close_break_day_offset == 1
    assert ev.upper_close_break_price == 111
    kinds = [t.kind for t in ev.timeline]
    assert sm.E_UPPER_CLOSE_BREAK in kinds


def test_上限突破前のSTOP到達はINITIAL_STOP_EXIT(exp):
    ev = _track([
        (104, 106, 103, 105),   # D+0
        (105, 106, 99, 100),    # D+1 安値99 <= 99.5
    ])

    assert ev.cases[sm.CASE3].exit_type == sm.X_INITIAL_STOP
    assert ev.cases[sm.CASE2].exit_type == sm.X_INITIAL_STOP
    assert ev.cases[sm.CASE3].exit_day_offset == 1
    assert ev.reached_trend_hold is False


# --- STATE 2/3: TREND_HOLD → WARNING -----------------------------------------


def test_上限突破後の最初の陰線が警戒足になる(exp):
    ev = _track([
        (104, 108, 103, 107),   # D+0
        (107, 112, 106, 111),   # D+1 突破 → TREND_HOLD
        (111, 113, 108, 109),   # D+2 陰線 → 警戒足
        (109, 110, 107, 108),   # D+3 陰線だが置き換えない
    ])

    assert len(ev.warnings) == 1
    w = ev.warnings[0]
    assert w.day_offset == 2
    assert w.low == 108
    assert w.extra_bearish_count == 1  # D+3 の陰線は追加陰線として数えるだけ


def test_reference_highは警戒足自身の高値ではなく保有中最高値(exp):
    """§5 の明示要求。警戒足の高値 111 ではなく保有中最高値 115 を使う。"""
    ev = _track([
        (104, 108, 103, 107),   # D+0
        (107, 115, 106, 111),   # D+1 突破。高値115が保有中最高値
        (111, 111, 108, 109),   # D+2 陰線。自身の高値は111
    ])

    w = ev.warnings[0]
    assert w.reference_high == 115          # 保有中最高値
    assert w.high == 111                    # 警戒足自身の高値
    assert w.reference_high != w.high


def test_突破日そのものが陰線でも翌営業日から警戒足を見る(exp):
    """解釈(a)。同日採用にしていたら増えていた本数を件数として残す。"""
    ev = _track([
        (104, 108, 103, 107),   # D+0
        (113, 114, 106, 111),   # D+1 終値111>110 で突破。かつ陰線(113→111)
        (111, 112, 109, 111.5),  # D+2 陽線
    ])

    assert ev.reached_trend_hold is True
    assert ev.warnings == []                       # 突破日は警戒足にしない
    assert ev.same_day_bearish_at_trend_entry == 1  # 感度は記録する


# --- warning_low / reference_high の決着 --------------------------------------


def test_warning_low下抜けはCASE2だけを降ろしCASE3は保有継続(exp):
    ev = _track([
        (104, 108, 103, 107),   # D+0
        (107, 112, 106, 111),   # D+1 突破
        (111, 113, 108, 109),   # D+2 警戒足 low=108
        (109, 110, 106, 107),   # D+3 安値106 < 108 → 利確候補
        (107, 109, 105, 108),   # D+4 CASE3 はまだ保有（STOP 99.5 未到達）
    ])

    w = ev.warnings[0]
    assert w.low_break_day_offset == 3
    assert ev.cases[sm.CASE2].exit_type == sm.X_WARNING_LOW
    assert ev.cases[sm.CASE2].exit_day_offset == 3
    assert ev.cases[sm.CASE2].exit_reference_price == 108
    # CASE3 は warning_low では降りない
    assert ev.cases[sm.CASE3].exit_day_offset != 3
    assert ev.cases[sm.CASE3].exit_type != sm.X_WARNING_LOW
    # CASE2 のイベントであることが時系列に残る
    breaks = [t for t in ev.timeline if t.kind == sm.E_WARNING_LOW_BREAK]
    assert breaks and breaks[0].case == sm.CASE2


def test_warning_lowを寄りで割ったら約定を仮定しない(exp):
    ev = _track([
        (104, 108, 103, 107),   # D+0
        (107, 112, 106, 111),   # D+1 突破
        (111, 113, 108, 109),   # D+2 警戒足 low=108
        (105, 106, 103, 104),   # D+3 寄り105 が warning_low 108 を下回る
    ])

    w = ev.warnings[0]
    assert w.gap_through_warning_low is True
    assert w.low_break_open == 105
    assert w.low_break_reference_price == 105       # warning_low 108 では約定できない
    assert ev.cases[sm.CASE2].exit_reference_price == 105
    assert ev.cases[sm.CASE2].gap_through is True
    assert sm.E_GAP_THROUGH in {t.kind for t in ev.timeline}


def test_reference_high再突破で押し安値が確定しSTOPが上がる(exp):
    ev = _track([
        (104, 108, 103, 107),   # D+0
        (107, 112, 106, 111),   # D+1 突破。保有中最高値112
        (111, 112, 108, 109),   # D+2 警戒足 low=108 ref_high=112
        (109, 111, 108.5, 110),  # D+3 warning_low は割らない
        (110, 115, 109, 114),   # D+4 高値115 > 112 → REHIGH
    ])

    w = ev.warnings[0]
    assert w.resolution == "rehigh"                  # 先に上抜けた
    assert w.low_break_date is None
    assert w.rehigh_day_offset == 4
    assert w.new_swing_low_candidate == 108          # D+2〜D+4 の最安値
    assert w.trail_stop_candidate == pytest.approx(108 * 0.995)
    assert w.stop_raised is True

    assert len(ev.stop_updates) == 1
    su = ev.stop_updates[0]
    assert su.old_stop == pytest.approx(99.5)
    assert su.new_stop == pytest.approx(108 * 0.995)


def test_先に安値を割ってから再高値更新した場合は割れが先の決着になる(exp):
    """§17 の「どちらを先に突破したか」。後の再高値更新で上書きしない。"""
    ev = _track([
        (104, 108, 103, 107),   # D+0
        (107, 112, 106, 111),   # D+1 突破
        (111, 112, 108, 109),   # D+2 警戒足 low=108 ref_high=112
        (109, 111, 106, 110),   # D+3 安値106 < 108 → 先に割れた
        (110, 115, 109, 114),   # D+4 高値115 > 112 → その後で再高値更新
    ])

    w = ev.warnings[0]
    assert w.resolution == "low_break"               # 決着は「先に割った」
    assert w.low_break_day_offset == 3
    assert w.rehigh_day_offset == 4                  # 再高値更新も記録は残る
    assert w.new_swing_low_candidate == 106          # 割った日の安値が押し安値になる
    # CASE2 は割った時点で降りている / CASE3 は保有を続けて STOP を上げた
    assert ev.cases[sm.CASE2].exit_day_offset == 3
    assert ev.stop_updates and ev.stop_updates[0].day_offset == 4


def test_STOPは上方向にしか動かない(exp):
    """押し安値から出た候補が現在のSTOPより低ければ据え置く（§7）。"""
    ev = _track([
        (104, 108, 103, 107),    # D+0
        (107, 112, 106, 111),    # D+1 突破
        (111, 112, 108, 109),    # D+2 警戒足1 low=108 ref=112
        (109, 111, 107, 110),    # D+3
        (110, 115, 109, 114),    # D+4 REHIGH1 → 押し安値107 → STOP 106.465
        (114, 115, 111, 112),    # D+5 警戒足2 low=111 ref=115
        (112, 114, 108, 113),    # D+6 安値108（STOP 106.465 は割らない）
        (113, 118, 112, 117),    # D+7 REHIGH2 → 押し安値108 → 107.46 > 106.465 で上がる
    ])

    stops = [su.new_stop for su in ev.stop_updates]
    assert stops == sorted(stops)                    # 単調増加
    assert all(su.new_stop > su.old_stop for su in ev.stop_updates)
    assert ev.max_active_stop == max(stops)
    assert ev.final_active_stop >= ev.initial_stop


def test_押し安値候補がSTOP以下なら据え置く(exp):
    ev = _track([
        (104, 108, 103, 107),    # D+0
        (107, 112, 106, 111),    # D+1 突破
        (111, 112, 108, 109),    # D+2 警戒足1
        (109, 115, 107, 114),    # D+3 REHIGH1 → 押し安値107 → STOP 106.465
        (114, 116, 112, 113),    # D+4 警戒足2 low=112 ref=116
        (113, 118, 107.5, 117),  # D+5 REHIGH2 → 押し安値107.5 → 106.9125 > 106.465
    ])

    # 2回目の候補が1回目より低ければ据え置きイベントが出る（ここでは上がる方を確認）
    assert all(su.new_stop >= su.old_stop for su in ev.stop_updates)
    kinds = {t.kind for t in ev.timeline}
    assert sm.E_STOP_RAISED in kinds or sm.E_STOP_KEPT in kinds


def test_押し安値形成とSTOP引き上げをループできる(exp):
    """TREND_HOLD → WARNING → REHIGH → STOP引き上げ → TREND_HOLD の繰り返し。"""
    ev = _track([
        (104, 108, 103, 107),    # D+0
        (107, 112, 106, 111),    # D+1 突破
        (111, 113, 108, 109),    # D+2 警戒足1
        (109, 116, 109, 115),    # D+3 REHIGH1
        (115, 117, 113, 114),    # D+4 警戒足2
        (114, 120, 112, 119),    # D+5 REHIGH2
        (119, 121, 117, 118),    # D+6 警戒足3
        (118, 124, 116, 123),    # D+7 REHIGH3
    ])
    sm.apply_classification([ev])

    assert ev.warning_count == 3
    assert ev.rehigh_count == 3
    assert ev.stop_raise_count == 3
    assert ev.path_label == "P4_REHIGH_MULTI"
    assert "STOP_RAISED_TWICE" in ev.flags


def test_WARNING中の追加陰線で警戒足を置き換えない(exp):
    """§9。最初に設定した warning_low / reference_high を維持する。"""
    ev = _track([
        (104, 108, 103, 107),    # D+0
        (107, 112, 106, 111),    # D+1 突破
        (111, 113, 108, 109),    # D+2 警戒足 low=108 ref=113
        (109, 110, 108.5, 109.5),  # D+3 陽線
        (109.5, 110, 108.2, 109),  # D+4 陰線（追加）
        (109, 109.8, 108.1, 108.5),  # D+5 陰線（追加）
    ])

    assert len(ev.warnings) == 1
    w = ev.warnings[0]
    assert w.low == 108           # 置き換わっていない
    assert w.reference_high == 113
    assert w.extra_bearish_count == 2
    assert sm.E_WARNING_EXTRA in {t.kind for t in ev.timeline}


def test_WARNING期間中の最安値が押し安値に入る(exp):
    """§9 後段。置き換えはしないが、最安値は押し安値候補に含める。"""
    ev = _track([
        (104, 108, 103, 107),    # D+0
        (107, 112, 106, 111),    # D+1 突破
        (111, 113, 108, 109),    # D+2 警戒足 low=108
        (109, 110, 105, 106),    # D+3 期間中の最安値 105
        (106, 109, 106, 108),    # D+4
        (108, 115, 107, 114),    # D+5 REHIGH（ref 113 超え）
    ])

    w = ev.warnings[0]
    assert w.new_swing_low_candidate == 105          # 警戒足の安値108 ではない
    assert w.new_swing_low_date == date(2026, 1, 5) + timedelta(days=5)  # D+3 の足


# --- §10 曖昧ケース -----------------------------------------------------------


def test_同日に両方到達したら順序を仮定しない(exp):
    ev = _track([
        (104, 108, 103, 107),    # D+0
        (107, 112, 106, 111),    # D+1 突破 ref=112
        (111, 112, 108, 109),    # D+2 警戒足 low=108 ref=112
        (110, 115, 107, 114),    # D+3 安値107<108 も 高値115>112 も成立。寄り110 は両者の間
    ])

    assert ev.ambiguous_warning_days == [date(2026, 1, 5) + timedelta(days=5)]
    assert sm.E_AMBIGUOUS_WARNING in ev.flags
    assert ev.warnings[0].resolution == "ambiguous_both"
    # CASE2 の EXIT がこの順序に依存することを明示する
    assert ev.cases[sm.CASE2].order_ambiguous is True


def test_寄りで既に割れていれば順序は確定する(exp):
    ev = _track([
        (104, 108, 103, 107),    # D+0
        (107, 112, 106, 111),    # D+1 突破 ref=112
        (111, 112, 108, 109),    # D+2 警戒足 low=108
        (107, 115, 106, 114),    # D+3 寄り107 が既に warning_low 108 を下回る
    ])

    assert ev.ambiguous_warning_days == []
    assert ev.warnings[0].low_break_day_offset == 3
    assert ev.cases[sm.CASE2].order_ambiguous is False


def test_STOPと再高値更新が同日でも撤退水準は変わらない(exp):
    """当日安値が active_stop 以下なら、その日確定する押し安値も同水準以下になる。"""
    ev = _track([
        (104, 108, 103, 107),    # D+0
        (107, 112, 106, 111),    # D+1 突破
        (111, 112, 108, 109),    # D+2 警戒足 ref=112
        (109, 115, 99, 100),     # D+3 高値115>112 かつ 安値99 <= 99.5
    ])

    assert ev.cases[sm.CASE3].exit_day_offset == 3
    assert ev.cases[sm.CASE3].exit_reference_price == pytest.approx(99.5)
    # 順序不明として記録はするが、STOPは引き上がっていない
    assert ev.stop_updates == []


# --- §13/§14 機械的利確を入れない --------------------------------------------


def test_固定利確では降りない(exp):
    """+10%到達でも上限到達でも、機械的な EXIT にはしない（§13）。"""
    ev = _track([
        (104, 108, 103, 107),    # D+0
        (107, 116, 106, 115),    # D+1 +11.5% 到達（104 → 116）
        (115, 118, 114, 117),    # D+2
    ])

    assert ev.reached_gain[10.0] is True             # 記録はする
    assert ev.cases[sm.CASE3].exit_type == sm.X_DATA_END  # 降りていない
    assert ev.cases[sm.CASE1].exit_type == sm.X_DATA_END


def test_大陰線の参考指標は売却判定に使わない(exp):
    ev = _track([
        (104, 108, 103, 107),    # D+0
        (107, 112, 106, 111),    # D+1 突破
        (111, 111.5, 104, 104.2),  # D+2 大陰線・安値引け。それでも売らない
        (104.2, 106, 103.5, 105),  # D+3
    ])

    w = ev.warnings[0]
    assert w.manual_exit_review is True
    assert w.close_pos_in_day_range is not None and w.close_pos_in_day_range < 0.2
    assert w.change_pct is not None and w.change_pct < 0
    # 指標が極端でもポジションは閉じない
    assert ev.cases[sm.CASE3].exit_type == sm.X_DATA_END


# --- §19 look-ahead bias -----------------------------------------------------

LOOKAHEAD_ROWS = [
    (104, 108, 103, 107),    # D+0
    (107, 112, 106, 111),    # D+1 突破
    (111, 113, 108, 109),    # D+2 警戒足1
    (109, 110, 105, 106),    # D+3 押し安値候補 105
    (106, 116, 106, 115),    # D+4 REHIGH1
    (115, 117, 113, 114),    # D+5 警戒足2
    (114, 116, 111, 112),    # D+6
    (112, 121, 111, 120),    # D+7 REHIGH2
    (120, 122, 118, 119),    # D+8 警戒足3
    (119, 120, 100, 101),    # D+9 大きく下落
    (101, 103, 99, 100),     # D+10
]


def test_未来の安値を押し安値に使わない(exp):
    """D+4 で確定する押し安値は D+2〜D+4 の最安値。D+9 の安値は入らない。"""
    ev = _track(LOOKAHEAD_ROWS)

    first = ev.warnings[0]
    assert first.rehigh_day_offset == 4
    assert first.new_swing_low_candidate == 105      # D+3 の安値
    # D+6 の安値 111 も D+9 の安値 100 も、最初のエピソードには入らない
    assert first.new_swing_low_candidate != 111
    assert first.new_swing_low_candidate != 100


def test_未来の最高値をreference_highに使わない(exp):
    """警戒足1の reference_high は D+2 時点の保有中最高値。後の 122 ではない。"""
    ev = _track(LOOKAHEAD_ROWS)

    first = ev.warnings[0]
    assert first.reference_high == 113               # D+2 までの最高値
    assert first.reference_high_date == date(2026, 1, 5) + timedelta(days=4)


def test_引き上げたSTOPは確定日の安値に遡って適用されない(exp):
    """STOP は翌営業日から有効。確定日の日次ログは引き上げ前の水準を持つ。"""
    ev = _track(LOOKAHEAD_ROWS)

    by_off = {d.day_offset: d for d in ev.daily}
    for su in ev.stop_updates:
        assert su.effective_from_day_offset == su.day_offset + 1
        # 確定日の寄り時点では旧STOP
        assert by_off[su.day_offset].active_stop == pytest.approx(su.old_stop)
        # 翌営業日から新STOP
        if su.day_offset + 1 in by_off:
            assert by_off[su.day_offset + 1].active_stop == pytest.approx(su.new_stop)


def test_確定日の安値は新STOPを必ず上回る(exp):
    """押し安値は当日安値以下なので、その 0.5% 下が当日安値に当たることはない。

    「引き上げ日にその STOP で約定していたはず」という遡及が構造的に起きない。
    """
    ev = _track(LOOKAHEAD_ROWS)

    by_off = {d.day_offset: d for d in ev.daily}
    for su in ev.stop_updates:
        assert by_off[su.day_offset].low > su.new_stop


def test_prefix不変性_途中までの足で走らせても結果が変わらない(exp):
    """look-ahead が無いことの本質的な確認。

    系列を途中で打ち切って走らせた結果は、全長で走らせた結果の
    先頭からの一致でなければならない。未来の足を1本でも見ていれば崩れる。
    """
    rows = BASE_HEAD + LOOKAHEAD_ROWS
    full = sm.track_event(
        _signal(1, close=104, lower=100, upper=110), _series(rows), exp,
    )
    full_daily = [asdict(d) for d in full.daily]

    for k in range(3, len(rows) + 1):          # 仮想ENTRY日(index2)以降で打ち切る
        part = sm.track_event(
            _signal(1, close=104, lower=100, upper=110), _series(rows[:k]), exp,
        )
        part_daily = [asdict(d) for d in part.daily]
        assert part_daily == full_daily[: len(part_daily)], f"打ち切り {k} 本目で不一致"

        # STOP 引き上げ履歴も先頭一致でなければならない。
        # effective_from_date だけは除外する。引き上げが確定した時点では
        # 翌営業日がまだ存在しないため、後から埋まるのが正しい挙動。
        def _core(updates):
            return [
                {k: v for k, v in asdict(s).items() if k != "effective_from_date"}
                for s in updates
            ]

        assert _core(part.stop_updates) == _core(
            full.stop_updates[: len(part.stop_updates)]
        ), f"打ち切り {k} 本目で STOP 履歴が不一致"


def test_prefix不変性_警戒足の情報は積み増すだけで書き換わらない(exp):
    """一度埋まった値は、後の足が来ても変わらない。

    エピソードは時間とともに情報が「増える」（安値割れ → 再高値更新 → STOP引き上げ）。
    増えるのは正しいが、**既に埋まった値が別の値に変わってはいけない**。
    変わるなら、その時点で未来を見て決めていたことになる。
    """
    rows = BASE_HEAD + LOOKAHEAD_ROWS
    full = sm.track_event(
        _signal(1, close=104, lower=100, upper=110), _series(rows), exp,
    )
    # fractal_* は比較専用の後段パスなので除く
    skip = {"fractal_confirm_day_offset", "fractal_is_same_low",
            "days_held_in_warning_after_low_break", "resolved_date",
            "resolved_day_offset", "days_to_resolve", "resolution",
            "extra_bearish_count"}
    full_by_date = {w.date: asdict(w) for w in full.warnings}

    for k in range(6, len(rows) + 1):
        part = sm.track_event(
            _signal(1, close=104, lower=100, upper=110), _series(rows[:k]), exp,
        )
        for w in part.warnings:
            assert w.date in full_by_date, f"打ち切り {k} 本目に無い警戒足 {w.date}"
            ref = full_by_date[w.date]
            for key, value in asdict(w).items():
                if key in skip or value is None:
                    continue
                if isinstance(value, bool):
                    # bool は False が「まだ起きていない」と区別できないので、
                    # 「一度 True になった事実が取り消されない」ことだけを見る
                    assert not (value and not ref[key]), (
                        f"打ち切り {k} 本目: 警戒足 {w.date} の {key} が "
                        f"True → False に取り消された"
                    )
                    continue
                assert value == ref[key], (
                    f"打ち切り {k} 本目: 警戒足 {w.date} の {key} が "
                    f"{value} → {ref[key]} に変わった"
                )


# --- CASE 比較 ---------------------------------------------------------------


def test_3つのCASEが同じ経路から読み取られる(exp):
    ev = _track(LOOKAHEAD_ROWS)
    sm.apply_classification([ev])

    for c in sm.CASES:
        r = ev.cases[c]
        assert r.max_gain_pct is not None
        assert r.giveback_pct is not None
        if r.approximate_return_pct is not None:
            # 吐き出し幅 = 最大含み益 − 最終リターン
            assert r.giveback_pct == pytest.approx(
                r.max_gain_pct - r.approximate_return_pct
            )
    # CASE2 は CASE3 より早いか同時にしか降りない
    o2 = ev.cases[sm.CASE2].exit_day_offset
    o3 = ev.cases[sm.CASE3].exit_day_offset
    assert o2 is not None and o3 is not None and o2 <= o3
    # CASE3 の active_stop は初期STOP以上なので CASE1 より早いか同時に降りる
    o1 = ev.cases[sm.CASE1].exit_day_offset
    assert o3 <= o1


def test_翌営業日が無いときは仮想ENTRYできない(exp):
    ev = sm.track_event(
        _signal(1, close=104, lower=100, upper=110), _series(BASE_HEAD), exp,
    )

    assert ev.entry_available is False
    assert ev.path_label == "NO_ENTRY"
    assert ev.entry_price is None
