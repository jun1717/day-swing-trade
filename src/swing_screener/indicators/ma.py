"""移動平均と、その傾き判定（DESIGN.md §7 / CODEX_HANDOFF §10）。

MA25 を「上向き」と判定する方法は未確定なので、実装をコードに固定せず
experimental.yaml の `ma_slope.method` で切り替えられる registry 形式にしている。
method を増やしたい場合は @register_slope_method で追加するだけでよい。
"""

from __future__ import annotations

from typing import Callable, Sequence

from ..explain import fmt_pct, fmt_price
from ..models import OHLCVBar

# 傾き算出メソッド: (MA値の並び(Noneなし), lookback) -> (slope_pct, 説明テキスト)
SlopeMethod = Callable[[Sequence[float], int, int], "tuple[float | None, str]"]

_SLOPE_METHODS: dict[str, SlopeMethod] = {}


def register_slope_method(name: str) -> Callable[[SlopeMethod], SlopeMethod]:
    def deco(fn: SlopeMethod) -> SlopeMethod:
        _SLOPE_METHODS[name] = fn
        return fn

    return deco


def available_slope_methods() -> tuple[str, ...]:
    return tuple(sorted(_SLOPE_METHODS))


# --- 移動平均 ---------------------------------------------------------------


def calc_ma_series(bars: Sequence[OHLCVBar], period: int) -> list[float | None]:
    """終値の単純移動平均。bars と同じ長さで、確定前は None を返す。"""
    if period <= 0:
        raise ValueError("period は 1 以上である必要があります")
    out: list[float | None] = []
    total = 0.0
    for i, bar in enumerate(bars):
        total += bar.close
        if i >= period:
            total -= bars[i - period].close
        out.append(total / period if i >= period - 1 else None)
    return out


# --- 傾きの算出メソッド -----------------------------------------------------


@register_slope_method("vs_n_days_ago")
def _slope_vs_n_days_ago(
    values: Sequence[float], lookback: int, period: int
) -> tuple[float | None, str]:
    """N営業日前の MA と比較する。もっとも素直な実装。"""
    now = values[-1]
    before = values[-1 - lookback]
    if before == 0:
        return None, "比較対象の MA が 0 のため判定不能"
    slope_pct = (now - before) / before * 100.0
    sign = ">" if now > before else ("<" if now < before else "=")
    detail = (
        f"MA{period} {fmt_price(now)} {sign} {lookback}日前 {fmt_price(before)}"
        f" ({fmt_pct(slope_pct)})"
    )
    return slope_pct, detail


@register_slope_method("linreg")
def _slope_linreg(
    values: Sequence[float], lookback: int, period: int
) -> tuple[float | None, str]:
    """直近 lookback+1 本の線形回帰。ノイズに強いが反応は鈍い。

    回帰の傾き(円/日)を lookback 日分に伸ばし、期間平均で割って % 換算する。
    こうすると vs_n_days_ago と同じスケールで min_slope_pct を比較できる。
    """
    ys = list(values[-(lookback + 1) :])
    n = len(ys)
    if n < 2:
        return None, "MA の本数が不足しているため判定不能"
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0 or mean_y == 0:
        return None, "回帰が計算できないため判定不能"
    slope_per_day = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
    slope_pct = slope_per_day * lookback / mean_y * 100.0
    detail = (
        f"MA{period} 直近{n}本の回帰傾き {slope_per_day:+,.1f}円/日"
        f"（{lookback}日換算 {fmt_pct(slope_pct)}、平均 {fmt_price(mean_y)}）"
    )
    return slope_pct, detail


# --- 傾き判定 ---------------------------------------------------------------


def ma_slope(
    ma_series: Sequence[float | None], exp, period: int = 25
) -> tuple[str, float | None, str]:
    """MA の向きを判定して (direction, slope_pct, detail) を返す。

    direction は "up" / "flat" / "down"。しきい値 min_slope_pct を超えたら up、
    下回ったら down、その間は flat。すべて experimental.yaml から読む。
    """
    method = exp.ma_slope.method
    lookback = int(exp.ma_slope.lookback)
    min_slope_pct = float(exp.get("ma_slope.min_slope_pct", 0.0))

    fn = _SLOPE_METHODS.get(method)
    if fn is None:
        raise ValueError(
            f"未知の ma_slope.method '{method}'（利用可能: {', '.join(available_slope_methods())}）"
        )

    values = [v for v in ma_series if v is not None]
    if len(values) < lookback + 1:
        return (
            "flat",
            None,
            f"MA{period} の履歴が {len(values)}本しかなく {lookback}日前と比較できない",
        )

    slope_pct, detail = fn(values, lookback, period)
    if slope_pct is None:
        return "flat", None, detail

    if slope_pct > min_slope_pct:
        direction = "up"
    elif slope_pct < -min_slope_pct:
        direction = "down"
    else:
        direction = "flat"
    return direction, slope_pct, detail


DIRECTION_LABEL = {"up": "上向き", "flat": "横ばい", "down": "下向き"}
