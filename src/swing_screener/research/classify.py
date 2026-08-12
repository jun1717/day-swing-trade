"""形状分類と転帰分類（RESEARCH_DESIGN §7）。

形状（シグナル時点）と転帰（シグナル後）は**別軸**で記録する。
同じ形状でも転帰は分かれるため、1つのバケットに押し込むと
「どの形状がどう転ぶか」が見えなくなる。

これらのラベルは**観察のためのもの**であって売買ルールではない。
"""

from __future__ import annotations

from swing_screener.research.config import ShapeParams

# --- 形状ラベル（ユーザー指定の A〜D に対応）--------------------------------

SHAPE_IDEAL = "A_ideal"  # 理想的な下限反発
SHAPE_SLOW_TOUCH = "A_slow_touch"  # 位置は良いが下限接触が古い
SHAPE_LATE = "B_late"  # 反発は明確だが既にレンジ上側
SHAPE_NEAR_UPPER = "C_near_upper"  # 実質レンジ上限付近を買う形
SHAPE_UPPER_ZONE = "D_upper_zone"  # 実質的な上限ブレイク買い

SHAPE_ORDER = (
    SHAPE_IDEAL,
    SHAPE_SLOW_TOUCH,
    SHAPE_LATE,
    SHAPE_NEAR_UPPER,
    SHAPE_UPPER_ZONE,
)

SHAPE_LABELS_JA = {
    SHAPE_IDEAL: "A 理想的な下限反発",
    SHAPE_SLOW_TOUCH: "A' 位置は良いが接触が古い",
    SHAPE_LATE: "B 反発したが少し遅い",
    SHAPE_NEAR_UPPER: "C 上限付近でのENTRY",
    SHAPE_UPPER_ZONE: "D 実質的な上限ブレイク買い",
}

# --- 転帰ラベル（E ダマシに対応）--------------------------------------------

OUTCOME_STOPPED = "stopped_out"  # E: 損切り到達
OUTCOME_BREAKDOWN = "range_breakdown"  # E: レンジ崩壊
OUTCOME_REACHED_UPPER = "reached_upper"
OUTCOME_NEUTRAL = "neutral"
OUTCOME_INCOMPLETE = "incomplete"

OUTCOME_ORDER = (
    OUTCOME_REACHED_UPPER,
    OUTCOME_NEUTRAL,
    OUTCOME_BREAKDOWN,
    OUTCOME_STOPPED,
    OUTCOME_INCOMPLETE,
)

OUTCOME_LABELS_JA = {
    OUTCOME_STOPPED: "損切り到達（ダマシ）",
    OUTCOME_BREAKDOWN: "レンジ崩壊（ダマシ）",
    OUTCOME_REACHED_UPPER: "レンジ上限到達",
    OUTCOME_NEUTRAL: "中立",
    OUTCOME_INCOMPLETE: "データ不足",
}


def classify_shape(
    position: float | None,
    days_from_touch: int | None,
    close: float,
    upper_zone_low: float,
    params: ShapeParams,
) -> str:
    """シグナル時点の形状をラベル付けする。

    注意: range_upper = max(high) は当日高値を含むため、終値がレンジ上限を
    超えることは原理上ありえない（position <= 1.0 が常に成立する）。
    したがって「上限ブレイク」は position が 1.0 を超えたかではなく、
    終値が上限zone に入ったかで判定する。
    """
    if position is None:
        return SHAPE_LATE

    if position >= params.near_upper_max_position or close >= upper_zone_low:
        return SHAPE_UPPER_ZONE
    if position >= params.late_max_position:
        return SHAPE_NEAR_UPPER
    if position >= params.ideal_max_position:
        return SHAPE_LATE
    if days_from_touch is not None and days_from_touch > params.ideal_max_days_from_touch:
        return SHAPE_SLOW_TOUCH
    return SHAPE_IDEAL


def classify_outcome(
    *,
    complete: bool,
    hit_stop: bool,
    closed_below_lower: bool,
    reached_upper: bool,
) -> str:
    """シグナル後の転帰をラベル付けする（最長 horizon で評価）。

    優先順位は「損切り到達 > レンジ崩壊 > 上限到達 > 中立」。
    損切りに当たった後に上限へ行っても、実運用では損切りで降りているため。
    """
    if not complete:
        return OUTCOME_INCOMPLETE
    if hit_stop:
        return OUTCOME_STOPPED
    if closed_below_lower:
        return OUTCOME_BREAKDOWN
    if reached_upper:
        return OUTCOME_REACHED_UPPER
    return OUTCOME_NEUTRAL
