"""`warning_low` を割ったあと「どこで降りるか」だけを比較する検証（research 専用）。

現在の文章ルールは

    警戒陰線の安値を下抜け → 利確候補

としか書いていない。「安値を 1 円でも割ったら即売却」までは決めていないので、
日足ベースの運用として自然な読み方を 3 通り並べて比較する。

    参考 HOLD_UNTIL_STOP  割っても降りない（前回 CASE3。比較の基準）
    V1   LOW_BREAK        low   < warning_low
    V2   CLOSE_BREAK      close < warning_low
    V3   STRUCTURAL_BREAK close < warning_low かつ close < original_range_upper

トリガーは入れ子になっている（V3 ⊆ V2 ⊆ V1）。`close < warning_low` なら
その日の安値も必ず warning_low を下回るからで、V2 の成立日は V1 の成立日と
同じかそれより後、V3 はさらに後になる。

--------------------------------------------------------------------------
固定するもの（4 案で完全に同一。今回は一切触らない）
--------------------------------------------------------------------------

    ENTRY ロジック / near.max_position_in_range = 0.65
    初期STOP = range_lower * 0.995
    WARNING 開始条件（研究上の固定基準として VARIANT A を使う）
    reference_high = 警戒足発生時点までの保有中最高値
    押し安値 = WARNING 期間中の最安値 / trail = 押し安値 * 0.995 / 上方向のみ

VARIANT A を使うのは「今回の原因を 1 つに絞るため」であって、
A を正式採用するという意味ではない（前回の warning_start_study の結論は保留のまま）。

--------------------------------------------------------------------------
約定の扱い（§3 / §14）
--------------------------------------------------------------------------

主分析の仮想EXITは **4 案とも「トリガー翌営業日の始値」** に統一する。
現在の運用が日足・引け後判断である以上、日中に warning_low を割ったことも
引けるまで確定しないので、V1 でも「翌営業日の寄り」が実運用に近い。

V1 についてだけ、事前に STOP 注文を置いていた場合の参考価格
（= warning_low、寄りが既に下ならその寄り値）を **別列** で持つ。
これは `SMEvent.cases[CASE2]` にそのまま入っている。両者は混ぜない。

--------------------------------------------------------------------------
このモジュールがやらないこと
--------------------------------------------------------------------------

* 最も仮想利益が高い案を採用する、という結論は出さない（§20）。
* 新しい数値閾値を探索しない。V3 が使うのは既存の
  `warning_low` と `original_range_upper` だけで、%閾値を持たない。
* reference_high / 押し安値 / trail / WARNING 開始条件を変更しない。
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

# 今回の比較で固定する WARNING 開始条件（研究上の基準。正式採用ではない）
FIXED_VARIANT = sm.VARIANT_A

# 参考基準を先頭に置いた比較順
RULES: tuple[str, ...] = (
    sm.BREAK_HOLD, sm.BREAK_LOW, sm.BREAK_CLOSE, sm.BREAK_STRUCT,
)
# 「実際に降りる」3 案（参考基準を除く）
EXIT_RULES: tuple[str, ...] = (sm.BREAK_LOW, sm.BREAK_CLOSE, sm.BREAK_STRUCT)

CSV_NOTE = (
    "# 注記: warning_low を割ったあとの扱い（LOW/CLOSE/STRUCTURAL）だけを比較した検証であり、"
    "収益バックテストではない。WARNING 開始条件は VARIANT A に固定しているが、"
    "これは原因を 1 つに絞るための研究上の基準であって A の正式採用ではない。"
    " 4 案で違うのは「warning_low 割れ後の処理」だけで、reference_high の定義 /"
    " 押し安値 / トレーリング / 初期STOP はすべて同一。"
    " 主分析の仮想EXITはトリガー翌営業日の始値。warning_low での約定は仮定していない。"
    " 最も仮想利益が高い案を採用する、という使い方はしない（母数 32 件）。"
)


# --- 4 案の実行 ---------------------------------------------------------------


def key_of(ev: sm.SMEvent) -> EventKey:
    return (ev.code, ev.signal_date)


@dataclass
class RuleRun:
    """1 案分の追跡結果。"""

    rule: str
    events: list[sm.SMEvent]

    @property
    def label(self) -> str:
        return sm.BREAK_RULE_LABELS_JA[self.rule]

    @property
    def short(self) -> str:
        return sm.BREAK_RULE_SHORT_JA[self.rule]

    @property
    def by_key(self) -> dict[EventKey, sm.SMEvent]:
        return {key_of(e): e for e in self.events}

    @property
    def entered(self) -> list[sm.SMEvent]:
        return [e for e in self.events if e.entry_available]


def run_rules(
    prepared: list[tuple[dict[str, Any], PriceSeries]],
    exp=None,
    *,
    max_track_days: int = MAX_TRACK_DAYS,
    rules: tuple[str, ...] = RULES,
    variant: str = FIXED_VARIANT,
) -> dict[str, RuleRun]:
    """同じ 32 件を 4 案で追跡する。案ごとに独立に 1 営業日ずつ再生する。"""
    runs: dict[str, RuleRun] = {}
    for rule in rules:
        events = [
            sm.track_event(
                row, series, exp, max_track_days=max_track_days,
                variant=variant, break_rule=rule,
            )
            for row, series in prepared
        ]
        sm.apply_classification(events)
        runs[rule] = RuleRun(rule=rule, events=events)
    return runs


# --- 共通の観測窓 -------------------------------------------------------------


def hold_window(runs: dict[str, RuleRun]) -> dict[EventKey, list[sm.DailyState]]:
    """「降りない解釈でも保有が続いていた期間」の日足。

    EXIT 後にどこまで伸びたか（§13）を案によらず同じ物差しで測るための窓。
    HOLD_UNTIL_STOP は warning_low では降りないので、初期STOP／trail STOP に
    当たるまでの期間が 4 案の中で最も長い。STOP に当たった後の値動きは
    「降りなければ取れた利益」ではないので窓に入れない。
    """
    hold = runs.get(sm.BREAK_HOLD)
    if hold is None:
        return {}
    out: dict[EventKey, list[sm.DailyState]] = {}
    for ev in hold.events:
        end = ev.path_result.exit_day_offset
        rows = [
            ds for ds in ev.daily
            if end is None or ds.day_offset <= end
        ]
        out[key_of(ev)] = rows
    return out


def reference_max_gain(runs: dict[str, RuleRun]) -> dict[EventKey, float]:
    """イベントごとの「どこまで伸びたか」を案に依存しない形で 1 つだけ決める。

    案ごとの `max_gain_pct` は EXIT 日までしか見ないので、案が違うと分母まで
    変わってしまい「利益をどれだけ残せたか」を比較できない。ここでは
    `hold_window` の最大含み益を 4 案共通の分母にする。
    """
    hold = runs.get(sm.BREAK_HOLD)
    if hold is None:
        return {}
    win = hold_window(runs)
    out: dict[EventKey, float] = {}
    for ev in hold.events:
        k = key_of(ev)
        rows = win.get(k) or []
        if ev.entry_price is None or not rows:
            continue
        out[k] = max((ds.high - ev.entry_price) / ev.entry_price * 100.0 for ds in rows)
    return out


def _breaks(run: RuleRun) -> list[sm.WarningBreak]:
    return [b for e in run.entered for b in e.warning_breaks]


def _fmt(v: float | None, unit: str = "%", *, sign: bool = True) -> str:
    if v is None:
        return "－"
    return f"{v:+.2f}{unit}" if sign else f"{v:.2f}{unit}"


def _days(vals: list[float]) -> str:
    m = _median(vals)
    return f"{m:.1f} 日" if m is not None else "－"


# --- §8 warning_low 割れの実態 -------------------------------------------------


@dataclass(frozen=True)
class BreakReality:
    """§8。HOLD_UNTIL_STOP（最も長く観測できる案）で数えた割れ方の実態。"""

    metric: str
    count: int
    denominator: int
    rate: str
    note: str = ""


def break_reality(runs: dict[str, RuleRun]) -> list[BreakReality]:
    """§8。「どこで 3 案の違いが生まれるか」を件数で押さえる。

    分母は HOLD_UNTIL_STOP の警戒足。V1/V2 は自分がそこで降りてしまうため
    その後の終値割れ・上限割れを観測できないので、実態集計には使わない。
    """
    hold = runs.get(sm.BREAK_HOLD)
    if hold is None:
        return []
    bs = _breaks(hold)
    total = len(bs)
    ev_total = len([e for e in hold.entered if e.warnings])
    intraday = [b for b in bs if b.intraday_break_date is not None]
    closed = [b for b in bs if b.close_break_date is not None]
    struct = [b for b in bs if b.struct_break_date is not None]
    recovered_day = [b for b in intraday if b.intraday_break_close_recovered]
    never_closed = [b for b in intraday if b.close_break_date is None]
    above_upper = [b for b in closed if b.struct_break_date is None]

    def ev_with(pred) -> int:
        return len([
            e for e in hold.entered if any(pred(b) for b in e.warning_breaks)
        ])

    rows = [
        BreakReality(
            "対象の警戒足（WARNING 開始条件は VARIANT A 固定）", total, total, "100%",
            f"WARNING が発生したイベントは {ev_total} 件",
        ),
        BreakReality(
            "warning_low を日中に割った警戒足", len(intraday), total,
            _rate(len(intraday), total),
            "EXIT VARIANT 1 のトリガー。ここが 3 案の出発点",
        ),
        BreakReality(
            "warning_low を終値で割った警戒足", len(closed), total,
            _rate(len(closed), total),
            "EXIT VARIANT 2 のトリガー",
        ),
        BreakReality(
            "日中に割ったが、その日の終値では回復した警戒足",
            len(recovered_day), len(intraday), _rate(len(recovered_day), len(intraday)),
            "初回の日中割れ当日に close >= warning_low へ戻した。"
            "V1 と V2 が最初に分かれる形",
        ),
        BreakReality(
            "日中に割ったが、WARNING でいる間に終値では一度も割らなかった警戒足",
            len(never_closed), len(intraday), _rate(len(never_closed), len(intraday)),
            "V1 は降りるが V2/V3 は最後まで降りない形（§9 の候補）",
        ),
        BreakReality(
            "終値で割ったが、元レンジ上限より上にいた警戒足",
            len(above_upper), len(closed), _rate(len(above_upper), len(closed)),
            "V2 は降りるが V3 は降りない形。V2 と V3 が分かれる場所",
        ),
        BreakReality(
            "warning_low と元レンジ上限の両方を終値で割った警戒足",
            len(struct), total, _rate(len(struct), total),
            "EXIT VARIANT 3 のトリガー",
        ),
        BreakReality(
            "日中割れから終値割れまでの営業日数（中央値）",
            len([b for b in closed if b.days_from_intraday_to_close_break is not None]),
            len(closed),
            _days([
                float(b.days_from_intraday_to_close_break) for b in closed
                if b.days_from_intraday_to_close_break is not None
            ]),
            "0 なら同じ日に日中割れと終値割れ（V1 と V2 の EXIT 日が同じ）",
        ),
        BreakReality(
            "終値割れから上限割れまでの営業日数（中央値）",
            len([b for b in struct if b.days_from_close_to_struct_break is not None]),
            len(struct),
            _days([
                float(b.days_from_close_to_struct_break) for b in struct
                if b.days_from_close_to_struct_break is not None
            ]),
            "0 なら同じ日に成立（V2 と V3 の EXIT 日が同じ）",
        ),
        BreakReality(
            "寄りが既に warning_low 以下だった日中割れ",
            len([b for b in intraday if b.intraday_break_gap_open]), len(intraday),
            _rate(len([b for b in intraday if b.intraday_break_gap_open]), len(intraday)),
            "warning_low での約定は仮定できない（§14）",
        ),
        BreakReality(
            "終値割れと reference_high 再突破が同日だった警戒足",
            len([b for b in bs if b.same_day_rehigh_on_close_break]), total,
            _rate(len([b for b in bs if b.same_day_rehigh_on_close_break]), total),
            "既存の REHIGH ロジックを変更しない制約から再高値更新を優先した件（解釈(e)）",
        ),
        BreakReality(
            "日中割れがあったイベント", ev_with(lambda b: b.intraday_break_date),
            len(hold.entered), _rate(ev_with(lambda b: b.intraday_break_date),
                                     len(hold.entered)),
            "イベント単位。分母は仮想ENTRYできた 32 件",
        ),
        BreakReality(
            "終値割れがあったイベント", ev_with(lambda b: b.close_break_date),
            len(hold.entered), _rate(ev_with(lambda b: b.close_break_date),
                                     len(hold.entered)),
        ),
        BreakReality(
            "上限割れまで行ったイベント", ev_with(lambda b: b.struct_break_date),
            len(hold.entered), _rate(ev_with(lambda b: b.struct_break_date),
                                     len(hold.entered)),
        ),
    ]
    return rows


# --- 案ごとの横並び指標（§11 / §12 / §13 / §14）--------------------------------


@dataclass(frozen=True)
class MetricRow:
    """4 案を横並びにするための 1 指標。"""

    section: str
    metric: str
    values: dict[str, str]
    note: str = ""


def _exit_events(run: RuleRun) -> list[sm.SMEvent]:
    """その案の利確候補（warning_low 割れ）で降りたイベント。"""
    return [
        e for e in run.entered
        if e.path_result.exit_type in sm.BREAK_EXIT_TYPES
    ]


def _post_exit_gain(
    ev: sm.SMEvent, win: list[sm.DailyState]
) -> tuple[float | None, date | None]:
    """EXIT の翌営業日以降、HOLD 窓の中で付けた最大含み益。"""
    off = ev.path_result.exit_day_offset
    if ev.entry_price is None or off is None:
        return None, None
    rows = [ds for ds in win if ds.day_offset > off]
    if not rows:
        return None, None
    best = max(rows, key=lambda ds: ds.high)
    return (best.high - ev.entry_price) / ev.entry_price * 100.0, best.date


def _post_exit_gain_vs_exit(
    ev: sm.SMEvent, win: list[sm.DailyState]
) -> float | None:
    """EXIT 価格から見て、その後さらに何 % 上があったか。"""
    px = ev.path_result.exit_reference_price
    off = ev.path_result.exit_day_offset
    if px is None or off is None:
        return None
    rows = [ds for ds in win if ds.day_offset > off]
    if not rows:
        return None
    return (max(ds.high for ds in rows) - px) / px * 100.0


def compare_metrics(runs: dict[str, RuleRun]) -> list[MetricRow]:
    """§11 / §12 / §13 / §14 の指標を 4 案横並びで作る。"""
    rs = [r for r in RULES if r in runs]
    rows: list[MetricRow] = []
    win = hold_window(runs)
    ref = reference_max_gain(runs)

    def add(section: str, metric: str, fn, note: str = "") -> None:
        rows.append(MetricRow(section, metric, {r: fn(runs[r]) for r in rs}, note))

    # --- 前提（4 案で同じであることを見せる）---
    add("前提（4案で同一）", "対象イベント数", lambda r: str(len(r.events)),
        "near.max_position_in_range=0.65 で発生した ENTRY_CANDIDATE。前回と同一")
    add("前提（4案で同一）", "元レンジ上限を終値突破した件数",
        lambda r: _rate(sum(1 for e in r.entered if e.reached_trend_hold),
                        len(r.entered)),
        "突破の判定も WARNING 開始条件（VARIANT A）も 4 案で同じ")
    add("前提（4案で同一）", "最初の警戒足が出た件数",
        lambda r: _rate(sum(1 for e in r.entered if e.warnings), len(r.entered)),
        "最初の警戒足は 4 案で必ず同一。違いはそのあとだけ")
    add("前提（4案で同一）", "警戒足の総本数",
        lambda r: f"{sum(e.warning_count for e in r.entered)} 本",
        "早く降りる案ほど 2 本目以降の警戒足に到達しないので減る")

    # --- EXIT の内訳 ---
    def exit_type_count(r: RuleRun, kinds: tuple[str, ...]) -> str:
        return _rate(
            sum(1 for e in r.entered if e.path_result.exit_type in kinds),
            len(r.entered),
        )

    add("EXIT の内訳", "利確候補（warning_low 割れ）で降りた件数",
        lambda r: exit_type_count(r, sm.BREAK_EXIT_TYPES),
        "参考基準は定義上 0 件")
    add("EXIT の内訳", "初期STOPで降りた件数",
        lambda r: exit_type_count(r, (sm.X_INITIAL_STOP, sm.X_INITIAL_STOP_AFTER_BREAK)),
        "初期STOP = range_lower*0.995。4 案とも同じ水準")
    add("EXIT の内訳", "trail STOPで降りた件数",
        lambda r: exit_type_count(r, (sm.X_TRAIL_STOP,)),
        "trail が成立したうえで引き上げ後の STOP に当たった件")
    add("EXIT の内訳", "追跡終端まで保有継続",
        lambda r: exit_type_count(r, (sm.X_DATA_END,)))
    add("EXIT の内訳", "保有日数の中央値",
        lambda r: _days([
            float(e.path_result.holding_days) for e in r.entered
            if e.path_result.holding_days is not None
        ]),
        "仮想ENTRY日を 1 日目とする")

    # --- §11 STUCK_IN_WARNING ---
    def stuck_eps(r: RuleRun) -> list[sm.WarningEpisode]:
        return [
            w for e in r.entered for w in e.warnings
            if (w.days_held_in_warning_after_low_break or 0) > 0
        ]

    add("§11 STUCK_IN_WARNING", "STUCK_IN_WARNING のイベント件数",
        lambda r: _rate(sum(1 for e in r.entered if "STUCK_IN_WARNING" in e.flags),
                        len(r.entered)),
        "warning_low を日中に割ったのに WARNING に留まった日が 1 日以上あった件。"
        "V2/V3 では意図した猶予でもあるので「多い＝悪い」ではない")
    add("§11 STUCK_IN_WARNING", "滞留した警戒足の本数",
        lambda r: f"{len(stuck_eps(r))} 本")
    add("§11 STUCK_IN_WARNING", "WARNING 滞留日数の中央値",
        lambda r: _days([
            float(w.days_held_in_warning_after_low_break) for w in stuck_eps(r)
        ]),
        "日中割れの日から WARNING を抜けるまでの営業日数")
    add("§11 STUCK_IN_WARNING", "WARNING 滞留日数の最大",
        lambda r: (
            f"{max(w.days_held_in_warning_after_low_break for w in stuck_eps(r))} 日"
            if stuck_eps(r) else "－"
        ))
    add("§11 STUCK_IN_WARNING", "割ったまま何も起きず初期STOPまで戻った件数",
        lambda r: _rate(
            sum(1 for e in r.entered
                if any(b.intraday_break_date is not None for b in e.warning_breaks)
                and e.path_result.exit_type in (
                    sm.X_INITIAL_STOP, sm.X_INITIAL_STOP_AFTER_BREAK)),
            len(r.entered)),
        "前回いちばん不自然だった形。warning_low を割ったのに降りも上げもしなかった件")
    add("§11 STUCK_IN_WARNING", "WARNING 中に一度でも +5% 以上の含み益があった件数",
        lambda r: _rate(
            sum(1 for e in r.entered if e.warnings and (ref.get(key_of(e)) or -99) >= 5.0),
            sum(1 for e in r.entered if e.warnings)),
        "含み益は 4 案共通の分母（降りない解釈での最大含み益）で測るので値は同じ")
    add("§11 STUCK_IN_WARNING", "WARNING 中に一度でも +10% 以上の含み益があった件数",
        lambda r: _rate(
            sum(1 for e in r.entered if e.warnings and (ref.get(key_of(e)) or -99) >= 10.0),
            sum(1 for e in r.entered if e.warnings)))

    # --- §12 利益保持（参考値）---
    def path_vals(r: RuleRun, attr: str) -> list[float]:
        return [
            getattr(e.path_result, attr) for e in r.entered
            if getattr(e.path_result, attr) is not None
        ]

    add("§12 利益保持（参考値）", "仮想EXIT件数（追跡終端を除く）",
        lambda r: _rate(sum(1 for e in r.entered if not e.path_result.still_open),
                        len(r.entered)),
        "約定価格は保証されない。母数 32 件なので順位づけには使わない")
    add("§12 利益保持（参考値）", "仮想リターンの中央値",
        lambda r: _fmt(_median(path_vals(r, "approximate_return_pct"))),
        "主分析の約定はトリガー翌営業日の始値（4 案とも同じ約束）")
    add("§12 利益保持（参考値）", "最大含み益の中央値",
        lambda r: _fmt(_median(path_vals(r, "max_gain_pct"))),
        "その案が実際に保有していた期間内の最大含み益")
    add("§12 利益保持（参考値）", "EXIT時リターンの中央値（勝ち負け別・勝ち）",
        lambda r: _fmt(_median([
            v for v in path_vals(r, "approximate_return_pct") if v > 0
        ])))
    add("§12 利益保持（参考値）", "EXIT時リターンの中央値（勝ち負け別・負け）",
        lambda r: _fmt(_median([
            v for v in path_vals(r, "approximate_return_pct") if v <= 0
        ])))
    add("§12 利益保持（参考値）", "最大含み益からEXITまでの吐き出し幅（中央値）",
        lambda r: (
            f"{_median(path_vals(r, 'giveback_pct')):.2f}pt"
            if path_vals(r, "giveback_pct") else "－"
        ),
        "最大含み益 − 最終リターン")
    for th in (3.0, 5.0):
        add("§12 利益保持（参考値）", f"+{th:.0f}%以上まで伸びた後に損失EXITとなった件数",
            lambda r, t=th: _rate(
                sum(1 for e in r.entered
                    if (ref.get(key_of(e)) or -99) >= t
                    and (e.path_result.approximate_return_pct or 0) < 0),
                sum(1 for e in r.entered if (ref.get(key_of(e)) or -99) >= t)),
            "分母は 4 案共通（降りない解釈での最大含み益）なので件数を直接比べられる")
    add("§12 利益保持（参考値）", "+10%以上まで伸びた後に利益の大半を失った件数",
        lambda r: _rate(
            sum(1 for e in r.entered
                if (ref.get(key_of(e)) or -99) >= 10.0
                and (e.path_result.approximate_return_pct or 0)
                < (ref.get(key_of(e)) or 0) * sm.GIVEBACK_MOST_RATIO),
            sum(1 for e in r.entered if (ref.get(key_of(e)) or -99) >= 10.0)),
        "「大半＝過半」をそのまま数式にした集計上の定義。売買閾値ではない")
    for th in (5.0, 10.0):
        def _cap(r: RuleRun, t=th) -> str:
            vals = [
                (e.path_result.approximate_return_pct / ref[key_of(e)] * 100.0)
                for e in r.entered
                if (ref.get(key_of(e)) or -99) >= t
                and e.path_result.approximate_return_pct is not None
                and ref.get(key_of(e))
            ]
            n = sum(1 for e in r.entered if (ref.get(key_of(e)) or -99) >= t)
            m = _median(vals)
            return f"{m:.0f}%（{n}件）" if m is not None else "－"
        add("§12 利益保持（参考値）",
            f"最大含み益+{th:.0f}%以上のケースで残せた割合（中央値）", _cap,
            "最終リターン ÷ 最大含み益。100% なら天井で降りられたということ")

    # --- §13 EXIT 後にさらに伸びたか ---
    def after(r: RuleRun, th: float) -> str:
        ex = _exit_events(r)
        if not ex:
            return "－（利確候補で降りない）"
        n = sum(
            1 for e in ex
            if (_post_exit_gain_vs_exit(e, win.get(key_of(e), [])) or -99) >= th
        )
        return _rate(n, len(ex))

    for th in (3.0, 5.0, 10.0):
        add("§13 EXIT後の伸び", f"EXIT価格からさらに +{th:.0f}% 以上伸びた件数",
            lambda r, t=th: after(r, t),
            "分母はその案が利確候補で降りた件数。伸びを測る窓は"
            "「降りない解釈でも保有が続いていた期間」に揃えてある")
    add("§13 EXIT後の伸び", "EXIT後の最大上昇率（中央値・EXIT価格比）",
        lambda r: (
            _fmt(_median([
                v for e in _exit_events(r)
                if (v := _post_exit_gain_vs_exit(e, win.get(key_of(e), []))) is not None
            ]))
            if _exit_events(r) else "－（利確候補で降りない）"
        ),
        "厳しすぎる EXIT の副作用を測る主指標")

    # --- §14 ギャップ ---
    def fills(r: RuleRun) -> list[sm.CaseResult]:
        return [
            e.path_result for e in r.entered
            if e.path_result.fill_rule == "next_open"
            and not e.path_result.fill_pending
        ]

    add("§14 ギャップ", "EXITシグナル件数",
        lambda r: (
            f"{len(_exit_events(r))} 件" if _exit_events(r)
            else "0 件（利確候補で降りない）"
        ))
    add("§14 ギャップ", "翌営業日始値がトリガー基準より下だった件数",
        lambda r: (
            _rate(sum(1 for c in fills(r) if (c.fill_gap_pct or 0) < 0), len(fills(r)))
            if fills(r) else "－"
        ),
        "基準は V1 が warning_low、V2/V3 がトリガー日の終値")
    add("§14 ギャップ", "ギャップ率の中央値",
        lambda r: (
            _fmt(_median([c.fill_gap_pct for c in fills(r) if c.fill_gap_pct is not None]))
            if fills(r) else "－"
        ))
    add("§14 ギャップ", "最大の下方ギャップ",
        lambda r: (
            _fmt(min(c.fill_gap_pct for c in fills(r) if c.fill_gap_pct is not None))
            if any(c.fill_gap_pct is not None for c in fills(r)) else "－"
        ))
    add("§14 ギャップ", "翌営業日がなく約定を置けなかった件数",
        lambda r: f"{sum(1 for e in r.entered if e.path_result.fill_pending)} 件",
        "追跡窓（60営業日）の終端でトリガーした件。終値で代用しているが約定の主張ではない")
    add("§14 ギャップ", "参考: V1 を warning_low の STOP 注文で約定させた場合の中央値",
        lambda r: _fmt(_median([
            e.cases[sm.CASE2].approximate_return_pct for e in r.entered
            if e.cases[sm.CASE2].approximate_return_pct is not None
        ])),
        "cases[CASE2] は 4 案とも同じ意味の参考値で、"
        "「最初の日中割れを warning_low で降りられた場合」。主分析とは混ぜない")

    return rows


# --- §9 「割った後に復活した」ケース -------------------------------------------


@dataclass(frozen=True)
class RevivalCase:
    """V1 なら降りるが、V2/V3 は保有を続け、その後に上昇したケース。"""

    code: str
    name: str
    signal_date: date
    rule: str
    warning_date: date
    warning_low: float
    intraday_break_date: date | None
    intraday_break_low: float | None
    intraday_break_close: float | None
    close_recovered: bool
    never_closed_below: bool          # WARNING 中に終値では一度も割らなかった
    v1_exit_date: date | None
    v1_return_pct: float | None
    rule_exit_date: date | None
    rule_exit_type: str
    rule_return_pct: float | None
    diff_pt: float | None             # この案 − V1
    rehigh_after_break: bool
    stop_raised_after_break: bool
    max_gain_after_break_pct: float | None
    max_gain_after_break_date: date | None
    reached_new_high: bool            # 割った後に reference_high を超えた


def extract_revivals(runs: dict[str, RuleRun]) -> list[RevivalCase]:
    """§9。日中割れがノイズだった可能性のあるケースを抜き出す。"""
    v1 = runs.get(sm.BREAK_LOW)
    if v1 is None:
        return []
    v1_map = v1.by_key
    win = hold_window(runs)
    out: list[RevivalCase] = []
    for rule in (sm.BREAK_CLOSE, sm.BREAK_STRUCT):
        run = runs.get(rule)
        if run is None:
            continue
        for k, ev in run.by_key.items():
            e1 = v1_map.get(k)
            if e1 is None or not e1.warning_breaks:
                continue
            # V1 が「日中割れで実際に降りた」件だけを対象にする。
            # 同じ日に STOP へ当たって降りた件は、割れの解釈とは無関係。
            if e1.path_result.exit_type not in sm.BREAK_EXIT_TYPES:
                continue
            b1 = next(
                (b for b in e1.warning_breaks if b.intraday_break_date is not None), None
            )
            if b1 is None or b1.intraday_break_day_offset is None:
                continue
            brk = b1.intraday_break_day_offset
            # この案は同じ日にはまだ降りていないか？
            # 比べるのは**トリガー日**。約定日（翌営業日始値）で比べると、
            # 同じ日に降りた案まで「1 日長く持った」ことになってしまう。
            t = ev.path_result.trigger_day_offset
            if t is None:
                t = ev.path_result.exit_day_offset
            if t is not None and t <= brk:
                continue
            b = next(
                (x for x in ev.warning_breaks
                 if x.warning_day_offset == b1.warning_day_offset), None
            )
            rows = [ds for ds in win.get(k, []) if ds.day_offset > brk]
            entry = ev.entry_price
            best = max(rows, key=lambda ds: ds.high) if rows else None
            post = (
                (best.high - entry) / entry * 100.0
                if best is not None and entry else None
            )
            r1 = e1.path_result.approximate_return_pct
            rr = ev.path_result.approximate_return_pct
            rehigh_after = any(
                w.rehigh_day_offset is not None and w.rehigh_day_offset > brk
                for w in ev.warnings
            )
            out.append(
                RevivalCase(
                    code=ev.code, name=ev.name, signal_date=ev.signal_date, rule=rule,
                    warning_date=b1.warning_date, warning_low=b1.warning_low,
                    intraday_break_date=b1.intraday_break_date,
                    intraday_break_low=b1.intraday_break_low,
                    intraday_break_close=b1.intraday_break_close,
                    close_recovered=b1.intraday_break_close_recovered,
                    never_closed_below=(b.close_break_date is None) if b else False,
                    v1_exit_date=e1.path_result.exit_date,
                    v1_return_pct=r1,
                    rule_exit_date=ev.path_result.exit_date,
                    rule_exit_type=ev.path_result.exit_type,
                    rule_return_pct=rr,
                    diff_pt=(rr - r1) if (rr is not None and r1 is not None) else None,
                    rehigh_after_break=rehigh_after,
                    stop_raised_after_break=any(
                        s.day_offset > brk for s in ev.stop_updates
                    ),
                    max_gain_after_break_pct=post,
                    max_gain_after_break_date=best.date if best else None,
                    reached_new_high=(
                        best is not None and best.high > b1.reference_high
                    ),
                )
            )
    out.sort(key=lambda c: -(c.diff_pt if c.diff_pt is not None else -999))
    return out


# --- §10 「待ちすぎた」ケース --------------------------------------------------


@dataclass(frozen=True)
class WaitedTooLongCase:
    """V1 なら早く撤退できたが、V2/V3 が待った結果、悪化したケース。"""

    code: str
    name: str
    signal_date: date
    rule: str
    warning_date: date
    warning_low: float
    original_range_upper: float
    v1_exit_date: date | None
    v1_return_pct: float | None
    rule_exit_date: date | None
    rule_exit_type: str
    rule_return_pct: float | None
    diff_pt: float | None
    days_waited: int | None
    rule_max_gain_pct: float | None
    rule_giveback_pct: float | None
    back_inside_range: bool          # 元レンジ上限の内側まで終値で戻った
    hit_initial_stop: bool
    gap_pct_at_exit: float | None


def extract_waited_too_long(runs: dict[str, RuleRun]) -> list[WaitedTooLongCase]:
    """§10。特に V3 が遅すぎないかを見る。"""
    v1 = runs.get(sm.BREAK_LOW)
    if v1 is None:
        return []
    v1_map = v1.by_key
    out: list[WaitedTooLongCase] = []
    for rule in (sm.BREAK_CLOSE, sm.BREAK_STRUCT):
        run = runs.get(rule)
        if run is None:
            continue
        for k, ev in run.by_key.items():
            e1 = v1_map.get(k)
            if e1 is None or not e1.warning_breaks:
                continue
            r1 = e1.path_result.approximate_return_pct
            rr = ev.path_result.approximate_return_pct
            if r1 is None or rr is None or rr >= r1:
                continue
            b1 = next(
                (b for b in e1.warning_breaks if b.intraday_break_date is not None), None
            )
            if b1 is None:
                continue
            b = next(
                (x for x in ev.warning_breaks
                 if x.warning_day_offset == b1.warning_day_offset), None
            )
            o1 = e1.path_result.exit_day_offset
            o2 = ev.path_result.exit_day_offset
            pr = ev.path_result
            out.append(
                WaitedTooLongCase(
                    code=ev.code, name=ev.name, signal_date=ev.signal_date, rule=rule,
                    warning_date=b1.warning_date, warning_low=b1.warning_low,
                    original_range_upper=ev.range_upper,
                    v1_exit_date=e1.path_result.exit_date, v1_return_pct=r1,
                    rule_exit_date=pr.exit_date, rule_exit_type=pr.exit_type,
                    rule_return_pct=rr, diff_pt=rr - r1,
                    days_waited=(o2 - o1) if (o1 is not None and o2 is not None) else None,
                    rule_max_gain_pct=pr.max_gain_pct,
                    rule_giveback_pct=pr.giveback_pct,
                    back_inside_range=bool(b and b.struct_break_date is not None),
                    hit_initial_stop=pr.exit_type in (
                        sm.X_INITIAL_STOP, sm.X_INITIAL_STOP_AFTER_BREAK),
                    gap_pct_at_exit=pr.fill_gap_pct,
                )
            )
    out.sort(key=lambda c: (c.diff_pt if c.diff_pt is not None else 0.0))
    return out


# --- §15 どの案が自然だったかの分類 --------------------------------------------

NATURAL_LABELS_JA = {
    "v1_natural": "即EXIT（V1）が最も良かった。待つほど悪化した",
    "v2_natural": "終値確認（V2）まで待つのが最も良かった",
    "v3_natural": "上限割れ（V3）まで待つのが最も良かった",
    "no_break": "warning_low を割らずに決着（3案とも同じ）",
    "same": "どの案でも同じ結果（差が出ない）",
    "all_bad": "どの案でも損失EXIT（warning_low の扱いだけでは救えない）",
}

NATURAL_ORDER = ("v1_natural", "v2_natural", "v3_natural", "same", "no_break", "all_bad")

SHAPE_LABELS_JA = {
    "no_break": "warning_low を割らなかった",
    "recovered_intraday": "日中は割ったが、終値では最後まで割らなかった",
    "held_upper": "終値で警戒安値は割ったが、元レンジ上限は維持した",
    "same_day_all": "日中割れ・終値割れ・上限割れが同じ日に起きた",
    "staged_break": "日中割れ → 終値割れ → 上限割れが段階的に進んだ",
}

SHAPE_ORDER = (
    "recovered_intraday", "held_upper", "staged_break", "same_day_all", "no_break",
)


@dataclass(frozen=True)
class NaturalCase:
    """§15。イベントごとに、どの解釈がいちばん自然だったかの分類。"""

    code: str
    name: str
    signal_date: date
    category: str          # どの案が最も良い結果だったか（§15 の 4 区分）
    shape: str             # チャート上の割れ方（リターンとは独立）
    warning_date: date | None
    intraday_break_date: date | None
    close_break_date: date | None
    struct_break_date: date | None
    hold_return_pct: float | None
    v1_return_pct: float | None
    v2_return_pct: float | None
    v3_return_pct: float | None
    best_rule: str
    spread_pt: float | None          # 最良 − 最悪（案の違いがどれだけ効いたか）
    max_gain_pct: float | None
    note: str


def classify_naturalness(runs: dict[str, RuleRun]) -> list[NaturalCase]:
    """§15。「割れ方の形」と「どの案が良かったか」を **別々に** 付ける。

    形（shape）はチャート上の事実で、リターンを一切見ない。
    区分（category）は結果で、`shape` とは独立に決める。
    片方だけでは誤解を招く（例: 日中割れがノイズだった形でも、
    その後 trail STOP で吐き出せば V1 の方が結果は良い）ので両方を残す。
    """
    hold = runs.get(sm.BREAK_HOLD)
    if hold is None:
        return []
    ref = reference_max_gain(runs)
    out: list[NaturalCase] = []
    for ev in hold.entered:
        k = key_of(ev)
        b = next((x for x in ev.warning_breaks if x.intraday_break_date is not None), None)
        rets = {
            r: (runs[r].by_key[k].path_result.approximate_return_pct
                if r in runs and k in runs[r].by_key else None)
            for r in RULES
        }
        vals = [v for v in (rets[r] for r in EXIT_RULES) if v is not None]
        spread = (max(vals) - min(vals)) if vals else None
        best = max(
            EXIT_RULES, key=lambda r: (rets[r] if rets[r] is not None else -1e9)
        )
        wd = ev.warnings[0].date if ev.warnings else None

        # --- 形（リターンを見ない）---
        if b is None:
            shape = "no_break"
        elif b.close_break_date is None:
            shape = "recovered_intraday"
        elif b.struct_break_date is None:
            shape = "held_upper"
        elif (
            b.intraday_break_day_offset == b.close_break_day_offset
            == b.struct_break_day_offset
        ):
            shape = "same_day_all"
        else:
            shape = "staged_break"

        # --- 区分（結果。形とは独立に決める）---
        if shape == "no_break":
            cat = "no_break"
        elif all((rets[r] if rets[r] is not None else 0) < 0 for r in EXIT_RULES):
            cat = "all_bad"
        elif (spread or 0) < 0.01:
            cat = "same"
        else:
            cat = {sm.BREAK_LOW: "v1_natural", sm.BREAK_CLOSE: "v2_natural",
                   sm.BREAK_STRUCT: "v3_natural"}[best]
        note = SHAPE_LABELS_JA[shape]
        if cat == "all_bad" and (spread or 0) >= 0.01:
            note += f" / 3案とも損失だが {sm.BREAK_RULE_SHORT_JA[best]} が最も傷が浅い"
        out.append(
            NaturalCase(
                code=ev.code, name=ev.name, signal_date=ev.signal_date,
                category=cat, shape=shape, warning_date=wd,
                intraday_break_date=b.intraday_break_date if b else None,
                close_break_date=b.close_break_date if b else None,
                struct_break_date=b.struct_break_date if b else None,
                hold_return_pct=rets[sm.BREAK_HOLD],
                v1_return_pct=rets[sm.BREAK_LOW],
                v2_return_pct=rets[sm.BREAK_CLOSE],
                v3_return_pct=rets[sm.BREAK_STRUCT],
                best_rule=best, spread_pt=spread,
                max_gain_pct=ref.get(k), note=note,
            )
        )
    out.sort(key=lambda c: (NATURAL_ORDER.index(c.category), c.signal_date, c.code))
    return out


# --- CSV 出力（§18）------------------------------------------------------------

EXTRA_EVENT_COLUMNS = [
    "break_rule", "break_rule_label", "break_condition",
    "path_exit_type", "path_exit_date", "path_exit_day_offset",
    "path_trigger_date", "path_fill_rule", "path_fill_pending",
    "path_fill_gap_pct", "path_return_pct", "path_max_gain_pct",
    "path_giveback_pct", "post_exit_max_gain_vs_exit_pct",
    "intraday_break_count", "close_break_count", "struct_break_count",
    "close_break_with_same_day_rehigh",
]


def write_events_csv(runs: dict[str, RuleRun], path: Path) -> Path:
    """4 案分のイベントを縦に並べる（break_rule 列で区別）。"""
    cols = EXTRA_EVENT_COLUMNS + sm.EVENT_COLUMNS
    win = hold_window(runs)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(CSV_NOTE + "\n")
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for rule in RULES:
            run = runs.get(rule)
            if run is None:
                continue
            for ev in run.events:
                row = sm.event_row(ev)
                pr = ev.path_result
                row.update({
                    "break_rule": rule,
                    "break_rule_label": sm.BREAK_RULE_LABELS_JA[rule],
                    "break_condition": sm.BREAK_RULE_CONDITION_JA[rule],
                    "path_exit_type": pr.exit_type,
                    "path_exit_date": _cell(pr.exit_date),
                    "path_exit_day_offset": _cell(pr.exit_day_offset),
                    "path_trigger_date": _cell(pr.trigger_date),
                    "path_fill_rule": pr.fill_rule,
                    "path_fill_pending": _cell(pr.fill_pending),
                    "path_fill_gap_pct": _cell(pr.fill_gap_pct),
                    "path_return_pct": _cell(pr.approximate_return_pct),
                    "path_max_gain_pct": _cell(pr.max_gain_pct),
                    "path_giveback_pct": _cell(pr.giveback_pct),
                    "post_exit_max_gain_vs_exit_pct": _cell(
                        _post_exit_gain_vs_exit(ev, win.get(key_of(ev), []))
                    ),
                    "intraday_break_count": _cell(
                        sum(b.intraday_break_days for b in ev.warning_breaks)),
                    "close_break_count": _cell(
                        sum(b.close_break_days for b in ev.warning_breaks)),
                    "struct_break_count": _cell(
                        sum(b.struct_break_days for b in ev.warning_breaks)),
                    "close_break_with_same_day_rehigh": _cell(
                        ev.close_break_with_same_day_rehigh),
                })
                w.writerow(row)
    return path


def write_warning_breaks_csv(runs: dict[str, RuleRun], path: Path) -> Path:
    """§18 の warning_breaks.csv。警戒足ごとの「割れ方」の観測記録。"""
    cols = ["break_rule", "code", "name", "signal_date", "entry_price", "initial_stop",
            "path_exit_type", "path_exit_date"]
    cols += [f for f in sm.WarningBreak.__dataclass_fields__]
    cols += ["intraday_only", "close_break_held_upper"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(
            "# 警戒足ごとに warning_low をどう割ったかの観測記録。"
            " 観測できる範囲はその案が WARNING に留まっていた期間までなので、"
            "実態集計（§8）は最も長く観測できる HOLD_UNTIL_STOP を分母に使う。"
            " *_next_open_* は約定側の情報で、トリガーの判定には使っていない。\n"
        )
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for rule in RULES:
            run = runs.get(rule)
            if run is None:
                continue
            for ev in run.events:
                for b in ev.warning_breaks:
                    row = {k: _cell(v) for k, v in asdict(b).items()}
                    row.update(
                        break_rule=rule, code=ev.code, name=ev.name,
                        signal_date=ev.signal_date.isoformat(),
                        entry_price=_cell(ev.entry_price),
                        initial_stop=_cell(ev.initial_stop),
                        path_exit_type=ev.path_result.exit_type,
                        path_exit_date=_cell(ev.path_result.exit_date),
                        intraday_only=_cell(b.intraday_only),
                        close_break_held_upper=_cell(b.close_break_held_upper),
                    )
                    w.writerow(row)
    return path


def write_variant_comparison_csv(rows: list[MetricRow], path: Path) -> Path:
    cols = ["section", "metric"] + [f"rule_{r}" for r in RULES] + ["note"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(
            "# 4 案の横並び比較。違うのは「warning_low を割ったあとの処理」だけ。"
            "最も仮想利益が高い案を採用する、という使い方はしない（§20）。\n"
        )
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            row = {"section": r.section, "metric": r.metric, "note": r.note}
            for rule in RULES:
                row[f"rule_{rule}"] = r.values.get(rule, "")
            w.writerow(row)
    return path


def write_summary_csv(runs: dict[str, RuleRun], path: Path) -> Path:
    """案ごとの詳細集計（exit_state_machine.summarize をそのまま流用）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write("# 案ごとの詳細集計。横並びの比較は variant_comparison.csv を見る。\n")
        w = csv.DictWriter(
            f,
            fieldnames=["break_rule", "break_rule_label", "section", "metric",
                        "value", "note"],
        )
        w.writeheader()
        for rule in RULES:
            run = runs.get(rule)
            if run is None:
                continue
            for r in sm.summarize(run.events):
                w.writerow({
                    "break_rule": rule,
                    "break_rule_label": sm.BREAK_RULE_LABELS_JA[rule],
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


def write_break_reality_csv(rows: list[BreakReality], path: Path) -> Path:
    return _write_dataclass_csv(
        rows, path,
        "# §8 warning_low 割れの実態。分母は HOLD_UNTIL_STOP（降りない解釈）の警戒足で、"
        "V1/V2 は自分がそこで降りるため終値割れ・上限割れを観測できないため。",
    )


def write_revival_csv(rows: list[RevivalCase], path: Path) -> Path:
    return _write_dataclass_csv(
        rows, path,
        "# §9 V1 なら降りるが V2/V3 は保有を続けたケース。"
        " max_gain_after_break_pct は日中割れの翌日以降に付けた最大含み益で、"
        "そこで降りられたという意味ではない（人間がチャートで見るための材料）。",
    )


def write_waited_csv(rows: list[WaitedTooLongCase], path: Path) -> Path:
    return _write_dataclass_csv(
        rows, path,
        "# §10 V1 なら早く撤退できたが、V2/V3 が待った結果、悪化したケース。"
        " days_waited は V1 の EXIT からその案の EXIT までの営業日数。",
    )


def write_naturalness_csv(rows: list[NaturalCase], path: Path) -> Path:
    return _write_dataclass_csv(
        rows, path,
        "# §15 イベントごとに、どの解釈がチャート上いちばん自然だったかの分類。"
        " まず「割ったか／終値で割ったか／上限まで割ったか」という形で分け、"
        "そのうえでリターン差を添えている。リターンだけで決めてはいない。",
    )
