"""ENTRY 後の追跡（EXIT スタディ）の検証。

このモジュールが守るべき境界をテストで固定する:

    - ポジションを閉じる機械判定は確定ルールの初期損切りだけ
    - 警戒陰線・トレーリングは記録するだけで売却しない
    - 日足で先後が決められない日を有利な順番に丸めない

合成OHLCVを使い、株価APIには依存させない。
"""

from __future__ import annotations

from datetime import date, timedelta

from swing_screener.models import OHLCVBar, PriceSeries
from swing_screener.research import exit_study


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
        "range_width_pct": "5.0",
        "position_in_range": str((close - lower) / (upper - lower)),
        "initial_stop": str(lower * 0.995),
        "stop_distance_pct_from_close": "1.0",
        "ma25": "",
    }


def test_仮想ENTRY価格はシグナル翌営業日の始値(exp):
    bars = _series([
        (100, 105, 99, 104),   # 0
        (100, 105, 99, 104),   # 1 = シグナル日
        (108, 110, 107, 109),  # 2 = 翌営業日。始値 108 で入る
        (109, 112, 108, 111),
    ])
    ev = exit_study.track_event(_signal(1, close=104, lower=100, upper=110), bars, exp)

    assert ev.entry_available is True
    assert ev.entry_price == 108
    assert ev.entry_date == bars.bars[2].date
    # シグナル日終値 104 → 翌日始値 108 のギャップ
    assert ev.gap_pct == (108 - 104) / 104 * 100


def test_翌営業日が無いときは仮想ENTRYできない(exp):
    bars = _series([(100, 105, 99, 104), (100, 105, 99, 104)])
    ev = exit_study.track_event(_signal(1, close=104, lower=100, upper=110), bars, exp)

    assert ev.entry_available is False
    assert ev.type_label == "NO_ENTRY"
    assert ev.entry_price is None


def test_初期損切りだけがポジションを閉じる(exp):
    """陰線が何本出てもポジションは閉じない。閉じるのは stop 到達のみ。"""
    bars = _series([
        (100, 105, 99, 104),
        (100, 105, 99, 104),   # 1 = シグナル日
        (104, 106, 103, 103),  # 2 = ENTRY日。陰線
        (103, 104, 102, 102),  # 3 陰線
        (102, 103, 101, 101),  # 4 陰線
        (101, 102, 94, 95),    # 5 stop 99.5 到達
        (95, 120, 94, 119),    # 6 到達後は追跡しない
    ])
    ev = exit_study.track_event(_signal(1, close=104, lower=100, upper=110), bars, exp)

    assert ev.hit_initial_stop is True
    assert ev.stop_day_offset == 3  # ENTRY日=0 なので index5 は D+3
    assert ev.bars_tracked == 4
    # stop 後の高値 120 は最大上昇に入らない
    assert ev.max_gain_pct < 3.0
    # 確定ルール由来のイベントは ENTRY と STOP だけで、STOP がポジションの終端
    rule_based = [t for t in ev.timeline if t.is_rule_based]
    assert [t.kind for t in rule_based] == [exit_study.K_ENTRY, exit_study.K_STOP_HIT]
    assert max(t.day_offset for t in ev.timeline) == ev.stop_day_offset


def test_同日にSTOPと利益方向へ到達したら順序を仮定しない(exp):
    """日足では先後が分からない日を有利な順番に丸めない。"""
    bars = _series([
        (100, 105, 99, 104),
        (100, 105, 99, 104),   # 1 = シグナル日
        (104, 106, 103, 105),  # 2 = ENTRY日（始値104）
        (105, 115, 94, 100),   # 3 上限110到達と stop 99.5 到達が同日
    ])
    ev = exit_study.track_event(_signal(1, close=104, lower=100, upper=110), bars, exp)

    assert ev.ambiguous_days == [bars.bars[3].date]
    assert ev.first_event_order == "ambiguous"
    assert exit_study.K_AMBIGUOUS in {t.kind for t in ev.timeline}
    # 到達した事実そのものは残す
    assert ev.reached_upper is True


def test_寄りでSTOPを割っていれば順序は確定する(exp):
    """始値が既に stop 以下なら、先後は日足だけで決まる。"""
    bars = _series([
        (100, 105, 99, 104),
        (100, 105, 99, 104),   # 1 = シグナル日
        (104, 106, 103, 105),  # 2 = ENTRY日
        (95, 115, 94, 100),    # 3 寄り95 は stop 99.5 以下
    ])
    ev = exit_study.track_event(_signal(1, close=104, lower=100, upper=110), bars, exp)

    assert ev.ambiguous_days == []
    assert ev.first_event_order == "stop_first"
    # 寄りで割った分はスリッページとして記録する
    assert ev.stop_gap_down_pct is not None and ev.stop_gap_down_pct < 0


def test_レンジ上限は到達と終値突破を区別する(exp):
    bars = _series([
        (100, 105, 99, 104),
        (100, 105, 99, 104),   # 1 = シグナル日
        (104, 112, 103, 108),  # 2 高値は上限110超だが終値108は上限以下
        (108, 113, 107, 112),  # 3 終値112 > 上限110
    ])
    ev = exit_study.track_event(_signal(1, close=104, lower=100, upper=110), bars, exp)

    assert ev.reached_upper is True
    assert ev.upper_touch_day_offset == 0
    assert ev.upper_high_only_break is True
    assert ev.upper_close_break is True
    assert ev.upper_close_break_day_offset == 1


def test_警戒陰線は安値割れを追うが売却しない(exp):
    bars = _series([
        (100, 105, 99, 104),
        (100, 105, 99, 104),   # 1 = シグナル日
        (104, 109, 103, 108),  # 2 = ENTRY日。陽線
        (108, 109, 105, 106),  # 3 陰線（警戒足）。安値105
        (106, 111, 105.5, 110),  # 4 安値を割らずに高値更新
        (110, 111, 104, 105),  # 5 警戒陰線の安値105を下抜け → 利確候補
    ])
    ev = exit_study.track_event(_signal(1, close=104, lower=100, upper=115), bars, exp)

    first = ev.warning_candles[0]
    assert first.date == bars.bars[3].date
    assert first.is_first is True
    assert first.low == 105
    assert first.broke_low_date == bars.bars[5].date
    assert first.new_high_vs_candle_high_before_break is True
    assert first.manual_exit_review is True
    # 利確候補が出てもポジションは閉じない（確定ルールではないため）
    assert ev.hit_initial_stop is False
    assert ev.exit_reason in ("data_end", "track_limit")
    assert exit_study.K_WARNING_BREAK in {t.kind for t in ev.timeline}


def test_MANUAL_EXIT_REVIEWの指標は売却判定に使わない(exp):
    """指標は計算するが、それでポジションが閉じてはいけない。"""
    bars = _series(
        [(100, 105, 99, 104)] * 20
        + [
            (104, 109, 103, 108),   # 20 = シグナル日
            (108, 110, 107, 109),   # 21 = ENTRY日
            (109, 110, 100.5, 101),  # 22 大陰線・安値引け。それでも売らない
            (101, 103, 100.2, 102),
        ]
    )
    sig = _signal(20, close=108, lower=100, upper=115)
    ev = exit_study.track_event(sig, bars, exp)

    wc = ev.warning_candles[0]
    assert wc.change_pct is not None and wc.change_pct < 0
    assert wc.close_pos_in_day_range is not None and wc.close_pos_in_day_range < 0.2
    assert wc.manual_exit_review is True
    assert ev.hit_initial_stop is False  # stop 99.5 には未到達


def test_トレーリング候補は初期STOPを上回るものだけ数える(exp):
    """trail は参考。初期STOPより下の水準は「有利な移行」ではない。"""
    bars = _series([
        (100, 105, 99, 104),
        (100, 105, 99, 104),
        (104, 110, 103, 109),
        (109, 115, 108, 114),
        (114, 116, 105, 106),
        (106, 112, 105, 111),
        (111, 120, 110, 119),
        (119, 125, 118, 124),
    ])
    ev = exit_study.track_event(_signal(1, close=104, lower=100, upper=115), bars, exp)

    for tc in ev.trail_candidates:
        assert tc.trail_stop_candidate == tc.swing_low_price * 0.995
        assert tc.variant in ("strict", "loose")
    assert all(
        tc.trail_stop_candidate > ev.initial_stop
        for tc in ev.trail_candidates
        if tc.improves_on_initial_stop
    )
    # trail はポジションを閉じる判定には使わない
    assert {t.kind for t in ev.timeline if t.is_rule_based} <= {
        exit_study.K_ENTRY, exit_study.K_STOP_HIT
    }


def test_保有中の重複ENTRYは別イベントとして残す(exp):
    """同じポジションへの新規買いとして自動処理しない。"""
    bars = _series([
        (100, 105, 99, 104),
        (100, 105, 99, 104),   # 1 = シグナル日A
        (104, 106, 103, 105),  # 2 = A の ENTRY日
        (105, 107, 104, 106),  # 3 = シグナル日B（A を保有中）
        (106, 108, 105, 107),  # 4 = B の ENTRY日
        (107, 108, 94, 95),    # 5 stop
    ])
    a = exit_study.track_event(_signal(1, close=104, lower=100, upper=110), bars, exp)
    b = exit_study.track_event(_signal(3, close=106, lower=100, upper=110), bars, exp)
    exit_study.apply_classification([a, b])

    assert a.duplicate_entry_while_holding is False
    assert b.duplicate_entry_while_holding is True
    assert "DUPLICATE_ENTRY_WHILE_HOLDING" in b.flags
    # 別個のイベントとして両方残る
    assert a.entry_price == 104 and b.entry_price == 106


def test_TYPE分類は上限到達と伸びで決まる(exp):
    """TYPE5/TYPE6 は TYPE1〜4 と排他ではないので flags 側に置く。"""
    bars = _series([
        (100, 105, 99, 104),
        (100, 105, 99, 104),
        (104, 106, 103, 105),
        (105, 106, 94, 95),
    ])
    ev = exit_study.track_event(_signal(1, close=104, lower=100, upper=115), bars, exp)
    exit_study.apply_classification([ev])
    assert ev.type_label == "TYPE1"  # 上限到達前に stop
