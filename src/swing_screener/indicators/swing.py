"""swing high / swing low の検出（DESIGN.md §7 / CODEX_HANDOFF §11）。

pivot 検出アルゴリズムは未確定。実装を固定しないため method の registry にしている。
高値・安値の切り上げ判定はこの結果に依存するので、検出方法を変えると
トレンド判定の結論も変わる。だからこそ experimental.yaml で切り替える。
"""

from __future__ import annotations

from typing import Callable, Sequence

from ..models import OHLCVBar, SwingPoint

SwingMethod = Callable[[Sequence[OHLCVBar], object, int], "tuple[list, list]"]

_SWING_METHODS: dict[str, SwingMethod] = {}


def register_swing_method(name: str) -> Callable[[SwingMethod], SwingMethod]:
    def deco(fn: SwingMethod) -> SwingMethod:
        _SWING_METHODS[name] = fn
        return fn

    return deco


def available_swing_methods() -> tuple[str, ...]:
    return tuple(sorted(_SWING_METHODS))


@register_swing_method("fractal")
def _fractal(
    bars: Sequence[OHLCVBar], exp, offset: int
) -> tuple[list[SwingPoint], list[SwingPoint]]:
    """左右 pivot_window 本より高い(安い)高値(安値)を pivot とする。

    pivot_window を大きくすると pivot が減り、大きな波だけを見ることになる。
    直近 pivot_window 本は「右側」が揃わないため確定しない（後追いになる）。
    """
    w = int(exp.swing.pivot_window)
    highs: list[SwingPoint] = []
    lows: list[SwingPoint] = []
    if w < 1 or len(bars) < w * 2 + 1:
        return highs, lows

    for i in range(w, len(bars) - w):
        bar = bars[i]
        neighbours = list(range(i - w, i)) + list(range(i + 1, i + w + 1))
        if all(bar.high > bars[j].high for j in neighbours):
            highs.append(SwingPoint(offset + i, bar.date, bar.high))
        if all(bar.low < bars[j].low for j in neighbours):
            lows.append(SwingPoint(offset + i, bar.date, bar.low))
    return highs, lows


@register_swing_method("zigzag")
def _zigzag(
    bars: Sequence[OHLCVBar], exp, offset: int
) -> tuple[list[SwingPoint], list[SwingPoint]]:
    """zigzag_pct 以上の逆行が出た時点で直前の極値を pivot として確定する。

    fractal と違い「何%動いたか」で pivot を決めるため、小さなノイズを拾いにくい。
    """
    pct = float(exp.get("swing.zigzag_pct", 3.0)) / 100.0
    highs: list[SwingPoint] = []
    lows: list[SwingPoint] = []
    if len(bars) < 2 or pct <= 0:
        return highs, lows

    direction = 0  # 0=未確定 / 1=上昇中 / -1=下降中
    hi_i = lo_i = 0
    for i in range(1, len(bars)):
        bar = bars[i]
        if direction >= 0:
            if bar.high >= bars[hi_i].high:
                hi_i = i
            elif bar.low <= bars[hi_i].high * (1 - pct):
                highs.append(SwingPoint(offset + hi_i, bars[hi_i].date, bars[hi_i].high))
                direction = -1
                lo_i = i
        if direction <= 0:
            if bar.low <= bars[lo_i].low:
                lo_i = i
            elif bar.high >= bars[lo_i].low * (1 + pct):
                lows.append(SwingPoint(offset + lo_i, bars[lo_i].date, bars[lo_i].low))
                direction = 1
                hi_i = i
    return highs, lows


def detect_swings(
    bars: Sequence[OHLCVBar], exp
) -> tuple[tuple[SwingPoint, ...], tuple[SwingPoint, ...]]:
    """(swing highs, swing lows) を日付昇順で返す。index は bars 全体での位置。"""
    method = exp.swing.method
    fn = _SWING_METHODS.get(method)
    if fn is None:
        raise ValueError(
            f"未知の swing.method '{method}'（利用可能: {', '.join(available_swing_methods())}）"
        )

    lookback = int(exp.get("swing.lookback_bars", 0) or 0)
    work = list(bars[-lookback:]) if lookback > 0 else list(bars)
    offset = len(bars) - len(work)

    highs, lows = fn(work, exp, offset)
    return tuple(highs), tuple(lows)
