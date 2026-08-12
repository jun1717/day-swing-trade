"""ENTRY 後の値動き追跡（EXIT スタディ）。

前回の閾値スイープ（sweep.py / events.py）とは**目的が違う**。あちらは
「max_position_in_range の違いで何を拾い何を捨てるか」を見るもので、
こちらは **現行 0.65 で発生した ENTRY_CANDIDATE が、その後どう動いたか** を
現在の売買ルールに沿って時系列で再生する。

このモジュールが守る境界:

1. **確定ルールだけを機械判定に使う。**
   確定しているのは `initial_stop = range_lower * 0.995`（CODEX_HANDOFF §20）だけ。
   仮想ポジションを閉じるのはこの初期損切りのみ。

2. **未確定ルールは「参考トラック」として分離する。**
   警戒陰線（§30）とトレーリング（§30）は機械定義が未確定なので、
   イベントとして記録するだけで、ポジションを閉じる判断には使わない。
   列名・イベント名に `reference` / `CANDIDATE` を残して混同を防ぐ。

3. **日足だけで先後が分からない日は有利な順番を仮定しない。**
   同日に初期損切りと利益方向の水準の両方へ到達した場合は
   `AMBIGUOUS_INTRADAY_ORDER` として明示する。

4. **新しい閾値を作らない。**
   ギャップ率・陰線サイズ・出来高倍率などは観察値として記録するだけで、
   売買判定には一切使わない。

5. **本番の config.yaml / experimental.yaml / output/ には書き込まない。**
"""

from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass, field, fields
from datetime import date
from pathlib import Path
from typing import Any

from swing_screener.indicators.swing import detect_swings
from swing_screener.models import OHLCVBar, PriceSeries

# --- イベント種別 -------------------------------------------------------------
# 確定ルール由来（ポジションを閉じる）
K_ENTRY = "ENTRY"
K_STOP_HIT = "INITIAL_STOP_HIT"

# 観察イベント（ポジションを閉じない）
K_UPPER_TOUCH = "RANGE_UPPER_TOUCH"          # 高値がレンジ上限に到達
K_UPPER_HIGH_ONLY = "RANGE_UPPER_HIGH_ONLY"  # 高値は上限超だが終値は上限以下
K_UPPER_CLOSE_BREAK = "RANGE_UPPER_CLOSE_BREAK"  # 終値でレンジ上限突破
K_NEW_HIGH = "NEW_HIGH"                      # 保有中の高値更新
K_GAIN = "GAIN_{pct}PCT"                     # 仮想ENTRY価格からの到達
K_MAX_GAIN = "MAX_GAIN"

# 未確定ルール由来（参考。ポジションを閉じない）
K_WARNING = "WARNING_CANDLE"                 # 保有中に出現した陰線
K_WARNING_BREAK = "WARNING_CANDLE_LOW_BREAK"  # その安値を下抜け → 利確候補
K_SWING_LOW = "SWING_LOW_CONFIRMED"          # 既存 swing 検出が押し安値を確定
K_TRAIL = "TRAIL_STOP_CANDIDATE"             # 押し安値形成後の高値更新

# 状態フラグ
K_AMBIGUOUS = "AMBIGUOUS_INTRADAY_ORDER"
K_DATA_END = "DATA_END_STILL_OPEN"
K_GAP_ABOVE_UPPER = "NEXT_OPEN_ABOVE_RANGE_UPPER"
K_ENTRY_BELOW_STOP = "NEXT_OPEN_BELOW_INITIAL_STOP"

GAIN_TARGETS: tuple[float, ...] = (3.0, 5.0, 10.0)

# 保有中の追跡上限（営業日）。売買ルールではなく観察の打ち切り点。
MAX_TRACK_DAYS = 60

CSV_NOTE = (
    "# 注記: 本ファイルは ENTRY 後の値動きの観察記録であり収益バックテストではない。"
    " entry_price は「シグナル翌営業日の始値」で、これは検証用の約定価格であって"
    "「翌日始値で必ず買う」という売買ルールではない。"
    " ポジションを閉じる機械判定に使ったのは確定ルールの initial_stop = range_lower*0.995 のみ。"
    " 警戒陰線・トレーリングの列はすべて未確定ルールの参考値であり、売買判定には使っていない。"
)


# --- 参考指標（売買判定には使わない）-----------------------------------------


def _atr(bars: list[OHLCVBar], index: int, period: int = 14) -> float | None:
    """index 日までの ATR。MANUAL_EXIT_REVIEW 用の参考スケールにすぎない。"""
    if index < period:
        return None
    trs: list[float] = []
    for i in range(index - period + 1, index + 1):
        prev_close = bars[i - 1].close if i > 0 else bars[i].open
        trs.append(
            max(
                bars[i].high - bars[i].low,
                abs(bars[i].high - prev_close),
                abs(bars[i].low - prev_close),
            )
        )
    return sum(trs) / len(trs) if trs else None


def _avg_volume(bars: list[OHLCVBar], index: int, period: int = 25) -> float | None:
    if index + 1 < period:
        return None
    window = bars[index - period + 1 : index + 1]
    total = sum(b.volume for b in window)
    return total / len(window) if window else None


# --- データ構造 ---------------------------------------------------------------


@dataclass(frozen=True)
class TimelineEvent:
    """ENTRY 後に起きたことを 1 行で表す。順序がこの検証の主役。"""

    day_offset: int  # 仮想ENTRY日 = 0
    date: date
    kind: str
    price: float | None
    detail: str
    is_rule_based: bool  # True = 確定ルール由来 / False = 観察 or 未確定ルール参考

    def label(self) -> str:
        return f"D+{self.day_offset} {self.kind}"


@dataclass(frozen=True)
class WarningCandle:
    """保有中に出現した陰線（close < open）。単純な定義のまま扱う。

    「最初の陰線」を警戒足とするのが §30 だが、複雑化を避けるため
    保有中の陰線をすべて記録し `is_first` で区別する。
    """

    date: date
    day_offset: int
    is_first: bool
    open: float
    high: float
    low: float
    close: float
    volume: int

    # 安値割れ（= 利確候補）
    broke_low_date: date | None
    broke_low_day_offset: int | None
    days_from_candle_to_break: int | None

    # 「安値を割る前に高値更新したか」— 何の高値かが未確定なので両方を持つ
    new_high_vs_candle_high_before_break: bool
    new_high_vs_position_peak_before_break: bool

    # MANUAL_EXIT_REVIEW 用の参考指標（売却判定には使わない）
    change_pct: float | None       # 前日終値比
    body_pct: float                # 実体幅 / 始値
    body_to_atr: float | None      # 実体幅 / ATR14
    volume_ratio: float | None     # 出来高 / 25日平均出来高
    close_pos_in_day_range: float | None  # 0=安値引け, 1=高値引け
    manual_exit_review: bool       # 常に True。人間がチャートを見る対象


@dataclass(frozen=True)
class TrailCandidate:
    """「新しい押し安値 → 再度高値更新」で引き上げ得た損切り水準（参考）。

    §30 の trailing は機械定義が未確定。既存 swing 検出（fractal）で
    押し安値を拾えた場合のみ、参考シミュレーションとして記録する。

    variant は §30 の読み方が 2 通りあるために付けている。**どちらが正しいかを
    この検証で決めない。**

    - `strict`: 「新しい押し安値形成後に高値更新」を文字通り取り、
      押し安値より前の高値を上抜くことを要求する。
    - `loose` : 押し安値が確定した時点で trail を引き上げてよいと取る。
      fractal の確定自体が「安値の後に 2 本上げた」ことを含むため。
    """

    variant: str  # "strict" / "loose"
    swing_low_date: date
    swing_low_price: float
    swing_low_confirmed_date: date          # fractal は右側 pivot_window 本ぶん遅れる
    armed_date: date                        # trail が有効になった日
    armed_day_offset: int
    trail_stop_candidate: float             # swing_low * 0.995
    improves_on_initial_stop: bool
    before_initial_stop_hit: bool


@dataclass(frozen=True)
class TrailSimulation:
    """trail 候補で降りていたらどうなったかの参考シミュレーション。

    **本番の売買ルールではない。** 初期STOPより有利な撤退ラインへ移行できたかを
    見るためだけのもの。
    """

    variant: str
    armed: bool                     # 初期STOP到達前に trail が有効になったか
    trail_stop_level: float | None  # 有効になった最終的な trail 水準
    exit_date: date | None
    exit_day_offset: int | None
    exit_price_reference: float | None
    exit_return_pct: float | None
    better_than_initial_stop: bool
    ambiguous_with_initial_stop: bool  # 同日に trail と初期STOPの両方へ到達


@dataclass
class TrackedEvent:
    """1 件の ENTRY_CANDIDATE を追跡した結果。"""

    # --- シグナル（前回検証から引き継ぐ）---
    signal_date: date
    code: str
    name: str
    sector: str
    signal_close: float
    signal_index: int
    range_lower: float
    range_upper: float
    range_width_pct: float
    position_in_range: float
    initial_stop: float
    stop_distance_pct_from_close: float
    ma25: float | None

    # --- 仮想 ENTRY（翌営業日始値。検証用の約定価格）---
    entry_available: bool
    entry_date: date | None
    entry_index: int | None
    entry_price: float | None
    gap_pct: float | None

    # --- ギャップ後の再評価（§4。閾値は作らない）---
    position_in_range_at_entry: float | None
    dist_to_upper_pct_at_entry: float | None
    dist_to_stop_pct_at_entry: float | None
    entry_above_range_upper: bool
    entry_below_initial_stop: bool

    # --- 初期展開 ---
    hit_initial_stop: bool
    stop_date: date | None
    stop_day_offset: int | None
    reached_upper: bool
    upper_touch_date: date | None
    upper_touch_day_offset: int | None
    first_event_order: str  # "stop_first" / "upper_first" / "ambiguous" / "neither"

    # --- レンジ上限 ---
    upper_high_only_break: bool
    upper_close_break: bool
    upper_close_break_date: date | None
    upper_close_break_day_offset: int | None

    # --- 伸び（仮想ENTRY価格基準）---
    reached_gain: dict[float, bool]
    days_to_gain: dict[float, int | None]
    max_gain_pct: float | None
    max_gain_date: date | None
    days_to_max_gain: int | None
    max_loss_pct: float | None

    # --- 上限突破後 ---
    post_break_max_gain_pct_from_break_close: float | None
    post_break_max_loss_pct_from_break_close: float | None
    post_break_days_to_max_gain: int | None
    post_break_max_gain_pct_from_entry: float | None

    # --- 警戒陰線（参考）---
    warning_candles: list[WarningCandle]
    first_warning_candle_date: date | None
    first_warning_break_date: date | None
    warning_break_count: int
    new_high_before_first_break: bool | None

    # --- トレーリング（参考）---
    trail_candidates: list[TrailCandidate]
    trail_before_stop_count: int
    best_trail_stop_before_stop: float | None
    trail_sim_strict: TrailSimulation | None
    trail_sim_loose: TrailSimulation | None

    # --- §12 用: 「損切り失敗」と一括りにしないための材料 ---
    upper_break_before_stop: bool
    max_gain_before_stop_pct: float | None
    stop_gap_down_pct: float | None  # 寄りがSTOPを割った分。0 なら STOP 通りに約定

    # --- 曖昧日 ---
    ambiguous_days: list[date]
    ambiguous_detail: list[str]

    # --- 終了状態 ---
    exit_reason: str  # "initial_stop" / "data_end" / "track_limit" / "no_entry"
    exit_date: date | None
    exit_price_reference: float | None
    exit_return_pct: float | None
    bars_tracked: int
    tracking_truncated: bool

    # --- 重複 ENTRY ---
    duplicate_entry_while_holding: bool
    duplicate_of: str

    # --- 分類 ---
    type_label: str
    flags: list[str]

    timeline: list[TimelineEvent] = field(default_factory=list)


# --- 追跡本体 -----------------------------------------------------------------


def track_event(
    signal: dict[str, Any],
    series: PriceSeries,
    exp,
    *,
    max_track_days: int = MAX_TRACK_DAYS,
) -> TrackedEvent:
    """1 件の ENTRY_CANDIDATE を ENTRY 後から時系列で追跡する。

    `signal` は前回検証の events_pos065.csv の 1 行（文字列 dict）。
    ポジションを閉じるのは確定ルールの初期損切りのみ。
    """
    bars = list(series.bars)
    i = int(signal["signal_index"])
    signal_close = float(signal["signal_close"])
    lower = float(signal["range_lower"])
    upper = float(signal["range_upper"])
    stop = float(signal["initial_stop"])
    span = upper - lower

    timeline: list[TimelineEvent] = []
    flags: list[str] = []

    # --- 仮想 ENTRY = 翌営業日始値 ---
    if i + 1 >= len(bars):
        return _no_entry_result(signal, bars, i, lower, upper, stop, span)

    entry_index = i + 1
    entry_bar = bars[entry_index]
    entry_price = entry_bar.open
    gap_pct = (entry_price - signal_close) / signal_close * 100.0

    pos_at_entry = (entry_price - lower) / span if span > 0 else None
    dist_upper = (upper - entry_price) / entry_price * 100.0
    dist_stop = (entry_price - stop) / entry_price * 100.0
    above_upper = entry_price > upper
    below_stop = entry_price <= stop

    if above_upper:
        flags.append(K_GAP_ABOVE_UPPER)
        timeline.append(
            TimelineEvent(
                0, entry_bar.date, K_GAP_ABOVE_UPPER, entry_price,
                f"翌日始値 {entry_price:.1f} が元レンジ上限 {upper:.1f} を上回って始まった",
                is_rule_based=False,
            )
        )
    if below_stop:
        flags.append(K_ENTRY_BELOW_STOP)

    timeline.append(
        TimelineEvent(
            0, entry_bar.date, K_ENTRY, entry_price,
            f"翌日始値 {entry_price:.1f}（ギャップ {gap_pct:+.2f}%） / "
            f"初期STOP {stop:.1f}（-{dist_stop:.2f}%） / 上限 {upper:.1f}（+{dist_upper:.2f}%）",
            is_rule_based=True,
        )
    )

    targets = {t: entry_price * (1 + t / 100.0) for t in GAIN_TARGETS}
    reached_gain = {t: False for t in GAIN_TARGETS}
    days_to_gain: dict[float, int | None] = {t: None for t in GAIN_TARGETS}

    # --- swing low（既存 fractal 検出。未来を見ない前提でスライスして呼ぶ）---
    pivot_w = int(exp.get("swing.pivot_window", 2) or 2)
    seen_swing_lows: set[int] = set()
    known_before = {sp.index for sp in detect_swings(bars[: entry_index + 1], exp)[1]}
    seen_swing_lows |= known_before

    peak_high = -math.inf
    peak_index = entry_index
    pending_swing: tuple[int, date, float, date] | None = None  # (idx, date, low, confirm)
    trail_candidates: list[TrailCandidate] = []
    # trail 水準の階段（day_offset, level）。初期STOPを上回るものだけを積む
    trail_levels: dict[str, list[tuple[int, float]]] = {"strict": [], "loose": []}
    trail_strict: float | None = None
    trail_loose: float | None = None
    trail_strict_armed: int | None = None
    trail_loose_armed: int | None = None

    warning_raw: list[dict[str, Any]] = []
    ambiguous_days: list[date] = []
    ambiguous_detail: list[str] = []

    hit_stop = False
    stop_date: date | None = None
    stop_offset: int | None = None
    reached_upper = False
    upper_touch_date: date | None = None
    upper_touch_offset: int | None = None
    upper_high_only = False
    upper_close_break = False
    upper_break_date: date | None = None
    upper_break_offset: int | None = None
    first_order = "neither"

    max_gain = None
    max_gain_date: date | None = None
    days_to_max_gain: int | None = None
    max_loss = None

    last_index = min(len(bars) - 1, entry_index + max_track_days - 1)

    d = entry_index
    while d <= last_index:
        bar = bars[d]
        off = d - entry_index

        # --- 今日の利益方向の到達水準を洗い出す（記録は必ずする）---
        favourable: list[tuple[str, float, str]] = []
        if not reached_upper and bar.high >= upper:
            favourable.append((K_UPPER_TOUCH, upper, f"高値 {bar.high:.1f} が上限 {upper:.1f} へ到達"))
        for t in GAIN_TARGETS:
            if not reached_gain[t] and bar.high >= targets[t]:
                favourable.append(
                    (K_GAIN.format(pct=int(t)), targets[t],
                     f"高値 {bar.high:.1f} が ENTRY+{t:.0f}% ({targets[t]:.1f}) へ到達")
                )

        stop_today = bar.low <= stop

        # --- 同日に両方到達した場合の先後判定 ---
        ambiguous_today = False
        stop_first = False
        if stop_today and favourable:
            if bar.open <= stop:
                stop_first = True  # 寄りで既にSTOP以下 → 順序は確定
            elif all(bar.open >= level for _, level, _ in favourable):
                stop_first = False  # 寄りで既に利益方向の水準以上 → 順序は確定
            else:
                ambiguous_today = True
        elif stop_today:
            stop_first = True

        if ambiguous_today:
            names = " / ".join(k for k, _, _ in favourable)
            detail = (
                f"同日に初期STOP({stop:.1f}) と {names} の両方へ到達。"
                f"日足（O{bar.open:.1f} H{bar.high:.1f} L{bar.low:.1f} C{bar.close:.1f}）"
                "では先後を決められない"
            )
            ambiguous_days.append(bar.date)
            ambiguous_detail.append(f"{bar.date}: {detail}")
            timeline.append(
                TimelineEvent(off, bar.date, K_AMBIGUOUS, None, detail, is_rule_based=False)
            )
            flags.append(K_AMBIGUOUS)

        # --- 利益方向の到達を記録（曖昧でも「到達した事実」は残す）---
        for kind, level, detail in favourable:
            note = detail + ("（順序不明）" if ambiguous_today else "")
            timeline.append(
                TimelineEvent(off, bar.date, kind, level, note, is_rule_based=False)
            )
            if kind == K_UPPER_TOUCH:
                reached_upper = True
                upper_touch_date = bar.date
                upper_touch_offset = off
            else:
                for t in GAIN_TARGETS:
                    if kind == K_GAIN.format(pct=int(t)):
                        reached_gain[t] = True
                        days_to_gain[t] = off

        # --- 上限突破の内訳 ---
        if bar.close > upper:
            if not upper_close_break:
                upper_close_break = True
                upper_break_date = bar.date
                upper_break_offset = off
                timeline.append(
                    TimelineEvent(
                        off, bar.date, K_UPPER_CLOSE_BREAK, bar.close,
                        f"終値 {bar.close:.1f} > 元レンジ上限 {upper:.1f}"
                        + ("（順序不明日）" if ambiguous_today else ""),
                        is_rule_based=False,
                    )
                )
        elif bar.high > upper and not upper_high_only and not upper_close_break:
            upper_high_only = True
            timeline.append(
                TimelineEvent(
                    off, bar.date, K_UPPER_HIGH_ONLY, bar.high,
                    f"高値 {bar.high:.1f} は上限超だが終値 {bar.close:.1f} は上限以下",
                    is_rule_based=False,
                )
            )

        # --- 最大上昇・最大下落（仮想ENTRY価格基準）---
        gain = (bar.high - entry_price) / entry_price * 100.0
        loss = (bar.low - entry_price) / entry_price * 100.0
        if max_gain is None or gain > max_gain:
            max_gain, max_gain_date, days_to_max_gain = gain, bar.date, off
        if max_loss is None or loss < max_loss:
            max_loss = loss

        # --- 高値更新 ---
        made_new_high = bar.high > peak_high
        if made_new_high:
            if peak_high > -math.inf:
                timeline.append(
                    TimelineEvent(
                        off, bar.date, K_NEW_HIGH, bar.high,
                        f"保有中高値を {peak_high:.1f} → {bar.high:.1f} に更新",
                        is_rule_based=False,
                    )
                )
            # strict: 押し安値の形成後に「前の高値を上抜いた」場合のみ trail を上げる
            if pending_swing is not None:
                s_idx, s_date, s_low, s_conf = pending_swing
                cand = s_low * 0.995
                trail_candidates.append(
                    TrailCandidate(
                        variant="strict",
                        swing_low_date=s_date, swing_low_price=s_low,
                        swing_low_confirmed_date=s_conf,
                        armed_date=bar.date, armed_day_offset=off,
                        trail_stop_candidate=cand,
                        improves_on_initial_stop=cand > stop,
                        before_initial_stop_hit=not hit_stop,
                    )
                )
                timeline.append(
                    TimelineEvent(
                        off, bar.date, K_TRAIL, cand,
                        f"[strict] 押し安値 {s_date} {s_low:.1f} 形成後に高値更新 → "
                        f"trail候補 {cand:.1f}"
                        f"（初期STOP {stop:.1f} を{'上回る' if cand > stop else '下回る'}）"
                        " ※参考。売買ルールではない",
                        is_rule_based=False,
                    )
                )
                if cand > stop and (trail_strict is None or cand > trail_strict):
                    trail_strict = cand
                    trail_strict_armed = off
                    trail_levels["strict"].append((off, cand))
                pending_swing = None
            peak_high = bar.high
            peak_index = d

        # --- 警戒陰線候補（保有中の陰線。単純な定義のまま）---
        if bar.close < bar.open:
            warning_raw.append(
                {
                    "index": d,
                    "offset": off,
                    "bar": bar,
                    "peak_at_time": peak_high,
                }
            )
            timeline.append(
                TimelineEvent(
                    off, bar.date, K_WARNING, bar.low,
                    f"陰線 O{bar.open:.1f} C{bar.close:.1f} 安値 {bar.low:.1f}"
                    "（警戒足候補。売却はしない）",
                    is_rule_based=False,
                )
            )

        # --- swing low の確定（既存 fractal 検出をそのまま使う）---
        lows = detect_swings(bars[: d + 1], exp)[1]
        for sp in lows:
            if sp.index in seen_swing_lows:
                continue
            seen_swing_lows.add(sp.index)
            if sp.index <= entry_index:
                continue
            timeline.append(
                TimelineEvent(
                    off, bar.date, K_SWING_LOW, sp.price,
                    f"{sp.date} の安値 {sp.price:.1f} を押し安値として確定"
                    f"（fractal pivot_window={pivot_w} のため {pivot_w} 本遅れて確定）",
                    is_rule_based=False,
                )
            )
            # 直近の高値より後にできた押し安値だけが strict の対象
            if sp.index > peak_index:
                pending_swing = (sp.index, sp.date, sp.price, bar.date)
            # loose: 押し安値が確定した時点で trail を引き上げてよい、という読み方
            cand = sp.price * 0.995
            trail_candidates.append(
                TrailCandidate(
                    variant="loose",
                    swing_low_date=sp.date, swing_low_price=sp.price,
                    swing_low_confirmed_date=bar.date,
                    armed_date=bar.date, armed_day_offset=off,
                    trail_stop_candidate=cand,
                    improves_on_initial_stop=cand > stop,
                    before_initial_stop_hit=not hit_stop,
                )
            )
            if cand > stop and (trail_loose is None or cand > trail_loose):
                trail_loose = cand
                trail_loose_armed = off
                trail_levels["loose"].append((off, cand))
                timeline.append(
                    TimelineEvent(
                        off, bar.date, K_TRAIL, cand,
                        f"[loose] 押し安値 {sp.date} {sp.price:.1f} 確定時点で "
                        f"trail候補 {cand:.1f}（初期STOP {stop:.1f} を上回る）"
                        " ※参考。売買ルールではない",
                        is_rule_based=False,
                    )
                )

        # --- 初期損切り（確定ルール。ここだけがポジションを閉じる）---
        if stop_today:
            hit_stop = True
            stop_date = bar.date
            stop_offset = off
            if first_order == "neither":
                first_order = "ambiguous" if ambiguous_today else "stop_first"
            fill = min(bar.open, stop) if bar.open < stop else stop
            timeline.append(
                TimelineEvent(
                    off, bar.date, K_STOP_HIT, fill,
                    f"安値 {bar.low:.1f} <= 初期STOP {stop:.1f}"
                    + (f"（寄り {bar.open:.1f} で既にSTOP以下）" if bar.open < stop else "")
                    + ("（同日に利益方向へも到達。順序不明）" if ambiguous_today else ""),
                    is_rule_based=True,
                )
            )
            break

        if first_order == "neither" and reached_upper:
            first_order = "upper_first"

        d += 1

    # --- 先後関係の確定 ---
    if first_order == "neither":
        first_order = "upper_first" if reached_upper else "neither"

    # --- 終了状態 ---
    if hit_stop:
        exit_reason = "initial_stop"
        exit_date = stop_date
        exit_price = stop if bars[d].open >= stop else bars[d].open
        bars_tracked = (stop_offset or 0) + 1
        truncated = False
    else:
        end_index = min(last_index, len(bars) - 1)
        exit_date = bars[end_index].date
        exit_price = bars[end_index].close
        bars_tracked = end_index - entry_index + 1
        if end_index == len(bars) - 1:
            exit_reason = "data_end"
            detail = f"データ終端 {exit_date} 時点で保有継続中（初期STOP未到達）"
        else:
            exit_reason = "track_limit"
            detail = f"追跡上限 {max_track_days} 営業日に到達（初期STOP未到達）"
        truncated = True
        timeline.append(
            TimelineEvent(
                bars_tracked - 1, exit_date, K_DATA_END, exit_price, detail,
                is_rule_based=False,
            )
        )

    exit_return = (
        (exit_price - entry_price) / entry_price * 100.0 if exit_price and entry_price else None
    )

    # --- 警戒陰線の後処理（安値割れ・高値更新）---
    hold_end_index = entry_index + bars_tracked - 1
    warnings: list[WarningCandle] = []
    for n, w in enumerate(warning_raw):
        bar: OHLCVBar = w["bar"]
        w_idx = w["index"]
        broke_idx = next(
            (j for j in range(w_idx + 1, hold_end_index + 1) if bars[j].low < bar.low), None
        )
        upto = broke_idx if broke_idx is not None else hold_end_index + 1
        new_high_vs_candle = any(
            bars[j].high > bar.high for j in range(w_idx + 1, upto)
        )
        new_high_vs_peak = any(
            bars[j].high > w["peak_at_time"] for j in range(w_idx + 1, upto)
        )
        prev_close = bars[w_idx - 1].close if w_idx > 0 else None
        atr = _atr(bars, w_idx)
        avg_vol = _avg_volume(bars, w_idx)
        day_range = bar.high - bar.low
        body = abs(bar.close - bar.open)
        warnings.append(
            WarningCandle(
                date=bar.date,
                day_offset=w["offset"],
                is_first=(n == 0),
                open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                volume=bar.volume,
                broke_low_date=bars[broke_idx].date if broke_idx is not None else None,
                broke_low_day_offset=(broke_idx - entry_index) if broke_idx is not None else None,
                days_from_candle_to_break=(broke_idx - w_idx) if broke_idx is not None else None,
                new_high_vs_candle_high_before_break=new_high_vs_candle,
                new_high_vs_position_peak_before_break=new_high_vs_peak,
                change_pct=(
                    (bar.close - prev_close) / prev_close * 100.0 if prev_close else None
                ),
                body_pct=body / bar.open * 100.0 if bar.open else 0.0,
                body_to_atr=(body / atr if atr else None),
                volume_ratio=(bar.volume / avg_vol if avg_vol else None),
                close_pos_in_day_range=(
                    (bar.close - bar.low) / day_range if day_range > 0 else None
                ),
                manual_exit_review=True,
            )
        )
        if broke_idx is not None:
            timeline.append(
                TimelineEvent(
                    broke_idx - entry_index, bars[broke_idx].date, K_WARNING_BREAK, bar.low,
                    f"{bar.date} の警戒陰線安値 {bar.low:.1f} を下抜け（安値 "
                    f"{bars[broke_idx].low:.1f}）→ 利確候補。※未確定ルールのため売却はしない",
                    is_rule_based=False,
                )
            )

    timeline.sort(key=lambda e: (e.day_offset, 0 if e.is_rule_based else 1))

    first_warn = warnings[0] if warnings else None
    breaks = [w for w in warnings if w.broke_low_date is not None]
    first_break = min((w.broke_low_date for w in breaks), default=None)

    trail_before = [
        t for t in trail_candidates
        if t.before_initial_stop_hit and t.improves_on_initial_stop and t.variant == "strict"
    ]
    best_trail = max((t.trail_stop_candidate for t in trail_before), default=None)

    sim_strict = _simulate_trail(
        "strict", trail_levels["strict"], bars, entry_index, hold_end_index,
        entry_price=entry_price, initial_stop=stop,
    )
    sim_loose = _simulate_trail(
        "loose", trail_levels["loose"], bars, entry_index, hold_end_index,
        entry_price=entry_price, initial_stop=stop,
    )

    # --- §12 用の材料 ---
    upper_break_before_stop = bool(
        upper_close_break
        and (stop_offset is None or (upper_break_offset or 0) < stop_offset)
    )
    stop_gap_down = None
    if hit_stop and stop_offset is not None:
        stop_bar = bars[entry_index + stop_offset]
        stop_gap_down = (
            (stop_bar.open - stop) / stop * 100.0 if stop_bar.open < stop else 0.0
        )

    # --- 上限突破後の伸び ---
    pb_gain = pb_loss = pb_days = pb_gain_entry = None
    if upper_break_date is not None:
        b_idx = next(j for j in range(entry_index, hold_end_index + 1)
                     if bars[j].date == upper_break_date)
        base = bars[b_idx].close
        window = bars[b_idx : hold_end_index + 1]
        if window:
            hi = max(b.high for b in window)
            lo = min(b.low for b in window)
            pb_gain = (hi - base) / base * 100.0
            pb_loss = (lo - base) / base * 100.0
            pb_days = next(k for k, b in enumerate(window) if b.high == hi)
            pb_gain_entry = (hi - entry_price) / entry_price * 100.0

    return TrackedEvent(
        signal_date=date.fromisoformat(signal["date"]),
        code=signal["code"], name=signal["name"], sector=signal["sector"],
        signal_close=signal_close, signal_index=i,
        range_lower=lower, range_upper=upper,
        range_width_pct=float(signal["range_width_pct"]),
        position_in_range=float(signal["position_in_range"]),
        initial_stop=stop,
        stop_distance_pct_from_close=float(signal["stop_distance_pct_from_close"]),
        ma25=float(signal["ma25"]) if signal.get("ma25") else None,
        entry_available=True, entry_date=entry_bar.date, entry_index=entry_index,
        entry_price=entry_price, gap_pct=gap_pct,
        position_in_range_at_entry=pos_at_entry,
        dist_to_upper_pct_at_entry=dist_upper,
        dist_to_stop_pct_at_entry=dist_stop,
        entry_above_range_upper=above_upper,
        entry_below_initial_stop=below_stop,
        hit_initial_stop=hit_stop, stop_date=stop_date, stop_day_offset=stop_offset,
        reached_upper=reached_upper, upper_touch_date=upper_touch_date,
        upper_touch_day_offset=upper_touch_offset,
        first_event_order=first_order,
        upper_high_only_break=upper_high_only,
        upper_close_break=upper_close_break,
        upper_close_break_date=upper_break_date,
        upper_close_break_day_offset=upper_break_offset,
        reached_gain=reached_gain, days_to_gain=days_to_gain,
        max_gain_pct=max_gain, max_gain_date=max_gain_date,
        days_to_max_gain=days_to_max_gain, max_loss_pct=max_loss,
        post_break_max_gain_pct_from_break_close=pb_gain,
        post_break_max_loss_pct_from_break_close=pb_loss,
        post_break_days_to_max_gain=pb_days,
        post_break_max_gain_pct_from_entry=pb_gain_entry,
        warning_candles=warnings,
        first_warning_candle_date=first_warn.date if first_warn else None,
        first_warning_break_date=first_break,
        warning_break_count=len(breaks),
        new_high_before_first_break=(
            first_warn.new_high_vs_candle_high_before_break if first_warn else None
        ),
        trail_candidates=trail_candidates,
        trail_before_stop_count=len(trail_before),
        best_trail_stop_before_stop=best_trail,
        trail_sim_strict=sim_strict, trail_sim_loose=sim_loose,
        upper_break_before_stop=upper_break_before_stop,
        max_gain_before_stop_pct=max_gain if hit_stop else None,
        stop_gap_down_pct=stop_gap_down,
        ambiguous_days=ambiguous_days, ambiguous_detail=ambiguous_detail,
        exit_reason=exit_reason, exit_date=exit_date,
        exit_price_reference=exit_price, exit_return_pct=exit_return,
        bars_tracked=bars_tracked, tracking_truncated=truncated,
        duplicate_entry_while_holding=False, duplicate_of="",
        type_label="", flags=sorted(set(flags)),
        timeline=timeline,
    )


def _simulate_trail(
    variant: str,
    levels: list[tuple[int, float]],
    bars: list[OHLCVBar],
    entry_index: int,
    hold_end_index: int,
    *,
    entry_price: float,
    initial_stop: float,
) -> TrailSimulation:
    """trail 水準の階段を当てはめて、どこで降りられたかを見る（参考のみ）。

    trail 水準は常に初期STOPより上なので、trail での撤退日は初期STOP到達日以前になる。
    同日に trail と初期STOPの両方へ到達した場合は順序を仮定せず ambiguous を立てる。
    """
    if not levels:
        return TrailSimulation(variant, False, None, None, None, None, None, False, False)

    level_by_day: list[tuple[int, float]] = sorted(levels)
    for d in range(entry_index, hold_end_index + 1):
        off = d - entry_index
        active = [lv for day, lv in level_by_day if day < off]
        if not active:
            continue
        level = max(active)
        bar = bars[d]
        if bar.low <= level:
            fill = min(bar.open, level) if bar.open < level else level
            ambiguous = bar.low <= initial_stop and bar.open > initial_stop
            return TrailSimulation(
                variant=variant, armed=True, trail_stop_level=level,
                exit_date=bar.date, exit_day_offset=off, exit_price_reference=fill,
                exit_return_pct=(fill - entry_price) / entry_price * 100.0,
                better_than_initial_stop=fill > initial_stop,
                ambiguous_with_initial_stop=ambiguous,
            )

    # 期間中に trail へ触れなかった（＝まだ保有継続）
    level = max(lv for _, lv in level_by_day)
    return TrailSimulation(
        variant=variant, armed=True, trail_stop_level=level,
        exit_date=None, exit_day_offset=None, exit_price_reference=None,
        exit_return_pct=None, better_than_initial_stop=True,
        ambiguous_with_initial_stop=False,
    )


def _no_entry_result(signal, bars, i, lower, upper, stop, span) -> TrackedEvent:
    """翌営業日が存在しない（＝仮想ENTRYできない）ケース。"""
    return TrackedEvent(
        signal_date=date.fromisoformat(signal["date"]),
        code=signal["code"], name=signal["name"], sector=signal["sector"],
        signal_close=float(signal["signal_close"]), signal_index=i,
        range_lower=lower, range_upper=upper,
        range_width_pct=float(signal["range_width_pct"]),
        position_in_range=float(signal["position_in_range"]),
        initial_stop=stop,
        stop_distance_pct_from_close=float(signal["stop_distance_pct_from_close"]),
        ma25=float(signal["ma25"]) if signal.get("ma25") else None,
        entry_available=False, entry_date=None, entry_index=None,
        entry_price=None, gap_pct=None,
        position_in_range_at_entry=None, dist_to_upper_pct_at_entry=None,
        dist_to_stop_pct_at_entry=None, entry_above_range_upper=False,
        entry_below_initial_stop=False,
        hit_initial_stop=False, stop_date=None, stop_day_offset=None,
        reached_upper=False, upper_touch_date=None, upper_touch_day_offset=None,
        first_event_order="no_entry",
        upper_high_only_break=False, upper_close_break=False,
        upper_close_break_date=None, upper_close_break_day_offset=None,
        reached_gain={t: False for t in GAIN_TARGETS},
        days_to_gain={t: None for t in GAIN_TARGETS},
        max_gain_pct=None, max_gain_date=None, days_to_max_gain=None, max_loss_pct=None,
        post_break_max_gain_pct_from_break_close=None,
        post_break_max_loss_pct_from_break_close=None,
        post_break_days_to_max_gain=None, post_break_max_gain_pct_from_entry=None,
        warning_candles=[], first_warning_candle_date=None,
        first_warning_break_date=None, warning_break_count=0,
        new_high_before_first_break=None,
        trail_candidates=[], trail_before_stop_count=0, best_trail_stop_before_stop=None,
        trail_sim_strict=None, trail_sim_loose=None,
        upper_break_before_stop=False, max_gain_before_stop_pct=None,
        stop_gap_down_pct=None,
        ambiguous_days=[], ambiguous_detail=[],
        exit_reason="no_entry", exit_date=None, exit_price_reference=None,
        exit_return_pct=None, bars_tracked=0, tracking_truncated=True,
        duplicate_entry_while_holding=False, duplicate_of="",
        type_label="NO_ENTRY", flags=["NO_NEXT_OPEN"], timeline=[],
    )


# --- 分類（分析用ラベル。売買ルールではない）---------------------------------

TYPE_LABELS_JA = {
    "TYPE1": "TYPE1 上限到達前に初期STOP",
    "TYPE2": "TYPE2 上限到達も突破できず",
    "TYPE3": "TYPE3 上限突破 +3〜5%",
    "TYPE4": "TYPE4 上限突破 +5%以上",
    "TYPE2b": "TYPE2b 上限突破したが+3%未満",
    "OPEN": "追跡中（データ終端）",
    "NO_ENTRY": "仮想ENTRY不可",
}

FLAG_LABELS_JA = {
    "ENTRY_POSITION_ABOVE_GUARD": (
        "TYPE5 翌日始値でレンジ内位置が現行ガード0.65を超えた"
    ),
    "TYPE6_AMBIGUOUS": "TYPE6 日足だけでは順序判定不能",
    K_AMBIGUOUS: "同日にSTOPと利益方向の両方へ到達",
    K_GAP_ABOVE_UPPER: "翌日始値が元レンジ上限超",
    K_ENTRY_BELOW_STOP: "翌日始値が初期STOP以下",
    "DUPLICATE_ENTRY_WHILE_HOLDING": "保有中の重複ENTRY",
    "STOP_GAPPED_DOWN": "初期STOPを寄りで割って約定（スリッページ）",
    "BIG_GAIN_THEN_STOP": "STOP前に+5%以上の含み益があった",
}


def classify_type(ev: TrackedEvent) -> str:
    """TYPE1〜4 は「結果の主軸」。TYPE5/6 は別軸なので flags 側に置く。

    ユーザー指定の TYPE5（ギャップで遅い）と TYPE6（順序判定不能）は
    TYPE1〜4 と排他ではないため、同じ列に押し込むと結果が見えなくなる。
    """
    if not ev.entry_available:
        return "NO_ENTRY"
    if not ev.reached_upper:
        return "TYPE1" if ev.hit_initial_stop else "OPEN"
    if not ev.upper_close_break:
        return "TYPE2"
    gain = ev.max_gain_pct or 0.0
    if gain >= 5.0:
        return "TYPE4"
    if gain >= 3.0:
        return "TYPE3"
    return "TYPE2b"


def apply_classification(events: list[TrackedEvent]) -> None:
    """TYPE ラベル・別軸フラグ・重複 ENTRY を確定させる。"""
    open_positions: dict[str, list[TrackedEvent]] = {}
    for ev in sorted(events, key=lambda e: (e.signal_date, e.code)):
        prior = open_positions.setdefault(ev.code, [])
        for p in prior:
            if p.entry_date is None or p.exit_date is None:
                continue
            if p.entry_date <= ev.signal_date <= p.exit_date:
                ev.duplicate_entry_while_holding = True
                ev.duplicate_of = f"{p.code} {p.signal_date}"
                ev.flags.append("DUPLICATE_ENTRY_WHILE_HOLDING")
        prior.append(ev)

    for ev in events:
        ev.type_label = classify_type(ev)
        if ev.ambiguous_days:
            ev.flags.append("TYPE6_AMBIGUOUS")
        # ユーザー指定の TYPE5（ギャップでENTRYが遅くなる）。
        # **新しいギャップ閾値は作らない。** 判定に使うのは既に運用中の
        # near.max_position_in_range = 0.65 という確定済みガードだけで、
        # 「シグナル日には通ったガードを、翌日始値では超えていた」件を数えている。
        if (
            ev.position_in_range_at_entry is not None
            and ev.position_in_range_at_entry > 0.65
        ):
            ev.flags.append("ENTRY_POSITION_ABOVE_GUARD")
        if ev.stop_gap_down_pct is not None and ev.stop_gap_down_pct < 0:
            ev.flags.append("STOP_GAPPED_DOWN")
        if ev.hit_initial_stop and (ev.max_gain_before_stop_pct or 0) >= 5.0:
            ev.flags.append("BIG_GAIN_THEN_STOP")
        ev.flags = sorted(set(ev.flags))


# --- CSV 出力 -----------------------------------------------------------------

EVENT_COLUMNS = [
    "signal_date", "code", "name", "sector",
    "type_label", "flags",
    "signal_close", "entry_date", "entry_price", "gap_pct",
    "range_lower", "range_upper", "range_width_pct",
    "position_in_range", "position_in_range_at_entry",
    "initial_stop", "stop_distance_pct_from_close", "dist_to_stop_pct_at_entry",
    "dist_to_upper_pct_at_entry", "entry_above_range_upper", "entry_below_initial_stop",
    "hit_initial_stop", "stop_date", "stop_day_offset",
    "reached_upper", "upper_touch_date", "upper_touch_day_offset",
    "first_event_order",
    "upper_high_only_break", "upper_close_break", "upper_close_break_date",
    "upper_close_break_day_offset",
    "reached_gain_3", "days_to_gain_3",
    "reached_gain_5", "days_to_gain_5",
    "reached_gain_10", "days_to_gain_10",
    "max_gain_pct", "days_to_max_gain", "max_loss_pct",
    "post_break_max_gain_pct_from_break_close",
    "post_break_max_loss_pct_from_break_close",
    "post_break_days_to_max_gain", "post_break_max_gain_pct_from_entry",
    "warning_candle_count", "first_warning_candle_date", "first_warning_break_date",
    "warning_break_count", "new_high_before_first_break",
    "trail_candidate_count", "trail_before_stop_count", "best_trail_stop_before_stop",
    "ambiguous_day_count", "ambiguous_days",
    "duplicate_entry_while_holding", "duplicate_of",
    "exit_reason", "exit_date", "exit_price_reference", "exit_return_pct",
    "bars_tracked", "tracking_truncated",
    "timeline",
]


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def event_row(ev: TrackedEvent) -> dict[str, str]:
    row: dict[str, Any] = {
        "signal_date": ev.signal_date, "code": ev.code, "name": ev.name,
        "sector": ev.sector, "type_label": ev.type_label, "flags": ";".join(ev.flags),
        "signal_close": ev.signal_close, "entry_date": ev.entry_date,
        "entry_price": ev.entry_price, "gap_pct": ev.gap_pct,
        "range_lower": ev.range_lower, "range_upper": ev.range_upper,
        "range_width_pct": ev.range_width_pct,
        "position_in_range": ev.position_in_range,
        "position_in_range_at_entry": ev.position_in_range_at_entry,
        "initial_stop": ev.initial_stop,
        "stop_distance_pct_from_close": ev.stop_distance_pct_from_close,
        "dist_to_stop_pct_at_entry": ev.dist_to_stop_pct_at_entry,
        "dist_to_upper_pct_at_entry": ev.dist_to_upper_pct_at_entry,
        "entry_above_range_upper": ev.entry_above_range_upper,
        "entry_below_initial_stop": ev.entry_below_initial_stop,
        "hit_initial_stop": ev.hit_initial_stop, "stop_date": ev.stop_date,
        "stop_day_offset": ev.stop_day_offset,
        "reached_upper": ev.reached_upper, "upper_touch_date": ev.upper_touch_date,
        "upper_touch_day_offset": ev.upper_touch_day_offset,
        "first_event_order": ev.first_event_order,
        "upper_high_only_break": ev.upper_high_only_break,
        "upper_close_break": ev.upper_close_break,
        "upper_close_break_date": ev.upper_close_break_date,
        "upper_close_break_day_offset": ev.upper_close_break_day_offset,
        "max_gain_pct": ev.max_gain_pct, "days_to_max_gain": ev.days_to_max_gain,
        "max_loss_pct": ev.max_loss_pct,
        "post_break_max_gain_pct_from_break_close": ev.post_break_max_gain_pct_from_break_close,
        "post_break_max_loss_pct_from_break_close": ev.post_break_max_loss_pct_from_break_close,
        "post_break_days_to_max_gain": ev.post_break_days_to_max_gain,
        "post_break_max_gain_pct_from_entry": ev.post_break_max_gain_pct_from_entry,
        "warning_candle_count": len(ev.warning_candles),
        "first_warning_candle_date": ev.first_warning_candle_date,
        "first_warning_break_date": ev.first_warning_break_date,
        "warning_break_count": ev.warning_break_count,
        "new_high_before_first_break": ev.new_high_before_first_break,
        "trail_candidate_count": len(ev.trail_candidates),
        "trail_before_stop_count": ev.trail_before_stop_count,
        "best_trail_stop_before_stop": ev.best_trail_stop_before_stop,
        "ambiguous_day_count": len(ev.ambiguous_days),
        "ambiguous_days": ";".join(d.isoformat() for d in ev.ambiguous_days),
        "duplicate_entry_while_holding": ev.duplicate_entry_while_holding,
        "duplicate_of": ev.duplicate_of,
        "exit_reason": ev.exit_reason, "exit_date": ev.exit_date,
        "exit_price_reference": ev.exit_price_reference,
        "exit_return_pct": ev.exit_return_pct,
        "bars_tracked": ev.bars_tracked, "tracking_truncated": ev.tracking_truncated,
        "timeline": " > ".join(e.label() for e in ev.timeline),
    }
    for t in GAIN_TARGETS:
        row[f"reached_gain_{int(t)}"] = ev.reached_gain[t]
        row[f"days_to_gain_{int(t)}"] = ev.days_to_gain[t]
    return {k: _cell(row.get(k)) for k in EVENT_COLUMNS}


def write_events_csv(events: list[TrackedEvent], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(CSV_NOTE + "\n")
        w = csv.DictWriter(f, fieldnames=EVENT_COLUMNS)
        w.writeheader()
        for ev in events:
            w.writerow(event_row(ev))
    return path


WARNING_COLUMNS = [f.name for f in fields(WarningCandle)]


def write_warning_candles_csv(events: list[TrackedEvent], path: Path) -> Path:
    """警戒陰線の一覧。MANUAL_EXIT_REVIEW 用の参考指標つき。

    ここに書かれた指標で売却判定は**していない**。人間がチャートを見るための材料。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(
            "# 保有中に出現した陰線（close < open）の一覧。"
            "§30 の警戒足ルールは機械定義が未確定のため、ここでは売却判定をしていない。"
            " change_pct / body_to_atr / volume_ratio / close_pos_in_day_range は"
            "MANUAL_EXIT_REVIEW（人間がチャート確認）のための参考指標であり閾値は設けていない。\n"
        )
        w = csv.DictWriter(f, fieldnames=["code", "name", "signal_date", *WARNING_COLUMNS])
        w.writeheader()
        for ev in events:
            for wc in ev.warning_candles:
                row = {k: _cell(v) for k, v in asdict(wc).items()}
                row.update(
                    code=ev.code, name=ev.name, signal_date=ev.signal_date.isoformat()
                )
                w.writerow(row)
    return path


TIMELINE_COLUMNS = ["code", "name", "signal_date", "day_offset", "date", "kind",
                    "price", "is_rule_based", "detail"]


def write_timeline_csv(events: list[TrackedEvent], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(
            "# ENTRY 後に起きたことの時系列。is_rule_based=true は確定ルール由来"
            "（ENTRY と初期損切りのみ）。false は観察 or 未確定ルールの参考イベント。\n"
        )
        w = csv.DictWriter(f, fieldnames=TIMELINE_COLUMNS)
        w.writeheader()
        for ev in events:
            for te in ev.timeline:
                w.writerow({
                    "code": ev.code, "name": ev.name,
                    "signal_date": ev.signal_date.isoformat(),
                    "day_offset": te.day_offset, "date": te.date.isoformat(),
                    "kind": te.kind, "price": _cell(te.price),
                    "is_rule_based": _cell(te.is_rule_based), "detail": te.detail,
                })
    return path


# --- 集計 ---------------------------------------------------------------------


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def _rate(num: int, den: int) -> str:
    return f"{num}/{den} ({num / den * 100:.0f}%)" if den else "－"


@dataclass(frozen=True)
class SummaryRow:
    section: str
    metric: str
    value: str
    note: str = ""


def summarize(events: list[TrackedEvent]) -> list[SummaryRow]:
    """§11 の主要指標。単一の勝率指標にまとめない（§12）。"""
    n = len(events)
    entered = [e for e in events if e.entry_available]
    ne = len(entered)
    gaps = [e.gap_pct for e in entered if e.gap_pct is not None]

    rows: list[SummaryRow] = [
        SummaryRow("ENTRY", "ENTRYイベント数", str(n), "0.65 で発生した ENTRY_CANDIDATE"),
        SummaryRow("ENTRY", "翌営業日始値が取得できた件数", _rate(ne, n)),
        SummaryRow("ENTRY", "翌日ギャップ率の中央値",
                   f"{_median(gaps):+.2f}%" if gaps else "－",
                   f"最小 {min(gaps):+.2f}% / 最大 {max(gaps):+.2f}%" if gaps else ""),
        SummaryRow("ENTRY", "翌日始値が元レンジ上限を超えていた件数",
                   _rate(sum(1 for e in entered if e.entry_above_range_upper), ne)),
        SummaryRow("ENTRY", "翌日始値が初期STOP以下だった件数",
                   _rate(sum(1 for e in entered if e.entry_below_initial_stop), ne)),
        SummaryRow("ENTRY", "翌日始値でレンジ内位置が 0.65 を超えた件数",
                   _rate(sum(1 for e in entered
                             if e.position_in_range_at_entry is not None
                             and e.position_in_range_at_entry > 0.65), ne),
                   "現行ガードと同じ位置基準で見た「遅くなった」件数"),
        SummaryRow("ENTRY", "保有中の重複ENTRY",
                   _rate(sum(1 for e in entered if e.duplicate_entry_while_holding), ne),
                   "同じポジションへの新規買いとしては処理していない"),
    ]

    stop_first = sum(1 for e in entered if e.first_event_order == "stop_first")
    upper_first = sum(1 for e in entered if e.first_event_order == "upper_first")
    ambiguous = sum(1 for e in entered if e.first_event_order == "ambiguous")
    neither = sum(1 for e in entered if e.first_event_order == "neither")
    rows += [
        SummaryRow("初期展開", "初期STOPへ先に到達", _rate(stop_first, ne)),
        SummaryRow("初期展開", "レンジ上限へ先に到達", _rate(upper_first, ne)),
        SummaryRow("初期展開", "日足では先後不明", _rate(ambiguous, ne),
                   "AMBIGUOUS_INTRADAY_ORDER"),
        SummaryRow("初期展開", "どちらにも到達せず", _rate(neither, ne)),
    ]

    stopped = [e for e in entered if e.hit_initial_stop]
    stop_days = [e.stop_day_offset for e in stopped if e.stop_day_offset is not None]
    gapped = [e for e in stopped if (e.stop_gap_down_pct or 0) < 0]
    rows += [
        SummaryRow("初期STOP", "初期STOP到達（時期を問わず）", _rate(len(stopped), ne),
                   "利確ルールが未確定なので、初期STOP以外にポジションを閉じる機械判定がない。"
                   "この率を『損切り失敗率』として読まないこと"),
        SummaryRow("初期STOP", "ENTRYからSTOPまでの営業日数の中央値",
                   f"{_median([float(d) for d in stop_days]):.0f}日" if stop_days else "－",
                   f"最短 {min(stop_days)}日 / 最長 {max(stop_days)}日" if stop_days else ""),
        SummaryRow("初期STOP", "STOP前に一度でも上限を終値突破していた件数",
                   _rate(sum(1 for e in stopped if e.upper_break_before_stop), len(stopped)),
                   "§12 の「上昇後に遅れてSTOP」に該当。単純な損切り失敗ではない"),
        SummaryRow("初期STOP", "STOP前に +5% 以上の含み益があった件数",
                   _rate(sum(1 for e in stopped
                             if (e.max_gain_before_stop_pct or 0) >= 5.0), len(stopped))),
        SummaryRow("初期STOP", "STOPを寄りでギャップ割れした件数",
                   _rate(len(gapped), len(stopped)),
                   (f"割れ幅の中央値 "
                    f"{_median([e.stop_gap_down_pct for e in gapped]):.2f}%"
                    if gapped else "") + " ／ 日足では STOP 通りに約定できない"),
    ]

    reached = [e for e in entered if e.reached_upper]
    broke = [e for e in entered if e.upper_close_break]
    rows += [
        SummaryRow("レンジ上限", "レンジ上限到達率", _rate(len(reached), ne)),
        SummaryRow("レンジ上限", "高値だけ上限突破（終値は上限以下）",
                   _rate(sum(1 for e in entered
                             if e.upper_high_only_break and not e.upper_close_break), ne)),
        SummaryRow("レンジ上限", "終値でのレンジ上限突破率", _rate(len(broke), ne)),
    ]
    for t in GAIN_TARGETS:
        rows.append(
            SummaryRow(
                "上限突破後の伸び", f"上限突破後 +{t:.0f}% 到達率",
                _rate(sum(1 for e in broke if e.reached_gain[t]), len(broke)),
                "分母は終値で上限突破した件。基準は仮想ENTRY価格（翌日始値）",
            )
        )
    for t in GAIN_TARGETS:
        rows.append(
            SummaryRow(
                "全件の伸び", f"全件で +{t:.0f}% 到達率",
                _rate(sum(1 for e in entered if e.reached_gain[t]), ne),
                "基準は仮想ENTRY価格（翌日始値）",
            )
        )

    warn_events = [e for e in entered if e.warning_candles]
    broke_warn = [e for e in entered if e.warning_break_count > 0]
    new_high_first = [
        e for e in entered
        if e.warning_candles and e.warning_candles[0].new_high_vs_candle_high_before_break
    ]
    rows += [
        SummaryRow("EXIT（参考）", "警戒陰線が発生した件数", _rate(len(warn_events), ne),
                   f"陰線の総数 {sum(len(e.warning_candles) for e in entered)} 本"),
        SummaryRow("EXIT（参考）", "警戒陰線安値を割った件数", _rate(len(broke_warn), ne)),
        SummaryRow("EXIT（参考）", "最初の警戒陰線の安値を割る前に高値更新した件数",
                   _rate(len(new_high_first), len(warn_events)),
                   "「高値」は警戒陰線自身の高値で判定（定義未確定）"),
    ]

    for variant, label in (("strict", "参考A strict（押し安値の後に高値更新を要求）"),
                           ("loose", "参考B loose（押し安値の確定時点で引き上げ）")):
        sims = [
            (e, getattr(e, f"trail_sim_{variant}")) for e in entered
            if getattr(e, f"trail_sim_{variant}") is not None
        ]
        armed = [(e, s) for e, s in sims if s.armed]
        better = [(e, s) for e, s in armed if s.better_than_initial_stop]
        rows += [
            SummaryRow("トレーリング（参考）", f"{label}: trailが有効になった件数",
                       _rate(len(armed), ne),
                       "初期STOPより上の水準になった場合のみ有効とみなす"),
            SummaryRow("トレーリング（参考）",
                       f"{label}: 初期STOPより有利な撤退になった件数",
                       _rate(len(better), ne),
                       "参考シミュレーション。売買ルールにはしない"),
        ]
        rets = [s.exit_return_pct for _, s in armed if s.exit_return_pct is not None]
        if rets:
            rows.append(
                SummaryRow("トレーリング（参考）",
                           f"{label}: trail撤退時のリターン中央値",
                           f"{_median(rets):+.2f}%",
                           "仮想ENTRY価格（翌日始値）基準")
            )
        amb = [1 for _, s in armed if s.ambiguous_with_initial_stop]
        if amb:
            rows.append(
                SummaryRow("トレーリング（参考）",
                           f"{label}: trailと初期STOPが同日で順序不明",
                           _rate(len(amb), len(armed)))
            )

    stopped_rets = [
        e.exit_return_pct for e in entered
        if e.hit_initial_stop and e.exit_return_pct is not None
    ]
    if stopped_rets:
        rows.append(
            SummaryRow("参考リターン", "初期STOPのみで降りた場合のリターン中央値",
                       f"{_median(stopped_rets):+.2f}%",
                       "利確ルールが無いため、ほぼ全件がSTOPまで持ち切った結果。"
                       "戦略の成績ではない")
        )

    type_counts: dict[str, int] = {}
    for e in events:
        type_counts[e.type_label] = type_counts.get(e.type_label, 0) + 1
    for key in ("TYPE1", "TYPE2", "TYPE2b", "TYPE3", "TYPE4", "OPEN", "NO_ENTRY"):
        if key in type_counts:
            rows.append(SummaryRow("分類", TYPE_LABELS_JA[key], _rate(type_counts[key], n)))

    flag_counts: dict[str, int] = {}
    for e in events:
        for fl in e.flags:
            flag_counts[fl] = flag_counts.get(fl, 0) + 1
    for fl, c in sorted(flag_counts.items(), key=lambda kv: -kv[1]):
        rows.append(SummaryRow("フラグ（TYPE と排他ではない）",
                               FLAG_LABELS_JA.get(fl, fl), _rate(c, n)))

    truncated = [e for e in entered if e.tracking_truncated]
    rows.append(
        SummaryRow("データ制約", "データ終端時点で保有継続中",
                   _rate(len(truncated), ne),
                   "追跡が打ち切られており、その後の展開は不明")
    )
    return rows


def write_summary_csv(rows: list[SummaryRow], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(
            "# ENTRY 後の値動きの集計。勝率・平均利益率だけで戦略を評価しないこと（§12）。"
            " 分母が小さいので率は参考程度に留める。\n"
        )
        w = csv.DictWriter(f, fieldnames=["section", "metric", "value", "note"])
        w.writeheader()
        for r in rows:
            w.writerow(asdict(r))
    return path


TRAIL_COLUMNS = [f.name for f in fields(TrailCandidate)]


def write_trail_csv(events: list[TrackedEvent], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(
            "# トレーリング候補の参考シミュレーション。"
            "§30 の「新しい押し安値」は機械定義が未確定のため、"
            "既存の swing 検出（fractal, pivot_window=2）で確定した押し安値を暫定的に使っている。"
            " 本番の売買ルールには反映していない。\n"
        )
        w = csv.DictWriter(f, fieldnames=["code", "name", "signal_date", "initial_stop",
                                          *TRAIL_COLUMNS])
        w.writeheader()
        for ev in events:
            for tc in ev.trail_candidates:
                row = {k: _cell(v) for k, v in asdict(tc).items()}
                row.update(code=ev.code, name=ev.name,
                           signal_date=ev.signal_date.isoformat(),
                           initial_stop=_cell(ev.initial_stop))
                w.writerow(row)
    return path
