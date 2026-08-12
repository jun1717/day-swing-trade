"""保有銘柄の日次レビュー（TRADING_RULES.md §7 / v1）。

**このモジュールは「売れ」と言わない。**

研究フェーズ（RESEARCH_SUMMARY.md）で分かったのは、警戒陰線・reference_high・
押し安値・trail stop を機械で確定させると、正常な調整で早く降りるケースと
利益を大きく吐き出すケースが同時に増える、ということだった。32 イベントしか
無い母数でそれ以上詰めると過剰適合になる。

そこで v1 の EXIT は人間判断とし、このモジュールは

    **「今日チャートを見るべき保有銘柄はどれか」**

だけを示す。出すのは以下の 3 段階で、いずれも *見るべき理由* であって
売買指示ではない。

    SCENARIO_RISK  買った理由が崩れていないか確認する
    CAUTION        形に注意が要る
    REVIEW         一度チャートを見る

判定に使う閾値は既存の experimental.yaml のもの（大陰線 3.0% / 出来高急増 1.8倍）
だけで、EXIT 用の新しい閾値は**一切増やしていない**。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Sequence

from .indicators.swing import detect_swings
from .indicators.volume import trailing_avg_volume
from .models import Judgement, OHLCVBar, PriceSeries, SwingPoint
from .portfolio import Trade
from .rules.trend import evaluate_trend

# レビュー段階。数字が大きいほど強い注意喚起。
LEVEL_NONE = "OK"
LEVEL_REVIEW = "REVIEW"
LEVEL_CAUTION = "CAUTION"
LEVEL_SCENARIO_RISK = "SCENARIO_RISK"

LEVEL_ORDER: dict[str, int] = {
    LEVEL_NONE: 0,
    LEVEL_REVIEW: 1,
    LEVEL_CAUTION: 2,
    LEVEL_SCENARIO_RISK: 3,
}

LEVEL_LABELS_JA: dict[str, str] = {
    LEVEL_NONE: "変化なし",
    LEVEL_REVIEW: "チャート確認",
    LEVEL_CAUTION: "注意",
    LEVEL_SCENARIO_RISK: "シナリオ確認",
}

LEVEL_DESCRIPTIONS_JA: dict[str, str] = {
    LEVEL_NONE: "目立った変化はありません。保有継続の判断は人間が行ってください。",
    LEVEL_REVIEW: "今日の日足を一度見てください。売り判定ではありません。",
    LEVEL_CAUTION: "形に注意が要ります。日足を見て判断してください。売り判定ではありません。",
    LEVEL_SCENARIO_RISK: (
        "買った理由がまだ残っているかを確認してください。売り判定ではありません。"
    ),
}


@dataclass(frozen=True)
class ReviewSign:
    """1 つの「見るべき理由」。level と、なぜそう出たかの具体的な数値。"""

    key: str
    level: str
    label: str
    detail: str


@dataclass
class HoldingView:
    """保有 1 銘柄の当日の状態。数値は表示のためのもので、売買判定はしない。"""

    trade: Trade
    as_of: date | None = None
    close: float | None = None

    # 損益
    pnl_pct: float | None = None
    pnl_yen: float | None = None

    # STOP（初期STOPのみ。trail stop は v1 では自動化しない）
    initial_stop: float | None = None
    distance_to_stop_pct: float | None = None
    below_initial_stop: bool = False

    # 元レンジ
    range_lower: float | None = None
    range_upper: float | None = None
    reached_range_upper: bool = False
    closed_above_range_upper: bool = False
    below_range_lower: bool = False

    # 保有後の値動き
    holding_high: float | None = None
    holding_high_date: date | None = None
    holding_high_gain_pct: float | None = None
    drawdown_from_high_pct: float | None = None
    bars_held: int = 0

    # 直近の足
    ma25: float | None = None
    above_ma25: bool | None = None
    ma_direction: str | None = None
    is_uptrend: bool | None = None
    latest_body_pct: float | None = None
    volume: int | None = None
    volume_vs_avg20: float | None = None

    # 当日より前の直近陰線（CODEX_HANDOFF §30 の「警戒足」。v1 では**参考情報**）
    last_bearish_date: date | None = None
    last_bearish_low: float | None = None
    below_last_bearish_low: bool = False

    # 直近の局所安値候補（fractal。右側 pivot_window 本が確定するまで出ない）
    recent_swing_low: float | None = None
    recent_swing_low_date: date | None = None
    below_recent_swing_low: bool = False

    signs: list[ReviewSign] = field(default_factory=list)
    scenario: list[Judgement] = field(default_factory=list)
    note: str = ""

    @property
    def level(self) -> str:
        if not self.signs:
            return LEVEL_NONE
        return max((s.level for s in self.signs), key=lambda lv: LEVEL_ORDER.get(lv, 0))

    @property
    def level_label(self) -> str:
        return LEVEL_LABELS_JA.get(self.level, self.level)

    @property
    def sort_key(self) -> tuple:
        """強い注意喚起を上へ。同じ段階なら含み損の大きい順。"""
        return (
            -LEVEL_ORDER.get(self.level, 0),
            self.pnl_pct if self.pnl_pct is not None else 0.0,
            self.trade.code,
        )


def _pct(a: float, b: float) -> float:
    return (a - b) / b * 100.0


def _bars_held(
    bars: Sequence[OHLCVBar], entry_date: date | None, exit_date: date | None
) -> list[OHLCVBar]:
    """保有していた期間の足（ENTRY 日と EXIT 日を含む）。

    決済済みなら EXIT 日で打ち切る。打ち切らないと「保有後最高値」に
    降りたあとの上昇が混ざり、実際には取れていない値が出てしまう。
    """
    result = list(bars)
    if entry_date is not None:
        result = [b for b in result if b.date >= entry_date]
    if exit_date is not None:
        result = [b for b in result if b.date <= exit_date]
    return result


def _last_bearish(bars: Sequence[OHLCVBar]) -> OHLCVBar | None:
    for bar in reversed(bars):
        if bar.close < bar.open:
            return bar
    return None


def _recent_swing_low(
    lows: Sequence[SwingPoint], since: date | None
) -> SwingPoint | None:
    """ENTRY 日以降で最も新しい swing low。無ければ全体で最も新しいもの。"""
    if since is not None:
        after = [p for p in lows if p.date >= since]
        if after:
            return after[-1]
    return lows[-1] if lows else None


def build_view(
    trade: Trade,
    series: PriceSeries | None,
    cfg: Any,
    exp: Any,
) -> HoldingView:
    """保有 1 銘柄の当日の状態を組み立てる。

    株価キャッシュが無い・ENTRY 日以降の足が無い場合も、台帳の内容だけで
    行を返す（画面から保有が消えると「見落とし」になるため）。
    """
    view = HoldingView(
        trade=trade,
        initial_stop=trade.initial_stop,
        range_lower=trade.original_range_lower,
        range_upper=trade.original_range_upper,
    )

    if series is None or not series.bars:
        view.note = "株価キャッシュがありません（swing fetch を実行してください）。"
        return view

    bars = list(series.bars)
    latest = bars[-1]
    view.as_of = latest.date
    view.close = latest.close
    view.latest_body_pct = latest.body_pct
    view.volume = latest.volume

    avg20 = trailing_avg_volume(bars, len(bars) - 1, 20)
    if avg20:
        view.volume_vs_avg20 = latest.volume / avg20

    trend = evaluate_trend(bars, cfg, exp)
    view.ma25 = trend.ma
    view.ma_direction = trend.ma_direction
    view.is_uptrend = trend.is_uptrend
    if trend.ma is not None:
        view.above_ma25 = latest.close > trend.ma

    view.pnl_pct = trade.unrealized_pnl_pct(latest.close)
    if trade.entry_price is not None and trade.quantity:
        view.pnl_yen = (latest.close - trade.entry_price) * trade.quantity

    if trade.initial_stop:
        view.distance_to_stop_pct = _pct(latest.close, trade.initial_stop)
        view.below_initial_stop = latest.close <= trade.initial_stop

    held = _bars_held(bars, trade.entry_date, trade.exit_date)
    view.bars_held = len(held)
    if held:
        high_bar = max(held, key=lambda b: b.high)
        view.holding_high = high_bar.high
        view.holding_high_date = high_bar.date
        if trade.entry_price:
            view.holding_high_gain_pct = _pct(high_bar.high, trade.entry_price)
        if high_bar.high > 0:
            view.drawdown_from_high_pct = _pct(latest.close, high_bar.high)

        if trade.original_range_upper:
            upper = trade.original_range_upper
            view.reached_range_upper = any(b.high >= upper for b in held)
            view.closed_above_range_upper = any(b.close > upper for b in held)

    if trade.original_range_lower:
        view.below_range_lower = latest.close < trade.original_range_lower

    # 「当日より前の直近陰線」を使う。当日自身の安値と当日終値を比べても割れることは
    # 構造上ないので、割れ判定にはならない（CODEX_HANDOFF §30 の警戒足の見方）。
    bearish = _last_bearish((held or bars)[:-1])
    if bearish is not None:
        view.last_bearish_date = bearish.date
        view.last_bearish_low = bearish.low
        view.below_last_bearish_low = latest.close < bearish.low

    _, swing_lows = detect_swings(bars, exp)
    pivot = _recent_swing_low(swing_lows, trade.entry_date)
    if pivot is not None:
        view.recent_swing_low = pivot.price
        view.recent_swing_low_date = pivot.date
        view.below_recent_swing_low = latest.close < pivot.price

    view.scenario = _build_scenario(view, exp)
    if trade.is_open:
        view.signs = _build_signs(view, exp)
    else:
        # 決済済みに「チャートを見るべき理由」を出しても行動につながらない。
        # 数値は振り返りのために残し、注意喚起だけ出さない。
        view.note = f"{trade.exit_date} に決済済みです（{trade.exit_reason or '理由未記入'}）。"
    return view


def _build_signs(view: HoldingView, exp: Any) -> list[ReviewSign]:
    """「今日チャートを見るべき理由」を並べる。売買指示ではない。

    使う閾値は experimental.yaml の既存値のみ（大陰線 3.0% / 出来高急増 1.8倍）。
    EXIT 用の新しい閾値は導入しない。
    """
    signs: list[ReviewSign] = []
    close = view.close
    if close is None:
        return signs

    big_body = float(exp.get("range_quality.big_bearish_body_pct", 3.0))
    vol_ratio = float(exp.get("range_quality.big_bearish_volume_ratio", 1.8))

    # --- SCENARIO_RISK: 買った理由そのものに関わるもの --------------------------
    if view.below_initial_stop and view.initial_stop is not None:
        signs.append(
            ReviewSign(
                key="below_initial_stop",
                level=LEVEL_SCENARIO_RISK,
                label="初期STOP以下",
                detail=(
                    f"終値 {close:,.0f}円 ≦ 初期STOP {view.initial_stop:,.0f}円。"
                    "STOPは最大損失を保証する価格ではありません（ギャップダウン）。"
                ),
            )
        )
    if view.below_range_lower and view.range_lower is not None:
        signs.append(
            ReviewSign(
                key="below_range_lower",
                level=LEVEL_SCENARIO_RISK,
                label="元レンジ下限割れ",
                detail=(
                    f"終値 {close:,.0f}円 < 買ったときのレンジ下限 "
                    f"{view.range_lower:,.0f}円。下限反発というシナリオの前提が崩れています。"
                ),
            )
        )
    if view.above_ma25 is False and view.ma25 is not None:
        signs.append(
            ReviewSign(
                key="below_ma25",
                level=LEVEL_SCENARIO_RISK,
                label="MA25割れ",
                detail=f"終値 {close:,.0f}円 < MA25 {view.ma25:,.0f}円。上昇トレンドの前提が崩れています。",
            )
        )

    # --- CAUTION: 足の形 --------------------------------------------------------
    if view.latest_body_pct is not None and view.latest_body_pct <= -big_body:
        detail = f"当日の実体 {view.latest_body_pct:+.1f}%（大陰線の目安 -{big_body:.1f}%）"
        if view.volume_vs_avg20 is not None and view.volume_vs_avg20 >= vol_ratio:
            detail += f"。出来高は20日平均の {view.volume_vs_avg20:.1f}倍"
        signs.append(
            ReviewSign(key="big_bearish", level=LEVEL_CAUTION, label="大陰線", detail=detail)
        )
    elif (
        view.volume_vs_avg20 is not None
        and view.volume_vs_avg20 >= vol_ratio
        and view.latest_body_pct is not None
        and view.latest_body_pct < 0
    ):
        signs.append(
            ReviewSign(
                key="volume_spike",
                level=LEVEL_CAUTION,
                label="陰線＋出来高急増",
                detail=f"出来高が20日平均の {view.volume_vs_avg20:.1f}倍（急増の目安 {vol_ratio:.1f}倍）",
            )
        )

    if view.below_recent_swing_low and view.recent_swing_low is not None:
        signs.append(
            ReviewSign(
                key="below_swing_low",
                level=LEVEL_CAUTION,
                label="直近の局所安値割れ",
                detail=(
                    f"終値 {close:,.0f}円 < 直近の局所安値 {view.recent_swing_low:,.0f}円"
                    f"（{view.recent_swing_low_date:%m/%d}）"
                ),
            )
        )

    # --- REVIEW: 見ておきたい局面 ------------------------------------------------
    if view.below_last_bearish_low and view.last_bearish_low is not None:
        signs.append(
            ReviewSign(
                key="below_bearish_low",
                level=LEVEL_REVIEW,
                label="直近陰線の安値割れ",
                detail=(
                    f"終値 {close:,.0f}円 < 当日より前の直近陰線（{view.last_bearish_date:%m/%d}）の安値 "
                    f"{view.last_bearish_low:,.0f}円。利確を検討する材料であって、"
                    "自動の売り判定ではありません。"
                ),
            )
        )
    if view.closed_above_range_upper and view.range_upper is not None:
        signs.append(
            ReviewSign(
                key="above_range_upper",
                level=LEVEL_REVIEW,
                label="元レンジ上限を終値で突破済み",
                detail=(
                    f"買ったときの上限 {view.range_upper:,.0f}円を終値で超えています。"
                    "上昇シナリオが続くなら保有継続です。"
                ),
            )
        )
    elif view.reached_range_upper and view.range_upper is not None:
        signs.append(
            ReviewSign(
                key="reached_range_upper",
                level=LEVEL_REVIEW,
                label="元レンジ上限に到達済み",
                detail=f"買ったときの上限 {view.range_upper:,.0f}円に高値が到達しています（終値での突破はまだ）。",
            )
        )

    return signs


def _build_scenario(view: HoldingView, exp: Any) -> list[Judgement]:
    """「買った理由がまだ残っているか」の確認欄（§14）。

    ok=True/False は事実の成否であって、売買条件ではない。未確定の条件を
    勝手に売却ルールにしないため、すべて required=False で持つ。
    """
    close = view.close
    items: list[Judgement] = []

    items.append(
        Judgement(
            key="scenario.uptrend",
            label="上昇トレンド維持",
            ok=view.is_uptrend,
            detail=(
                f"MA25 {view.ma_direction or '—'}"
                + (f" / MA25 {view.ma25:,.0f}円" if view.ma25 is not None else "")
            ),
        )
    )
    items.append(
        Judgement(
            key="scenario.above_ma25",
            label="MA25維持",
            ok=view.above_ma25,
            detail=(
                f"終値 {close:,.0f}円 vs MA25 {view.ma25:,.0f}円"
                if close is not None and view.ma25 is not None
                else "MA25を算出できません"
            ),
        )
    )
    items.append(
        Judgement(
            key="scenario.range_upper",
            label="元レンジ上限突破済み",
            ok=view.closed_above_range_upper if view.range_upper is not None else None,
            detail=(
                f"上限 {view.range_upper:,.0f}円 / 高値到達 "
                f"{'済' if view.reached_range_upper else 'まだ'} / 終値突破 "
                f"{'済' if view.closed_above_range_upper else 'まだ'}"
                if view.range_upper is not None
                else "買ったときの上限が記録されていません"
            ),
        )
    )
    items.append(
        Judgement(
            key="scenario.support",
            label="重要支持帯を保っている",
            ok=(not view.below_range_lower) if view.range_lower is not None else None,
            detail=(
                f"元レンジ下限 {view.range_lower:,.0f}円"
                + (f" / 終値 {close:,.0f}円" if close is not None else "")
                if view.range_lower is not None
                else "買ったときの下限が記録されていません"
            ),
        )
    )

    big_body = float(exp.get("range_quality.big_bearish_body_pct", 3.0))
    items.append(
        Judgement(
            key="scenario.big_bearish",
            label="大きな陰線が出ていない",
            ok=(
                view.latest_body_pct > -big_body
                if view.latest_body_pct is not None
                else None
            ),
            detail=(
                f"当日の実体 {view.latest_body_pct:+.1f}%（目安 -{big_body:.1f}%）"
                if view.latest_body_pct is not None
                else "—"
            ),
        )
    )
    vol_ratio = float(exp.get("range_quality.big_bearish_volume_ratio", 1.8))
    items.append(
        Judgement(
            key="scenario.volume",
            label="出来高が急増していない",
            ok=(
                view.volume_vs_avg20 < vol_ratio
                if view.volume_vs_avg20 is not None
                else None
            ),
            detail=(
                f"20日平均の {view.volume_vs_avg20:.1f}倍（急増の目安 {vol_ratio:.1f}倍）"
                if view.volume_vs_avg20 is not None
                else "—"
            ),
        )
    )
    items.append(
        Judgement(
            key="scenario.swing_low",
            label="直近の局所安値を保っている",
            ok=(
                not view.below_recent_swing_low
                if view.recent_swing_low is not None
                else None
            ),
            detail=(
                f"{view.recent_swing_low:,.0f}円（{view.recent_swing_low_date:%m/%d}）"
                "／ fractalは右側の足が確定するまで出ません"
                if view.recent_swing_low is not None
                else "確定した局所安値がまだありません"
            ),
        )
    )
    return items


def build_views(
    trades: Sequence[Trade],
    price_map: dict[str, PriceSeries],
    cfg: Any,
    exp: Any,
) -> list[HoldingView]:
    """保有中の全銘柄をレビューし、注意喚起の強い順に並べる。"""
    views = [build_view(t, price_map.get(t.code), cfg, exp) for t in trades]
    views.sort(key=lambda v: v.sort_key)
    return views


def summarize_levels(views: Sequence[HoldingView]) -> dict[str, int]:
    counts = {lv: 0 for lv in (LEVEL_SCENARIO_RISK, LEVEL_CAUTION, LEVEL_REVIEW, LEVEL_NONE)}
    for v in views:
        counts[v.level] = counts.get(v.level, 0) + 1
    return counts
