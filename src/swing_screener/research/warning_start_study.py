"""警戒陰線を「いつ有効化するか」だけを比較する検証（research 専用）。

前回の `exit_state_machine.py` は、元レンジ上限を終値突破した **翌営業日から**
警戒足を拾った。その結果、突破した 22 件すべてで警戒足が出て、そのうち 14 件
（64%）は突破の翌営業日に出た。「上昇波の途中の調整」ではなく
「ブレイク直後の小休止」を拾っている可能性がある。

そこで **WARNING へ入る条件だけ** を 3 通りに変えて比較する:

    VARIANT A  上限を終値突破した翌営業日から（現行案・比較基準）
    VARIANT B  突破後に high  > breakout_day_high  を満たした日の翌営業日から
    VARIANT C  突破後に close > breakout_day_close を満たした日の翌営業日から

--------------------------------------------------------------------------
固定するもの（3 案で完全に同一。今回は一切触らない）
--------------------------------------------------------------------------

    ENTRY ロジック / near.max_position_in_range = 0.65
    初期STOP = range_lower * 0.995
    reference_high = 警戒足発生時点までの保有中最高値
    warning_low 割れ後に CASE3 が WARNING に留まる挙動（解釈(b)）
    押し安値 = WARNING 期間中の最安値
    trail stop = 押し安値 * 0.995 / STOP は上方向にのみ更新

--------------------------------------------------------------------------
このモジュールがやらないこと
--------------------------------------------------------------------------

* 最も仮想リターンが高い案を採用する、という結論は出さない（§17）。
  母数 32 件（+10% 到達は 6 件）で、率は参考程度にしかならない。
* 新しい数値閾値を探索しない。B/C の条件はどちらも
  「ブレイクアウト日の高値／終値を超えたか」だけで、調整幅もパラメータもない。
* 本番の config.yaml / experimental.yaml / スクリーナーには書き込まない。
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from swing_screener.models import PriceSeries
from swing_screener.research import exit_state_machine as sm
from swing_screener.research.exit_study import MAX_TRACK_DAYS, _cell, _median, _rate

EventKey = tuple[str, date]

CSV_NOTE = (
    "# 注記: 警戒陰線の有効化タイミング（VARIANT A/B/C）だけを比較した検証であり、"
    "収益バックテストではない。A/B/C で違うのは「WARNING へ入る条件」だけで、"
    "reference_high の定義 / warning_low 割れ後の CASE3 の扱い / 押し安値の取り方 /"
    "トレーリングは 3 案とも同一。"
    " 最も仮想リターンが高い案を採用する、という使い方はしない（母数 32 件）。"
    " 確定ルールとして変更していないのは ENTRY ロジック /"
    " max_position_in_range=0.65 / initial_stop = range_lower*0.995 の 3 点。"
)


# --- 3 案の実行 ---------------------------------------------------------------


def key_of(ev: sm.SMEvent) -> EventKey:
    return (ev.code, ev.signal_date)


@dataclass
class VariantRun:
    """1 案分の追跡結果。"""

    variant: str
    events: list[sm.SMEvent]

    @property
    def label(self) -> str:
        return sm.VARIANT_LABELS_JA[self.variant]

    @property
    def by_key(self) -> dict[EventKey, sm.SMEvent]:
        return {key_of(e): e for e in self.events}

    @property
    def entered(self) -> list[sm.SMEvent]:
        return [e for e in self.events if e.entry_available]


def run_variants(
    prepared: list[tuple[dict[str, Any], PriceSeries]],
    exp=None,
    *,
    max_track_days: int = MAX_TRACK_DAYS,
    variants: tuple[str, ...] = sm.VARIANTS,
) -> dict[str, VariantRun]:
    """同じ 32 件を 3 案で追跡する。案ごとに独立に 1 営業日ずつ再生する。"""
    runs: dict[str, VariantRun] = {}
    for v in variants:
        events = [
            sm.track_event(row, series, exp, max_track_days=max_track_days, variant=v)
            for row, series in prepared
        ]
        sm.apply_classification(events)
        runs[v] = VariantRun(variant=v, events=events)
    return runs


# --- 案ごとの指標（§5 / §6 / §7 / §12）----------------------------------------


@dataclass(frozen=True)
class MetricRow:
    """A/B/C を横並びにするための 1 指標。"""

    section: str
    metric: str
    values: dict[str, str]
    note: str = ""


def _first_warning(ev: sm.SMEvent) -> sm.WarningEpisode | None:
    return ev.warnings[0] if ev.warnings else None


def _days_from_breakout(ev: sm.SMEvent) -> int | None:
    w = _first_warning(ev)
    if w is None or ev.upper_close_break_day_offset is None:
        return None
    return w.day_offset - ev.upper_close_break_day_offset


def _max_gain_at_warning_pct(ev: sm.SMEvent, w: sm.WarningEpisode) -> float | None:
    """警戒足が出た時点での最大含み益。reference_high が保有中最高値そのもの。"""
    if ev.entry_price is None:
        return None
    return (w.reference_high - ev.entry_price) / ev.entry_price * 100.0


def _upper_excess_pct(ev: sm.SMEvent, w: sm.WarningEpisode) -> float:
    """警戒足の終値が元レンジ上限からどれだけ上か。"""
    return (w.close - ev.range_upper) / ev.range_upper * 100.0


def reference_max_gain(runs: dict[str, VariantRun]) -> dict[EventKey, float]:
    """イベントごとの「どこまで伸びたか」を案に依存しない形で 1 つだけ決める。

    各案の CASE ごとの `max_gain_pct` は EXIT した日までしか見ないので、
    案が違うと分母まで変わってしまい「利益をどれだけ残せたか」を比較できない。
    ここでは **3 案のいずれかが保有していた期間の和** で最大含み益を取り、
    案によらない固定の分母にする。
    """
    out: dict[EventKey, float] = {}
    for run in runs.values():
        for ev in run.events:
            if ev.entry_price is None or not ev.daily:
                continue
            best = max(
                (ds.high - ev.entry_price) / ev.entry_price * 100.0 for ds in ev.daily
            )
            k = key_of(ev)
            out[k] = max(out.get(k, -1e9), best)
    return out


def _capture_ratio(
    run: VariantRun, case: str, ref: dict[EventKey, float], min_gain: float
) -> tuple[float | None, int]:
    """最大含み益が min_gain 以上まで伸びた件で、最終リターン ÷ 最大含み益。"""
    vals: list[float] = []
    n = 0
    for e in run.entered:
        mg = ref.get(key_of(e))
        r = e.cases.get(case)
        if mg is None or mg < min_gain or r is None:
            continue
        n += 1
        if r.approximate_return_pct is not None and mg > 0:
            vals.append(r.approximate_return_pct / mg * 100.0)
    return _median(vals), n


def _fmt(v: float | None, unit: str = "%", *, sign: bool = True) -> str:
    if v is None:
        return "－"
    return f"{v:+.2f}{unit}" if sign else f"{v:.2f}{unit}"


def compare_metrics(runs: dict[str, VariantRun]) -> list[MetricRow]:
    """§5 / §6 / §7 / §12 の指標を A/B/C 横並びで作る。"""
    vs = [v for v in sm.VARIANTS if v in runs]
    rows: list[MetricRow] = []

    def add(section: str, metric: str, fn, note: str = "") -> None:
        rows.append(MetricRow(section, metric, {v: fn(runs[v]) for v in vs}, note))

    # --- 共通の前提（3 案で同じであることを見せる）---
    add("前提（3案で同一）", "対象イベント数",
        lambda r: str(len(r.events)),
        "near.max_position_in_range=0.65 で発生した ENTRY_CANDIDATE。前回と同一")
    add("前提（3案で同一）", "元レンジ上限を終値突破した件数",
        lambda r: _rate(sum(1 for e in r.entered if e.reached_trend_hold),
                        len(r.entered)),
        "突破の判定は 3 案で同じ。ここから先だけが違う")

    # --- §5 WARNING 発生状況 ---
    def warned(r: VariantRun) -> list[sm.SMEvent]:
        return [e for e in r.entered if e.warnings]

    def broke(r: VariantRun) -> list[sm.SMEvent]:
        return [e for e in r.entered if e.reached_trend_hold]

    add("§5 WARNING発生状況", "WARNINGが発生した件数",
        lambda r: _rate(len(warned(r)), len(broke(r))),
        "分母は上限を終値突破した件数")
    add("§5 WARNING発生状況", "警戒足の総本数",
        lambda r: f"{sum(e.warning_count for e in r.entered)} 本",
        "前回 exit_study（ENTRY直後からの全陰線）は 175 本")
    add("§5 WARNING発生状況", "WARNINGが一度も発生しなかった件数",
        lambda r: _rate(len(broke(r)) - len(warned(r)), len(broke(r))),
        "突破したのに警戒足が出ないまま終わった件")
    add("§5 WARNING発生状況", "うち UPTREND_CONFIRMED が来なかった件数",
        lambda r: _rate(sum(1 for e in r.entered if e.warning_gate_pending),
                        len(broke(r))),
        "B/C 固有。A は確認ゲートを持たないので 0")
    add("§5 WARNING発生状況", "上限突破からWARNINGまでの営業日数（中央値）",
        lambda r: (
            f"{_median([float(_days_from_breakout(e)) for e in warned(r)]):.0f} 日"
            if warned(r) else "－"
        ),
        "1 = 突破の翌営業日")
    add("§5 WARNING発生状況", "上限突破の翌営業日にWARNINGとなった件数",
        lambda r: _rate(sum(1 for e in warned(r) if _days_from_breakout(e) == 1),
                        len(broke(r))),
        "「ブレイク直後の普通の陰線を警戒足にする」問題の直接の指標")
    add("§5 WARNING発生状況", "上限突破から3営業日以内のWARNING件数",
        lambda r: _rate(
            sum(1 for e in warned(r) if (_days_from_breakout(e) or 99) <= 3),
            len(broke(r))),
        )
    add("§5 WARNING発生状況", "WARNING発生時の含み益率（中央値）",
        lambda r: _fmt(_median([
            _first_warning(e).unrealized_pct_at_warning for e in warned(r)
        ])),
        "最初の警戒足の終値ベース。仮想ENTRY価格（翌営業日始値）基準")
    add("§5 WARNING発生状況", "WARNING発生時の元レンジ上限からの上昇率（中央値）",
        lambda r: _fmt(_median([
            _upper_excess_pct(e, _first_warning(e)) for e in warned(r)
        ])),
        "警戒足の終値が元レンジ上限からどれだけ上か")
    add("§5 WARNING発生状況", "WARNING発生時点の最大含み益（中央値）",
        lambda r: _fmt(_median([
            g for e in warned(r)
            if (g := _max_gain_at_warning_pct(e, _first_warning(e))) is not None
        ])),
        "= reference_high の含み益率。警戒足が出るまでにどこまで伸びていたか")
    add("§5 WARNING発生状況", "UPTREND_CONFIRMEDまでの営業日数（中央値）",
        lambda r: (
            f"{_median([float(e.uptrend_confirmed_day_offset - (e.upper_close_break_day_offset or 0)) for e in r.entered if e.uptrend_confirmed_day_offset is not None]):.0f} 日"
            if any(e.uptrend_confirmed_day_offset is not None for e in r.entered)
            else "－（Aは確認を挟まない）"
        ),
        "突破日からの営業日数")
    add("§5 WARNING発生状況", "UPTREND_CONFIRMEDの日そのものが陰線だった件数",
        lambda r: _rate(sum(1 for e in r.entered if e.uptrend_confirm_day_bearish),
                        sum(1 for e in r.entered
                            if e.uptrend_confirmed_date is not None)),
        "§11。その日は警戒足に使わず、翌営業日以降の最初の陰線を使う")

    def peak_warnings(r: VariantRun) -> list[sm.WarningEpisode]:
        """警戒足の高値が、そのまま reference_high（保有中最高値）だったもの。

        この形だと「調整後の再高値更新」に天井の更新を要求することになり、
        トレーリングが再武装しにくくなる（前回の検証で見つかった構造的な詰まり）。
        """
        return [
            w for e in r.entered for w in e.warnings
            if abs(w.warning_high_vs_reference_high_pct) < 1e-9
        ]

    add("§5 WARNING発生状況", "警戒足がその足自身の保有中最高値だった本数",
        lambda r: _rate(len(peak_warnings(r)), sum(e.warning_count for e in r.entered)),
        "reference_high が直近の天井そのものになり、再高値更新に天井の更新を要求する形")
    add("§5 WARNING発生状況", "うち二度と reference_high を更新できなかった本数",
        lambda r: _rate(sum(1 for w in peak_warnings(r) if w.rehigh_date is None),
                        len(peak_warnings(r))))

    # --- §6 WARNING 後の決着 ---
    def res_count(r: VariantRun, key: str) -> int:
        return sum(1 for e in r.entered for w in e.warnings if w.resolution == key)

    def total_w(r: VariantRun) -> int:
        return sum(e.warning_count for e in r.entered)

    add("§6 WARNING後の決着", "warning_low を先に割った警戒足",
        lambda r: _rate(res_count(r, "low_break"), total_w(r)))
    add("§6 WARNING後の決着", "reference_high を先に更新した警戒足",
        lambda r: _rate(res_count(r, "rehigh"), total_w(r)),
        "「調整 → 再高値更新」という戦略意図どおりの経路")
    add("§6 WARNING後の決着", "同日に両方到達で順序不明",
        lambda r: _rate(res_count(r, "ambiguous_both"), total_w(r)),
        "有利・不利どちらの順番も仮定しない（§10）")
    add("§6 WARNING後の決着", "どちらにも到達せず終了（STOP到達）",
        lambda r: _rate(res_count(r, "stop"), total_w(r)))
    add("§6 WARNING後の決着", "どちらにも到達せず終了（追跡終端）",
        lambda r: _rate(res_count(r, "open"), total_w(r)))
    add("§6 WARNING後の決着", "STUCK_IN_WARNING の件数",
        lambda r: _rate(sum(1 for e in r.entered if "STUCK_IN_WARNING" in e.flags),
                        len(r.entered)),
        "warning_low を割ったのに CASE3 が WARNING に留まった（解釈(b)。今回は変更しない）")
    add("§6 WARNING後の決着", "STUCK_IN_WARNING の滞留日数（中央値）",
        lambda r: (
            f"{_median([float(w.days_held_in_warning_after_low_break) for e in r.entered for w in e.warnings if (w.days_held_in_warning_after_low_break or 0) > 0]):.1f} 日"
            if any((w.days_held_in_warning_after_low_break or 0) > 0
                   for e in r.entered for w in e.warnings) else "－"
        ))
    add("§6 WARNING後の決着", "warning_low を寄りでギャップ割れした警戒足",
        lambda r: _rate(
            sum(1 for e in r.entered for w in e.warnings if w.gap_through_warning_low),
            sum(1 for e in r.entered for w in e.warnings
                if w.low_break_date is not None)),
        "分母は warning_low を割った警戒足。warning_low での約定は仮定できない")

    # --- §7 トレーリングへの影響 ---
    add("§7 トレーリング", "REHIGH_CONFIRMED が発生した件数",
        lambda r: _rate(sum(1 for e in r.entered if e.rehigh_count >= 1),
                        len(r.entered)),
        "WARNING → 調整 → reference_high 再突破まで進んだ件")
    add("§7 トレーリング", "再高値更新の総回数",
        lambda r: f"{sum(e.rehigh_count for e in r.entered)} 回")
    add("§7 トレーリング", "trail stop を1回以上引き上げた件数",
        lambda r: _rate(sum(1 for e in r.entered if e.stop_raise_count >= 1),
                        len(r.entered)),
        "「警戒開始を遅らせるだけで trail 成立が改善するか」の主指標")
    add("§7 トレーリング", "trail stop を2回以上引き上げた件数",
        lambda r: _rate(sum(1 for e in r.entered if e.stop_raise_count >= 2),
                        len(r.entered)))
    add("§7 トレーリング", "trail stop 更新の総回数",
        lambda r: f"{sum(e.stop_raise_count for e in r.entered)} 回")
    add("§7 トレーリング", "初期STOPから最初のtrail stopまでの引き上げ幅（中央値）",
        lambda r: _fmt(_median([
            e.stop_updates[0].raise_pct_from_initial_stop
            for e in r.entered if e.stop_updates
        ])),
        "初期STOP比。1 回目の引き上げのみ")
    add("§7 トレーリング", "trail成立までの営業日数（中央値・仮想ENTRYから）",
        lambda r: (
            f"{_median([float(e.stop_updates[0].day_offset) for e in r.entered if e.stop_updates]):.0f} 日"
            if any(e.stop_updates for e in r.entered) else "－"
        ),
        "1 回目の引き上げが確定した日。有効になるのはその翌営業日")
    add("§7 トレーリング", "trail成立までの営業日数（中央値・上限突破から）",
        lambda r: (
            f"{_median([float(e.stop_updates[0].day_offset - (e.upper_close_break_day_offset or 0)) for e in r.entered if e.stop_updates]):.0f} 日"
            if any(e.stop_updates for e in r.entered) else "－"
        ))

    # --- §12 利益保持（参考値。最良案を採る材料にはしない）---
    for case in sm.CASES:
        lbl = sm.CASE_LABELS_JA[case].split(" ")[0]
        add("§12 利益保持（参考値）", f"{lbl}: 仮想リターン中央値",
            lambda r, c=case: _fmt(_median([
                e.cases[c].approximate_return_pct for e in r.entered
                if e.cases[c].approximate_return_pct is not None
            ])),
            "約定価格は保証されない。母数 32 件なので順位づけには使わない")
        add("§12 利益保持（参考値）", f"{lbl}: 最大含み益の中央値",
            lambda r, c=case: _fmt(_median([
                e.cases[c].max_gain_pct for e in r.entered
                if e.cases[c].max_gain_pct is not None
            ])))
        add("§12 利益保持（参考値）", f"{lbl}: 吐き出し幅の中央値",
            lambda r, c=case: (
                f"{_median([e.cases[c].giveback_pct for e in r.entered if e.cases[c].giveback_pct is not None]):.2f}pt"
                if any(e.cases[c].giveback_pct is not None for e in r.entered) else "－"
            ),
            "最大含み益 − 最終リターン")
    ref = reference_max_gain(runs)
    for case in (sm.CASE2, sm.CASE3):
        lbl = sm.CASE_LABELS_JA[case].split(" ")[0]
        for th in (5.0, 10.0):
            def _cap(r: VariantRun, c=case, t=th) -> str:
                med, n = _capture_ratio(r, c, ref, t)
                return f"{med:.0f}%（{n}件）" if med is not None else "－"
            add("§12 利益保持（参考値）",
                f"{lbl}: 最大含み益+{th:.0f}%以上のケースで残せた割合（中央値）",
                _cap,
                "最終リターン ÷ 最大含み益。100% なら天井で降りられたということ。"
                "分母の最大含み益は 3 案の保有期間の和で取った案に依存しない値なので、"
                f"対象件数は A/B/C で同じ（+{th:.0f}% 以上に伸びたイベント）")

    return rows


# --- §8 早すぎる警戒足の抽出 ---------------------------------------------------


@dataclass(frozen=True)
class EarlyWarningCase:
    """A では warning_low 割れになるが、B/C ではまだ WARNING ではなかった件。"""

    code: str
    name: str
    signal_date: date
    breakout_date: date | None
    a_warning_date: date | None
    a_warning_day_from_breakout: int | None
    a_warning_low: float | None
    a_low_break_date: date | None
    a_low_break_day_offset: int | None
    a_case2_return_pct: float | None
    other_variant: str
    other_warning_date: date | None
    other_state_at_a_break: str
    post_break_max_gain_pct: float | None     # A が降りた翌日以降の最大含み益
    post_break_max_gain_date: date | None
    gain_left_on_table_pt: float | None       # 上記 − A の CASE2 リターン
    other_case2_return_pct: float | None
    other_case3_return_pct: float | None
    other_stop_raise_count: int


def _state_at(ev: sm.SMEvent, day_offset: int) -> str:
    for ds in ev.daily:
        if ds.day_offset == day_offset:
            return ds.state
    return "（追跡終了後）"


def extract_early_warning_cases(
    runs: dict[str, VariantRun]
) -> list[EarlyWarningCase]:
    """§8。A の warning_low 割れ時点で B/C はまだ WARNING に入っていない件。"""
    a = runs[sm.VARIANT_A].by_key
    out: list[EarlyWarningCase] = []
    for v in (sm.VARIANT_B, sm.VARIANT_C):
        if v not in runs:
            continue
        for k, other in runs[v].by_key.items():
            ea = a.get(k)
            if ea is None or not ea.warnings:
                continue
            wa = next((w for w in ea.warnings if w.low_break_date is not None), None)
            if wa is None or wa.low_break_day_offset is None:
                continue
            brk = wa.low_break_day_offset
            ow = _first_warning(other)
            # 「その時点ではまだ WARNING ではない」= 警戒足が無いか、A の割れより後
            if ow is not None and ow.day_offset <= brk:
                continue
            post = [ds for ds in other.daily if ds.day_offset > brk]
            entry = other.entry_price
            best = max(post, key=lambda ds: ds.high) if post else None
            post_gain = (
                (best.high - entry) / entry * 100.0
                if best is not None and entry else None
            )
            a_ret = ea.cases[sm.CASE2].approximate_return_pct
            out.append(
                EarlyWarningCase(
                    code=ea.code, name=ea.name, signal_date=ea.signal_date,
                    breakout_date=ea.upper_close_break_date,
                    a_warning_date=wa.date,
                    a_warning_day_from_breakout=_days_from_breakout(ea),
                    a_warning_low=wa.low,
                    a_low_break_date=wa.low_break_date,
                    a_low_break_day_offset=brk,
                    a_case2_return_pct=a_ret,
                    other_variant=v,
                    other_warning_date=ow.date if ow else None,
                    other_state_at_a_break=_state_at(other, brk),
                    post_break_max_gain_pct=post_gain,
                    post_break_max_gain_date=best.date if best else None,
                    gain_left_on_table_pt=(
                        post_gain - a_ret
                        if post_gain is not None and a_ret is not None else None
                    ),
                    other_case2_return_pct=(
                        other.cases[sm.CASE2].approximate_return_pct
                    ),
                    other_case3_return_pct=(
                        other.cases[sm.CASE3].approximate_return_pct
                    ),
                    other_stop_raise_count=other.stop_raise_count,
                )
            )
    out.sort(key=lambda c: -(c.gain_left_on_table_pt or -999))
    return out


# --- §9 遅すぎる警戒足の抽出 ---------------------------------------------------


@dataclass(frozen=True)
class LateWarningCase:
    """A なら守れたが、B/C では WARNING 前に落ちた件。"""

    code: str
    name: str
    signal_date: date
    variant: str
    breakout_date: date | None
    uptrend_confirmed_date: date | None
    other_warning_date: date | None
    a_warning_date: date | None
    a_case2_return_pct: float | None
    variant_case2_return_pct: float | None
    diff_pt: float | None
    variant_case3_return_pct: float | None
    variant_max_gain_pct: float | None
    variant_giveback_pct: float | None
    variant_exit_type: str
    back_to_initial_stop: bool
    never_warned: bool


def extract_late_warning_cases(runs: dict[str, VariantRun]) -> list[LateWarningCase]:
    """§9。警戒開始を遅らせたことで、守れたはずの利益を落とした件。"""
    a = runs[sm.VARIANT_A].by_key
    out: list[LateWarningCase] = []
    for v in (sm.VARIANT_B, sm.VARIANT_C):
        if v not in runs:
            continue
        for k, other in runs[v].by_key.items():
            ea = a.get(k)
            if ea is None or not ea.entry_available or not ea.warnings:
                continue
            a_ret = ea.cases[sm.CASE2].approximate_return_pct
            o_ret = other.cases[sm.CASE2].approximate_return_pct
            if a_ret is None or o_ret is None or o_ret >= a_ret:
                continue
            ow = _first_warning(other)
            r3 = other.cases[sm.CASE3]
            out.append(
                LateWarningCase(
                    code=ea.code, name=ea.name, signal_date=ea.signal_date, variant=v,
                    breakout_date=ea.upper_close_break_date,
                    uptrend_confirmed_date=other.uptrend_confirmed_date,
                    other_warning_date=ow.date if ow else None,
                    a_warning_date=ea.warnings[0].date,
                    a_case2_return_pct=a_ret,
                    variant_case2_return_pct=o_ret,
                    diff_pt=o_ret - a_ret,
                    variant_case3_return_pct=r3.approximate_return_pct,
                    variant_max_gain_pct=r3.max_gain_pct,
                    variant_giveback_pct=r3.giveback_pct,
                    variant_exit_type=r3.exit_type,
                    back_to_initial_stop=r3.exit_type in (
                        sm.X_INITIAL_STOP, sm.X_INITIAL_STOP_AFTER_BREAK
                    ),
                    never_warned=ow is None,
                )
            )
    out.sort(key=lambda c: (c.diff_pt if c.diff_pt is not None else 0.0))
    return out


# --- §10 B と C の違い ---------------------------------------------------------


@dataclass(frozen=True)
class ConfirmComparison:
    code: str
    name: str
    signal_date: date
    breakout_date: date | None
    breakout_day_high: float | None
    breakout_day_close: float | None
    b_confirm_date: date | None
    b_confirm_day_offset: int | None
    c_confirm_date: date | None
    c_confirm_day_offset: int | None
    category: str          # both / only_b / only_c / neither
    order: str             # b_first / c_first / same_day / －
    b_warning_date: date | None
    c_warning_date: date | None
    warning_gap_days: int | None   # C の警戒足 − B の警戒足（営業日）


CONFIRM_CATEGORY_JA = {
    "both": "B・C とも確認成立",
    "only_b": "B だけ確認成立",
    "only_c": "C だけ確認成立",
    "neither": "どちらも成立しない",
}

CONFIRM_ORDER_JA = {
    "b_first": "B の方が先に成立",
    "c_first": "C の方が先に成立",
    "same_day": "同日成立",
    "－": "比較対象なし",
}


def compare_confirmations(runs: dict[str, VariantRun]) -> list[ConfirmComparison]:
    """§10。B（高値更新）と C（終値上昇）のどちらが先に確認成立するか。

    確認が成立するまでの状態は 3 案とも完全に同一（TREND_HOLD・初期STOP）なので、
    B と C の確認日はそのまま比較できる。
    """
    if sm.VARIANT_B not in runs or sm.VARIANT_C not in runs:
        return []
    b_map, c_map = runs[sm.VARIANT_B].by_key, runs[sm.VARIANT_C].by_key
    out: list[ConfirmComparison] = []
    for k, eb in b_map.items():
        ec = c_map.get(k)
        if ec is None or not eb.entry_available or not eb.reached_trend_hold:
            continue
        bo, co = eb.uptrend_confirmed_day_offset, ec.uptrend_confirmed_day_offset
        if bo is not None and co is not None:
            cat = "both"
            order = "b_first" if bo < co else ("c_first" if co < bo else "same_day")
        elif bo is not None:
            cat, order = "only_b", "b_first"
        elif co is not None:
            cat, order = "only_c", "c_first"
        else:
            cat, order = "neither", "－"
        wb, wc = _first_warning(eb), _first_warning(ec)
        out.append(
            ConfirmComparison(
                code=eb.code, name=eb.name, signal_date=eb.signal_date,
                breakout_date=eb.upper_close_break_date,
                breakout_day_high=eb.breakout_day_high,
                breakout_day_close=eb.breakout_day_close,
                b_confirm_date=eb.uptrend_confirmed_date, b_confirm_day_offset=bo,
                c_confirm_date=ec.uptrend_confirmed_date, c_confirm_day_offset=co,
                category=cat, order=order,
                b_warning_date=wb.date if wb else None,
                c_warning_date=wc.date if wc else None,
                warning_gap_days=(
                    wc.day_offset - wb.day_offset if wb and wc else None
                ),
            )
        )
    out.sort(key=lambda c: (c.category, c.signal_date, c.code))
    return out


# --- CSV 出力（§15）------------------------------------------------------------

EXTRA_EVENT_COLUMNS = [
    "variant", "variant_condition", "breakout_day_high", "breakout_day_close",
    "uptrend_confirmed_date", "uptrend_confirmed_day_offset",
    "uptrend_confirm_day_bearish", "warning_days_from_breakout",
    "warning_days_from_confirm", "rehigh_days_failing_own_confirm",
]


def write_events_csv(runs: dict[str, VariantRun], path: Path) -> Path:
    """3 案分のイベントを縦に並べる（variant 列で区別）。"""
    cols = EXTRA_EVENT_COLUMNS + sm.EVENT_COLUMNS
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(CSV_NOTE + "\n")
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for v in sm.VARIANTS:
            run = runs.get(v)
            if run is None:
                continue
            for ev in run.events:
                row = sm.event_row(ev)
                fw = _first_warning(ev)
                row.update({
                    "variant": v,
                    "variant_condition": sm.VARIANT_CONDITION_JA[v],
                    "breakout_day_high": _cell(ev.breakout_day_high),
                    "breakout_day_close": _cell(ev.breakout_day_close),
                    "uptrend_confirmed_date": _cell(ev.uptrend_confirmed_date),
                    "uptrend_confirmed_day_offset": _cell(
                        ev.uptrend_confirmed_day_offset),
                    "uptrend_confirm_day_bearish": _cell(
                        ev.uptrend_confirm_day_bearish),
                    "warning_days_from_breakout": _cell(_days_from_breakout(ev)),
                    "warning_days_from_confirm": _cell(
                        fw.day_offset - ev.uptrend_confirmed_day_offset
                        if fw and ev.uptrend_confirmed_day_offset is not None else None
                    ),
                    "rehigh_days_failing_own_confirm": _cell(
                        ev.rehigh_days_failing_own_confirm),
                })
                w.writerow(row)
    return path


def write_warnings_csv(runs: dict[str, VariantRun], path: Path) -> Path:
    cols = ["variant", "code", "name", "signal_date", "entry_price", "initial_stop",
            "breakout_date", "warning_days_from_breakout", "uptrend_confirmed_date"]
    cols += [f for f in sm.WarningEpisode.__dataclass_fields__]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(
            "# 3 案それぞれの警戒足。A/B/C で違うのは「どの陰線を警戒足にしたか」だけで、"
            "reference_high の定義（＝発生時点までの保有中最高値）は 3 案とも同一。\n"
        )
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for v in sm.VARIANTS:
            run = runs.get(v)
            if run is None:
                continue
            for ev in run.events:
                for wc in ev.warnings:
                    row = {k: _cell(val) for k, val in asdict(wc).items()}
                    row.update(
                        variant=v, code=ev.code, name=ev.name,
                        signal_date=ev.signal_date.isoformat(),
                        entry_price=_cell(ev.entry_price),
                        initial_stop=_cell(ev.initial_stop),
                        breakout_date=_cell(ev.upper_close_break_date),
                        warning_days_from_breakout=_cell(
                            wc.day_offset - ev.upper_close_break_day_offset
                            if ev.upper_close_break_day_offset is not None else None
                        ),
                        uptrend_confirmed_date=_cell(ev.uptrend_confirmed_date),
                    )
                    w.writerow(row)
    return path


def write_variant_comparison_csv(rows: list[MetricRow], path: Path) -> Path:
    cols = ["section", "metric"] + [f"variant_{v}" for v in sm.VARIANTS] + ["note"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(
            "# A/B/C の横並び比較。違うのは「WARNING へ入る条件」だけ。"
            "最も仮想リターンが高い案を採用する、という使い方はしない（§17）。\n"
        )
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            row = {"section": r.section, "metric": r.metric, "note": r.note}
            for v in sm.VARIANTS:
                row[f"variant_{v}"] = r.values.get(v, "")
            w.writerow(row)
    return path


def write_summary_csv(runs: dict[str, VariantRun], path: Path) -> Path:
    """案ごとの詳細集計（exit_state_machine.summarize をそのまま流用）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(
            "# 案ごとの詳細集計。横並びの比較は variant_comparison.csv を見る。\n"
        )
        w = csv.DictWriter(
            f, fieldnames=["variant", "variant_label", "section", "metric", "value", "note"]
        )
        w.writeheader()
        for v in sm.VARIANTS:
            run = runs.get(v)
            if run is None:
                continue
            for r in sm.summarize(run.events):
                w.writerow({
                    "variant": v, "variant_label": sm.VARIANT_LABELS_JA[v],
                    "section": r.section, "metric": r.metric,
                    "value": r.value, "note": r.note,
                })
    return path


def _write_dataclass_csv(rows: list[Any], path: Path, note: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        with path.open("w", encoding="utf-8", newline="") as f:
            f.write(note + "\n# 該当なし\n")
        return path
    cols = list(type(rows[0]).__dataclass_fields__)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(note + "\n")
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: _cell(v) for k, v in asdict(r).items()})
    return path


def write_early_warning_csv(rows: list[EarlyWarningCase], path: Path) -> Path:
    return _write_dataclass_csv(
        rows, path,
        "# §8 A では warning_low 割れになるが、B/C ではその時点でまだ WARNING ではなかった件。"
        " gain_left_on_table_pt は「A が降りた翌日以降に付けた最大含み益 − A の CASE2 リターン」で、"
        "実際にそこで降りられたという意味ではない（人間がチャートで見るための材料）。",
    )


def write_late_warning_csv(rows: list[LateWarningCase], path: Path) -> Path:
    return _write_dataclass_csv(
        rows, path,
        "# §9 警戒開始を遅らせた副作用。A の CASE2 より B/C の CASE2 が悪化した件。"
        " never_warned=true は「WARNING が一度も出ないまま終わった」件。",
    )


def write_confirm_comparison_csv(rows: list[ConfirmComparison], path: Path) -> Path:
    return _write_dataclass_csv(
        rows, path,
        "# §10 B（high > breakout_day_high）と C（close > breakout_day_close）の"
        "確認成立日の比較。確認前の状態は 3 案とも同一なので日付はそのまま比較できる。",
    )
