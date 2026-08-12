"""`reference_high` の決め方だけを比較する検証（research 専用）。

「WARNING 後に、何をもって“調整終了・上昇再開”と判断するのが自然か」を確かめる。
現行の

    reference_high = 警戒足発生時点までの保有中最高値

が、押し安値確定・trail stop 引き上げを不必要に遅らせていないかを見る。

    RH-A HOLDING_HIGH            max(high) ENTRY〜警戒足当日（現行。比較基準）
    RH-B WARNING_HIGH            warning_high
    RH-C PRE_WARNING_CLOSE_HIGH  max(close) ENTRY〜警戒足前日
    RH-D WARNING_OPEN            warning_open
    RH-E PRE_WARNING_HIGH        max(high) ENTRY〜警戒足前日（§6 の参考VARIANT）

RH-E を 1 案だけ足した理由はモジュール `exit_state_machine` の `RH_EXTRA_RULES`
のコメントに書いた。新しい調整パラメータ（ATR倍率・%・N日高値・移動平均）は
一切使っていない。

--------------------------------------------------------------------------
固定するもの（5 案で完全に同一。今回は一切触らない）
--------------------------------------------------------------------------

    ENTRY ロジック / near.max_position_in_range = 0.65
    初期STOP = range_lower * 0.995
    WARNING 開始条件（研究上の固定基準として VARIANT A）
    warning_low の定義
    warning_low 割れ後の処理（研究上の固定条件として CLOSE_BREAK、翌営業日始値）
    押し安値 = min(low) 警戒足〜再突破日
    trail = 押し安値 * 0.995 / STOP は下方向へ動かさない / 翌営業日から有効
    固定利確なし / 新しい%閾値なし

VARIANT A と CLOSE_BREAK を使うのは「今回の原因を 1 つに絞るため」であって、
どちらも正式採用ではない（前 2 回の検証の結論は保留のまま）。

--------------------------------------------------------------------------
同日に REHIGH と利確候補が両立した場合（§7）
--------------------------------------------------------------------------

日足では先後が決まらないので、どちらかを正解として持ち込まない。
`AMBIGUOUS_REHIGH_EXIT_ORDER` として分離して件数を出し、さらに
**REHIGH 優先 / EXIT 優先の両方**で全体を走らせて、結論が順序に依存するかを見る。

--------------------------------------------------------------------------
このモジュールがやらないこと
--------------------------------------------------------------------------

* trail 成立件数が多い案・仮想利益が高い案を採用する、という結論は出さない（§22）。
* 新しい数値閾値を探索しない。
* ENTRY / WARNING 開始条件 / warning_low 処理 / 初期STOP を変更しない。
* 本番の config.yaml / experimental.yaml / スクリーナーには書き込まない。
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from swing_screener.indicators.swing import detect_swings
from swing_screener.models import OHLCVBar, PriceSeries
from swing_screener.research import exit_state_machine as sm
from swing_screener.research.exit_study import MAX_TRACK_DAYS, _cell, _median, _rate

EventKey = tuple[str, date]

# 今回の比較で固定する条件（研究上の基準。いずれも正式採用ではない）
FIXED_VARIANT = sm.VARIANT_A
FIXED_BREAK_RULE = sm.BREAK_CLOSE

RULES: tuple[str, ...] = sm.RH_RULES

# §14 で「その後さらに伸びたか」を測る水準
POST_EXIT_TARGETS: tuple[float, ...] = (3.0, 5.0, 10.0)
# §13 のバケット
GAIN_BUCKETS: tuple[float, ...] = (5.0, 10.0)

CSV_NOTE = (
    "# 注記: reference_high の決め方だけを 5 案で比較した検証であり、収益バックテストではない。"
    " WARNING 開始条件は VARIANT A、warning_low 割れ後は CLOSE_BREAK（仮想EXITは翌営業日始値）に"
    "固定した研究上の基準であって、いずれも正式ルールではない。"
    " ENTRY ロジック / max_position_in_range=0.65 / initial_stop = range_lower*0.995 /"
    " warning_low の定義 / 押し安値の取り方 / trail = 押し安値*0.995 / STOP を下げないこと は"
    "5 案で完全に同一。新しい%閾値は追加していない。"
)


def key_of(ev: sm.SMEvent) -> EventKey:
    return (ev.code, ev.signal_date)


@dataclass
class RHRun:
    """1 案分の追跡結果。"""

    rule: str
    events: list[sm.SMEvent]
    ambiguous_order: str = sm.AMB_REHIGH

    @property
    def label(self) -> str:
        return sm.RH_RULE_LABELS_JA[self.rule]

    @property
    def short(self) -> str:
        return sm.RH_RULE_SHORT_JA[self.rule]

    @property
    def by_key(self) -> dict[EventKey, sm.SMEvent]:
        return {key_of(e): e for e in self.events}

    @property
    def entered(self) -> list[sm.SMEvent]:
        return [e for e in self.events if e.entry_available]

    @property
    def snapshots(self) -> list[sm.RefHighSnapshot]:
        return [s for e in self.entered for s in e.ref_highs]


def run_rules(
    prepared: list[tuple[dict[str, Any], PriceSeries]],
    exp=None,
    *,
    max_track_days: int = MAX_TRACK_DAYS,
    rules: tuple[str, ...] = RULES,
    variant: str = FIXED_VARIANT,
    break_rule: str = FIXED_BREAK_RULE,
    ambiguous_order: str = sm.AMB_REHIGH,
) -> dict[str, RHRun]:
    """同じイベント群を 5 案で追跡する。案ごとに独立に 1 営業日ずつ再生する。"""
    runs: dict[str, RHRun] = {}
    for rule in rules:
        events = [
            sm.track_event(
                row, series, exp, max_track_days=max_track_days,
                variant=variant, break_rule=break_rule,
                rh_rule=rule, ambiguous_order=ambiguous_order,
            )
            for row, series in prepared
        ]
        sm.apply_classification(events)
        runs[rule] = RHRun(rule=rule, events=events, ambiguous_order=ambiguous_order)
    return runs


# --- 共通の観測窓 -------------------------------------------------------------


@dataclass(frozen=True)
class Frame:
    """イベント 1 件の「案に依存しない物差し」。

    5 案は ENTRY も追跡窓も同じで、違うのは降りる日だけ。
    §13 の分母（どこまで伸びたか）と §14 の EXIT 後の値動きは、案ごとの
    保有期間ではなくこの共通窓で測らないと比較にならない。
    """

    key: EventKey
    bars: list[OHLCVBar]
    entry_index: int
    entry_price: float
    last_off: int                 # 追跡窓の最終オフセット（ENTRY 日 = 0）

    def gain_at(self, off: int) -> float:
        b = self.bars[self.entry_index + off]
        return (b.high - self.entry_price) / self.entry_price * 100.0

    def max_gain(self, lo: int, hi: int) -> float | None:
        lo, hi = max(0, lo), min(self.last_off, hi)
        if lo > hi:
            return None
        return max(self.gain_at(o) for o in range(lo, hi + 1))

    def max_high(self, lo: int, hi: int) -> float | None:
        lo, hi = max(0, lo), min(self.last_off, hi)
        if lo > hi:
            return None
        return max(self.bars[self.entry_index + o].high for o in range(lo, hi + 1))


def build_frames(
    prepared: list[tuple[dict[str, Any], PriceSeries]],
    *,
    max_track_days: int = MAX_TRACK_DAYS,
) -> dict[EventKey, Frame]:
    frames: dict[EventKey, Frame] = {}
    for row, series in prepared:
        bars = list(series.bars)
        i = int(row["signal_index"])
        if i + 1 >= len(bars):
            continue
        entry_index = i + 1
        last_index = min(len(bars) - 1, entry_index + max_track_days - 1)
        frames[(row["code"], date.fromisoformat(row["date"]))] = Frame(
            key=(row["code"], date.fromisoformat(row["date"])),
            bars=bars, entry_index=entry_index,
            entry_price=bars[entry_index].open,
            last_off=last_index - entry_index,
        )
    return frames


def available_max_gain(frames: dict[EventKey, Frame]) -> dict[EventKey, float]:
    """追跡窓の中でその銘柄が実際に見せた最大含み益（5 案共通の分母）。"""
    return {k: (f.max_gain(0, f.last_off) or 0.0) for k, f in frames.items()}


def exit_off(ev: sm.SMEvent) -> int | None:
    return ev.path_result.exit_day_offset


def _fmt(v: float | None, unit: str = "%", *, sign: bool = True) -> str:
    if v is None:
        return "－"
    return f"{v:+.2f}{unit}" if sign else f"{v:.2f}{unit}"


def _days(vals: list[float]) -> str:
    m = _median(vals)
    return f"{m:.1f} 日" if m is not None else "－"


# --- §11 REHIGH の発生状況 -----------------------------------------------------


@dataclass(frozen=True)
class MetricRow:
    """5 案を横に並べる 1 行。"""

    section: str
    metric: str
    values: dict[str, str] = field(default_factory=dict)
    note: str = ""


def _row(section: str, metric: str, per_rule: dict[str, str], note: str = "") -> MetricRow:
    return MetricRow(section=section, metric=metric, values=per_rule, note=note)


def _snap_of(run: RHRun) -> list[sm.RefHighSnapshot]:
    return run.snapshots


def rehigh_metrics(runs: dict[str, RHRun]) -> list[MetricRow]:
    """§11。案ごとの WARNING → REHIGH の成立状況。"""
    rows: list[MetricRow] = []

    def per(f) -> dict[str, str]:
        return {r: f(runs[r]) for r in runs}

    rows.append(_row("§11", "WARNING 発生件数", per(lambda r: f"{len(_snap_of(r))} 件")))
    rows.append(_row(
        "§11", "REHIGH_CONFIRMED 件数",
        per(lambda r: _rate(
            sum(1 for s in _snap_of(r) if s.rehigh_date is not None), len(_snap_of(r))
        )),
        "分母は同じ案での WARNING 件数。案によって WARNING の出方自体が変わる",
    ))
    rows.append(_row(
        "§11", "REHIGH した イベント数",
        per(lambda r: _rate(
            sum(1 for e in r.entered if e.rehigh_count >= 1), len(r.entered)
        )),
    ))
    rows.append(_row(
        "§11", "WARNING → REHIGH までの営業日数 中央値",
        per(lambda r: _days(
            [float(s.days_to_rehigh) for s in _snap_of(r) if s.days_to_rehigh is not None]
        )),
    ))
    for cls, label in (
        ("close_break_first", "warning_low 終値割れが先"),
        ("rehigh_first", "REHIGH が先"),
        ("ambiguous_same_day", "同日に両方成立（順序不明）"),
        ("neither", "どちらにも到達しなかった"),
    ):
        rows.append(_row(
            "§11", f"決着の内訳: {label}",
            per(lambda r, c=cls: _rate(
                sum(1 for s in _snap_of(r) if s.order_class == c), len(_snap_of(r))
            )),
            "同日成立は日足では先後を決められないので、どちらにも寄せずに分離した（§7）"
            if cls == "ambiguous_same_day" else "",
        ))
    return rows


# --- §12 trail の成立 ----------------------------------------------------------


def trail_metrics(runs: dict[str, RHRun]) -> list[MetricRow]:
    rows: list[MetricRow] = []

    def per(f) -> dict[str, str]:
        return {r: f(runs[r]) for r in runs}

    rows.append(_row(
        "§12", "trail stop を1回以上引き上げたイベント数",
        per(lambda r: _rate(
            sum(1 for e in r.entered if e.stop_raise_count >= 1), len(r.entered)
        )),
    ))
    rows.append(_row(
        "§12", "trail stop を2回以上引き上げたイベント数",
        per(lambda r: _rate(
            sum(1 for e in r.entered if e.stop_raise_count >= 2), len(r.entered)
        )),
    ))
    rows.append(_row(
        "§12", "trail stop 更新の総回数",
        per(lambda r: f"{sum(e.stop_raise_count for e in r.entered)} 回"),
    ))
    rows.append(_row(
        "§12", "初回 STOP 引き上げまでの営業日数 中央値",
        per(lambda r: _days([
            float(e.stop_updates[0].day_offset) for e in r.entered if e.stop_updates
        ])),
        "ENTRY 日を 0 とした営業日数。引き上げは翌営業日から有効",
    ))
    rows.append(_row(
        "§12", "initial_stop → 初回 trail stop の引き上げ幅 中央値",
        per(lambda r: _fmt(_median([
            e.stop_updates[0].raise_pct_from_initial_stop
            for e in r.entered if e.stop_updates
        ]))),
    ))
    rows.append(_row(
        "§12", "STOP を ENTRY 価格より上へ持ち上げたイベント数",
        per(lambda r: _rate(
            sum(1 for e in r.entered
                if e.entry_price is not None and e.max_active_stop > e.entry_price),
            len(r.entered),
        )),
        "手数料等を考慮していないので、これがそのまま「損益分岐点より上」の件数"
        "（§12 の参考条件 trail_stop > entry_price）",
    ))
    return rows


# --- §13 利益の吐き出し --------------------------------------------------------


def giveback_metrics(
    runs: dict[str, RHRun], frames: dict[EventKey, Frame]
) -> list[MetricRow]:
    avail = available_max_gain(frames)
    rows: list[MetricRow] = []

    def per(f) -> dict[str, str]:
        return {r: f(runs[r]) for r in runs}

    def rets(run: RHRun) -> list[float]:
        return [
            e.path_result.approximate_return_pct for e in run.entered
            if e.path_result.approximate_return_pct is not None
        ]

    rows.append(_row(
        "§13", "仮想EXITリターン 中央値", per(lambda r: _fmt(_median(rets(r)))),
    ))
    rows.append(_row(
        "§13", "案ごとの最大含み益 中央値",
        per(lambda r: _fmt(_median([
            e.path_result.max_gain_pct for e in r.entered
            if e.path_result.max_gain_pct is not None
        ]))),
        "その案が実際に保有していた期間だけで測った値。案によって窓が違う",
    ))
    rows.append(_row(
        "§13", "追跡窓の最大含み益 中央値（5案共通の分母）",
        per(lambda r: _fmt(_median([
            avail[key_of(e)] for e in r.entered if key_of(e) in avail
        ]))),
        "ENTRY から追跡終端までにその銘柄が見せた最大含み益。案に依存しない",
    ))
    rows.append(_row(
        "§13", "吐き出し幅 中央値（共通分母 − 仮想EXITリターン）",
        per(lambda r: _fmt(_median([
            avail[key_of(e)] - e.path_result.approximate_return_pct
            for e in r.entered
            if key_of(e) in avail and e.path_result.approximate_return_pct is not None
        ]), sign=False)),
        "小さいほど取り切れている",
    ))

    for th in GAIN_BUCKETS:
        def bucket(run: RHRun, th=th) -> list[sm.SMEvent]:
            return [
                e for e in run.entered
                if key_of(e) in avail and avail[key_of(e)] >= th
            ]

        rows.append(_row(
            "§13", f"最大含み益 +{th:.0f}% 以上に到達したイベント数",
            per(lambda r, b=bucket: f"{len(b(r))} 件"),
            "共通分母で数えているので 5 案とも同じ件数になる",
        ))
        rows.append(_row(
            "§13", f"　うち trail stop を引き上げられた件数（+{th:.0f}%）",
            per(lambda r, b=bucket: _rate(
                sum(1 for e in b(r) if e.stop_raise_count >= 1), len(b(r))
            )),
        ))
        rows.append(_row(
            "§13", f"　EXIT 時に残せた利益 中央値（+{th:.0f}%）",
            per(lambda r, b=bucket: _fmt(_median([
                e.path_result.approximate_return_pct for e in b(r)
                if e.path_result.approximate_return_pct is not None
            ]))),
        ))
        rows.append(_row(
            "§13", f"　最大利益のうち残せた割合 中央値（+{th:.0f}%）",
            per(lambda r, b=bucket: _fmt(_median([
                e.path_result.approximate_return_pct / avail[key_of(e)] * 100.0
                for e in b(r)
                if e.path_result.approximate_return_pct is not None
                and avail.get(key_of(e))
            ]), sign=False)),
            "共通分母に対する割合。100% なら天井で降りられたということ",
        ))
    return rows


# --- §14 早すぎる trail の副作用 -----------------------------------------------


@dataclass(frozen=True)
class EarlyTrailCase:
    """trail stop で降りた後さらに上昇したケース（§14）。"""

    rh_rule: str
    rh_rule_label: str
    code: str
    name: str
    signal_date: date
    entry_date: date | None
    entry_price: float | None
    exit_type: str
    exit_date: date | None
    exit_day_offset: int | None
    exit_return_pct: float | None
    rehigh_date: date | None
    new_swing_low_candidate: float | None
    trail_stop: float | None
    post_exit_max_gain_pct: float | None      # EXIT 後の最大含み益（ENTRY 比）
    post_exit_rise_pct: float | None          # EXIT 価格からの上昇率
    rose3_after_exit: bool
    rose5_after_exit: bool
    rose10_after_exit: bool
    exceeded_holding_high: bool               # EXIT 時点の保有中最高値を更新した
    holding_high_at_exit: float | None
    post_exit_max_high: float | None
    note: str = ""


def extract_early_trail(
    runs: dict[str, RHRun], frames: dict[EventKey, Frame]
) -> list[EarlyTrailCase]:
    """trail STOP で降りた後の値動きを案ごとに集める。

    EXIT 後の窓は **追跡窓の終端まで** で、案によらず同じ。
    「降りなければ取れた」ではなく「降りた後どう動いたか」の事実を残す。
    """
    out: list[EarlyTrailCase] = []
    for rule in RULES:
        run = runs.get(rule)
        if run is None:
            continue
        for ev in run.entered:
            res = ev.path_result
            if res.exit_type != sm.X_TRAIL_STOP or res.exit_day_offset is None:
                continue
            f = frames.get(key_of(ev))
            if f is None or res.exit_day_offset >= f.last_off:
                continue
            off = res.exit_day_offset
            post_gain = f.max_gain(off + 1, f.last_off)
            post_high = f.max_high(off + 1, f.last_off)
            hh = f.max_high(0, off)
            rise = (
                (post_high - res.exit_reference_price) / res.exit_reference_price * 100.0
                if post_high is not None and res.exit_reference_price else None
            )
            rh = next(
                (s for s in reversed(ev.ref_highs) if s.rehigh_date is not None), None
            )
            out.append(EarlyTrailCase(
                rh_rule=rule, rh_rule_label=sm.RH_RULE_SHORT_JA[rule],
                code=ev.code, name=ev.name, signal_date=ev.signal_date,
                entry_date=ev.entry_date, entry_price=ev.entry_price,
                exit_type=res.exit_type, exit_date=res.exit_date,
                exit_day_offset=off,
                exit_return_pct=res.approximate_return_pct,
                rehigh_date=rh.rehigh_date if rh else None,
                new_swing_low_candidate=rh.new_swing_low_candidate if rh else None,
                trail_stop=res.exit_reference_price,
                post_exit_max_gain_pct=post_gain,
                post_exit_rise_pct=rise,
                rose3_after_exit=bool(rise is not None and rise >= 3.0),
                rose5_after_exit=bool(rise is not None and rise >= 5.0),
                rose10_after_exit=bool(rise is not None and rise >= 10.0),
                exceeded_holding_high=bool(
                    post_high is not None and hh is not None and post_high > hh
                ),
                holding_high_at_exit=hh,
                post_exit_max_high=post_high,
                note="EXIT 後の窓は追跡終端まで。案によらず同じ物差し",
            ))
    return out


def early_trail_metrics(
    runs: dict[str, RHRun], cases: list[EarlyTrailCase]
) -> list[MetricRow]:
    by_rule: dict[str, list[EarlyTrailCase]] = {r: [] for r in RULES}
    for c in cases:
        by_rule.setdefault(c.rh_rule, []).append(c)
    rows: list[MetricRow] = []

    def per(f) -> dict[str, str]:
        return {r: f(runs[r]) for r in runs}

    rows.append(_row(
        "§14", "trail STOP で降りたイベント数",
        per(lambda r: _rate(
            sum(1 for e in r.entered if e.path_result.exit_type == sm.X_TRAIL_STOP),
            len(r.entered),
        )),
    ))
    for th, attr in (
        (3.0, "rose3_after_exit"), (5.0, "rose5_after_exit"), (10.0, "rose10_after_exit"),
    ):
        rows.append(_row(
            "§14", f"　うち EXIT 後さらに +{th:.0f}% 以上上昇",
            per(lambda r, a=attr: _rate(
                sum(1 for c in by_rule.get(r.rule, []) if getattr(c, a)),
                len(by_rule.get(r.rule, [])),
            )),
            "早く上げすぎた trail で、正常な調整で降りた疑いのあるケース",
        ))
    rows.append(_row(
        "§14", "　うち EXIT 後に保有中最高値を更新",
        per(lambda r: _rate(
            sum(1 for c in by_rule.get(r.rule, []) if c.exceeded_holding_high),
            len(by_rule.get(r.rule, [])),
        )),
        "降りた後で上昇が続いていた（調整が終わっていなかった）ことの直接的な証拠",
    ))
    rows.append(_row(
        "§14", "　EXIT 後の上昇率 中央値",
        per(lambda r: _fmt(_median([
            c.post_exit_rise_pct for c in by_rule.get(r.rule, [])
            if c.post_exit_rise_pct is not None
        ]))),
    ))
    return rows


# --- §15 reference_high 同士の位置関係 -----------------------------------------


@dataclass(frozen=True)
class PositionRow:
    """§15。各案が実際どの程度違うハードルだったか。"""

    metric: str
    value: str
    count: str = ""
    note: str = ""


def position_rows(runs: dict[str, RHRun]) -> list[PositionRow]:
    """RH-A の警戒足を母集団にして 5 案の水準差を数える。

    案が違うと REHIGH の有無が変わり、その後に出る警戒足も変わってしまうので、
    「同じ警戒足の上で 5 案を並べる」には母集団を 1 つに固定する必要がある。
    現行案である RH-A の警戒足を使う。
    """
    base = runs.get(sm.RH_HOLDING)
    if base is None:
        return []
    snaps = base.snapshots
    n = len(snaps)
    rows: list[PositionRow] = [
        PositionRow("母集団（RH-A の警戒足）", f"{n} 件", "",
                    "5 案の水準はすべて同じ警戒足の当日までの足から決まる"),
    ]
    if not n:
        return rows

    rows.append(PositionRow(
        "RH-A と RH-B が同じ（警戒陰線自身が保有中最高値を作った）",
        _rate(sum(1 for s in snaps if s.a_equals_b), n), "",
        "この場合、RH-B に変えても「警戒足の天井をもう一度抜く」条件は緩まない",
    ))
    for label, attr in (
        ("RH-B 警戒足高値", "rh_b_vs_a_pct"),
        ("RH-C 前日までの終値高値", "rh_c_vs_a_pct"),
        ("RH-D 警戒足始値", "rh_d_vs_a_pct"),
        ("RH-E 前日までの高値", "rh_e_vs_a_pct"),
    ):
        vals = [getattr(s, attr) for s in snaps]
        below = sum(1 for v in vals if v < -1e-9)
        rows.append(PositionRow(
            f"{label} が RH-A より低い幅 中央値",
            _fmt(_median(vals)), _rate(below, n),
            "RH-A を 0 とした差。負なら RH-A より低いハードル",
        ))
    for rule in RULES:
        rows.append(PositionRow(
            f"5 案の中で最も低かった: {sm.RH_RULE_SHORT_JA[rule]}",
            _rate(sum(1 for s in snaps if s.lowest_rule == rule), n),
        ))
    c_below_b = sum(1 for s in snaps if s.rh_c_vs_a_pct < s.rh_b_vs_a_pct - 1e-9)
    c_below_d = sum(1 for s in snaps if s.rh_c_vs_a_pct < s.rh_d_vs_a_pct - 1e-9)
    rows.append(PositionRow(
        "RH-C が RH-B より低い", _rate(c_below_b, n), "",
        "終値ベースの高値が警戒足の高値より下だったケース",
    ))
    rows.append(PositionRow(
        "RH-C が RH-D より低い", _rate(c_below_d, n), "",
        "終値ベースの高値が警戒足の始値より下だったケース",
    ))
    spread = [
        max(s.holding_high, s.warning_high, s.pre_warning_close_high,
            s.warning_open, s.pre_warning_high)
        - min(s.holding_high, s.warning_high, s.pre_warning_close_high,
              s.warning_open, s.pre_warning_high)
        for s in snaps
    ]
    rows.append(PositionRow(
        "5 案の水準差（最高 − 最低）の ENTRY 比 中央値",
        _fmt(_median([
            d / s.entry_price * 100.0 for d, s in zip(spread, snaps) if s.entry_price
        ]), sign=False), "",
        "案の違いが価格として何%ぶんのハードル差になっているか",
    ))
    return rows


# --- §21 Q9 STUCK / 初期STOP ---------------------------------------------------


def stuck_metrics(runs: dict[str, RHRun]) -> list[MetricRow]:
    rows: list[MetricRow] = []

    def per(f) -> dict[str, str]:
        return {r: f(runs[r]) for r in runs}

    rows.append(_row(
        "§21-9", "STUCK_IN_WARNING",
        per(lambda r: _rate(
            sum(1 for e in r.entered if "STUCK_IN_WARNING" in e.flags), len(r.entered)
        )),
        "warning_low を日中に割ったのに WARNING に留まった日があったイベント",
    ))
    rows.append(_row(
        "§21-9", "INITIAL_STOP_EXIT_AFTER_BREAKOUT",
        per(lambda r: _rate(
            sum(1 for e in r.entered
                if e.path_result.exit_type == sm.X_INITIAL_STOP_AFTER_BREAK),
            len(r.entered),
        )),
        "上限突破後、trail を一度も上げられないまま初期STOPまで戻った",
    ))
    rows.append(_row(
        "§21-9", "EXIT 種別: 利確候補（終値割れ）",
        per(lambda r: _rate(
            sum(1 for e in r.entered
                if e.path_result.exit_type in sm.BREAK_EXIT_TYPES),
            len(r.entered),
        )),
    ))
    rows.append(_row(
        "§21-9", "EXIT 種別: trail STOP",
        per(lambda r: _rate(
            sum(1 for e in r.entered if e.path_result.exit_type == sm.X_TRAIL_STOP),
            len(r.entered),
        )),
    ))
    rows.append(_row(
        "§21-9", "EXIT 種別: 初期STOP（突破前を含む）",
        per(lambda r: _rate(
            sum(1 for e in r.entered
                if e.path_result.exit_type in (
                    sm.X_INITIAL_STOP, sm.X_INITIAL_STOP_AFTER_BREAK
                )),
            len(r.entered),
        )),
    ))
    rows.append(_row(
        "§7", "同日に REHIGH と利確候補（順序不明）",
        per(lambda r: f"{sum(e.ambiguous_rehigh_exit_count for e in r.entered)} 日"),
        "どちらが先かは日足では決められないため、順序を決めずに分離した",
    ))
    return rows


# --- §19 fractal との参考比較 --------------------------------------------------


@dataclass(frozen=True)
class FractalRow:
    """§19。押し安値の確定タイミングを既存 fractal と並べる（成績競争はしない）。"""

    rh_rule: str
    rh_rule_label: str
    ours_confirmed: int             # 今回の方法で押し安値確定
    both_recognized: int            # fractal でも同じ安値を押し安値と認識
    ours_only: int                  # 今回のみ
    fractal_only: int               # fractal のみ（今回は REHIGH せず確定できなかった）
    ours_first: int
    same_day: int
    fractal_first: int
    median_lead_days: str           # 今回の確定が fractal より何営業日早いか
    note: str = ""


def fractal_rows(
    runs: dict[str, RHRun],
    frames: dict[EventKey, Frame],
    exp,
) -> list[FractalRow]:
    """押し安値の確定タイミングを既存 fractal と比較する（参考）。

    `fractal_only` は「今回は REHIGH に到達せず押し安値を確定できなかったが、
    その WARNING 期間の中に fractal なら押し安値として認識する安値があった」件数。
    fractal 側は右側 pivot が揃うまで確定しないので、`_fractal_confirm_index` と
    同じやり方で確定日まで求める。状態機械には一切影響しない後段の比較。
    """
    rows: list[FractalRow] = []
    for rule in RULES:
        run = runs.get(rule)
        if run is None:
            continue
        ours = both = ours_only = fractal_only = 0
        first_ours = same = first_frac = 0
        leads: list[float] = []
        for ev in run.entered:
            f = frames.get(key_of(ev))
            for w, s in zip(ev.warnings, ev.ref_highs):
                if w.new_swing_low_candidate is not None:
                    ours += 1
                    if w.fractal_is_same_low:
                        both += 1
                        fo = w.fractal_confirm_day_offset
                        ro = s.rehigh_day_offset
                        if fo is not None and ro is not None:
                            if ro < fo:
                                first_ours += 1
                            elif ro == fo:
                                same += 1
                            else:
                                first_frac += 1
                            leads.append(float(fo - ro))
                    else:
                        ours_only += 1
                elif f is not None and exp is not None:
                    if _fractal_low_in_window(f, s, exp):
                        fractal_only += 1
        rows.append(FractalRow(
            rh_rule=rule, rh_rule_label=sm.RH_RULE_SHORT_JA[rule],
            ours_confirmed=ours, both_recognized=both, ours_only=ours_only,
            fractal_only=fractal_only,
            ours_first=first_ours, same_day=same, fractal_first=first_frac,
            median_lead_days=_days(leads),
            note="成績競争ではなく、確定タイミングが自然かどうかの参考比較（§19）",
        ))
    return rows


def _fractal_low_in_window(
    frame: Frame, snap: sm.RefHighSnapshot, exp
) -> bool:
    """その WARNING 期間の安値を fractal なら押し安値と認めたか（比較用）。"""
    lo = frame.entry_index + snap.warning_day_offset
    hi = min(
        frame.entry_index + snap.warning_day_offset + max(snap.observed_days, 1),
        frame.entry_index + frame.last_off,
    )
    upto = min(len(frame.bars) - 1, frame.entry_index + frame.last_off)
    lows = detect_swings(frame.bars[: upto + 1], exp)[1]
    return any(lo <= sp.index <= hi for sp in lows)


# --- §7 の感度: 同日順序を逆にしたときの差分 -----------------------------------


@dataclass(frozen=True)
class AmbiguitySensitivity:
    """同日成立の扱いを逆にしたら結論が変わるか（§7）。"""

    rh_rule: str
    rh_rule_label: str
    ambiguous_days: int
    events_affected: int
    events_changed: int             # 実際に仮想EXITが変わったイベント数
    rehigh_first_median_return: str
    exit_first_median_return: str
    note: str = ""


def ambiguity_sensitivity(
    runs_rehigh: dict[str, RHRun], runs_exit: dict[str, RHRun]
) -> list[AmbiguitySensitivity]:
    out: list[AmbiguitySensitivity] = []
    for rule in RULES:
        a, b = runs_rehigh.get(rule), runs_exit.get(rule)
        if a is None or b is None:
            continue
        bk = b.by_key
        affected = changed = 0
        for ev in a.entered:
            other = bk.get(key_of(ev))
            if other is None:
                continue
            if ev.ambiguous_rehigh_exit_count:
                affected += 1
            if (
                ev.path_result.exit_date != other.path_result.exit_date
                or ev.path_result.exit_type != other.path_result.exit_type
            ):
                changed += 1
        out.append(AmbiguitySensitivity(
            rh_rule=rule, rh_rule_label=sm.RH_RULE_SHORT_JA[rule],
            ambiguous_days=sum(e.ambiguous_rehigh_exit_count for e in a.entered),
            events_affected=affected, events_changed=changed,
            rehigh_first_median_return=_fmt(_median([
                e.path_result.approximate_return_pct for e in a.entered
                if e.path_result.approximate_return_pct is not None
            ])),
            exit_first_median_return=_fmt(_median([
                e.path_result.approximate_return_pct for e in b.entered
                if e.path_result.approximate_return_pct is not None
            ])),
            note="どちらが正しい順序かは決めていない。両方走らせた結果を並べただけ",
        ))
    return out


# --- 代表ケースの選定材料 ------------------------------------------------------


@dataclass(frozen=True)
class CaseRow:
    """イベント × 案 の結果を 1 行にしたもの（代表チャートの選定と §16 用）。"""

    code: str
    name: str
    signal_date: date
    rh_rule: str
    rh_rule_label: str
    reference_high: float | None
    warning_date: date | None
    warning_low: float | None
    rehigh_date: date | None
    days_to_rehigh: int | None
    stop_raises: int
    max_active_stop: float | None
    exit_type: str
    exit_date: date | None
    exit_return_pct: float | None
    available_max_gain_pct: float | None
    kept_ratio_pct: float | None
    order_class: str
    flags: str


def case_rows(
    runs: dict[str, RHRun], frames: dict[EventKey, Frame]
) -> list[CaseRow]:
    avail = available_max_gain(frames)
    out: list[CaseRow] = []
    for rule in RULES:
        run = runs.get(rule)
        if run is None:
            continue
        for ev in run.entered:
            s = ev.ref_highs[0] if ev.ref_highs else None
            rh = next(
                (x for x in ev.ref_highs if x.rehigh_date is not None), None
            )
            res = ev.path_result
            a = avail.get(key_of(ev))
            out.append(CaseRow(
                code=ev.code, name=ev.name, signal_date=ev.signal_date,
                rh_rule=rule, rh_rule_label=sm.RH_RULE_SHORT_JA[rule],
                reference_high=s.reference_high if s else None,
                warning_date=s.warning_date if s else None,
                warning_low=s.warning_low if s else None,
                rehigh_date=rh.rehigh_date if rh else None,
                days_to_rehigh=rh.days_to_rehigh if rh else None,
                stop_raises=ev.stop_raise_count,
                max_active_stop=ev.max_active_stop,
                exit_type=res.exit_type, exit_date=res.exit_date,
                exit_return_pct=res.approximate_return_pct,
                available_max_gain_pct=a,
                kept_ratio_pct=(
                    res.approximate_return_pct / a * 100.0
                    if a and res.approximate_return_pct is not None else None
                ),
                order_class=s.order_class if s else "",
                flags=";".join(ev.flags),
            ))
    return out


# --- CSV 出力 ------------------------------------------------------------------


EXTRA_EVENT_COLUMNS = [
    "rh_rule", "rh_rule_label", "rh_condition", "ambiguous_order",
    "path_exit_type", "path_exit_date", "path_exit_day_offset",
    "path_return_pct", "path_max_gain_pct", "path_giveback_pct",
    "available_max_gain_pct", "kept_ratio_pct",
    "warning_count", "rehigh_count", "stop_raise_count",
    "max_active_stop", "stop_above_entry", "ambiguous_rehigh_exit_days",
]


def write_events_csv(
    runs: dict[str, RHRun], frames: dict[EventKey, Frame], path: Path
) -> Path:
    avail = available_max_gain(frames)
    cols = EXTRA_EVENT_COLUMNS + sm.EVENT_COLUMNS
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
                res = ev.path_result
                a = avail.get(key_of(ev))
                row.update(
                    rh_rule=rule,
                    rh_rule_label=sm.RH_RULE_LABELS_JA[rule],
                    rh_condition=sm.RH_RULE_CONDITION_JA[rule],
                    ambiguous_order=ev.ambiguous_order,
                    path_exit_type=res.exit_type,
                    path_exit_date=_cell(res.exit_date),
                    path_exit_day_offset=_cell(res.exit_day_offset),
                    path_return_pct=_cell(res.approximate_return_pct),
                    path_max_gain_pct=_cell(res.max_gain_pct),
                    path_giveback_pct=_cell(res.giveback_pct),
                    available_max_gain_pct=_cell(a),
                    kept_ratio_pct=_cell(
                        res.approximate_return_pct / a * 100.0
                        if a and res.approximate_return_pct is not None else None
                    ),
                    warning_count=ev.warning_count,
                    rehigh_count=ev.rehigh_count,
                    stop_raise_count=ev.stop_raise_count,
                    max_active_stop=_cell(ev.max_active_stop),
                    stop_above_entry=_cell(
                        ev.entry_price is not None
                        and ev.max_active_stop > ev.entry_price
                    ),
                    ambiguous_rehigh_exit_days=ev.ambiguous_rehigh_exit_count,
                )
                w.writerow(row)
    return path


def write_rehigh_events_csv(runs: dict[str, RHRun], path: Path) -> Path:
    cols = ["code", "name", "signal_date", "rh_rule", "rh_rule_label"]
    cols += list(sm.RefHighSnapshot.__dataclass_fields__)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(
            CSV_NOTE + " holding_high / warning_high / pre_warning_close_high /"
            " warning_open / pre_warning_high は 5 案の水準を同じ警戒足の上で"
            "並べた観測値で、実際に使ったのは rh_rule の 1 つだけ。"
            " order_class=ambiguous_same_day は日足では先後を決められない日（§7）。\n"
        )
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for rule in RULES:
            run = runs.get(rule)
            if run is None:
                continue
            for ev in run.entered:
                for s in ev.ref_highs:
                    row = {k: _cell(v) for k, v in asdict(s).items()}
                    row.update(
                        code=ev.code, name=ev.name,
                        signal_date=ev.signal_date.isoformat(),
                        rh_rule=rule, rh_rule_label=sm.RH_RULE_SHORT_JA[rule],
                    )
                    w.writerow(row)
    return path


def write_stop_updates_csv(runs: dict[str, RHRun], path: Path) -> Path:
    cols = ["code", "name", "signal_date", "rh_rule", "rh_rule_label",
            "entry_price", "initial_stop"]
    cols += list(sm.StopUpdate.__dataclass_fields__)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(
            CSV_NOTE + " STOP は上方向にしか動かさず、引き上げは翌営業日から有効。"
            " effective_from_* 列でそれを確認できる。\n"
        )
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for rule in RULES:
            run = runs.get(rule)
            if run is None:
                continue
            for ev in run.entered:
                for su in ev.stop_updates:
                    row = {k: _cell(v) for k, v in asdict(su).items()}
                    row.update(
                        code=ev.code, name=ev.name,
                        signal_date=ev.signal_date.isoformat(),
                        rh_rule=rule, rh_rule_label=sm.RH_RULE_SHORT_JA[rule],
                        entry_price=_cell(ev.entry_price),
                        initial_stop=_cell(ev.initial_stop),
                    )
                    w.writerow(row)
    return path


def write_ambiguous_csv(runs: dict[str, RHRun], path: Path) -> Path:
    """§7。同日に REHIGH と利確候補が両立した警戒足だけを抜き出す。"""
    cols = [
        "code", "name", "signal_date", "rh_rule", "rh_rule_label",
        "warning_date", "warning_day_offset", "warning_low", "reference_high",
        "close_break_date", "close_break_close", "rehigh_date", "rehigh_high",
        "ambiguous_open_above_reference", "ambiguous_resolved_as",
        "this_run_exit_type", "this_run_exit_date", "this_run_return_pct",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(
            CSV_NOTE + " ここに載る日は「終値で warning_low 割れ」と"
            "「high > reference_high」が同じ日に成立した日で、日足では先後を決められない。"
            " ambiguous_resolved_as はこの実行でどちらを先に採ったかを示すだけで、"
            "正しい順序という主張ではない（比較側で逆順も走らせている）。"
            " ambiguous_open_above_reference は参考情報で、順序の判定には使っていない。\n"
        )
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for rule in RULES:
            run = runs.get(rule)
            if run is None:
                continue
            for ev in run.entered:
                res = ev.path_result
                for s in ev.ref_highs:
                    if not s.order_ambiguous:
                        continue
                    w.writerow({
                        "code": ev.code, "name": ev.name,
                        "signal_date": ev.signal_date.isoformat(),
                        "rh_rule": rule,
                        "rh_rule_label": sm.RH_RULE_SHORT_JA[rule],
                        "warning_date": _cell(s.warning_date),
                        "warning_day_offset": s.warning_day_offset,
                        "warning_low": _cell(s.warning_low),
                        "reference_high": _cell(s.reference_high),
                        "close_break_date": _cell(s.close_break_date),
                        "close_break_close": _cell(s.close_break_close),
                        "rehigh_date": _cell(s.rehigh_date),
                        "rehigh_high": _cell(s.rehigh_high),
                        "ambiguous_open_above_reference": _cell(
                            s.ambiguous_open_above_reference
                        ),
                        "ambiguous_resolved_as": s.ambiguous_resolved_as,
                        "this_run_exit_type": res.exit_type,
                        "this_run_exit_date": _cell(res.exit_date),
                        "this_run_return_pct": _cell(res.approximate_return_pct),
                    })
    return path


def write_variant_comparison_csv(rows: list[MetricRow], path: Path) -> Path:
    cols = ["section", "metric"] + [sm.RH_RULE_SHORT_JA[r] for r in RULES] + ["note"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(CSV_NOTE + "\n")
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in rows:
            out = {"section": row.section, "metric": row.metric, "note": row.note}
            for r in RULES:
                out[sm.RH_RULE_SHORT_JA[r]] = row.values.get(r, "－")
            w.writerow(out)
    return path


def write_summary_csv(
    runs: dict[str, RHRun], frames: dict[EventKey, Frame], path: Path
) -> Path:
    avail = available_max_gain(frames)
    cols = [
        "rh_rule", "rh_rule_label", "rh_condition", "events", "entered",
        "warnings", "rehigh_confirmed", "rehigh_rate",
        "median_days_to_rehigh", "stop_raised_events", "stop_raise_total",
        "stop_above_entry_events", "trail_stop_exits", "break_exits",
        "initial_stop_exits", "stuck_in_warning",
        "median_return_pct", "median_available_max_gain_pct",
        "median_giveback_pct", "ambiguous_same_day_warnings",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(CSV_NOTE + "\n")
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for rule in RULES:
            run = runs.get(rule)
            if run is None:
                continue
            snaps = run.snapshots
            ent = run.entered
            rets = [
                e.path_result.approximate_return_pct for e in ent
                if e.path_result.approximate_return_pct is not None
            ]
            w.writerow({
                "rh_rule": rule,
                "rh_rule_label": sm.RH_RULE_LABELS_JA[rule],
                "rh_condition": sm.RH_RULE_CONDITION_JA[rule],
                "events": len(run.events), "entered": len(ent),
                "warnings": len(snaps),
                "rehigh_confirmed": sum(
                    1 for s in snaps if s.rehigh_date is not None
                ),
                "rehigh_rate": _rate(
                    sum(1 for s in snaps if s.rehigh_date is not None), len(snaps)
                ),
                "median_days_to_rehigh": _days([
                    float(s.days_to_rehigh) for s in snaps
                    if s.days_to_rehigh is not None
                ]),
                "stop_raised_events": sum(1 for e in ent if e.stop_raise_count >= 1),
                "stop_raise_total": sum(e.stop_raise_count for e in ent),
                "stop_above_entry_events": sum(
                    1 for e in ent
                    if e.entry_price is not None and e.max_active_stop > e.entry_price
                ),
                "trail_stop_exits": sum(
                    1 for e in ent if e.path_result.exit_type == sm.X_TRAIL_STOP
                ),
                "break_exits": sum(
                    1 for e in ent if e.path_result.exit_type in sm.BREAK_EXIT_TYPES
                ),
                "initial_stop_exits": sum(
                    1 for e in ent
                    if e.path_result.exit_type in (
                        sm.X_INITIAL_STOP, sm.X_INITIAL_STOP_AFTER_BREAK
                    )
                ),
                "stuck_in_warning": sum(
                    1 for e in ent if "STUCK_IN_WARNING" in e.flags
                ),
                "median_return_pct": _cell(_median(rets)),
                "median_available_max_gain_pct": _cell(_median([
                    avail[key_of(e)] for e in ent if key_of(e) in avail
                ])),
                "median_giveback_pct": _cell(_median([
                    avail[key_of(e)] - e.path_result.approximate_return_pct
                    for e in ent
                    if key_of(e) in avail
                    and e.path_result.approximate_return_pct is not None
                ])),
                "ambiguous_same_day_warnings": sum(
                    1 for s in snaps if s.order_ambiguous
                ),
            })
    return path


def _write_dataclass_csv(rows: list[Any], path: Path, note: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        with path.open("w", encoding="utf-8", newline="") as f:
            f.write(note + "\n")
            f.write("# 該当なし\n")
        return path
    cols = list(type(rows[0]).__dataclass_fields__)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(note + "\n")
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: _cell(v) for k, v in asdict(r).items()})
    return path


def write_early_trail_csv(rows: list[EarlyTrailCase], path: Path) -> Path:
    return _write_dataclass_csv(
        rows, path,
        CSV_NOTE + " trail STOP で降りた後の値動き（§14）。"
        "EXIT 後の窓は追跡終端までで、5 案とも同じ物差し。",
    )


def write_position_csv(rows: list[PositionRow], path: Path) -> Path:
    return _write_dataclass_csv(
        rows, path,
        CSV_NOTE + " 各案が実際どの程度違うハードルだったか（§15）。"
        "母集団は RH-A の警戒足に固定している。",
    )


def write_fractal_csv(rows: list[FractalRow], path: Path) -> Path:
    return _write_dataclass_csv(
        rows, path,
        CSV_NOTE + " 既存 fractal との参考比較（§19）。成績競争ではなく、"
        "押し安値の確定タイミングが自然かどうかを見るためのもの。",
    )


def write_sensitivity_csv(rows: list[AmbiguitySensitivity], path: Path) -> Path:
    return _write_dataclass_csv(
        rows, path,
        CSV_NOTE + " 同日成立の扱い（REHIGH 優先 / EXIT 優先）を逆にしたときの差分（§7）。"
        "どちらが正しい順序かは決めていない。",
    )


def write_case_rows_csv(rows: list[CaseRow], path: Path) -> Path:
    return _write_dataclass_csv(
        rows, path,
        CSV_NOTE + " イベント × 案 の結果一覧（代表チャートの選定材料）。",
    )


def all_metrics(
    runs: dict[str, RHRun],
    frames: dict[EventKey, Frame],
    early: list[EarlyTrailCase],
) -> list[MetricRow]:
    return (
        rehigh_metrics(runs)
        + trail_metrics(runs)
        + giveback_metrics(runs, frames)
        + early_trail_metrics(runs, early)
        + stuck_metrics(runs)
    )
