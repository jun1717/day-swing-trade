"""状態分類 OUT / RANGE / NEAR / ENTRY_CANDIDATE（DESIGN.md §8 / CODEX_HANDOFF §22）。

DESIGN.md の表の順に評価し、最初に該当した理由で OUT にする。
OUT になった銘柄も「なぜ落ちたか」を out_reason と judgements に残す。
これが精度改善の唯一の入力である（CODEX_HANDOFF §34）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..explain import fmt_md, fmt_pct, fmt_price
from ..models import (
    STATUS_ENTRY,
    STATUS_NEAR,
    STATUS_OUT,
    STATUS_RANGE,
    Judgement,
    OHLCVBar,
    PriceSeries,
    RangeCandidate,
    ReboundInfo,
    Stock,
    TrendResult,
    VolumeInfo,
)
from . import trend as trend_rules
from .range_detect import range_judgements


@dataclass(frozen=True)
class StatusOutcome:
    """status 判定の結果一式。ScreenResult へそのまま流し込む。"""

    status: str
    out_reason: str
    judgements: tuple[Judgement, ...]
    price_filter_ok: bool = False
    distance_to_lower_pct: float | None = None
    touched_lower_recently: bool = False
    days_since_lower_touch: int | None = None
    stop_price: float | None = None


# --- 事前チェック（データが揃っているか） -----------------------------------


def check_data(stock: Stock, series: PriceSeries | None, cfg) -> StatusOutcome | None:
    """enabled / データ不足を評価する。OUT なら StatusOutcome、続行可能なら None。"""
    if not stock.enabled:
        return StatusOutcome(
            status=STATUS_OUT,
            out_reason="監視対象外（stocks.csv の enabled=false）",
            judgements=(
                Judgement(
                    key="filter.enabled",
                    label="監視対象",
                    ok=False,
                    detail="enabled=false のためスクリーニング対象外",
                    required=True,
                ),
            ),
        )

    min_bars = int(cfg.get("data.min_bars", 60))
    period = int(cfg.ma.period)
    needed = max(min_bars, period)
    n = len(series.bars) if series is not None else 0

    if series is None or n == 0:
        return StatusOutcome(
            status=STATUS_OUT,
            out_reason="株価データがない（swing fetch を実行してください）",
            judgements=(
                Judgement(
                    key="filter.data",
                    label="株価データ",
                    ok=False,
                    detail="キャッシュに日足がない",
                    required=True,
                ),
            ),
        )

    if n < needed:
        return StatusOutcome(
            status=STATUS_OUT,
            out_reason=f"データ不足（{n}本 < 必要{needed}本）",
            judgements=(
                Judgement(
                    key="filter.data",
                    label="株価データ",
                    ok=False,
                    detail=f"{n}本しかない（MA{period}判定に必要 {needed}本）",
                    required=True,
                ),
            ),
        )
    return None


# --- 本判定 -----------------------------------------------------------------


def _price_filter_judgement(close: float, cfg) -> tuple[bool, Judgement]:
    """価格フィルタ。境界値は含む（2000ちょうど・7000ちょうどは通す）。"""
    lo = float(cfg.price_filter.min)
    hi = float(cfg.price_filter.max)
    ok = lo <= close <= hi
    detail = (
        f"{fmt_price(close)} は 対象レンジ {fmt_price(lo)}〜{fmt_price(hi)} の"
        + ("内（境界を含む）" if ok else "外")
    )
    return ok, Judgement(
        key="filter.price",
        label="株価フィルタ",
        ok=ok,
        detail=detail,
        required=True,
    )


def _last_touch_offset(
    bars: Sequence[OHLCVBar], range_: RangeCandidate
) -> tuple[int | None, OHLCVBar | None]:
    """レンジ window 内で最後に下限zoneへ触れたのが何営業日前か（当日=0）。"""
    last_index = len(bars) - 1
    for i in range(last_index, range_.start_index - 1, -1):
        if bars[i].low <= range_.lower_zone_high:
            return last_index - i, bars[i]
    return None, None


def _broke_down(
    prev_range: RangeCandidate | None, close: float, break_tol: float
) -> float | None:
    """前日までのレンジを下抜けているなら、その下抜け率(負値)を返す。

    レンジ window は常に最新足で終わるため、当日安値がそのまま range_lower に
    なる。つまり「採用レンジに対する当日終値」は構造上マイナスにならない。
    そこで「昨日までのレンジ」を基準に下抜けを判定する。これが実運用で言う
    レンジ崩壊（昨日まで支持されていた下限を今日割った）である。
    """
    if prev_range is None or not prev_range.lower:
        return None
    drop_pct = (close - prev_range.lower) / prev_range.lower * 100.0
    return drop_pct if drop_pct < -break_tol else None


def classify(
    *,
    bars: Sequence[OHLCVBar],
    trend: TrendResult,
    range_: RangeCandidate | None,
    candidates: Sequence[RangeCandidate],
    volume: VolumeInfo | None,
    rebound: ReboundInfo | None,
    cfg,
    exp,
    prev_range: RangeCandidate | None = None,
) -> StatusOutcome:
    """DESIGN.md §8 の順で状態を決める。judgements は表示順に連結して返す。"""
    close = bars[-1].close
    price_ok, j_price = _price_filter_judgement(close, cfg)
    range_js = range_judgements(range_, candidates, cfg, exp)

    def assemble(extra: Sequence[Judgement] = ()) -> tuple[Judgement, ...]:
        parts: list[Judgement] = [j_price]
        parts.extend(trend.judgements)
        parts.extend(range_js)
        if volume is not None:
            parts.extend(volume.judgements)
        if rebound is not None:
            parts.extend(rebound.judgements)
        parts.extend(extra)
        return tuple(parts)

    def status_judgement(status: str, detail: str) -> Judgement:
        return Judgement(
            key="status.result",
            label="状態",
            ok=status != STATUS_OUT,
            detail=f"{status} — {detail}",
            required=False,
        )

    # 1) 価格フィルタ
    if not price_ok:
        reason = (
            f"株価 {fmt_price(close)} が対象レンジ "
            f"{fmt_price(float(cfg.price_filter.min))}〜"
            f"{fmt_price(float(cfg.price_filter.max))} の外"
        )
        return StatusOutcome(
            status=STATUS_OUT,
            out_reason=reason,
            judgements=assemble((status_judgement(STATUS_OUT, reason),)),
            price_filter_ok=False,
        )

    # 2) 上昇トレンド
    if not trend.is_uptrend:
        failed = trend_rules.failed_required(trend)
        detail = "、".join(f"{j.label}: {j.detail}" for j in failed) or "必須条件を満たさない"
        reason = f"上昇トレンド条件を満たさない（{detail}）"
        return StatusOutcome(
            status=STATUS_OUT,
            out_reason=reason,
            judgements=assemble((status_judgement(STATUS_OUT, reason),)),
            price_filter_ok=True,
        )

    break_tol = float(exp.get("near.break_tolerance_pct", 1.5))

    # 3) 品質を満たすレンジ
    if range_ is None:
        # 「昨日まではレンジがあり、今日それを下抜けた」場合は、単なる
        # レンジ不成立ではなくレンジ崩壊として報告する（そのほうが原因が分かる）
        drop = _broke_down(prev_range, close, break_tol)
        if drop is not None and prev_range is not None:
            reason = (
                f"前日までのレンジ下限 {fmt_price(prev_range.lower)} を "
                f"{fmt_pct(drop)} 下抜け（許容 -{fmt_pct(break_tol, signed=False)}）"
                f"＝レンジ崩壊"
            )
            extra: tuple[Judgement, ...] = (
                Judgement(
                    key="status.range_break",
                    label="レンジ維持",
                    ok=False,
                    detail=reason,
                    required=True,
                ),
            )
        else:
            best_effort = max(candidates, key=lambda c: c.quality, default=None)
            if best_effort is not None:
                hint = (
                    f"最良は{best_effort.days}日 window（"
                    + " / ".join(best_effort.reject_reasons or ("理由なし",))
                    + "）"
                )
            else:
                hint = "評価できる window がない"
            reason = f"品質を満たすレンジがない（{hint}）"
            extra = ()
        return StatusOutcome(
            status=STATUS_OUT,
            out_reason=reason,
            judgements=assemble(extra + (status_judgement(STATUS_OUT, reason),)),
            price_filter_ok=True,
        )

    # --- ここから先はレンジあり ---
    lower = range_.lower
    distance = (close - lower) / lower * 100.0 if lower else None
    stop_price = lower * (1 - float(cfg.stop.buffer_pct))
    days_since, touch_bar = _last_touch_offset(bars, range_)

    j_distance = Judgement(
        key="status.distance",
        label="下限までの距離",
        ok=None,
        detail=(
            f"終値 {fmt_price(close)} は 下限 {fmt_price(lower)} から {fmt_pct(distance)}"
        ),
        required=False,
    )
    j_stop = Judgement(
        key="status.stop",
        label="損切り候補",
        ok=None,
        detail=(
            f"{fmt_price(stop_price)} = 下限 {fmt_price(lower)} の "
            f"{float(cfg.stop.buffer_pct) * 100:g}% 下"
        ),
        required=False,
    )

    # 4) レンジ崩壊（採用レンジ、または前日までのレンジの下限を大きく下抜け）
    prev_drop = _broke_down(prev_range, close, break_tol)
    if (distance is not None and distance < -break_tol) or prev_drop is not None:
        if prev_drop is not None and prev_range is not None and (
            distance is None or distance >= -break_tol
        ):
            reason = (
                f"前日までのレンジ下限 {fmt_price(prev_range.lower)} を "
                f"{fmt_pct(prev_drop)} 下抜け"
                f"（許容 -{fmt_pct(break_tol, signed=False)}）＝レンジ崩壊"
            )
        else:
            reason = (
                f"レンジ下限 {fmt_price(lower)} を {fmt_pct(distance)} 下抜け"
                f"（許容 -{fmt_pct(break_tol, signed=False)}）＝レンジ崩壊"
            )
        j_break = Judgement(
            key="status.range_break",
            label="レンジ維持",
            ok=False,
            detail=reason,
            required=True,
        )
        return StatusOutcome(
            status=STATUS_OUT,
            out_reason=reason,
            judgements=assemble(
                (j_distance, j_break, j_stop, status_judgement(STATUS_OUT, reason))
            ),
            price_filter_ok=True,
            distance_to_lower_pct=distance,
            touched_lower_recently=False,
            days_since_lower_touch=days_since,
            stop_price=stop_price,
        )

    # 5) NEAR 判定
    threshold = float(exp.near.lower_threshold_pct)
    lookback = int(exp.get("near.lookback_days", 0) or 0)
    near_by_distance = distance is not None and distance <= threshold
    # lookback は「当日を含む直近 N 本」。0 なら当日の距離だけで判定する
    # （CODEX_HANDOFF の厳密定義）。反発すると価格は下限から離れるため、
    # 0 のままだと「下限付近 かつ 反発確認」が同時成立しにくい。
    touched_recently = bool(
        lookback > 0 and days_since is not None and days_since < lookback
    )
    # 反発が強すぎて既にレンジ中央より上、というケースを落とすための任意の上限。
    # 既定は無効（DESIGN.md §8 のまま）。experimental.yaml に値を書けば有効になる。
    lookback_max = exp.get("near.lookback_max_distance_pct", None)
    rebounded_too_far = bool(
        touched_recently
        and lookback_max is not None
        and distance is not None
        and distance > float(lookback_max)
    )
    if rebounded_too_far:
        touched_recently = False

    # レンジ内位置による構造的ガード。
    # position = (close - lower) / (upper - lower)。0 が下限、1 が上限。
    # 距離(%)だけで判定すると、レンジ幅が広い銘柄では「下限から+7%」でも
    # 実際には上限に張り付いている、という状態を NEAR/ENTRY として拾ってしまう。
    # それは CODEX_HANDOFF §21 が新規エントリーに使わないと明記した
    # 「レンジ上限ブレイク」そのもので、最も買ってはいけない位置になる。
    # position はレンジ幅に自動でスケールするため、この取り違えを構造的に防ぐ。
    max_position = exp.get("near.max_position_in_range", None)
    position = None
    span = range_.upper - range_.lower
    if span > 0 and close is not None:
        position = (close - range_.lower) / span
    too_high_in_range = bool(
        max_position is not None
        and position is not None
        and position > float(max_position)
    )
    if too_high_in_range:
        near_by_distance = False
        touched_recently = False

    is_near = near_by_distance or touched_recently

    if too_high_in_range:
        near_detail = (
            f"レンジ内位置 {position:.2f} が上限寄り"
            f"（しきい値 {float(max_position):.2f}、0=下限 / 1=上限）。"
            f"下限まで {fmt_pct(distance)} だが既にレンジ上部にあり、"
            f"下限反発の買い場ではない"
        )
    elif near_by_distance:
        near_detail = (
            f"距離 {fmt_pct(distance)} <= しきい値 "
            f"{fmt_pct(threshold, signed=False)}"
        )
    elif touched_recently:
        near_detail = (
            f"距離 {fmt_pct(distance)} > しきい値 {fmt_pct(threshold, signed=False)} "
            f"だが {days_since}営業日前（{fmt_md(touch_bar.date) if touch_bar else '－'}）に"
            f"下限zone {fmt_price(range_.lower_zone_high)} 以下へ接触"
            f"（lookback {lookback}営業日）"
        )
    elif rebounded_too_far:
        near_detail = (
            f"{days_since}営業日前に下限zoneへ接触したが、既に "
            f"{fmt_pct(distance)} 離れており "
            f"near.lookback_max_distance_pct {lookback_max}% を超える"
        )
    else:
        near_detail = (
            f"距離 {fmt_pct(distance)} > しきい値 {fmt_pct(threshold, signed=False)}"
            + (
                f"、直近の下限zone接触は {days_since}営業日前"
                f"（lookback {lookback}営業日）"
                if days_since is not None
                else "、レンジ内に下限zone接触なし"
            )
        )

    j_near = Judgement(
        key="status.near",
        label="下限付近",
        ok=is_near,
        detail=near_detail,
        required=False,
    )

    if not is_near:
        status = STATUS_RANGE
        if too_high_in_range:
            summary = (
                f"上昇トレンド＋{range_.days}営業日のレンジ。ただしレンジ内位置 "
                f"{position:.2f} と上限寄りで、下限反発の買い場ではない"
            )
        else:
            summary = (
                f"上昇トレンド＋{range_.days}営業日のレンジ。下限まで {fmt_pct(distance)} で"
                f"まだ遠い（しきい値 {fmt_pct(threshold, signed=False)}）"
            )
    elif rebound is not None and rebound.confirmed:
        status = STATUS_ENTRY
        summary = (
            f"上昇トレンド＋レンジ下限付近（下限まで {fmt_pct(distance)}）＋反発確認"
            f"（終値 {fmt_price(close)} > 前日高値 {fmt_price(rebound.prev_high)}）。"
            f"必ず日足を人間が確認する"
        )
    else:
        status = STATUS_NEAR
        summary = (
            f"上昇トレンド＋レンジ下限付近（下限まで {fmt_pct(distance)}）。"
            f"反発確認はまだ＝最重要監視候補"
        )

    return StatusOutcome(
        status=status,
        out_reason="",
        judgements=assemble(
            (j_distance, j_near, j_stop, status_judgement(status, summary))
        ),
        price_filter_ok=True,
        distance_to_lower_pct=distance,
        touched_lower_recently=touched_recently,
        days_since_lower_touch=days_since,
        stop_price=stop_price,
    )
