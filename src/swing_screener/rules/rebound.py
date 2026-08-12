"""反発確認（DESIGN.md §8 / CODEX_HANDOFF §18）。

確定ルールはただ 1 つ:

    rebound_confirmed = 当日終値 > 前日高値

陽線・長い下ヒゲ・出来高回復は「加点材料」であり、単独では ENTRY 判定しない。
表示のみに使う。
"""

from __future__ import annotations

from typing import Sequence

from ..explain import fmt_pct, fmt_price, fmt_ratio, fmt_volume
from ..models import Judgement, OHLCVBar, ReboundInfo, VolumeInfo


def evaluate_rebound(
    bars: Sequence[OHLCVBar], exp, volume: VolumeInfo | None = None
) -> ReboundInfo:
    if len(bars) < 2:
        return ReboundInfo(
            prev_high=None,
            confirmed=False,
            bullish_candle=False,
            long_lower_wick=False,
            volume_recovered=False,
            judgements=(
                Judgement(
                    key="rebound.confirmed",
                    label="反発確認",
                    ok=False,
                    detail="前日足がなく判定不能",
                    required=False,
                ),
            ),
        )

    latest, prev = bars[-1], bars[-2]
    prev_high = prev.high
    confirmed = latest.close > prev_high
    diff_pct = (latest.close - prev_high) / prev_high * 100 if prev_high else 0.0

    # --- 加点材料 ---
    bullish = latest.close > latest.open
    body = abs(latest.close - latest.open)
    lower_wick = min(latest.open, latest.close) - latest.low
    day_range = latest.high - latest.low
    wick_ratio = float(exp.get("rebound.long_lower_wick_ratio", 1.5))
    if body > 0:
        long_lower_wick = lower_wick >= body * wick_ratio
        wick_detail = (
            f"下ヒゲ {fmt_price(lower_wick, unit='')} / 実体 "
            f"{fmt_price(body, unit='')} = {fmt_ratio(lower_wick / body)}"
            f"（{wick_ratio}倍以上で長い下ヒゲ）"
        )
    else:
        # 十字線は実体で割れないので、日中値幅に対する比率で見る
        doji_ratio = float(exp.get("rebound.doji_lower_wick_ratio", 0.5))
        long_lower_wick = day_range > 0 and lower_wick >= day_range * doji_ratio
        wick_detail = (
            f"実体なし(十字線)。下ヒゲ/日中値幅 = "
            f"{fmt_ratio(lower_wick / day_range if day_range else 0.0)}"
            f"（{doji_ratio}以上で長い下ヒゲ）"
        )

    recovery_ratio = float(exp.get("volume.recovery_ratio", 1.2))
    avg5 = volume.avg5 if volume is not None else None
    if avg5 is None:
        vols = [float(b.volume) for b in bars[-5:]]
        avg5 = sum(vols) / len(vols) if vols else None
    volume_recovered = bool(avg5 and latest.volume >= avg5 * recovery_ratio)

    judgements = (
        Judgement(
            key="rebound.confirmed",
            label="反発確認（終値 > 前日高値）",
            ok=confirmed,
            detail=(
                f"終値 {fmt_price(latest.close)} {'>' if confirmed else '<='} "
                f"前日高値 {fmt_price(prev_high)} ({fmt_pct(diff_pct)})"
            ),
            required=False,  # 必須ではなく ENTRY_CANDIDATE への昇格条件
        ),
        Judgement(
            key="rebound.bullish",
            label="陽線",
            ok=bullish,
            detail=(
                f"始値 {fmt_price(latest.open)} → 終値 {fmt_price(latest.close)}"
                f" ({fmt_pct(latest.body_pct)})"
            ),
            required=False,
        ),
        Judgement(
            key="rebound.lower_wick",
            label="長い下ヒゲ",
            ok=long_lower_wick,
            detail=wick_detail,
            required=False,
        ),
        Judgement(
            key="rebound.volume",
            label="出来高回復",
            ok=volume_recovered,
            detail=(
                f"当日 {fmt_volume(latest.volume)} vs 5日平均 {fmt_volume(avg5)}"
                f"（{fmt_ratio(latest.volume / avg5 if avg5 else None)}倍、"
                f"{recovery_ratio}倍以上で回復）"
            ),
            required=False,
        ),
    )

    return ReboundInfo(
        prev_high=prev_high,
        confirmed=confirmed,
        bullish_candle=bullish,
        long_lower_wick=long_lower_wick,
        volume_recovered=volume_recovered,
        judgements=judgements,
    )
