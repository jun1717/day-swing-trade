"""上昇トレンド判定（DESIGN.md §8 / CODEX_HANDOFF §10, §11）。

判定項目:
  1. close > MA25
  2. MA25 の向き（up / flat / down）
  3. 高値切り上げ（直近2つの swing high 比較）
  4. 安値切り上げ（直近2つの swing low 比較）

どれを必須にするかは experimental.yaml の trend.require_* で切り替える。
初期値では 3・4 を必須にしていない。swing 検出アルゴリズムが未確定であり、
必須にすると「拾ってほしい銘柄」を取りこぼす方向に効くためである。
"""

from __future__ import annotations

from typing import Sequence

from ..explain import fmt_md, fmt_pct, fmt_price
from ..indicators.ma import DIRECTION_LABEL, calc_ma_series, ma_slope
from ..indicators.swing import detect_swings
from ..models import Judgement, OHLCVBar, SwingPoint, TrendResult


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _swing_compare(
    points: Sequence[SwingPoint], key: str, label: str, required: bool, what: str
) -> tuple[bool | None, Judgement]:
    """直近2つの swing を比較して「切り上げているか」を判定する。"""
    if len(points) < 2:
        return None, Judgement(
            key=key,
            label=label,
            ok=None,
            detail=f"{what}が {len(points)}個しか検出できず判定不能",
            required=required,
        )
    prev, last = points[-2], points[-1]
    ok = last.price > prev.price
    diff_pct = (last.price - prev.price) / prev.price * 100 if prev.price else 0.0
    sign = ">" if ok else "<="
    detail = (
        f"直近 {fmt_price(last.price)}({fmt_md(last.date)}) {sign} "
        f"前回 {fmt_price(prev.price)}({fmt_md(prev.date)}) ({fmt_pct(diff_pct)})"
    )
    return ok, Judgement(key=key, label=label, ok=ok, detail=detail, required=required)


def evaluate_trend(bars: Sequence[OHLCVBar], cfg, exp) -> TrendResult:
    """上昇トレンドを判定する。必須条件がすべて OK のとき is_uptrend=True。"""
    period = int(cfg.ma.period)
    ma_series = calc_ma_series(bars, period)
    ma = ma_series[-1] if ma_series else None
    close = bars[-1].close if bars else None

    deviation = None
    if ma and close is not None:
        deviation = (close - ma) / ma * 100.0

    direction, slope_pct, slope_detail = ma_slope(ma_series, exp, period)
    highs, lows = detect_swings(bars, exp)

    req_close = bool(exp.get("trend.require_close_above_ma", True))
    req_ma_up = bool(exp.get("trend.require_ma_up", True))
    req_hh = bool(exp.get("trend.require_higher_highs", False))
    req_hl = bool(exp.get("trend.require_higher_lows", False))

    # 1. 株価 > MA
    close_above_ma = bool(ma is not None and close is not None and close > ma)
    if ma is None:
        close_detail = f"MA{period} が未確定（データ不足）"
    else:
        close_detail = (
            f"{fmt_price(close)} {'>' if close_above_ma else '<='} MA{period} "
            f"{fmt_price(ma)} ({fmt_pct(deviation)})"
        )
    j_close = Judgement(
        key="trend.close_above_ma",
        label=f"株価 > MA{period}",
        ok=close_above_ma,
        detail=close_detail,
        required=req_close,
    )

    # 2. MA の向き
    j_dir = Judgement(
        key="trend.ma_direction",
        label=f"MA{period}の向き",
        ok=(direction == "up"),
        detail=f"{DIRECTION_LABEL.get(direction, direction)} — {slope_detail}",
        required=req_ma_up,
    )

    # 3, 4. 高値・安値切り上げ
    higher_highs, j_hh = _swing_compare(
        highs, "trend.higher_highs", "高値切り上げ", req_hh, "swing high"
    )
    higher_lows, j_hl = _swing_compare(
        lows, "trend.higher_lows", "安値切り上げ", req_hl, "swing low"
    )

    # トレンド強度（並び順の第2キー。売買条件ではない）
    dev_w = float(exp.get("trend_strength.deviation_weight", 0.5))
    slope_w = float(exp.get("trend_strength.slope_weight", 0.5))
    dev_cap = float(exp.get("trend_strength.deviation_cap_pct", 15.0))
    slope_cap = float(exp.get("trend_strength.slope_cap_pct", 5.0))
    dev_norm = _clamp01((deviation or 0.0) / dev_cap) if dev_cap else 0.0
    slope_norm = _clamp01((slope_pct or 0.0) / slope_cap) if slope_cap else 0.0
    total_w = dev_w + slope_w
    strength = (dev_w * dev_norm + slope_w * slope_norm) / total_w if total_w else 0.0
    j_strength = Judgement(
        key="trend.strength",
        label="トレンド強度",
        ok=None,
        detail=(
            f"{strength:.2f} ← 乖離 {fmt_pct(deviation)}"
            f"（正規化 {dev_norm:.2f} × 重み{dev_w}）"
            f" + 傾き {fmt_pct(slope_pct)}（正規化 {slope_norm:.2f} × 重み{slope_w}）"
        ),
        required=False,
    )

    judgements = (j_dir, j_close, j_hh, j_hl, j_strength)
    is_uptrend = all(j.ok is True for j in judgements if j.required)

    return TrendResult(
        ma=ma,
        ma_deviation_pct=deviation,
        ma_direction=direction,
        ma_slope_pct=slope_pct,
        close_above_ma=close_above_ma,
        higher_highs=higher_highs,
        higher_lows=higher_lows,
        swing_highs=highs,
        swing_lows=lows,
        is_uptrend=is_uptrend,
        strength=strength,
        judgements=judgements,
    )


def failed_required(trend: TrendResult) -> list[Judgement]:
    """必須条件のうち満たせなかったもの（OUT 理由の生成に使う）。"""
    return [j for j in trend.judgements if j.required and j.ok is not True]
