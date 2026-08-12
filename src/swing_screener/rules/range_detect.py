"""短期レンジ検出（DESIGN.md §8 / CODEX_HANDOFF §12, §13, §14, §15）。

`range.min_days`〜`range.max_days`（既定 3〜10）の各 window を評価し、
除外条件に該当せず品質が最も高いものを採用する。

v0.1 の目的は完璧なレンジ認識ではなく「人間が確認する価値のあるチャートを拾う」
ことなので、判定はヒューリスティックで構わない。ただし
  * どの window を検討したか
  * なぜ不採用にしたか
  * 品質スコアの内訳
をすべて残す。ここが後からパラメータを調整するための唯一の入力になる。

しきい値・重みはすべて experimental.yaml から読む。YAML に存在しないキーは
`exp.get(path, default)` で既定値を使う（キーを足すだけで調整可能）。
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

from ..explain import fmt_md, fmt_pct, fmt_price, fmt_ratio
from ..indicators.volume import trailing_avg_volume
from ..models import Judgement, OHLCVBar, RangeCandidate


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _mean(values: Sequence[float]) -> float | None:
    values = list(values)
    if not values:
        return None
    return sum(values) / len(values)


# --- window ごとの算出値 ----------------------------------------------------


def count_lower_touches(
    window: Sequence[OHLCVBar], lower_zone_high: float
) -> tuple[int, tuple[date, ...]]:
    """下限zoneへの「反応回数」と、その代表日を返す。

    重要: 連続した日は 1 回の反応としてまとめる。2日続けて下限を這うのは
    ヒトの目には 1 回の反応であり、素朴に日数を数えると反応回数が水増しされて
    レンジ品質の評価が壊れる。
    代表日は連続グループ内で最も安値が低かった日とする。
    """
    groups: list[list[OHLCVBar]] = []
    current: list[OHLCVBar] = []
    for bar in window:
        if bar.low <= lower_zone_high:
            current.append(bar)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    dates = tuple(min(g, key=lambda b: b.low).date for g in groups)
    return len(groups), dates


def _volatility_change(window: Sequence[OHLCVBar]) -> float:
    """後半の平均日中値幅 / 前半の平均日中値幅。1 未満なら収縮＝レンジらしい。"""
    half = len(window) // 2
    if half == 0:
        return 1.0
    first = _mean([b.range_pct for b in window[:half]])
    second = _mean([b.range_pct for b in window[-half:]])
    if not first:
        return 1.0
    return second / first


def _volume_change(bars: Sequence[OHLCVBar], start: int, days: int) -> float:
    """window の平均出来高 / 直前同日数の平均出来高。"""
    pre = bars[max(0, start - days) : start]
    if not pre:
        return 1.0
    win_avg = _mean([float(b.volume) for b in bars[start:]])
    pre_avg = _mean([float(b.volume) for b in pre])
    if not pre_avg or win_avg is None:
        return 1.0
    return win_avg / pre_avg


def _max_lower_low_streak(window: Sequence[OHLCVBar]) -> tuple[int, list[OHLCVBar]]:
    """安値が連続して切り下がっている最長区間（本数と該当足）。

    [10, 9, 8] は「3本連続で切り下がり」と数える（比較回数ではなく本数）。
    こう定義しないと最短の3日windowが永久に除外されない。
    """
    best_len, best_start = 1, 0
    cur_len, cur_start = 1, 0
    for i in range(1, len(window)):
        if window[i].low < window[i - 1].low:
            cur_len += 1
        else:
            cur_len, cur_start = 1, i
        if cur_len > best_len:
            best_len, best_start = cur_len, cur_start
    return best_len, list(window[best_start : best_start + best_len])


def _find_big_bearish(
    bars: Sequence[OHLCVBar], start: int, exp
) -> tuple[OHLCVBar, float] | None:
    """大陰線＋出来高急増の足を探す（レンジの前提が崩れている合図）。"""
    body_pct = float(exp.get("range_quality.big_bearish_body_pct", 3.0))
    vol_ratio = float(exp.get("range_quality.big_bearish_volume_ratio", 1.8))
    for i in range(start, len(bars)):
        bar = bars[i]
        if bar.body_pct > -body_pct:
            continue
        avg20 = trailing_avg_volume(bars, i, 20)
        if not avg20:
            continue
        ratio = bar.volume / avg20
        if ratio >= vol_ratio:
            return bar, ratio
    return None


# --- 品質スコア -------------------------------------------------------------

_DEFAULT_WEIGHTS = {
    "narrowness": 0.25,
    "lower_touches": 0.30,
    "volatility_contraction": 0.20,
    "volume_contraction": 0.15,
    "duration": 0.10,
}


def _quality(
    *,
    days: int,
    width_pct: float,
    touch_count: int,
    volatility_change: float,
    volume_change: float,
    cfg,
    exp,
) -> tuple[float, tuple[Judgement, ...]]:
    """0〜1 の品質スコアと、その内訳を返す。内訳は必ず UI に出す。"""
    min_width = float(exp.get("range_quality.min_width_pct", 1.0))
    max_width = float(exp.get("range_quality.max_width_pct", 10.0))
    max_vc = float(exp.get("range_quality.max_volatility_change", 1.15))
    # 「これ以上収縮していれば満点」の基準。experimental に置けば調整できる。
    ideal_vc = float(exp.get("range_quality.ideal_volatility_change", 0.60))
    contract = float(exp.get("volume.contract_ratio", 0.80))
    expand = float(exp.get("volume.expand_ratio", 1.30))
    min_touches = int(cfg.range.min_lower_touches)
    min_days = int(cfg.range.min_days)
    max_days = int(cfg.range.max_days)

    weights = dict(_DEFAULT_WEIGHTS)
    for name in weights:
        weights[name] = float(exp.get(f"range_quality.weights.{name}", weights[name]))

    # 値幅は狭いほど良い（min_width で 1.0、max_width で 0.0）
    span = max_width - min_width
    narrowness = _clamp01((max_width - width_pct) / span) if span > 0 else 0.0
    # 下限反応は min_lower_touches で満点
    touches = _clamp01(touch_count / min_touches) if min_touches > 0 else 1.0
    # 値幅収縮は ideal_vc で満点、max_vc で 0
    vc_span = max_vc - ideal_vc
    vola = _clamp01((max_vc - volatility_change) / vc_span) if vc_span > 0 else 0.0
    # 出来高は contract_ratio 以下で満点、expand_ratio 以上で 0
    vol_span = expand - contract
    vol = _clamp01((expand - volume_change) / vol_span) if vol_span > 0 else 0.0
    # 日数は長いほうが僅かに良い
    day_span = max_days - min_days
    duration = _clamp01((days - min_days) / day_span) if day_span > 0 else 1.0

    parts = (
        ("narrowness", "値幅の狭さ", narrowness, f"値幅 {fmt_pct(width_pct, signed=False)}"),
        ("lower_touches", "下限反応", touches, f"{touch_count}回 / 目標{min_touches}回"),
        (
            "volatility_contraction",
            "値幅の収縮",
            vola,
            f"後半/前半 {fmt_ratio(volatility_change)}",
        ),
        (
            "volume_contraction",
            "出来高の減少",
            vol,
            f"レンジ/直前 {fmt_ratio(volume_change)}",
        ),
        ("duration", "日数", duration, f"{days}営業日"),
    )

    total_w = sum(weights.values())
    score = sum(weights[name] * value for name, _, value, _ in parts)
    quality = score / total_w if total_w else 0.0

    breakdown = tuple(
        Judgement(
            key=f"range.quality.{name}",
            label=label,
            ok=None,
            detail=f"{src} → {value:.2f} × 重み{weights[name]:g}",
            required=False,
        )
        for name, label, value, src in parts
    )
    return quality, breakdown


# --- window 評価 ------------------------------------------------------------


def evaluate_window(bars: Sequence[OHLCVBar], days: int, cfg, exp) -> RangeCandidate:
    """直近 days 本を 1 つのレンジ候補として評価する。"""
    if days <= 0 or days > len(bars):
        raise ValueError(f"days={days} は bars({len(bars)}本) に対して不正です")

    start = len(bars) - days
    window = list(bars[start:])

    upper = max(b.high for b in window)
    lower = min(b.low for b in window)
    lt = float(exp.range_zone.lower_tolerance_pct) / 100.0
    ut = float(exp.range_zone.upper_tolerance_pct) / 100.0
    lower_zone_low, lower_zone_high = lower * (1 - lt), lower * (1 + lt)
    upper_zone_low, upper_zone_high = upper * (1 - ut), upper * (1 + ut)

    width_pct = (upper - lower) / lower * 100.0 if lower else 0.0
    touch_count, touch_dates = count_lower_touches(window, lower_zone_high)
    volatility_change = _volatility_change(window)
    volume_change = _volume_change(bars, start, days)

    # --- 除外条件（CODEX_HANDOFF §12 の「除外」） ---
    reasons: list[str] = []
    min_width = float(exp.get("range_quality.min_width_pct", 1.0))
    max_width = float(exp.get("range_quality.max_width_pct", 10.0))
    max_vc = float(exp.get("range_quality.max_volatility_change", 1.15))
    max_streak = int(exp.get("range_quality.reject_consecutive_lower_lows", 3))

    if width_pct > max_width:
        reasons.append(
            f"値幅が広すぎる（{fmt_pct(width_pct, signed=False)} > "
            f"{fmt_pct(max_width, signed=False)}）"
        )
    if width_pct < min_width:
        reasons.append(
            f"値幅が狭すぎて動きがない（{fmt_pct(width_pct, signed=False)} < "
            f"{fmt_pct(min_width, signed=False)}）"
        )

    streak, streak_bars = _max_lower_low_streak(window)
    if max_streak > 0 and streak >= max_streak:
        chain = "→".join(fmt_price(b.low, unit="") for b in streak_bars)
        reasons.append(f"安値が{streak}本連続で切り下がり（{chain}）")

    big = _find_big_bearish(bars, start, exp)
    if big is not None:
        bar, ratio = big
        reasons.append(
            f"大陰線＋出来高急増 {fmt_md(bar.date)}"
            f"（実体 {fmt_pct(bar.body_pct)}、出来高 20日平均の{fmt_ratio(ratio)}倍）"
        )

    if volatility_change > max_vc:
        reasons.append(
            f"日々の値幅が拡大中（後半/前半 {fmt_ratio(volatility_change)} > "
            f"{fmt_ratio(max_vc)}）"
        )

    quality, breakdown = _quality(
        days=days,
        width_pct=width_pct,
        touch_count=touch_count,
        volatility_change=volatility_change,
        volume_change=volume_change,
        cfg=cfg,
        exp=exp,
    )

    min_quality = float(exp.range_quality.min_quality)
    if not reasons and quality < min_quality:
        reasons.append(
            f"レンジ品質が不足（{fmt_ratio(quality)} < {fmt_ratio(min_quality)}）"
        )

    return RangeCandidate(
        days=days,
        start_index=start,
        end_index=len(bars) - 1,
        start_date=window[0].date,
        end_date=window[-1].date,
        upper=upper,
        upper_zone_low=upper_zone_low,
        upper_zone_high=upper_zone_high,
        lower=lower,
        lower_zone_low=lower_zone_low,
        lower_zone_high=lower_zone_high,
        width_pct=width_pct,
        lower_touch_count=touch_count,
        lower_touch_dates=touch_dates,
        volatility_change=volatility_change,
        volume_change=volume_change,
        quality=quality,
        accepted=not reasons,
        reject_reasons=tuple(reasons),
        quality_breakdown=breakdown,
    )


def detect_range(
    bars: Sequence[OHLCVBar], cfg, exp
) -> tuple[RangeCandidate | None, tuple[RangeCandidate, ...]]:
    """3〜10日の全 window を評価し (採用レンジ, 全候補) を返す。

    採用レンジは「除外条件に該当せず品質しきい値を満たす window」のうち
    quality が最大のもの。同点なら日数の長いほうを採る（形として見やすい）。
    """
    min_days = int(cfg.range.min_days)
    max_days = int(cfg.range.max_days)

    candidates: list[RangeCandidate] = []
    for days in range(min_days, max_days + 1):
        if days > len(bars):
            break
        candidates.append(evaluate_window(bars, days, cfg, exp))

    accepted = [c for c in candidates if c.accepted]
    best = max(accepted, key=lambda c: (c.quality, c.days)) if accepted else None
    return best, tuple(candidates)


# --- 表示用 Judgement -------------------------------------------------------


def range_judgements(
    best: RangeCandidate | None,
    candidates: Sequence[RangeCandidate],
    cfg,
    exp,
) -> tuple[Judgement, ...]:
    """採用レンジと、検討した window 一覧を Judgement 化する。"""
    min_touches = int(cfg.range.min_lower_touches)
    min_quality = float(exp.range_quality.min_quality)
    lt = float(exp.range_zone.lower_tolerance_pct)
    max_width = float(exp.get("range_quality.max_width_pct", 10.0))
    min_width = float(exp.get("range_quality.min_width_pct", 1.0))
    max_vc = float(exp.get("range_quality.max_volatility_change", 1.15))

    summary = _candidates_summary(best, candidates)

    if best is None:
        return (
            Judgement(
                key="range.found",
                label="短期レンジ",
                ok=False,
                detail=(
                    f"{int(cfg.range.min_days)}〜{int(cfg.range.max_days)}日の"
                    f"{len(candidates)}通りを評価したが、採用できる window がない"
                ),
                required=True,
            ),
            Judgement(
                key="range.candidates",
                label="検討した window",
                ok=None,
                detail=summary,
                required=False,
            ),
        )

    return (
        Judgement(
            key="range.found",
            label="短期レンジ",
            ok=True,
            detail=(
                f"{best.days}営業日 ({fmt_md(best.start_date)}〜{fmt_md(best.end_date)})"
            ),
            required=True,
        ),
        Judgement(
            key="range.bounds",
            label="レンジ上限/下限",
            ok=None,
            detail=(
                f"下限 {fmt_price(best.lower)}"
                f"（zone {fmt_price(best.lower_zone_low)}〜{fmt_price(best.lower_zone_high)}）"
                f" / 上限 {fmt_price(best.upper)}"
                f"（zone {fmt_price(best.upper_zone_low)}〜{fmt_price(best.upper_zone_high)}）"
            ),
            required=False,
        ),
        Judgement(
            key="range.width",
            label="値幅",
            ok=min_width <= best.width_pct <= max_width,
            detail=(
                f"{fmt_pct(best.width_pct, signed=False)}"
                f"（許容 {fmt_pct(min_width, signed=False)}〜"
                f"{fmt_pct(max_width, signed=False)}）"
            ),
            required=False,
        ),
        Judgement(
            key="range.lower_touches",
            label="下限反応",
            ok=best.lower_touch_count >= min_touches,
            detail=(
                f"下限{fmt_price(best.lower)}±{fmt_pct(lt, signed=False)} に "
                f"{best.lower_touch_count}回反応"
                + (
                    f" ({', '.join(fmt_md(d) for d in best.lower_touch_dates)})"
                    if best.lower_touch_dates
                    else ""
                )
                + f"（目標 {min_touches}回・連続日は1回に集約）"
            ),
            required=False,
        ),
        Judgement(
            key="range.volatility",
            label="値幅の推移",
            ok=best.volatility_change <= max_vc,
            detail=(
                f"後半/前半 = {fmt_ratio(best.volatility_change)}"
                f"（1.00未満が収縮、除外は {fmt_ratio(max_vc)} 超）"
            ),
            required=False,
        ),
        Judgement(
            key="range.volume_change",
            label="レンジ中の出来高",
            ok=None,
            detail=f"レンジ平均/直前同日数平均 = {fmt_ratio(best.volume_change)}",
            required=False,
        ),
        Judgement(
            key="range.quality",
            label="レンジ品質",
            ok=best.quality >= min_quality,
            detail=(
                f"{fmt_ratio(best.quality)} ≧ しきい値 {fmt_ratio(min_quality)}"
                if best.quality >= min_quality
                else f"{fmt_ratio(best.quality)} < しきい値 {fmt_ratio(min_quality)}"
            )
            + "（内訳: "
            + " / ".join(j.detail for j in best.quality_breakdown)
            + "）",
            required=False,
        ),
        Judgement(
            key="range.candidates",
            label="検討した window",
            ok=None,
            detail=summary,
            required=False,
        ),
    )


def _candidates_summary(
    best: RangeCandidate | None, candidates: Sequence[RangeCandidate]
) -> str:
    """「なぜこのレンジになったか」を1行で示す。"""
    parts: list[str] = []
    for c in candidates:
        mark = "採用" if best is not None and c.days == best.days else ""
        if c.accepted:
            body = f"品質{fmt_ratio(c.quality)}"
            if not mark:
                mark = "可"
        else:
            body = " / ".join(c.reject_reasons)
            mark = "不採用"
        parts.append(f"{c.days}日={mark}({body})")
    return "、".join(parts) if parts else "評価できる window なし"
