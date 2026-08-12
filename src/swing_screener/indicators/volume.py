"""出来高の集計（DESIGN.md §7 / CODEX_HANDOFF §16）。

出来高は単独で売買判定に使わない。表示と並び順の補助のみ。
「レンジ中は出来高が減り、反発で戻る」という形を確認するための材料である。
"""

from __future__ import annotations

from typing import Sequence

from ..explain import fmt_ratio, fmt_volume
from ..models import Judgement, OHLCVBar, VolumeInfo

STATE_LABEL = {
    "contracting": "レンジ中減少傾向",
    "neutral": "横ばい",
    "expanding": "レンジ中増加傾向",
    "unknown": "判定不能",
}


def _avg(values: Sequence[float]) -> float | None:
    values = list(values)
    if not values:
        return None
    return sum(values) / len(values)


def summarize_volume(
    bars: Sequence[OHLCVBar], range_start_idx: int | None, exp
) -> VolumeInfo:
    """当日 / 5日 / 20日 / レンジ期間 / レンジ前 の出来高をまとめる。

    range_start_idx はレンジ window の開始位置。レンジは常に最新足で終わるため、
    「レンジ前」は同じ日数だけ手前に遡った区間とする（同じ長さで比べないと
    平均の意味が変わってしまうため）。
    """
    if not bars:
        return VolumeInfo(
            latest=0,
            avg5=None,
            avg20=None,
            range_avg=None,
            pre_range_avg=None,
            range_vs_pre_ratio=None,
            latest_vs_avg5_ratio=None,
            state="unknown",
            state_label=STATE_LABEL["unknown"],
            judgements=(),
        )

    vols = [float(b.volume) for b in bars]
    latest = int(bars[-1].volume)
    avg5 = _avg(vols[-5:]) if len(vols) >= 5 else None
    avg20 = _avg(vols[-20:]) if len(vols) >= 20 else None

    range_avg: float | None = None
    pre_range_avg: float | None = None
    if range_start_idx is not None and 0 <= range_start_idx < len(vols):
        days = len(vols) - range_start_idx
        range_avg = _avg(vols[range_start_idx:])
        pre_slice = vols[max(0, range_start_idx - days) : range_start_idx]
        pre_range_avg = _avg(pre_slice) if pre_slice else None

    ratio: float | None = None
    if range_avg is not None and pre_range_avg:
        ratio = range_avg / pre_range_avg

    contract = float(exp.volume.contract_ratio)
    expand = float(exp.get("volume.expand_ratio", 1.3))
    if ratio is None:
        state = "unknown"
    elif ratio <= contract:
        state = "contracting"
    elif ratio >= expand:
        state = "expanding"
    else:
        state = "neutral"

    latest_vs_avg5 = latest / avg5 if avg5 else None

    judgements = (
        Judgement(
            key="volume.state",
            label="出来高の状態",
            ok=None,  # 出来高は単独で売買判定に使わない
            detail=(
                f"{STATE_LABEL[state]}"
                + (
                    f"（レンジ平均 {fmt_volume(range_avg)} / レンジ前平均 "
                    f"{fmt_volume(pre_range_avg)} = {fmt_ratio(ratio)}、"
                    f"減少判定 <= {contract} / 増加判定 >= {expand}）"
                    if ratio is not None
                    else "（レンジ未検出のため比較なし）"
                )
            ),
            required=False,
        ),
        Judgement(
            key="volume.averages",
            label="出来高の平均",
            ok=None,
            detail=(
                f"当日 {fmt_volume(latest)} / 5日平均 {fmt_volume(avg5)}"
                f"（{fmt_ratio(latest_vs_avg5)}倍）/ 20日平均 {fmt_volume(avg20)}"
            ),
            required=False,
        ),
    )

    return VolumeInfo(
        latest=latest,
        avg5=avg5,
        avg20=avg20,
        range_avg=range_avg,
        pre_range_avg=pre_range_avg,
        range_vs_pre_ratio=ratio,
        latest_vs_avg5_ratio=latest_vs_avg5,
        state=state,
        state_label=STATE_LABEL[state],
        judgements=judgements,
    )


def trailing_avg_volume(bars: Sequence[OHLCVBar], index: int, period: int = 20) -> float | None:
    """index の足を含む直近 period 本の平均出来高（大陰線+出来高急増の判定に使う）。"""
    if index < 0 or index >= len(bars):
        return None
    start = max(0, index - period + 1)
    return _avg([float(b.volume) for b in bars[start : index + 1]])
