"""EXIT ロジックを日足ベースの状態機械として再現できるかの検証（research 専用）。

前回の `exit_study.py` は「ENTRY 後に何が起きたか」を観察しただけで、
警戒陰線は **保有中の全陰線** を候補にしたため 175 本発生し選別にならなかった。

ここでは仮説を 1 つだけ変える:

    警戒陰線を「ENTRY 直後から」ではなく
    「元レンジ上限を終値で突破した後（＝上昇波が始まった後）」から有効化する。

これは **今回の検証用仮説であり、正式ルールではない。**

--------------------------------------------------------------------------
状態遷移
--------------------------------------------------------------------------

    INITIAL_HOLD ──(close > 元レンジ上限)──> TREND_HOLD
         │                                       │
         │                                  (最初の陰線)
         │                                       ↓
         │                                    WARNING
         │                                    │      │
         │                     (high > reference_high)│(low < warning_low)
         │                                    ↓      ↓
         │                          REHIGH_CONFIRMED  利確候補（CASE2 のみ EXIT）
         │                          STOP引き上げ
         │                                    ↓
         │                                TREND_HOLD（ループ）
         ↓
      active_stop 到達 = EXIT

--------------------------------------------------------------------------
このモジュールが守る境界
--------------------------------------------------------------------------

1. **確定ルールは一切変更しない。**
   `initial_stop = range_lower * 0.995`（CODEX_HANDOFF §20）も
   `near.max_position_in_range = 0.65` も ENTRY ロジックも触らない。
   仮想 ENTRY 価格は前回と同じ「シグナル翌営業日の始値」。

2. **今回の状態遷移は正式ルールではない。**
   イベント名・列名に `CANDIDATE` / `参考` を残し、CASE 比較として提示する。
   成績が良い CASE を採用する、という結論は出さない。

3. **look-ahead bias を作らない。**
   1 営業日ずつ前へ進み、その日までの足しか読まない。
   `new_swing_low_candidate` は reference_high を再突破した日に初めて確定し、
   引き上げた STOP は **翌営業日から** 有効にする（同じ日の安値に遡って
   適用しない）。この性質は prefix 不変性テストで固定する。

4. **日足で先後が分からない日に有利／不利な順番を仮定しない。**
   `AMBIGUOUS_WARNING_ORDER` / `AMBIGUOUS_STOP_ORDER` として分離し件数を報告する。

5. **固定利確（+3/+5/+10%・上限到達）を機械的な EXIT に使わない（§13）。**
   大陰線・出来高急増・安値引けも参考指標として記録するだけ（§14）。

6. **本番の config.yaml / experimental.yaml / output/ には書き込まない。**

--------------------------------------------------------------------------
文章ルールが曖昧で、実装時に読み方を決めた箇所（レポートで明示する）
--------------------------------------------------------------------------

(a) TREND_HOLD へ入った当日（＝上限突破日）と、再高値更新が確定した当日の
    ローソクが陰線だった場合に、それを即その状態の警戒足とするか。
    → 「その後に最初に発生した陰線」（§8）を素直に取り、**翌営業日から**
      警戒足の判定を始める。同日採用にした場合の差分は
      `same_day_bearish_at_trend_entry` として件数だけ記録する。

(b) CASE3（トレーリング）で warning_low を割ったあと、どの状態へ行くか。
    → 文章ルールに「警戒足を置き換えない」（§9）とあるので WARNING に留まり、
      reference_high を抜くか active_stop に当たるまで待つ。
      この「割ったのに WARNING に居続ける」滞留日数を記録する。

(c) 上限突破後・トレーリング成立前に active_stop（＝初期STOPのまま）へ
    到達した場合。§12 の EXIT A/B/C のどれにも当てはまらないため
    `INITIAL_STOP_EXIT_AFTER_BREAKOUT` として 4 つ目の EXIT 種別を置いた。

(d) 警戒足の有効化タイミング（VARIANT A/B/C）。
    後続の検証（warning_start_study）で「WARNING へ入る条件」だけを差し替える。
    `variant="A"` が既定で、既存の追跡結果は一切変わらない。
      A: 上限を終値突破した翌営業日から警戒足を拾う（従来）
      B: 突破後に `high > breakout_day_high` を満たした日を UPTREND_CONFIRMED とし、
         その翌営業日から拾う
      C: 突破後に `close > breakout_day_close` を満たした日を UPTREND_CONFIRMED とし、
         その翌営業日から拾う
    この確認ゲートは**突破直後の 1 回だけ**に効かせる。再高値更新（REHIGH）で
    TREND_HOLD へ戻ったあとは、その日が定義上すでに「さらに上へ進んだ日」なので
    A と同じく翌営業日から警戒足を拾う。C ではその日が
    `close > breakout_day_close` を満たさない場合があり得るため、
    件数を `rehigh_days_failing_own_confirm` として記録する。

(e) warning_low を割ったあと「どこで降りるか」（EXIT VARIANT 1/2/3）。
    後続の検証（warning_break_study）で **warning_low 割れ後の処理だけ** を
    差し替える。`break_rule="HOLD_UNTIL_STOP"` が既定で、これは (b) と同じ
    「割っても降りない」挙動＝既存の CASE3 なので、既存の追跡結果は変わらない。
      HOLD_UNTIL_STOP : 降りない（前回 CASE3。比較の参考基準）
      LOW_BREAK       : low   < warning_low で利確候補
      CLOSE_BREAK     : close < warning_low で利確候補
      STRUCTURAL_BREAK: close < warning_low かつ close < 元レンジ上限 で利確候補
    トリガーは **その営業日の足だけ** で決める。仮想EXITの約定（翌営業日始値）は
    追跡ループの外で埋めるので、判定に翌日の足は一切入らない。
    close 型（CLOSE_BREAK / STRUCTURAL_BREAK）で同じ日に reference_high 再突破も
    成立した場合の扱いは (f) の `ambiguous_order` に切り出した。

(f) `reference_high` の決め方（RH-A/B/C/D/E）。「WARNING 後に、何をもって
    調整終了・上昇再開と判断するか」だけを差し替える。`rh_rule="HOLDING_HIGH"`
    が既定で、これは従来の「警戒足発生時点までの保有中最高値」なので
    既存の追跡結果は変わらない。
      HOLDING_HIGH           : max(high) ENTRY〜警戒足当日（現行 RH-A）
      WARNING_HIGH           : warning_high（RH-B）
      PRE_WARNING_CLOSE_HIGH : max(close) ENTRY〜警戒足前日（RH-C）
      WARNING_OPEN           : warning_open（RH-D）
      PRE_WARNING_HIGH       : max(high) ENTRY〜警戒足前日（RH-E。参考）
    どの案も **警戒足当日までの足だけ** から決まる。RH-C / RH-E は当日を含めない。
    再突破の判定は 5 案とも `high > reference_high` で共通、押し安値は
    `min(low)` 警戒足〜再突破日、trail は `押し安値 * 0.995`、STOP は
    翌営業日から有効で下方向へは動かさない。ここは一切変えていない。

    同じ日に「終値で warning_low 割れ（利確候補）」と「high > reference_high
    （再上昇）」の両方が成立した場合、日足では先後を決められない。
    `AMBIGUOUS_REHIGH_EXIT_ORDER` として分離し、どちらを採るかは
    `ambiguous_order` の外部パラメータにした。既定 `REHIGH_FIRST` は既存出力との
    互換のための既定値であって「正しい順序」という主張ではない。比較側では
    `EXIT_FIRST` も走らせて差分を報告する。
"""

from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

from swing_screener.indicators.swing import detect_swings
from swing_screener.models import OHLCVBar, PriceSeries
from swing_screener.research.exit_study import (
    MAX_TRACK_DAYS,
    _atr,
    _avg_volume,
    _cell,
    _median,
    _rate,
)

# --- 状態 ---------------------------------------------------------------------

S_INITIAL_HOLD = "INITIAL_HOLD"
S_TREND_HOLD = "TREND_HOLD"
S_WARNING = "WARNING"
S_CLOSED = "CLOSED"

# --- イベント種別 -------------------------------------------------------------

E_ENTRY = "ENTRY"
E_UPPER_CLOSE_BREAK = "RANGE_UPPER_CLOSE_BREAK"      # INITIAL_HOLD -> TREND_HOLD
E_UPPER_HIGH_ONLY = "RANGE_UPPER_HIGH_ONLY"          # 状態遷移させない
E_UPTREND_CONFIRMED = "UPTREND_CONFIRMED"            # VARIANT B/C の警戒足有効化日
E_WARNING_CANDLE = "WARNING_CANDLE"                  # TREND_HOLD -> WARNING
E_WARNING_EXTRA = "WARNING_CANDLE_NOT_REPLACED"      # WARNING 中の追加陰線（§9）
E_WARNING_LOW_BREAK = "WARNING_LOW_BREAK"
E_WARNING_LOW_CLOSE_BREAK = "WARNING_LOW_CLOSE_BREAK"    # 終値で warning_low 割れ
E_STRUCTURAL_BREAK = "STRUCTURAL_BREAK"                  # 終値で警戒安値と上限の両方割れ
E_INTRADAY_BREAK_RECOVERED = "INTRADAY_BREAK_RECOVERED"  # 日中割れたが終値は回復
E_CLOSE_BREAK_HELD = "CLOSE_BREAK_ABOVE_RANGE_UPPER"     # 終値割れだが上限は維持
E_GAP_THROUGH = "GAP_THROUGH_WARNING_LOW"
E_REHIGH = "REHIGH_CONFIRMED"                        # WARNING -> TREND_HOLD
E_STOP_RAISED = "TRAIL_STOP_RAISED"
E_STOP_KEPT = "TRAIL_STOP_NOT_RAISED"                # 引き下げは行わない
E_AMBIGUOUS_WARNING = "AMBIGUOUS_WARNING_ORDER"
E_AMBIGUOUS_STOP = "AMBIGUOUS_STOP_ORDER"
# 同日に「終値で warning_low 割れ」と「high > reference_high」の両方が成立（§7）
E_AMBIGUOUS_REHIGH_EXIT = "AMBIGUOUS_REHIGH_EXIT_ORDER"
E_DATA_END = "DATA_END_STILL_OPEN"

# EXIT 種別（§12）
X_INITIAL_STOP = "INITIAL_STOP_EXIT"                     # 上限突破前の active_stop 到達
X_INITIAL_STOP_AFTER_BREAK = "INITIAL_STOP_EXIT_AFTER_BREAKOUT"  # 突破後・trail 前
X_WARNING_LOW = "WARNING_LOW_EXIT_CANDIDATE"             # CASE2 のみ
X_TRAIL_STOP = "TRAIL_STOP_EXIT"                         # trail 引き上げ後の到達
X_DATA_END = "DATA_END_OPEN"                             # 追跡終端で保有継続

# --- CASE（§15）---------------------------------------------------------------

CASE1 = "CASE1_INITIAL_STOP_ONLY"
CASE2 = "CASE2_WARNING_EXIT"
CASE3 = "CASE3_TRAILING"
CASES = (CASE1, CASE2, CASE3)

CASE_LABELS_JA = {
    CASE1: "CASE1 初期STOPのみ（前回と同じ）",
    CASE2: "CASE2 warning_low 下抜けで利確",
    CASE3: "CASE3 トレーリング（warning_low では降りない）",
}

# --- VARIANT（解釈(d)。警戒足を有効化するタイミングだけを差し替える）-----------

VARIANT_A = "A"
VARIANT_B = "B"
VARIANT_C = "C"
VARIANTS = (VARIANT_A, VARIANT_B, VARIANT_C)

VARIANT_LABELS_JA = {
    VARIANT_A: "VARIANT A 上限を終値突破した翌営業日から（現行案・比較基準）",
    VARIANT_B: "VARIANT B 高値更新確認後（high > breakout_day_high）",
    VARIANT_C: "VARIANT C 終値上昇確認後（close > breakout_day_close）",
}

VARIANT_CONDITION_JA = {
    VARIANT_A: "（確認を挟まない）",
    VARIANT_B: "high > breakout_day_high",
    VARIANT_C: "close > breakout_day_close",
}

# --- BREAK RULE（解釈(e)。warning_low を割ったあとの扱いだけを差し替える）-------

BREAK_HOLD = "HOLD_UNTIL_STOP"
BREAK_LOW = "LOW_BREAK"
BREAK_CLOSE = "CLOSE_BREAK"
BREAK_STRUCT = "STRUCTURAL_BREAK"
BREAK_RULES = (BREAK_HOLD, BREAK_LOW, BREAK_CLOSE, BREAK_STRUCT)

BREAK_RULE_LABELS_JA = {
    BREAK_HOLD: "参考 warning_low では降りない（前回 CASE3 と同じ挙動）",
    BREAK_LOW: "EXIT VARIANT 1 LOW_BREAK（安値が warning_low を下回った日）",
    BREAK_CLOSE: "EXIT VARIANT 2 CLOSE_BREAK（終値が warning_low を下回った日）",
    BREAK_STRUCT: "EXIT VARIANT 3 STRUCTURAL_BREAK（終値が警戒安値と元レンジ上限の両方を下回った日）",
}

BREAK_RULE_SHORT_JA = {
    BREAK_HOLD: "参考 降りない",
    BREAK_LOW: "V1 LOW_BREAK",
    BREAK_CLOSE: "V2 CLOSE_BREAK",
    BREAK_STRUCT: "V3 STRUCTURAL_BREAK",
}

BREAK_RULE_CONDITION_JA = {
    BREAK_HOLD: "（利確候補で降りない。reference_high 再突破か active_stop 到達を待つ）",
    BREAK_LOW: "low < warning_low",
    BREAK_CLOSE: "close < warning_low",
    BREAK_STRUCT: "close < warning_low かつ close < original_range_upper",
}

# EXIT 種別（break rule ごと）
X_BREAK_EXIT = {
    BREAK_LOW: "WARNING_LOW_BREAK_EXIT",
    BREAK_CLOSE: "WARNING_LOW_CLOSE_BREAK_EXIT",
    BREAK_STRUCT: "STRUCTURAL_BREAK_EXIT",
}
BREAK_EXIT_TYPES = tuple(X_BREAK_EXIT.values())

# --- REFERENCE HIGH RULE（解釈(f)。「調整終了・上昇再開」の判定水準だけを差し替える）---
#
# WARNING に入った日に `reference_high` を 1 つ決める。決め方だけが違い、
# 再突破の判定（high > reference_high）／押し安値の取り方／トレーリング／
# 初期STOP／ENTRY／WARNING 開始条件／warning_low の扱いは 5 案で完全に同一。
#
# 価格の大小関係（陰線なので open > close、high >= open）:
#     RH_HOLDING >= RH_WARNING_HIGH >= RH_WARNING_OPEN
#     RH_HOLDING >= RH_PRE_HIGH,  RH_HOLDING >= RH_PRE_CLOSE
# RH_PRE_CLOSE と RH_WARNING_HIGH / RH_WARNING_OPEN の前後は決まらない。
RH_HOLDING = "HOLDING_HIGH"                  # RH-A 現行
RH_WARNING_HIGH = "WARNING_HIGH"             # RH-B
RH_PRE_CLOSE = "PRE_WARNING_CLOSE_HIGH"      # RH-C
RH_WARNING_OPEN = "WARNING_OPEN"             # RH-D
RH_PRE_HIGH = "PRE_WARNING_HIGH"             # RH-E（§6 の参考VARIANT）
RH_RULES = (RH_HOLDING, RH_WARNING_HIGH, RH_PRE_CLOSE, RH_WARNING_OPEN, RH_PRE_HIGH)

RH_RULE_LABELS_JA = {
    RH_HOLDING: "RH-A HOLDING_HIGH（警戒足発生時点までの保有中最高値。現行）",
    RH_WARNING_HIGH: "RH-B WARNING_HIGH（警戒陰線自身の高値）",
    RH_PRE_CLOSE: "RH-C PRE_WARNING_CLOSE_HIGH（警戒足前日までの終値ベース最高値）",
    RH_WARNING_OPEN: "RH-D WARNING_OPEN（警戒陰線の始値）",
    RH_PRE_HIGH: "RH-E PRE_WARNING_HIGH（警戒足前日までの高値ベース最高値。参考）",
}

RH_RULE_SHORT_JA = {
    RH_HOLDING: "RH-A 保有中最高値",
    RH_WARNING_HIGH: "RH-B 警戒足高値",
    RH_PRE_CLOSE: "RH-C 前日までの終値高値",
    RH_WARNING_OPEN: "RH-D 警戒足始値",
    RH_PRE_HIGH: "RH-E 前日までの高値",
}

RH_RULE_CONDITION_JA = {
    RH_HOLDING: "max(high) ENTRY〜警戒足当日",
    RH_WARNING_HIGH: "warning_high",
    RH_PRE_CLOSE: "max(close) ENTRY〜警戒足前日",
    RH_WARNING_OPEN: "warning_open",
    RH_PRE_HIGH: "max(high) ENTRY〜警戒足前日",
}

# RH-E を入れた理由（§6 の「1 案まで」）。
# RH-A は保有中最高値に**警戒足自身の高値を含む**ため、上ヒゲの長い陰線が出ると
# 「その日の天井をもう一度抜く」ことが再上昇の条件になる。§4 で問題視された構造は
# ここにある。RH-B は警戒足の高値そのものなので、警戒足が最高値を作った日は
# RH-A と一致してしまい、この構造を切り分けられない。RH-E（前日までの高値）は
# 「警戒足が出る前に作った高値を回復したか」という同じ意味を、警戒足自身を
# 含めずに測る唯一の既存価格で、RH-C の終値版に対する高値版でもある。
# 新しい調整パラメータ（ATR 倍率・%・N日高値・移動平均）は使っていない。
RH_EXTRA_RULES = (RH_PRE_HIGH,)

# --- 同日に REHIGH と利確候補が両立した場合の扱い（§7）------------------------
# 日足では先後を決められない。どちらかを正解として持ち込まないため、
# **比較のたびに両方**（REHIGH 優先 / EXIT 優先）を走らせて差分を報告する。
# 既定は前回までの実装と同じ REHIGH 優先だが、これは既存出力との互換のための
# 既定値であって「正しい順序」という主張ではない。
AMB_REHIGH = "REHIGH_FIRST"
AMB_EXIT = "EXIT_FIRST"
AMBIGUOUS_ORDERS = (AMB_REHIGH, AMB_EXIT)

AMBIGUOUS_ORDER_LABELS_JA = {
    AMB_REHIGH: "同日は REHIGH を先に採る",
    AMB_EXIT: "同日は 利確候補（終値割れ）を先に採る",
}

# 分析指標としてのみ記録する到達水準（§13。機械的利確には使わない）
GAIN_TARGETS: tuple[float, ...] = (3.0, 5.0, 10.0)

# 「利益の大半を失った」の集計定義。売買閾値ではなく、
# 「大半＝過半」をそのまま数式にしただけのもの。
GIVEBACK_MOST_RATIO = 0.5

TRAIL_BUFFER = 0.995  # 押し安値の 0.5% 下（初期STOPと同じ確定済みバッファ）

CSV_NOTE = (
    "# 注記: 本ファイルは EXIT ロジックを日足の状態機械として再現できるかの検証であり、"
    "収益バックテストではない。CASE2/CASE3 は現行の文章ルールの読み方であって正式ルールではない。"
    " entry_price は「シグナル翌営業日の始値」（検証用の約定価格）。"
    " 確定ルールとして変更していないのは ENTRY ロジック / max_position_in_range=0.65 /"
    " initial_stop = range_lower*0.995 の 3 点。"
    " +3/+5/+10% と上限到達は分析指標としてのみ記録し、機械的な利確には使っていない。"
)


# --- データ構造 ---------------------------------------------------------------


@dataclass(frozen=True)
class StateEvent:
    """状態機械が 1 営業日に記録した出来事。"""

    day_offset: int          # 仮想ENTRY日 = 0
    date: date
    state_before: str
    state_after: str
    kind: str
    price: float | None
    detail: str
    case: str = "ALL"        # "ALL" / CASE2 / CASE3 のどれに効くか

    def label(self) -> str:
        return f"D+{self.day_offset} {self.kind}"


@dataclass(frozen=True)
class DailyState:
    """§11 の「各営業日について、その時点で有効な STOP」を残すための行。"""

    day_offset: int
    date: date
    state: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    active_stop: float           # その日の寄り時点で有効だった STOP
    holding_high: float
    reference_high: float | None
    warning_low: float | None
    unrealized_pct: float        # 終値ベースの含み損益


@dataclass(frozen=True)
class WarningEpisode:
    """TREND_HOLD 中に最初に出た陰線と、その後の決着。"""

    seq: int                     # 同一イベント内での通し番号（1 始まり）
    date: date
    day_offset: int
    open: float
    high: float
    low: float
    close: float
    volume: int

    # 比較したい解釈: reference_high は「警戒足自身の高値」ではなく保有中最高値
    reference_high: float
    reference_high_date: date
    warning_high_vs_reference_high_pct: float  # 警戒足の高値が保有中最高値からどれだけ下か

    unrealized_pct_at_warning: float           # 終値ベースの含み益率
    unrealized_high_pct_at_warning: float      # 高値ベース

    # 決着
    resolution: str  # "low_break" / "rehigh" / "ambiguous_both" / "stop" / "open"
    resolved_date: date | None
    resolved_day_offset: int | None
    days_to_resolve: int | None

    # warning_low 下抜け（CASE2 の EXIT 候補）
    low_break_date: date | None
    low_break_day_offset: int | None
    low_break_open: float | None
    low_break_low: float | None
    gap_through_warning_low: bool
    low_break_reference_price: float | None    # 約定を保証しない参考価格

    # reference_high 再突破
    rehigh_date: date | None
    rehigh_day_offset: int | None
    new_swing_low_candidate: float | None
    new_swing_low_date: date | None
    trail_stop_candidate: float | None
    active_stop_before: float
    active_stop_after: float | None
    stop_raised: bool

    # WARNING 中の追加陰線（置き換えない §9）
    extra_bearish_count: int
    # warning_low を割ったのに CASE3 が WARNING に留まった日数（(b) の副作用）
    days_held_in_warning_after_low_break: int | None

    # 大陰線例外の参考指標（§14。自動 EXIT には使わない）
    change_pct: float | None
    body_pct: float
    body_to_atr: float | None
    volume_ratio: float | None
    close_pos_in_day_range: float | None
    manual_exit_review: bool

    # 既存 fractal との比較（§20 Q3。状態機械には一切使わない）
    fractal_confirm_day_offset: int | None
    fractal_is_same_low: bool | None


@dataclass(frozen=True)
class RefHighSnapshot:
    """警戒足ごとに「5 案の reference_high が実際いくらだったか」を残す記録（解釈(f)）。

    **状態遷移には使わない。** 実際に使ったのは `rh_rule` の 1 つだけで、
    残り 4 つは §15（各案が実際にはどの程度違うハードルなのか）のための観測値。
    どの値も警戒足当日までの足だけから決まる。

    `order_class` は §11 の 4 分類で、「終値で warning_low を割る」のと
    「high > reference_high」のどちらが先だったかを表す。同じ日に両方成立した場合は
    日足では先後を決められないので `ambiguous_same_day` として分離し、
    どちらが正しいかはここでは決めない（§7）。
    """

    seq: int
    warning_date: date
    warning_day_offset: int
    rh_rule: str

    # 実際に使った水準
    reference_high: float
    reference_high_date: date

    # --- §15 各案の水準（警戒足当日までの足だけから決まる）---
    holding_high: float            # RH-A
    warning_high: float            # RH-B
    pre_warning_close_high: float  # RH-C
    warning_open: float            # RH-D
    pre_warning_high: float        # RH-E（参考）

    # 位置関係（RH-A を 100 とした差。負なら A より低いハードル）
    rh_b_vs_a_pct: float
    rh_c_vs_a_pct: float
    rh_d_vs_a_pct: float
    rh_e_vs_a_pct: float
    a_equals_b: bool               # 警戒足自身が保有中最高値を作っていた
    lowest_rule: str               # 5 案のうち最も低い水準

    # 文脈
    warning_low: float
    warning_close: float
    original_range_upper: float
    entry_price: float
    unrealized_pct_at_warning: float
    observed_days: int

    # --- 決着（§11）---
    rehigh_date: date | None
    rehigh_day_offset: int | None
    rehigh_high: float | None
    days_to_rehigh: int | None
    close_break_date: date | None
    close_break_day_offset: int | None
    close_break_close: float | None
    order_class: str               # rehigh_first / close_break_first / ambiguous_same_day / neither
    order_ambiguous: bool
    ambiguous_resolved_as: str     # "rehigh" / "exit" / ""
    # 曖昧日に「寄りが既に reference_high より上」だったか。
    # 参考情報であって、これで順序を決めることはしない（§7）。
    ambiguous_open_above_reference: bool | None

    # --- 押し安値 / trail（§8 / §12）---
    new_swing_low_candidate: float | None
    new_swing_low_date: date | None
    trail_stop_candidate: float | None
    active_stop_before: float
    active_stop_after: float | None
    stop_raised: bool


@dataclass(frozen=True)
class WarningBreak:
    """警戒足ごとの「warning_low をどう割ったか」の観測記録（解釈(e)の材料）。

    **状態遷移には使わない。** どの解釈（LOW / CLOSE / STRUCTURAL）で差が出るかを
    後から数えるための記録で、`break_rule` に関係なく同じ規則で埋める。
    ただし観測できる範囲は「その案が WARNING に留まっていた期間」までなので、
    §8 の実態集計は最も長く観測できる HOLD_UNTIL_STOP を分母に使う。

    `*_next_open_*` は仮想EXITの約定側の情報で、**追跡ループの外**で埋める。
    判定（トリガー）には翌営業日の足を一切使っていない。
    """

    seq: int
    warning_date: date
    warning_day_offset: int
    warning_low: float
    reference_high: float
    original_range_upper: float
    observed_days: int              # 警戒足の翌営業日以降、WARNING に留まっていた日数

    # --- 日中割れ（EXIT VARIANT 1 のトリガー）---
    intraday_break_date: date | None
    intraday_break_day_offset: int | None
    intraday_break_open: float | None
    intraday_break_low: float | None
    intraday_break_close: float | None
    intraday_break_gap_open: bool           # 寄りが既に warning_low 以下
    intraday_break_close_recovered: bool    # その日の終値は warning_low 以上へ戻した
    intraday_break_days: int
    intraday_break_next_open_date: date | None
    intraday_break_next_open: float | None
    intraday_break_next_open_gap_pct: float | None   # warning_low 比

    # --- 終値割れ（EXIT VARIANT 2 のトリガー）---
    close_break_date: date | None
    close_break_day_offset: int | None
    close_break_close: float | None
    close_break_above_range_upper: bool     # 終値は元レンジ上限より上に留まっていた
    close_break_days: int
    close_break_next_open_date: date | None
    close_break_next_open: float | None
    close_break_next_open_gap_pct: float | None      # 終値比
    days_from_intraday_to_close_break: int | None

    # --- 構造割れ（EXIT VARIANT 3 のトリガー）---
    struct_break_date: date | None
    struct_break_day_offset: int | None
    struct_break_close: float | None
    struct_break_days: int
    struct_break_next_open_date: date | None
    struct_break_next_open: float | None
    struct_break_next_open_gap_pct: float | None
    days_from_close_to_struct_break: int | None

    # --- 決着（既存の WarningEpisode と同じ値。突き合わせ用）---
    resolution: str
    left_warning_day_offset: int | None
    rehigh_date: date | None
    same_day_rehigh_on_close_break: bool

    @property
    def intraday_only(self) -> bool:
        """日中は割ったが、WARNING でいる間に終値では一度も割らなかった。"""
        return self.intraday_break_date is not None and self.close_break_date is None

    @property
    def close_break_held_upper(self) -> bool:
        """終値で警戒安値は割ったが、元レンジ上限の内側までは戻らなかった。"""
        return self.close_break_date is not None and self.struct_break_date is None


@dataclass(frozen=True)
class StopUpdate:
    """active_stop の引き上げ履歴（§11）。引き下げは起こらない。"""

    seq: int
    stop_update_date: date          # 再高値更新が確定した日
    day_offset: int
    old_stop: float
    new_stop: float
    new_swing_low_candidate: float
    new_swing_low_date: date
    reference_high: float
    rehigh_date: date
    effective_from_date: date | None  # look-ahead 回避のため翌営業日から有効
    effective_from_day_offset: int | None
    raise_pct_from_initial_stop: float


@dataclass(frozen=True)
class CaseResult:
    """1 CASE 分の仮想 EXIT 結果（§15/§16）。"""

    case: str
    exit_type: str
    exit_date: date | None
    exit_day_offset: int | None
    exit_reference_price: float | None
    gap_through: bool
    approximate_return_pct: float | None
    holding_days: int | None
    still_open: bool
    order_ambiguous: bool            # 同日到達で先後が決められなかった EXIT か

    # 利益保持（§17）
    max_gain_pct: float | None
    max_gain_date: date | None
    max_loss_pct: float | None
    giveback_pct: float | None       # 最大含み益 − 最終リターン

    # --- 解釈(e)。トリガー日と約定日を分けて持つ ---
    # 既定は「トリガー日にその場で約定」（STOP 注文型）なので trigger_* は None。
    trigger_date: date | None = None
    trigger_day_offset: int | None = None
    fill_rule: str = "same_day"       # "same_day" / "next_open"
    fill_pending: bool = False        # 翌営業日がまだ来ておらず約定を置けない
    fill_gap_pct: float | None = None  # 翌営業日始値 − トリガー基準価格（%）

    @property
    def gave_back_most(self) -> bool:
        if self.max_gain_pct is None or self.approximate_return_pct is None:
            return False
        if self.max_gain_pct < 10.0:
            return False
        return self.approximate_return_pct < self.max_gain_pct * GIVEBACK_MOST_RATIO

    @property
    def rose5_then_lost(self) -> bool:
        if self.max_gain_pct is None or self.approximate_return_pct is None:
            return False
        return self.max_gain_pct >= 5.0 and self.approximate_return_pct < 0


@dataclass
class SMEvent:
    """1 件の ENTRY_CANDIDATE を状態機械で追跡した結果。"""

    # --- シグナル（前回検証から引き継ぐ。値は変更しない）---
    signal_date: date
    code: str
    name: str
    sector: str
    signal_close: float
    signal_index: int
    range_lower: float
    range_upper: float
    initial_stop: float

    # --- 仮想 ENTRY ---
    entry_available: bool
    entry_date: date | None
    entry_index: int | None
    entry_price: float | None
    gap_pct: float | None

    # --- INITIAL_HOLD ---
    reached_trend_hold: bool
    upper_close_break_date: date | None
    upper_close_break_day_offset: int | None
    upper_close_break_price: float | None
    upper_high_only_before_break: bool

    # --- WARNING / REHIGH ---
    warnings: list[WarningEpisode]
    stop_updates: list[StopUpdate]
    final_active_stop: float
    max_active_stop: float
    same_day_bearish_at_trend_entry: int   # 解釈(a)の感度用

    # --- CASE 比較 ---
    cases: dict[str, CaseResult]

    # --- 分析指標のみ（機械的利確には使わない §13）---
    reached_gain: dict[float, bool]
    days_to_gain: dict[float, int | None]

    # --- 曖昧 ---
    ambiguous_warning_days: list[date]
    ambiguous_stop_days: list[date]

    # --- 追跡範囲 ---
    bars_tracked: int
    tracking_truncated: bool

    # --- 分類 ---
    path_label: str
    flags: list[str]

    timeline: list[StateEvent] = field(default_factory=list)
    daily: list[DailyState] = field(default_factory=list)

    # --- 警戒足の有効化タイミング（解釈(d)。A では確認ゲートを使わない）---
    variant: str = VARIANT_A
    breakout_day_high: float | None = None
    breakout_day_close: float | None = None
    uptrend_confirmed_date: date | None = None
    uptrend_confirmed_day_offset: int | None = None
    uptrend_confirmed_price: float | None = None
    uptrend_confirm_day_bearish: bool = False    # §11 確認日そのものが陰線だったか
    # C で「再高値更新日が自分の確認条件を満たさない」件数（解釈(d)の感度）
    rehigh_days_failing_own_confirm: int = 0

    # --- warning_low 割れ後の扱い（解釈(e)。HOLD は従来の CASE3 と同じ）---
    break_rule: str = BREAK_HOLD
    warning_breaks: list[WarningBreak] = field(default_factory=list)
    # close 型の EXIT 条件と reference_high 再突破が同じ日に成立した件数（§4 の感度）
    close_break_with_same_day_rehigh: int = 0

    # --- reference_high の決め方（解釈(f)。HOLDING_HIGH は従来と同じ）---
    rh_rule: str = RH_HOLDING
    ambiguous_order: str = AMB_REHIGH
    ref_highs: list[RefHighSnapshot] = field(default_factory=list)
    # 同日に REHIGH と利確候補が両立し、日足では先後を決められなかった日（§7）
    ambiguous_rehigh_exit_days: list[date] = field(default_factory=list)

    @property
    def ambiguous_rehigh_exit_count(self) -> int:
        return len(self.ambiguous_rehigh_exit_days)

    @property
    def path_result(self) -> CaseResult:
        """その break_rule で実際にたどった経路の仮想 EXIT。

        `break_rule=HOLD_UNTIL_STOP` では前回の CASE3 そのもの。
        LOW/CLOSE/STRUCTURAL では「利確候補で降りた」か「STOP に当たった」かの
        どちらか先に来た方になる。
        `cases[CASE2]` は **どの案でも同じ意味** で、
        「最初の日中割れをその場（warning_low の STOP 注文）で降りた場合」の参考値。
        """
        return self.cases[CASE3]

    @property
    def warning_gate_pending(self) -> bool:
        """突破したのに UPTREND_CONFIRMED が来ないまま終わったか（§9 の材料）。"""
        return (
            self.variant != VARIANT_A
            and self.reached_trend_hold
            and self.uptrend_confirmed_date is None
        )

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    @property
    def rehigh_count(self) -> int:
        return sum(1 for w in self.warnings if w.rehigh_date is not None)

    @property
    def stop_raise_count(self) -> int:
        return len(self.stop_updates)


# --- 追跡本体 -----------------------------------------------------------------


def track_event(
    signal: dict[str, Any],
    series: PriceSeries,
    exp=None,
    *,
    max_track_days: int = MAX_TRACK_DAYS,
    variant: str = VARIANT_A,
    break_rule: str = BREAK_HOLD,
    rh_rule: str = RH_HOLDING,
    ambiguous_order: str = AMB_REHIGH,
) -> SMEvent:
    """1 件の ENTRY_CANDIDATE を状態機械で追跡する。

    `signal` は前回検証の events_pos065.csv の 1 行（文字列 dict）。
    `exp` は既存 fractal との比較にのみ使い、状態遷移には一切使わない。

    切り替えられるのは互いに直交する 3 つだけで、どれも 1 か所しか変えない。

        `variant`     WARNING へ入る条件だけ（解釈(d)）
        `break_rule`  warning_low を割ったあとの扱いだけ（解釈(e)）
        `rh_rule`     reference_high の決め方だけ（解釈(f)）

    押し安値の取り方 / trail = 押し安値*0.995 / STOP は下げない / 初期STOP /
    ENTRY ロジックは、3 つのどの組み合わせでも完全に同一。

    `ambiguous_order` は「同日に REHIGH と利確候補が両方成立した日」の扱い。
    日足では先後を決められないので既定値は正解ではなく、比較側で両方走らせる（§7）。

    ループは 1 営業日ずつ前へ進み、その日までの足しか参照しない。
    close 型 EXIT の約定（翌営業日始値）はループ終了後に埋める。
    """
    if variant not in VARIANTS:
        raise ValueError(f"未知の variant: {variant}")
    if break_rule not in BREAK_RULES:
        raise ValueError(f"未知の break_rule: {break_rule}")
    if rh_rule not in RH_RULES:
        raise ValueError(f"未知の rh_rule: {rh_rule}")
    if ambiguous_order not in AMBIGUOUS_ORDERS:
        raise ValueError(f"未知の ambiguous_order: {ambiguous_order}")
    bars = list(series.bars)
    i = int(signal["signal_index"])
    signal_close = float(signal["signal_close"])
    lower = float(signal["range_lower"])
    upper = float(signal["range_upper"])
    initial_stop = float(signal["initial_stop"])

    if i + 1 >= len(bars):
        return _no_entry(
            signal, i, lower, upper, initial_stop, variant, break_rule,
            rh_rule, ambiguous_order,
        )

    entry_index = i + 1
    entry_bar = bars[entry_index]
    entry_price = entry_bar.open
    gap_pct = (entry_price - signal_close) / signal_close * 100.0
    last_index = min(len(bars) - 1, entry_index + max_track_days - 1)

    timeline: list[StateEvent] = [
        StateEvent(
            0, entry_bar.date, S_INITIAL_HOLD, S_INITIAL_HOLD, E_ENTRY, entry_price,
            f"翌営業日始値 {entry_price:.1f} で仮想ENTRY（ギャップ {gap_pct:+.2f}%） / "
            f"初期STOP {initial_stop:.1f} / 元レンジ上限 {upper:.1f}",
        )
    ]
    daily: list[DailyState] = []
    flags: list[str] = []

    # --- 状態変数 ---
    state = S_INITIAL_HOLD
    active_stop = initial_stop
    pending_stop: tuple[float, int] | None = None   # (level, confirmed_offset)
    holding_high = -math.inf
    holding_high_date = entry_bar.date
    # 終値ベースの保有中最高値（RH-C の材料）。その日の終値は日次処理の**最後**に
    # 入れるので、警戒足を開く時点では常に「前日まで」の値になる。
    close_high = -math.inf
    close_high_date = entry_bar.date
    warning_armed_from = 10**9         # この offset 以降で陰線を警戒足として拾う
    same_day_bearish = 0

    # 現在の WARNING エピソード（確定前の可変状態）
    w_open: dict[str, Any] | None = None
    warnings: list[WarningEpisode] = []
    # 確定した WARNING エピソードの生 dict（解釈(e) の観測値をループ外で組み立てる）
    raw_warnings: list[dict[str, Any]] = []
    stop_updates: list[StopUpdate] = []
    close_break_same_day_rehigh = 0
    ambiguous_rehigh_exit_days: list[date] = []

    upper_break_date: date | None = None
    upper_break_offset: int | None = None
    upper_break_price: float | None = None
    upper_high_only = False

    # VARIANT B/C の確認ゲート（解釈(d)）
    breakout_high: float | None = None
    breakout_close: float | None = None
    confirm_date: date | None = None
    confirm_offset: int | None = None
    confirm_price: float | None = None
    confirm_day_bearish = False
    rehigh_failing_own_confirm = 0

    ambiguous_warning_days: list[date] = []
    ambiguous_stop_days: list[date] = []

    # CASE 別の EXIT
    case1: dict[str, Any] | None = None
    case2: dict[str, Any] | None = None
    case3: dict[str, Any] | None = None
    sm_closed = False

    targets = {t: entry_price * (1 + t / 100.0) for t in GAIN_TARGETS}
    reached_gain = {t: False for t in GAIN_TARGETS}
    days_to_gain: dict[float, int | None] = {t: None for t in GAIN_TARGETS}

    for d in range(entry_index, last_index + 1):
        bar = bars[d]
        off = d - entry_index

        # --- 前日確定した STOP 引き上げを、今日の寄りから有効にする ---
        if pending_stop is not None and off > pending_stop[1]:
            active_stop = pending_stop[0]
            pending_stop = None
        # 日次ログは「その日の寄り時点で機械が持っていた状態」を残す。
        # ここが当日中に更新された値だと、引き上げた STOP を遡って
        # 適用していないことを外から確認できなくなる。
        stop_at_open = active_stop
        state_at_open = state
        ref_at_open = w_open["reference_high"] if w_open else None
        wlow_at_open = w_open["low"] if w_open else None
        high_at_open = holding_high if holding_high > -math.inf else bar.open
        # 「今日の足を入れる前」の水準。RH-C / RH-E はこの 2 つしか使わない。
        pre_high = holding_high if holding_high > -math.inf else entry_price
        pre_high_date = holding_high_date
        pre_close_high = close_high if close_high > -math.inf else entry_price
        pre_close_high_date = close_high_date

        # --- 分析指標のみ（EXIT には使わない §13）---
        for t in GAIN_TARGETS:
            if not reached_gain[t] and bar.high >= targets[t]:
                reached_gain[t] = True
                days_to_gain[t] = off

        # --- CASE1: 初期STOPのみ。状態機械とは独立に走らせる ---
        if case1 is None and bar.low <= initial_stop:
            case1 = _stop_exit(bar, off, initial_stop, X_INITIAL_STOP)

        if not sm_closed:
            stop_hit = bar.low <= stop_at_open

            if state == S_INITIAL_HOLD:
                if stop_hit:
                    ex = _stop_exit(bar, off, stop_at_open, X_INITIAL_STOP)
                    case3 = ex
                    case2 = case2 or ex
                    timeline.append(
                        StateEvent(
                            off, bar.date, state, S_CLOSED, X_INITIAL_STOP, ex["price"],
                            f"元レンジ上限を終値突破する前に active_stop {stop_at_open:.1f} へ到達"
                            f"（安値 {bar.low:.1f}）"
                            + (f" ※寄り {bar.open:.1f} で既に割れていた" if ex["gap"] else ""),
                        )
                    )
                    state = S_CLOSED
                    sm_closed = True
                else:
                    holding_high, holding_high_date = _upd_high(
                        bar, holding_high, holding_high_date
                    )
                    if bar.close > upper:
                        upper_break_date, upper_break_offset = bar.date, off
                        upper_break_price = bar.close
                        breakout_high, breakout_close = bar.high, bar.close
                        state = S_TREND_HOLD
                        if bar.close < bar.open:
                            same_day_bearish += 1
                        if variant == VARIANT_A:
                            warning_armed_from = off + 1
                            gate_note = "警戒足の判定は翌営業日から開始（解釈(a)）"
                        else:
                            # B/C は「さらに上へ進んだ」ことを確認するまで警戒足を拾わない
                            warning_armed_from = 10**9
                            gate_note = (
                                f"警戒足はまだ有効化しない。"
                                f"{VARIANT_CONDITION_JA[variant]} を満たす日"
                                f"（UPTREND_CONFIRMED）を待つ / "
                                f"breakout_day_high {bar.high:.1f} / "
                                f"breakout_day_close {bar.close:.1f}"
                            )
                        timeline.append(
                            StateEvent(
                                off, bar.date, S_INITIAL_HOLD, S_TREND_HOLD,
                                E_UPPER_CLOSE_BREAK, bar.close,
                                f"終値 {bar.close:.1f} > 元レンジ上限 {upper:.1f} → TREND_HOLD へ。"
                                + gate_note,
                            )
                        )
                    elif bar.high > upper and not upper_high_only:
                        upper_high_only = True
                        timeline.append(
                            StateEvent(
                                off, bar.date, state, state, E_UPPER_HIGH_ONLY, bar.high,
                                f"高値 {bar.high:.1f} は上限超だが終値 {bar.close:.1f} は上限以下 →"
                                " 状態遷移させない（§3）",
                            )
                        )

            elif state == S_TREND_HOLD:
                if stop_hit:
                    kind = (
                        X_TRAIL_STOP if stop_at_open > initial_stop
                        else X_INITIAL_STOP_AFTER_BREAK
                    )
                    ex = _stop_exit(bar, off, stop_at_open, kind)
                    case3 = ex
                    case2 = case2 or ex
                    timeline.append(
                        StateEvent(
                            off, bar.date, state, S_CLOSED, kind, ex["price"],
                            f"active_stop {stop_at_open:.1f} へ到達（安値 {bar.low:.1f}）"
                            + (f" ※寄り {bar.open:.1f} で既に割れていた" if ex["gap"] else "")
                            + (f" / 初期STOP {initial_stop:.1f} から引き上げ済み"
                               if stop_at_open > initial_stop else
                               " / 引き上げ前だったため水準は初期STOPのまま"),
                        )
                    )
                    state = S_CLOSED
                    sm_closed = True
                else:
                    holding_high, holding_high_date = _upd_high(
                        bar, holding_high, holding_high_date
                    )
                    # --- VARIANT B/C: 上昇継続の確認（解釈(d)）---
                    # 突破日より後の足だけを見る。ここで翌日以降の足は読まない。
                    if (
                        variant != VARIANT_A
                        and confirm_offset is None
                        and breakout_high is not None
                        and breakout_close is not None
                    ):
                        confirmed = (
                            bar.high > breakout_high if variant == VARIANT_B
                            else bar.close > breakout_close
                        )
                        if confirmed:
                            confirm_date, confirm_offset = bar.date, off
                            confirm_price = (
                                bar.high if variant == VARIANT_B else bar.close
                            )
                            confirm_day_bearish = bar.close < bar.open
                            warning_armed_from = off + 1
                            timeline.append(
                                StateEvent(
                                    off, bar.date, S_TREND_HOLD, S_TREND_HOLD,
                                    E_UPTREND_CONFIRMED, confirm_price,
                                    (
                                        f"高値 {bar.high:.1f} > breakout_day_high "
                                        f"{breakout_high:.1f}"
                                        if variant == VARIANT_B else
                                        f"終値 {bar.close:.1f} > breakout_day_close "
                                        f"{breakout_close:.1f}"
                                    )
                                    + f" → 上昇継続を確認（突破から {off - (upper_break_offset or 0)} 営業日）。"
                                    "警戒足の判定は翌営業日から開始"
                                    + ("。※この確認日自体が陰線だが警戒足には使わない（§11）"
                                       if confirm_day_bearish else ""),
                                )
                            )
                    if off >= warning_armed_from and bar.close < bar.open:
                        w_open = _open_warning(
                            bar, off, len(warnings) + 1, holding_high, holding_high_date,
                            entry_price, active_stop, bars, d,
                            rh_rule=rh_rule,
                            pre_high=pre_high, pre_high_date=pre_high_date,
                            pre_close_high=pre_close_high,
                            pre_close_high_date=pre_close_high_date,
                        )
                        state = S_WARNING
                        timeline.append(
                            StateEvent(
                                off, bar.date, S_TREND_HOLD, S_WARNING, E_WARNING_CANDLE,
                                bar.low,
                                f"上限突破後の最初の陰線 O{bar.open:.1f} C{bar.close:.1f} / "
                                f"warning_low {bar.low:.1f} / "
                                f"reference_high {w_open['reference_high']:.1f}"
                                + (
                                    f"（{holding_high_date}の保有中最高値）"
                                    if rh_rule == RH_HOLDING else
                                    f"（{RH_RULE_CONDITION_JA[rh_rule]}"
                                    f" / {w_open['reference_high_date']}）"
                                )
                                + f" / 含み益 {w_open['unrealized_pct']:+.2f}%",
                            )
                        )

            elif state == S_WARNING:
                assert w_open is not None
                warning_low = w_open["low"]
                reference_high = w_open["reference_high"]
                low_break = bar.low < warning_low
                rehigh = bar.high > reference_high

                # --- 観測のみ（解釈(e)の材料。状態遷移には使わない）---
                # 「どこで割ったか」を break_rule に関係なく同じ規則で数える。
                # 参照するのは今日の足だけ。
                _observe_break(w_open, bar, off, warning_low, upper)
                close_break_today = bar.close < warning_low
                struct_break_today = close_break_today and bar.close < upper

                if stop_hit:
                    # STOP と再高値更新が同日 → 順序不明（§10）。
                    # ただし後述のとおり結果は変わらないので、記録のみ行う。
                    if rehigh and not (bar.open <= stop_at_open or bar.open > reference_high):
                        ambiguous_stop_days.append(bar.date)
                        flags.append(E_AMBIGUOUS_STOP)
                        timeline.append(
                            StateEvent(
                                off, bar.date, state, state, E_AMBIGUOUS_STOP, None,
                                f"同日に active_stop {stop_at_open:.1f} 到達と "
                                f"reference_high {reference_high:.1f} 更新。"
                                f"日足（O{bar.open:.1f} H{bar.high:.1f} L{bar.low:.1f} "
                                f"C{bar.close:.1f}）では先後を決められない。"
                                "※今日の安値が active_stop 以下である以上、"
                                "その日に確定する押し安値は active_stop を上回らないため、"
                                "どちらの順でも撤退水準は変わらない",
                            )
                        )
                    if low_break:
                        _record_low_break(timeline, bar, off, state, warning_low, w_open)
                        if case2 is None:
                            case2 = _warning_exit(bar, off, warning_low)
                    kind = (
                        X_TRAIL_STOP if stop_at_open > initial_stop
                        else X_INITIAL_STOP_AFTER_BREAK
                    )
                    ex = _stop_exit(bar, off, stop_at_open, kind)
                    case3 = ex
                    case2 = case2 or ex
                    if w_open["resolution"] == "open":
                        w_open["resolution"] = "stop"
                        w_open["resolved"] = (bar.date, off)
                    w_open["left_warning_off"] = off
                    timeline.append(
                        StateEvent(
                            off, bar.date, state, S_CLOSED, kind, ex["price"],
                            f"WARNING 中に active_stop {stop_at_open:.1f} へ到達"
                            f"（安値 {bar.low:.1f}）",
                        )
                    )
                    state = S_CLOSED
                    sm_closed = True
                else:
                    ambiguous = False
                    if low_break and rehigh:
                        if bar.open < warning_low:
                            pass          # 寄りで既に割れている → 下抜けが先で確定
                        elif bar.open > reference_high:
                            low_break = False   # 寄りで既に上抜け → 再高値更新が先で確定
                        else:
                            ambiguous = True
                            ambiguous_warning_days.append(bar.date)
                            flags.append(E_AMBIGUOUS_WARNING)
                            timeline.append(
                                StateEvent(
                                    off, bar.date, state, state, E_AMBIGUOUS_WARNING, None,
                                    f"同日に warning_low {warning_low:.1f} 下抜けと "
                                    f"reference_high {reference_high:.1f} 更新の両方が成立。"
                                    f"日足（O{bar.open:.1f} H{bar.high:.1f} L{bar.low:.1f} "
                                    f"C{bar.close:.1f}）では先後を決められない。"
                                    "CASE2 の EXIT 有無がこの順序に依存する",
                                )
                            )

                    if low_break:
                        _record_low_break(timeline, bar, off, state, warning_low, w_open)
                        if case2 is None:
                            case2 = _warning_exit(bar, off, warning_low, ambiguous=ambiguous)
                        if break_rule == BREAK_LOW:
                            # EXIT VARIANT 1: 日中に少しでも割ったら利確候補。
                            # 押し安値の更新も再高値更新も、この日以降は行わない。
                            case3 = _break_trigger(
                                BREAK_LOW, bar, off, reference_price=warning_low,
                                ambiguous=ambiguous,
                            )
                            w_open["left_warning_off"] = off
                            timeline.append(
                                StateEvent(
                                    off, bar.date, state, S_CLOSED,
                                    X_BREAK_EXIT[BREAK_LOW], warning_low,
                                    f"安値 {bar.low:.1f} < warning_low {warning_low:.1f} "
                                    "→ 利確候補として仮想EXIT（EXIT VARIANT 1）。"
                                    "主分析の約定は翌営業日始値、"
                                    f"STOP注文を置いていた場合の参考価格は {warning_low:.1f}",
                                )
                            )
                            state = S_CLOSED
                            sm_closed = True

                    if not sm_closed:
                        if bar.low < w_open["min_low_value"]:
                            w_open["min_low_value"] = bar.low
                            w_open["min_low_date"] = bar.date
                            w_open["min_low_index"] = d

                        # --- §7: 同日に REHIGH と利確候補が両立した ---
                        # 引け後に日足を見た時点では両方が同時に見えるので、
                        # どちらを採るかは日足からは決まらない。ここでは
                        # 「決めない」ことを記録し、比較側で両方走らせる。
                        if rehigh and break_rule in (BREAK_CLOSE, BREAK_STRUCT):
                            fires_today = (
                                close_break_today if break_rule == BREAK_CLOSE
                                else struct_break_today
                            )
                            if fires_today:
                                ambiguous_rehigh_exit_days.append(bar.date)
                                w_open["order_ambiguous"] = True
                                w_open["ambiguous_open_above_reference"] = (
                                    bar.open > reference_high
                                )
                                w_open["ambiguous_resolved_as"] = (
                                    "rehigh" if ambiguous_order == AMB_REHIGH else "exit"
                                )
                                timeline.append(
                                    StateEvent(
                                        off, bar.date, state, state,
                                        E_AMBIGUOUS_REHIGH_EXIT, None,
                                        f"同日に終値 {bar.close:.1f} < warning_low "
                                        f"{warning_low:.1f}（利確候補）と 高値 "
                                        f"{bar.high:.1f} > reference_high "
                                        f"{reference_high:.1f}（再上昇）の両方が成立。"
                                        f"日足（O{bar.open:.1f} H{bar.high:.1f} "
                                        f"L{bar.low:.1f} C{bar.close:.1f}）では"
                                        "先後を決められない。"
                                        f"この実行では "
                                        f"{AMBIGUOUS_ORDER_LABELS_JA[ambiguous_order]}"
                                        "（正しい順序という主張ではなく、"
                                        "比較側で逆順も走らせる）",
                                    )
                                )
                                if ambiguous_order == AMB_EXIT:
                                    rehigh = False

                        if rehigh:
                            if close_break_today:
                                # close 型の EXIT 条件と同日に再高値更新も成立。
                                # 既存の REHIGH ロジックを変更しない制約から
                                # 再高値更新を優先し、件数だけ記録する（解釈(e)）。
                                close_break_same_day_rehigh += 1
                                w_open["same_day_rehigh_on_close_break"] = True
                            state, active_stop, pending_stop = _confirm_rehigh(
                                w_open, bar, off, d, active_stop, initial_stop,
                                timeline, stop_updates, warnings, raw_warnings, bars,
                                ambiguous=ambiguous,
                            )
                            holding_high, holding_high_date = _upd_high(
                                bar, holding_high, holding_high_date
                            )
                            # 再高値更新日は定義上すでに「さらに上へ進んだ日」なので、
                            # B/C でも確認ゲートを課さず A と同じく翌営業日から拾う（解釈(d)）。
                            warning_armed_from = off + 1
                            if bar.close < bar.open:
                                same_day_bearish += 1
                            if (
                                variant == VARIANT_C
                                and breakout_close is not None
                                and bar.close <= breakout_close
                            ):
                                rehigh_failing_own_confirm += 1
                            w_open = None
                        else:
                            if bar.close < bar.open:
                                w_open["extra_bearish"] += 1
                                timeline.append(
                                    StateEvent(
                                        off, bar.date, state, state, E_WARNING_EXTRA,
                                        bar.low,
                                        f"WARNING 中の追加陰線 安値 {bar.low:.1f}。"
                                        f"警戒足は {w_open['date']} のまま置き換えない（§9）",
                                    )
                                )
                            # --- EXIT VARIANT 2 / 3: 終値で判定する（解釈(e)）---
                            if break_rule in (BREAK_CLOSE, BREAK_STRUCT):
                                fired = (
                                    close_break_today if break_rule == BREAK_CLOSE
                                    else struct_break_today
                                )
                                if fired:
                                    case3 = _break_trigger(
                                        break_rule, bar, off, reference_price=bar.close,
                                    )
                                    w_open["left_warning_off"] = off
                                    timeline.append(
                                        StateEvent(
                                            off, bar.date, state, S_CLOSED,
                                            X_BREAK_EXIT[break_rule], bar.close,
                                            f"終値 {bar.close:.1f} < warning_low "
                                            f"{warning_low:.1f}"
                                            + (
                                                f" かつ < 元レンジ上限 {upper:.1f}"
                                                if break_rule == BREAK_STRUCT else ""
                                            )
                                            + " → 利確候補（EXIT VARIANT "
                                            + ("2" if break_rule == BREAK_CLOSE else "3")
                                            + "）。仮想EXITは翌営業日始値",
                                        )
                                    )
                                    state = S_CLOSED
                                    sm_closed = True
                                elif close_break_today and not w_open["held_noted"]:
                                    # 終値では割ったが元レンジ上限は維持 → V3 は保有継続
                                    w_open["held_noted"] = True
                                    timeline.append(
                                        StateEvent(
                                            off, bar.date, state, state,
                                            E_CLOSE_BREAK_HELD, bar.close,
                                            f"終値 {bar.close:.1f} は warning_low "
                                            f"{warning_low:.1f} を割ったが、元レンジ上限 "
                                            f"{upper:.1f} は維持 → EXIT VARIANT 3 は保有継続",
                                        )
                                    )
                                elif (
                                    low_break and not close_break_today
                                    and not w_open["recovered_noted"]
                                ):
                                    # 日中は割ったが終値は回復 → V2/V3 は保有継続
                                    w_open["recovered_noted"] = True
                                    timeline.append(
                                        StateEvent(
                                            off, bar.date, state, state,
                                            E_INTRADAY_BREAK_RECOVERED, bar.close,
                                            f"安値 {bar.low:.1f} は warning_low "
                                            f"{warning_low:.1f} を割ったが終値 "
                                            f"{bar.close:.1f} は回復 → "
                                            "EXIT VARIANT 2/3 は保有継続",
                                        )
                                    )

        daily.append(
            DailyState(
                day_offset=off, date=bar.date, state=state_at_open,
                open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                volume=bar.volume,
                active_stop=stop_at_open, holding_high=high_at_open,
                reference_high=ref_at_open, warning_low=wlow_at_open,
                unrealized_pct=(bar.close - entry_price) / entry_price * 100.0,
            )
        )

        # 終値ベースの最高値は日次処理の最後に更新する。こうしておけば、
        # 同じ日に開いた警戒足の reference_high（RH-C）には当日の終値が入らない。
        if bar.close > close_high:
            close_high, close_high_date = bar.close, bar.date

        if case1 is not None and sm_closed:
            break

    # --- 未決着の WARNING を確定させる ---
    if w_open is not None:
        warnings.append(
            _finalize_warning(w_open, bars, end_offset=last_index - entry_index)
        )
        raw_warnings.append(w_open)

    # --- 約定側（翌営業日始値）をここで初めて埋める ---
    # ループの中では一切参照していないので、判定に翌日の足は入っていない。
    end_off_seen = last_index - entry_index
    case3 = _resolve_next_open_fill(case3, bars, entry_index, end_off_seen)
    case2 = _resolve_next_open_fill(case2, bars, entry_index, end_off_seen)
    warning_breaks = [
        _build_break(w, upper, bars, entry_index, end_off_seen) for w in raw_warnings
    ]
    ref_highs = [_build_ref_high(w, upper, entry_price) for w in raw_warnings]

    end_index = min(last_index, len(bars) - 1)
    truncated = not (case1 is not None and sm_closed)
    if case1 is None:
        case1 = _open_exit(bars[end_index], end_index - entry_index)
    if case3 is None:
        case3 = _open_exit(bars[end_index], end_index - entry_index)
        timeline.append(
            StateEvent(
                end_index - entry_index, bars[end_index].date, state, state, E_DATA_END,
                bars[end_index].close,
                f"追跡終端 {bars[end_index].date} 時点で保有継続中（active_stop {active_stop:.1f} 未到達）",
            )
        )
    if case2 is None:
        case2 = dict(case3)

    bars_tracked = max(
        (c["offset"] for c in (case1, case2, case3) if c["offset"] is not None), default=0
    ) + 1

    timeline.sort(key=lambda e: e.day_offset)

    cases = {
        CASE1: _case_result(CASE1, case1, bars, entry_index, entry_price),
        CASE2: _case_result(CASE2, case2, bars, entry_index, entry_price),
        CASE3: _case_result(CASE3, case3, bars, entry_index, entry_price),
    }

    ev = SMEvent(
        signal_date=date.fromisoformat(signal["date"]),
        code=signal["code"], name=signal["name"], sector=signal.get("sector", ""),
        signal_close=signal_close, signal_index=i,
        range_lower=lower, range_upper=upper, initial_stop=initial_stop,
        entry_available=True, entry_date=entry_bar.date, entry_index=entry_index,
        entry_price=entry_price, gap_pct=gap_pct,
        reached_trend_hold=upper_break_date is not None,
        upper_close_break_date=upper_break_date,
        upper_close_break_day_offset=upper_break_offset,
        upper_close_break_price=upper_break_price,
        upper_high_only_before_break=upper_high_only,
        warnings=warnings, stop_updates=stop_updates,
        final_active_stop=active_stop,
        max_active_stop=max([initial_stop] + [s.new_stop for s in stop_updates]),
        same_day_bearish_at_trend_entry=same_day_bearish,
        cases=cases,
        reached_gain=reached_gain, days_to_gain=days_to_gain,
        ambiguous_warning_days=ambiguous_warning_days,
        ambiguous_stop_days=ambiguous_stop_days,
        bars_tracked=bars_tracked, tracking_truncated=truncated,
        path_label="", flags=sorted(set(flags)),
        timeline=timeline, daily=daily,
        variant=variant,
        breakout_day_high=breakout_high, breakout_day_close=breakout_close,
        uptrend_confirmed_date=confirm_date,
        uptrend_confirmed_day_offset=confirm_offset,
        uptrend_confirmed_price=confirm_price,
        uptrend_confirm_day_bearish=confirm_day_bearish,
        rehigh_days_failing_own_confirm=rehigh_failing_own_confirm,
        break_rule=break_rule,
        warning_breaks=warning_breaks,
        close_break_with_same_day_rehigh=close_break_same_day_rehigh,
        rh_rule=rh_rule,
        ambiguous_order=ambiguous_order,
        ref_highs=ref_highs,
        ambiguous_rehigh_exit_days=ambiguous_rehigh_exit_days,
    )
    # 引き上げた STOP が実際に有効になった日付を、到達済みの日次ログから埋める。
    # 到達していなければ None のまま（その営業日はまだ来ていない）。
    date_by_off = {d.day_offset: d.date for d in daily}
    ev.stop_updates = [
        replace(su, effective_from_date=date_by_off.get(su.effective_from_day_offset))
        for su in ev.stop_updates
    ]

    # 比較用の後段パス。状態機械の結果は既に確定しており、影響しない。
    attach_fractal_comparison(ev, series, exp)
    return ev


# --- 日次処理のヘルパ ---------------------------------------------------------


def _upd_high(bar: OHLCVBar, high: float, high_date: date) -> tuple[float, date]:
    return (bar.high, bar.date) if bar.high > high else (high, high_date)


def _stop_exit(bar: OHLCVBar, off: int, stop: float, kind: str) -> dict[str, Any]:
    """STOP 到達。寄りが STOP を割っていれば STOP 価格での約定を仮定しない。"""
    gapped = bar.open < stop
    return {
        "type": kind, "date": bar.date, "offset": off,
        "price": bar.open if gapped else stop,
        "gap": gapped, "ambiguous": False,
    }


def _warning_exit(
    bar: OHLCVBar, off: int, warning_low: float, *, ambiguous: bool = False
) -> dict[str, Any]:
    """CASE2 の EXIT 候補。warning_low での約定は保証されない（§6A）。"""
    gapped = bar.open < warning_low
    return {
        "type": X_WARNING_LOW, "date": bar.date, "offset": off,
        "price": bar.open if gapped else warning_low,
        "gap": gapped, "ambiguous": ambiguous,
    }


def _break_trigger(
    rule: str, bar: OHLCVBar, off: int, *, reference_price: float,
    ambiguous: bool = False,
) -> dict[str, Any]:
    """warning_low 割れによる利確候補のトリガー（解釈(e)）。

    **その日の足だけ** で決まる。約定（翌営業日始値）は
    `_resolve_next_open_fill` がループ終了後に埋める。
    `reference_price` はトリガーの基準（V1 は warning_low、V2/V3 はその日の終値）で、
    翌営業日始値がここからどれだけギャップしたかを測るために持つ。
    """
    return {
        "type": X_BREAK_EXIT[rule], "date": bar.date, "offset": off,
        "price": None, "gap": False, "ambiguous": ambiguous,
        "fill": "next_open", "trigger_date": bar.date, "trigger_offset": off,
        "trigger_reference_price": reference_price,
        "trigger_close": bar.close,
    }


def _resolve_next_open_fill(
    ex: dict[str, Any] | None, bars: list[OHLCVBar], entry_index: int, end_off: int
) -> dict[str, Any] | None:
    """トリガーの翌営業日始値を仮想EXIT価格として埋める（ループ外の後処理）。

    追跡窓の終端でトリガーした場合は翌営業日がまだ存在しないので、
    約定を置かず `fill_pending` を立てる。終値で代用した価格を入れるが、
    「その値段で売れた」という主張ではない。
    """
    if ex is None or ex.get("fill") != "next_open":
        return ex
    t_off = ex["trigger_offset"]
    j = entry_index + t_off + 1
    ref = ex["trigger_reference_price"]
    if j >= len(bars) or t_off + 1 > end_off:
        ex.update(
            price=ex["trigger_close"], fill_pending=True,
            window_offset=t_off, next_open=None, next_open_date=None, gap_pct=None,
        )
        return ex
    nxt = bars[j]
    ex.update(
        date=nxt.date, offset=t_off + 1, window_offset=t_off,
        price=nxt.open, gap=nxt.open < ref, fill_pending=False,
        next_open=nxt.open, next_open_date=nxt.date,
        gap_pct=(nxt.open - ref) / ref * 100.0,
    )
    return ex


def _observe_break(
    w: dict[str, Any], bar: OHLCVBar, off: int, warning_low: float, upper: float
) -> None:
    """warning_low をどう割ったかを記録するだけ（解釈(e)。状態遷移には使わない）。"""
    if bar.low < warning_low:
        w["intraday_break_days"] += 1
        if w["first_intraday"] is None:
            w["first_intraday"] = (
                bar.date, off, bar.open, bar.low, bar.close,
                bar.open < warning_low, bar.close >= warning_low,
            )
    if bar.close < warning_low:
        w["close_break_days"] += 1
        if w["first_close_break"] is None:
            w["first_close_break"] = (bar.date, off, bar.close, bar.close >= upper)
        if bar.close < upper:
            w["struct_break_days"] += 1
            if w["first_struct_break"] is None:
                w["first_struct_break"] = (bar.date, off, bar.close)
    w["observed_days"] += 1


def _next_open_of(
    bars: list[OHLCVBar], entry_index: int, off: int, end_off: int, ref: float
) -> tuple[date | None, float | None, float | None]:
    """`off` の翌営業日の始値（ループ外でのみ呼ぶ）。追跡窓の外なら None。"""
    j = entry_index + off + 1
    if j >= len(bars) or off + 1 > end_off:
        return None, None, None
    nxt = bars[j]
    return nxt.date, nxt.open, (nxt.open - ref) / ref * 100.0


def _build_break(
    w: dict[str, Any], upper: float, bars: list[OHLCVBar], entry_index: int,
    end_off: int,
) -> WarningBreak:
    """WARNING エピソード 1 件分の割れ方を確定させる（ループ外の後処理）。"""
    ind = w["first_intraday"]
    cb = w["first_close_break"]
    sb = w["first_struct_break"]
    i_date, i_open, i_gap = (
        _next_open_of(bars, entry_index, ind[1], end_off, w["low"])
        if ind else (None, None, None)
    )
    c_date, c_open, c_gap = (
        _next_open_of(bars, entry_index, cb[1], end_off, cb[2]) if cb else (None, None, None)
    )
    s_date, s_open, s_gap = (
        _next_open_of(bars, entry_index, sb[1], end_off, sb[2]) if sb else (None, None, None)
    )
    return WarningBreak(
        seq=w["seq"], warning_date=w["date"], warning_day_offset=w["offset"],
        warning_low=w["low"], reference_high=w["reference_high"],
        original_range_upper=upper, observed_days=w["observed_days"],
        intraday_break_date=ind[0] if ind else None,
        intraday_break_day_offset=ind[1] if ind else None,
        intraday_break_open=ind[2] if ind else None,
        intraday_break_low=ind[3] if ind else None,
        intraday_break_close=ind[4] if ind else None,
        intraday_break_gap_open=bool(ind[5]) if ind else False,
        intraday_break_close_recovered=bool(ind[6]) if ind else False,
        intraday_break_days=w["intraday_break_days"],
        intraday_break_next_open_date=i_date,
        intraday_break_next_open=i_open,
        intraday_break_next_open_gap_pct=i_gap,
        close_break_date=cb[0] if cb else None,
        close_break_day_offset=cb[1] if cb else None,
        close_break_close=cb[2] if cb else None,
        close_break_above_range_upper=bool(cb[3]) if cb else False,
        close_break_days=w["close_break_days"],
        close_break_next_open_date=c_date,
        close_break_next_open=c_open,
        close_break_next_open_gap_pct=c_gap,
        days_from_intraday_to_close_break=(cb[1] - ind[1]) if (cb and ind) else None,
        struct_break_date=sb[0] if sb else None,
        struct_break_day_offset=sb[1] if sb else None,
        struct_break_close=sb[2] if sb else None,
        struct_break_days=w["struct_break_days"],
        struct_break_next_open_date=s_date,
        struct_break_next_open=s_open,
        struct_break_next_open_gap_pct=s_gap,
        days_from_close_to_struct_break=(sb[1] - cb[1]) if (sb and cb) else None,
        resolution=w["resolution"],
        left_warning_day_offset=w.get("left_warning_off"),
        rehigh_date=w["rehigh"][0] if w.get("rehigh") else None,
        same_day_rehigh_on_close_break=bool(w["same_day_rehigh_on_close_break"]),
    )


def _build_ref_high(
    w: dict[str, Any], upper: float, entry_price: float
) -> RefHighSnapshot:
    """警戒足 1 件分の reference_high 記録を確定させる（ループ外の後処理）。

    5 案の水準はすべて警戒足当日までの足から決まっているので、
    ここで未来の足を読むことはない。
    """
    cands: dict[str, float] = w["rh_candidates"]
    a = cands[RH_HOLDING]
    trail = w.get("trail")
    rh = w.get("rehigh")
    cb = w.get("first_close_break")

    if w["order_ambiguous"]:
        order_class = "ambiguous_same_day"
    elif rh and (cb is None or rh[1] < cb[1]):
        order_class = "rehigh_first"
    elif cb and (rh is None or cb[1] < rh[1]):
        order_class = "close_break_first"
    else:
        order_class = "neither"

    def pct(v: float) -> float:
        return (v - a) / a * 100.0 if a else 0.0

    return RefHighSnapshot(
        seq=w["seq"], warning_date=w["date"], warning_day_offset=w["offset"],
        rh_rule=w["rh_rule"],
        reference_high=w["reference_high"],
        reference_high_date=w["reference_high_date"],
        holding_high=a,
        warning_high=cands[RH_WARNING_HIGH],
        pre_warning_close_high=cands[RH_PRE_CLOSE],
        warning_open=cands[RH_WARNING_OPEN],
        pre_warning_high=cands[RH_PRE_HIGH],
        rh_b_vs_a_pct=pct(cands[RH_WARNING_HIGH]),
        rh_c_vs_a_pct=pct(cands[RH_PRE_CLOSE]),
        rh_d_vs_a_pct=pct(cands[RH_WARNING_OPEN]),
        rh_e_vs_a_pct=pct(cands[RH_PRE_HIGH]),
        a_equals_b=abs(a - cands[RH_WARNING_HIGH]) < 1e-9,
        lowest_rule=min(RH_RULES, key=lambda r: cands[r]),
        warning_low=w["low"], warning_close=w["bar"].close,
        original_range_upper=upper, entry_price=entry_price,
        unrealized_pct_at_warning=w["unrealized_pct"],
        observed_days=w["observed_days"],
        rehigh_date=rh[0] if rh else None,
        rehigh_day_offset=rh[1] if rh else None,
        rehigh_high=w.get("rehigh_high"),
        days_to_rehigh=(rh[1] - w["offset"]) if rh else None,
        close_break_date=cb[0] if cb else None,
        close_break_day_offset=cb[1] if cb else None,
        close_break_close=cb[2] if cb else None,
        order_class=order_class,
        order_ambiguous=bool(w["order_ambiguous"]),
        ambiguous_resolved_as=w["ambiguous_resolved_as"],
        ambiguous_open_above_reference=w["ambiguous_open_above_reference"],
        new_swing_low_candidate=trail[0] if trail else None,
        new_swing_low_date=trail[1] if trail else None,
        trail_stop_candidate=trail[2] if trail else None,
        active_stop_before=w["active_stop_before"],
        active_stop_after=trail[4] if trail else None,
        stop_raised=bool(trail[5]) if trail else False,
    )


def _open_exit(bar: OHLCVBar, off: int) -> dict[str, Any]:
    return {
        "type": X_DATA_END, "date": bar.date, "offset": off,
        "price": bar.close, "gap": False, "ambiguous": False,
    }


def _record_low_break(
    timeline: list[StateEvent], bar: OHLCVBar, off: int, state: str,
    warning_low: float, w_open: dict[str, Any],
) -> None:
    if w_open.get("low_break") is not None:
        return
    gapped = bar.open < warning_low
    w_open["low_break"] = (bar.date, off, bar.open, bar.low, gapped)
    # §17 の「どちらを先に突破したか」。後で再高値更新が来ても、
    # 先に割った事実の方が決着として先である。
    if w_open["resolution"] == "open":
        w_open["resolution"] = "low_break"
        w_open["resolved"] = (bar.date, off)
    timeline.append(
        StateEvent(
            off, bar.date, state, state, E_WARNING_LOW_BREAK, warning_low,
            f"warning_low {warning_low:.1f} を下抜け（安値 {bar.low:.1f}）→ 利確候補。"
            "CASE2 はここで仮想EXIT、CASE3 は保有を続ける",
            case=CASE2,
        )
    )
    if gapped:
        timeline.append(
            StateEvent(
                off, bar.date, state, state, E_GAP_THROUGH, bar.open,
                f"寄り {bar.open:.1f} が warning_low {warning_low:.1f} を下回っており、"
                "warning_low での約定は仮定できない",
                case=CASE2,
            )
        )


def _open_warning(
    bar: OHLCVBar, off: int, seq: int, holding_high: float, holding_high_date: date,
    entry_price: float, active_stop: float, bars: list[OHLCVBar], index: int,
    *,
    rh_rule: str = RH_HOLDING,
    pre_high: float | None = None, pre_high_date: date | None = None,
    pre_close_high: float | None = None, pre_close_high_date: date | None = None,
) -> dict[str, Any]:
    """警戒足を開く。`reference_high` はこの時点で 1 回だけ決める（解釈(f)）。

    5 案とも警戒足**当日まで**の足だけから決まる。
    RH-C / RH-E は当日を含めない（呼び出し側が「今日の足を入れる前」の値を渡す）。
    """
    if pre_high is None:
        pre_high, pre_high_date = holding_high, holding_high_date
    if pre_close_high is None:
        pre_close_high, pre_close_high_date = bar.close, bar.date
    candidates: dict[str, tuple[float, date]] = {
        RH_HOLDING: (holding_high, holding_high_date),
        RH_WARNING_HIGH: (bar.high, bar.date),
        RH_PRE_CLOSE: (pre_close_high, pre_close_high_date or bar.date),
        RH_WARNING_OPEN: (bar.open, bar.date),
        RH_PRE_HIGH: (pre_high, pre_high_date or bar.date),
    }
    reference_high, reference_high_date = candidates[rh_rule]
    return {
        "seq": seq, "date": bar.date, "offset": off, "bar": bar, "index": index,
        "low": bar.low,
        "rh_rule": rh_rule,
        "reference_high": reference_high, "reference_high_date": reference_high_date,
        "rh_candidates": {k: v[0] for k, v in candidates.items()},
        "unrealized_pct": (bar.close - entry_price) / entry_price * 100.0,
        "unrealized_high_pct": (bar.high - entry_price) / entry_price * 100.0,
        "active_stop_before": active_stop,
        # 警戒足の日から「今日まで」の最安値。未来の足は入らない。
        "min_low_value": bar.low, "min_low_date": bar.date, "min_low_index": index,
        "extra_bearish": 0, "low_break": None,
        # resolution = warning_low と reference_high の「どちらを先に突破したか」
        "resolution": "open", "resolved": None,
        # left_warning_off = 実際に WARNING 状態を抜けた営業日オフセット。
        # warning_low を割っても CASE3 は WARNING に留まるため resolution とは別物。
        "left_warning_off": None,
        "rehigh": None, "trail": None,
        # --- 解釈(e) の観測値。break_rule に関係なく同じ規則で埋める ---
        "observed_days": 0,
        "first_intraday": None, "first_close_break": None, "first_struct_break": None,
        "intraday_break_days": 0, "close_break_days": 0, "struct_break_days": 0,
        "same_day_rehigh_on_close_break": False,
        "held_noted": False, "recovered_noted": False,
        # --- 解釈(f) / §7 の観測値 ---
        "order_ambiguous": False,
        "ambiguous_resolved_as": "",
        "ambiguous_open_above_reference": None,
        "rehigh_high": None,
    }


def _confirm_rehigh(
    w: dict[str, Any], bar: OHLCVBar, off: int, index: int,
    active_stop: float, initial_stop: float,
    timeline: list[StateEvent], stop_updates: list[StopUpdate],
    warnings: list[WarningEpisode], raw_warnings: list[dict[str, Any]],
    bars: list[OHLCVBar], *, ambiguous: bool,
) -> tuple[str, float, tuple[float, int] | None]:
    """reference_high を再突破した日に押し安値を確定し、STOP を引き上げる。

    `new_swing_low_candidate` は警戒足の日から **今日まで** の安値の最小値。
    未来の足は一切参照しない。引き上げた STOP は翌営業日から有効にする。
    """
    swing_low = w["min_low_value"]
    swing_low_date = w["min_low_date"]
    cand = swing_low * TRAIL_BUFFER
    old = active_stop
    new = max(old, cand)
    raised = new > old

    timeline.append(
        StateEvent(
            off, bar.date, S_WARNING, S_TREND_HOLD, E_REHIGH, bar.high,
            f"高値 {bar.high:.1f} > reference_high {w['reference_high']:.1f} → 調整後の再高値更新。"
            f"警戒足 {w['date']} から本日までの最安値 {swing_low:.1f}"
            f"（{swing_low_date}）を押し安値として確定"
            + ("（同日に warning_low も下抜けており順序不明）" if ambiguous else ""),
        )
    )
    if raised:
        timeline.append(
            StateEvent(
                off, bar.date, S_TREND_HOLD, S_TREND_HOLD, E_STOP_RAISED, new,
                f"active_stop {old:.1f} → {new:.1f}（押し安値 {swing_low:.1f} の 0.5% 下）。"
                "翌営業日から有効（当日の安値には遡って適用しない）",
            )
        )
        stop_updates.append(
            StopUpdate(
                seq=len(stop_updates) + 1,
                stop_update_date=bar.date, day_offset=off,
                old_stop=old, new_stop=new,
                new_swing_low_candidate=swing_low, new_swing_low_date=swing_low_date,
                reference_high=w["reference_high"], rehigh_date=bar.date,
                # 翌営業日の日付は「その日が来て」から埋める。ここで bars[index+1]
                # を読むと、確定した瞬間には知り得ない足を見たことになる。
                effective_from_date=None,
                effective_from_day_offset=off + 1,
                raise_pct_from_initial_stop=(new - initial_stop) / initial_stop * 100.0,
            )
        )
    else:
        timeline.append(
            StateEvent(
                off, bar.date, S_TREND_HOLD, S_TREND_HOLD, E_STOP_KEPT, old,
                f"押し安値から出た候補 {cand:.1f} は現在の active_stop {old:.1f} 以下のため据え置き。"
                "STOP は上方向にしか動かさない（§7）",
            )
        )

    if ambiguous:
        w["resolution"] = "ambiguous_both"
        w["resolved"] = (bar.date, off)
    elif w["resolution"] == "open":
        w["resolution"] = "rehigh"
        w["resolved"] = (bar.date, off)
    # 既に "low_break" で決着済みなら、先に割った事実の方を残す
    w["left_warning_off"] = off
    w["rehigh"] = (bar.date, off)
    w["rehigh_high"] = bar.high
    w["trail"] = (swing_low, swing_low_date, cand, old, new, raised)
    warnings.append(_finalize_warning(w, bars, end_offset=off))
    raw_warnings.append(w)

    return S_TREND_HOLD, active_stop, ((new, off) if raised else None)


def _finalize_warning(
    w: dict[str, Any], bars: list[OHLCVBar], *, end_offset: int
) -> WarningEpisode:
    """WARNING エピソードを確定させる。fractal との比較は後段の別パスで埋める。"""
    bar: OHLCVBar = w["bar"]
    idx: int = w["index"]
    prev_close = bars[idx - 1].close if idx > 0 else None
    atr = _atr(bars, idx)
    avg_vol = _avg_volume(bars, idx)
    day_range = bar.high - bar.low
    body = abs(bar.close - bar.open)

    lb = w.get("low_break")
    trail = w.get("trail")
    resolved = w.get("resolved")

    # warning_low を割ったあとも CASE3 が WARNING に留まり続けた日数（解釈(b)）。
    # 決着（resolution）ではなく、実際に WARNING を抜けた日で測る。
    stuck = None
    if lb is not None:
        left = w.get("left_warning_off")
        stuck = max(0, (left if left is not None else end_offset) - lb[1])

    return WarningEpisode(
        seq=w["seq"], date=bar.date, day_offset=w["offset"],
        open=bar.open, high=bar.high, low=bar.low, close=bar.close, volume=bar.volume,
        reference_high=w["reference_high"], reference_high_date=w["reference_high_date"],
        warning_high_vs_reference_high_pct=(
            (bar.high - w["reference_high"]) / w["reference_high"] * 100.0
        ),
        unrealized_pct_at_warning=w["unrealized_pct"],
        unrealized_high_pct_at_warning=w["unrealized_high_pct"],
        resolution=w["resolution"],
        resolved_date=resolved[0] if resolved else None,
        resolved_day_offset=resolved[1] if resolved else None,
        days_to_resolve=(resolved[1] - w["offset"]) if resolved else None,
        low_break_date=lb[0] if lb else None,
        low_break_day_offset=lb[1] if lb else None,
        low_break_open=lb[2] if lb else None,
        low_break_low=lb[3] if lb else None,
        gap_through_warning_low=bool(lb[4]) if lb else False,
        low_break_reference_price=(
            (lb[2] if lb[4] else w["low"]) if lb else None
        ),
        rehigh_date=w["rehigh"][0] if w.get("rehigh") else None,
        rehigh_day_offset=w["rehigh"][1] if w.get("rehigh") else None,
        new_swing_low_candidate=trail[0] if trail else None,
        new_swing_low_date=trail[1] if trail else None,
        trail_stop_candidate=trail[2] if trail else None,
        active_stop_before=w["active_stop_before"],
        active_stop_after=trail[4] if trail else None,
        stop_raised=bool(trail[5]) if trail else False,
        extra_bearish_count=w["extra_bearish"],
        days_held_in_warning_after_low_break=stuck,
        change_pct=((bar.close - prev_close) / prev_close * 100.0) if prev_close else None,
        body_pct=(body / bar.open * 100.0) if bar.open else 0.0,
        body_to_atr=(body / atr if atr else None),
        volume_ratio=(bar.volume / avg_vol if avg_vol else None),
        close_pos_in_day_range=((bar.close - bar.low) / day_range if day_range > 0 else None),
        manual_exit_review=True,
        fractal_confirm_day_offset=None,
        fractal_is_same_low=None,
    )


def attach_fractal_comparison(ev: SMEvent, series: PriceSeries, exp) -> None:
    """押し安値の確定が既存 fractal 検出より早いかを後段で埋める（§20 Q3）。

    **状態機械の結果には一切影響しない。** 比較のためだけに、確定済みの
    `new_swing_low_candidate` を fractal がいつ押し安値として認めるかを調べる。
    """
    if exp is None or ev.entry_index is None or not ev.warnings:
        return
    bars = list(series.bars)
    entry_index = ev.entry_index
    # fractal は右側 pivot_window 本が揃うまで確定しないので、保有期間より少し先まで見る
    upto = min(len(bars) - 1, entry_index + ev.bars_tracked + 10)
    updated: list[WarningEpisode] = []
    for w in ev.warnings:
        if w.new_swing_low_date is None:
            updated.append(w)
            continue
        low_index = next(
            (j for j in range(entry_index, upto + 1) if bars[j].date == w.new_swing_low_date),
            None,
        )
        if low_index is None:
            updated.append(w)
            continue
        j = _fractal_confirm_index(bars, low_index, upto, exp)
        updated.append(
            replace(
                w,
                fractal_confirm_day_offset=(j - entry_index) if j is not None else None,
                fractal_is_same_low=j is not None,
            )
        )
    ev.warnings = updated


def _fractal_confirm_index(
    bars: list[OHLCVBar], low_index: int, upto_index: int, exp
) -> int | None:
    """既存 fractal 検出がその安値を押し安値として確定する足の index（比較用）。

    **状態機械には一切使わない。** §20 Q3（既存 fractal より早いか）の材料。
    各 j でスライスして呼ぶので、この比較自体も未来を見ていない。
    """
    for j in range(low_index, min(upto_index, len(bars) - 1) + 1):
        lows = detect_swings(bars[: j + 1], exp)[1]
        if any(sp.index == low_index for sp in lows):
            return j
    return None


def _case_result(
    case: str, ex: dict[str, Any], bars: list[OHLCVBar], entry_index: int,
    entry_price: float,
) -> CaseResult:
    off = ex["offset"]
    # 最大含み益は「実際に保有していた期間」で測る。翌営業日始値で降りた場合、
    # その日の高値はもう自分のものではないので窓には入れない。
    w_off = ex.get("window_offset", off)
    window = bars[entry_index : entry_index + (w_off or 0) + 1]
    max_gain = max(((b.high - entry_price) / entry_price * 100.0) for b in window)
    max_gain_date = max(window, key=lambda b: b.high).date
    max_loss = min(((b.low - entry_price) / entry_price * 100.0) for b in window)
    ret = (ex["price"] - entry_price) / entry_price * 100.0 if ex["price"] else None
    return CaseResult(
        case=case, exit_type=ex["type"], exit_date=ex["date"], exit_day_offset=off,
        exit_reference_price=ex["price"], gap_through=bool(ex["gap"]),
        approximate_return_pct=ret, holding_days=(off + 1) if off is not None else None,
        still_open=(ex["type"] == X_DATA_END), order_ambiguous=bool(ex["ambiguous"]),
        max_gain_pct=max_gain, max_gain_date=max_gain_date, max_loss_pct=max_loss,
        giveback_pct=(max_gain - ret) if ret is not None else None,
        trigger_date=ex.get("trigger_date"),
        trigger_day_offset=ex.get("trigger_offset"),
        fill_rule=ex.get("fill", "same_day"),
        fill_pending=bool(ex.get("fill_pending", False)),
        fill_gap_pct=ex.get("gap_pct"),
    )


def _no_entry(
    signal, i, lower, upper, stop, variant: str = VARIANT_A,
    break_rule: str = BREAK_HOLD, rh_rule: str = RH_HOLDING,
    ambiguous_order: str = AMB_REHIGH,
) -> SMEvent:
    empty = {
        c: CaseResult(c, "NO_ENTRY", None, None, None, False, None, None, True, False,
                      None, None, None, None)
        for c in CASES
    }
    return SMEvent(
        signal_date=date.fromisoformat(signal["date"]),
        code=signal["code"], name=signal["name"], sector=signal.get("sector", ""),
        signal_close=float(signal["signal_close"]), signal_index=i,
        range_lower=lower, range_upper=upper, initial_stop=stop,
        entry_available=False, entry_date=None, entry_index=None,
        entry_price=None, gap_pct=None,
        reached_trend_hold=False, upper_close_break_date=None,
        upper_close_break_day_offset=None, upper_close_break_price=None,
        upper_high_only_before_break=False,
        warnings=[], stop_updates=[], final_active_stop=stop, max_active_stop=stop,
        same_day_bearish_at_trend_entry=0, cases=empty,
        reached_gain={t: False for t in GAIN_TARGETS},
        days_to_gain={t: None for t in GAIN_TARGETS},
        ambiguous_warning_days=[], ambiguous_stop_days=[],
        bars_tracked=0, tracking_truncated=True,
        path_label="NO_ENTRY", flags=["NO_NEXT_OPEN"],
        variant=variant, break_rule=break_rule,
        rh_rule=rh_rule, ambiguous_order=ambiguous_order,
    )


# --- 経路の分類（分析ラベル。売買ルールではない）------------------------------

PATH_LABELS_JA = {
    "P0_NO_BREAKOUT": "P0 上限を終値突破せずSTOP（TREND_HOLDへ到達せず）",
    "P1_BREAKOUT_NO_WARNING": "P1 突破したが警戒足が出る前にSTOP",
    "P2_WARNING_LOW_BREAK": "P2 警戒足 → warning_low 下抜け（再高値更新なし）",
    "P3_REHIGH_ONCE": "P3 警戒足 → 再高値更新1回 → STOP引き上げ",
    "P4_REHIGH_MULTI": "P4 再高値更新2回以上（複数回の押し安値形成）",
    "P5_WARNING_UNRESOLVED": "P5 警戒足のまま決着せず（追跡終端）",
    "NO_ENTRY": "仮想ENTRY不可",
}

FLAG_LABELS_JA = {
    E_AMBIGUOUS_WARNING: "同日に warning_low 下抜けと再高値更新（順序不明）",
    E_AMBIGUOUS_STOP: "同日に active_stop 到達と再高値更新（順序不明）",
    "STOP_RAISED": "active_stop を1回以上引き上げた",
    "STOP_RAISED_TWICE": "active_stop を2回以上引き上げた",
    "GAP_THROUGH_WARNING_LOW": "warning_low を寄りでギャップ割れ（約定を仮定できない）",
    "WARNING_TOO_EARLY": "警戒足が上限突破の翌営業日に出た",
    "NO_UPTREND_CONFIRM": "上限突破したが UPTREND_CONFIRMED が来ないまま終わった（B/C）",
    "WARNING_NEXT_DAY_AFTER_CONFIRM": "警戒足が UPTREND_CONFIRMED の翌営業日に出た（B/C）",
    "CONFIRM_DAY_BEARISH": "UPTREND_CONFIRMED の日そのものが陰線だった（§11）",
    "STUCK_IN_WARNING": "warning_low を割ったのに WARNING に留まり続けた（解釈(b)）",
    "CASE3_BETTER_THAN_CASE1": "CASE3 の方が CASE1 より有利な撤退になった",
    "CASE2_BETTER_THAN_CASE3": "CASE2 の方が CASE3 より有利な撤退になった",
    "ROSE5_THEN_LOST_CASE1": "CASE1 で +5%以上まで上昇後に損失",
}


def classify_path(ev: SMEvent) -> str:
    if not ev.entry_available:
        return "NO_ENTRY"
    if not ev.reached_trend_hold:
        return "P0_NO_BREAKOUT"
    if not ev.warnings:
        return "P1_BREAKOUT_NO_WARNING"
    if ev.rehigh_count >= 2:
        return "P4_REHIGH_MULTI"
    if ev.rehigh_count == 1:
        return "P3_REHIGH_ONCE"
    if any(w.low_break_date is not None for w in ev.warnings):
        return "P2_WARNING_LOW_BREAK"
    return "P5_WARNING_UNRESOLVED"


def apply_classification(events: list[SMEvent]) -> None:
    for ev in events:
        ev.path_label = classify_path(ev)
        if ev.stop_raise_count >= 1:
            ev.flags.append("STOP_RAISED")
        if ev.stop_raise_count >= 2:
            ev.flags.append("STOP_RAISED_TWICE")
        if any(w.gap_through_warning_low for w in ev.warnings):
            ev.flags.append("GAP_THROUGH_WARNING_LOW")
        if (
            ev.upper_close_break_day_offset is not None
            and ev.warnings
            and ev.warnings[0].day_offset == ev.upper_close_break_day_offset + 1
        ):
            ev.flags.append("WARNING_TOO_EARLY")
        if ev.warning_gate_pending:
            ev.flags.append("NO_UPTREND_CONFIRM")
        if (
            ev.uptrend_confirmed_day_offset is not None
            and ev.warnings
            and ev.warnings[0].day_offset == ev.uptrend_confirmed_day_offset + 1
        ):
            ev.flags.append("WARNING_NEXT_DAY_AFTER_CONFIRM")
        if ev.uptrend_confirm_day_bearish:
            ev.flags.append("CONFIRM_DAY_BEARISH")
        if any((w.days_held_in_warning_after_low_break or 0) > 0 for w in ev.warnings):
            ev.flags.append("STUCK_IN_WARNING")
        c1, c2, c3 = (ev.cases[c] for c in CASES)
        if (
            c1.approximate_return_pct is not None
            and c3.approximate_return_pct is not None
            and c3.approximate_return_pct > c1.approximate_return_pct
        ):
            ev.flags.append("CASE3_BETTER_THAN_CASE1")
        if (
            c2.approximate_return_pct is not None
            and c3.approximate_return_pct is not None
            and c2.approximate_return_pct > c3.approximate_return_pct
        ):
            ev.flags.append("CASE2_BETTER_THAN_CASE3")
        if c1.rose5_then_lost:
            ev.flags.append("ROSE5_THEN_LOST_CASE1")
        ev.flags = sorted(set(ev.flags))


# --- CSV 出力 -----------------------------------------------------------------

EVENT_COLUMNS = [
    "signal_date", "code", "name", "sector", "path_label", "flags",
    "signal_close", "entry_date", "entry_price", "gap_pct",
    "original_range_lower", "original_range_upper", "initial_stop",
    "reached_trend_hold", "range_upper_close_break_date",
    "range_upper_close_break_day_offset", "range_upper_close_break_price",
    "upper_high_only_before_break",
    "warning_count", "first_warning_date", "first_warning_day_offset",
    "first_warning_low", "first_warning_reference_high",
    "first_warning_unrealized_pct", "warning_low_break_count",
    "rehigh_count", "first_rehigh_date", "first_new_swing_low_candidate",
    "first_trail_stop_candidate",
    "stop_raise_count", "final_active_stop", "max_active_stop",
    "max_stop_raise_pct_from_initial",
    "same_day_bearish_at_trend_entry",
    "ambiguous_warning_day_count", "ambiguous_stop_day_count",
    "reached_gain_3", "reached_gain_5", "reached_gain_10",
    "bars_tracked", "tracking_truncated",
]
for _c in CASES:
    EVENT_COLUMNS += [
        f"{_c}_exit_type", f"{_c}_exit_date", f"{_c}_exit_day_offset",
        f"{_c}_exit_reference_price", f"{_c}_gap_through",
        f"{_c}_approximate_return_pct", f"{_c}_holding_days",
        f"{_c}_max_gain_pct", f"{_c}_max_gain_date", f"{_c}_max_loss_pct",
        f"{_c}_giveback_pct", f"{_c}_order_ambiguous",
    ]
EVENT_COLUMNS.append("timeline")


def event_row(ev: SMEvent) -> dict[str, str]:
    fw = ev.warnings[0] if ev.warnings else None
    rehighs = [w for w in ev.warnings if w.rehigh_date is not None]
    row: dict[str, Any] = {
        "signal_date": ev.signal_date, "code": ev.code, "name": ev.name,
        "sector": ev.sector, "path_label": ev.path_label, "flags": ";".join(ev.flags),
        "signal_close": ev.signal_close, "entry_date": ev.entry_date,
        "entry_price": ev.entry_price, "gap_pct": ev.gap_pct,
        "original_range_lower": ev.range_lower, "original_range_upper": ev.range_upper,
        "initial_stop": ev.initial_stop,
        "reached_trend_hold": ev.reached_trend_hold,
        "range_upper_close_break_date": ev.upper_close_break_date,
        "range_upper_close_break_day_offset": ev.upper_close_break_day_offset,
        "range_upper_close_break_price": ev.upper_close_break_price,
        "upper_high_only_before_break": ev.upper_high_only_before_break,
        "warning_count": ev.warning_count,
        "first_warning_date": fw.date if fw else None,
        "first_warning_day_offset": fw.day_offset if fw else None,
        "first_warning_low": fw.low if fw else None,
        "first_warning_reference_high": fw.reference_high if fw else None,
        "first_warning_unrealized_pct": fw.unrealized_pct_at_warning if fw else None,
        "warning_low_break_count": sum(
            1 for w in ev.warnings if w.low_break_date is not None
        ),
        "rehigh_count": ev.rehigh_count,
        "first_rehigh_date": rehighs[0].rehigh_date if rehighs else None,
        "first_new_swing_low_candidate": (
            rehighs[0].new_swing_low_candidate if rehighs else None
        ),
        "first_trail_stop_candidate": (
            rehighs[0].trail_stop_candidate if rehighs else None
        ),
        "stop_raise_count": ev.stop_raise_count,
        "final_active_stop": ev.final_active_stop,
        "max_active_stop": ev.max_active_stop,
        "max_stop_raise_pct_from_initial": (
            (ev.max_active_stop - ev.initial_stop) / ev.initial_stop * 100.0
        ),
        "same_day_bearish_at_trend_entry": ev.same_day_bearish_at_trend_entry,
        "ambiguous_warning_day_count": len(ev.ambiguous_warning_days),
        "ambiguous_stop_day_count": len(ev.ambiguous_stop_days),
        "bars_tracked": ev.bars_tracked, "tracking_truncated": ev.tracking_truncated,
        "timeline": " > ".join(e.label() for e in ev.timeline),
    }
    for t in GAIN_TARGETS:
        row[f"reached_gain_{int(t)}"] = ev.reached_gain[t]
    for c in CASES:
        r = ev.cases[c]
        row.update({
            f"{c}_exit_type": r.exit_type, f"{c}_exit_date": r.exit_date,
            f"{c}_exit_day_offset": r.exit_day_offset,
            f"{c}_exit_reference_price": r.exit_reference_price,
            f"{c}_gap_through": r.gap_through,
            f"{c}_approximate_return_pct": r.approximate_return_pct,
            f"{c}_holding_days": r.holding_days,
            f"{c}_max_gain_pct": r.max_gain_pct, f"{c}_max_gain_date": r.max_gain_date,
            f"{c}_max_loss_pct": r.max_loss_pct, f"{c}_giveback_pct": r.giveback_pct,
            f"{c}_order_ambiguous": r.order_ambiguous,
        })
    return {k: _cell(row.get(k)) for k in EVENT_COLUMNS}


def write_events_csv(events: list[SMEvent], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(CSV_NOTE + "\n")
        w = csv.DictWriter(f, fieldnames=EVENT_COLUMNS)
        w.writeheader()
        for ev in events:
            w.writerow(event_row(ev))
    return path


def write_timeline_csv(events: list[SMEvent], path: Path) -> Path:
    cols = ["code", "name", "signal_date", "day_offset", "date",
            "state_before", "state_after", "kind", "price", "case", "detail"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(
            "# 状態遷移の時系列。case 列が CASE2 のものは CASE2 だけに効くイベント"
            "（CASE3 は warning_low で降りない）。\n"
        )
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for ev in events:
            for te in ev.timeline:
                w.writerow({
                    "code": ev.code, "name": ev.name,
                    "signal_date": ev.signal_date.isoformat(),
                    "day_offset": te.day_offset, "date": te.date.isoformat(),
                    "state_before": te.state_before, "state_after": te.state_after,
                    "kind": te.kind, "price": _cell(te.price), "case": te.case,
                    "detail": te.detail,
                })
    return path


def write_warnings_csv(events: list[SMEvent], path: Path) -> Path:
    cols = ["code", "name", "signal_date", "entry_price", "initial_stop"]
    cols += [f for f in WarningEpisode.__dataclass_fields__]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(
            "# 元レンジ上限を終値突破した後の警戒足のみ。ENTRY 直後の陰線は含まない"
            "（今回の検証仮説）。change_pct / body_to_atr / volume_ratio /"
            " close_pos_in_day_range は大陰線例外の参考指標であり、"
            "自動 EXIT には使っていない（§14）。"
            " fractal_* 列は既存 swing 検出との比較用で、状態機械には使っていない。\n"
        )
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for ev in events:
            for wc in ev.warnings:
                row = {k: _cell(v) for k, v in asdict(wc).items()}
                row.update(
                    code=ev.code, name=ev.name,
                    signal_date=ev.signal_date.isoformat(),
                    entry_price=_cell(ev.entry_price),
                    initial_stop=_cell(ev.initial_stop),
                )
                w.writerow(row)
    return path


def write_stop_updates_csv(events: list[SMEvent], path: Path) -> Path:
    cols = ["code", "name", "signal_date", "entry_price", "initial_stop"]
    cols += [f for f in StopUpdate.__dataclass_fields__]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(
            "# active_stop の引き上げ履歴。STOP は上方向にしか動かない（§7）。"
            " effective_from_* は look-ahead を避けるため翌営業日から有効にしていることを示す。\n"
        )
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for ev in events:
            for su in ev.stop_updates:
                row = {k: _cell(v) for k, v in asdict(su).items()}
                row.update(
                    code=ev.code, name=ev.name,
                    signal_date=ev.signal_date.isoformat(),
                    entry_price=_cell(ev.entry_price),
                    initial_stop=_cell(ev.initial_stop),
                )
                w.writerow(row)
    return path


def write_daily_state_csv(events: list[SMEvent], path: Path) -> Path:
    cols = ["code", "signal_date"] + [f for f in DailyState.__dataclass_fields__]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(
            "# 各営業日の状態と、その日の寄り時点で有効だった active_stop（§11）。"
            " 引き上げた STOP を当日の安値へ遡って適用していないことがここで確認できる。\n"
        )
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for ev in events:
            for ds in ev.daily:
                row = {k: _cell(v) for k, v in asdict(ds).items()}
                row.update(code=ev.code, signal_date=ev.signal_date.isoformat())
                w.writerow(row)
    return path


def write_case_comparison_csv(events: list[SMEvent], path: Path) -> Path:
    cols = ["code", "name", "signal_date", "path_label", "case", "case_label",
            "exit_type", "exit_date", "exit_day_offset", "exit_reference_price",
            "gap_through", "approximate_return_pct", "holding_days", "still_open",
            "order_ambiguous", "max_gain_pct", "max_gain_date", "max_loss_pct",
            "giveback_pct", "rose5_then_lost", "gave_back_most"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(
            "# CASE 別の仮想 EXIT 比較（§15）。最も成績のよい CASE を採用する、"
            "という使い方はしない。gave_back_most は「+10%以上まで上昇し、"
            "最終リターンが最大含み益の半分未満」という集計上の定義。\n"
        )
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for ev in events:
            for c in CASES:
                r = ev.cases[c]
                w.writerow({
                    "code": ev.code, "name": ev.name,
                    "signal_date": ev.signal_date.isoformat(),
                    "path_label": ev.path_label, "case": c,
                    "case_label": CASE_LABELS_JA[c],
                    "exit_type": r.exit_type, "exit_date": _cell(r.exit_date),
                    "exit_day_offset": _cell(r.exit_day_offset),
                    "exit_reference_price": _cell(r.exit_reference_price),
                    "gap_through": _cell(r.gap_through),
                    "approximate_return_pct": _cell(r.approximate_return_pct),
                    "holding_days": _cell(r.holding_days),
                    "still_open": _cell(r.still_open),
                    "order_ambiguous": _cell(r.order_ambiguous),
                    "max_gain_pct": _cell(r.max_gain_pct),
                    "max_gain_date": _cell(r.max_gain_date),
                    "max_loss_pct": _cell(r.max_loss_pct),
                    "giveback_pct": _cell(r.giveback_pct),
                    "rose5_then_lost": _cell(r.rose5_then_lost),
                    "gave_back_most": _cell(r.gave_back_most),
                })
    return path


# --- 集計（§17）--------------------------------------------------------------


@dataclass(frozen=True)
class SummaryRow:
    section: str
    metric: str
    value: str
    note: str = ""


def summarize(events: list[SMEvent]) -> list[SummaryRow]:
    entered = [e for e in events if e.entry_available]
    n = len(entered)
    rows: list[SummaryRow] = [
        SummaryRow("INITIAL_HOLD", "対象イベント数", str(len(events)),
                   "near.max_position_in_range=0.65 で発生した ENTRY_CANDIDATE。前回と同一"),
        SummaryRow("INITIAL_HOLD", "仮想ENTRYできた件数", _rate(n, len(events)),
                   "ENTRY価格はシグナル翌営業日の始値"),
    ]

    no_break = [e for e in entered if not e.reached_trend_hold]
    broke = [e for e in entered if e.reached_trend_hold]
    rows += [
        SummaryRow("INITIAL_HOLD", "上限を終値突破する前にSTOPした件数",
                   _rate(len(no_break), n), "TREND_HOLD へ到達しなかった件"),
        SummaryRow("INITIAL_HOLD", "元レンジ上限を終値突破した件数（TREND_HOLD到達）",
                   _rate(len(broke), n)),
        SummaryRow("INITIAL_HOLD", "高値だけ上限を超えたが終値では超えなかった件数",
                   _rate(sum(1 for e in entered if e.upper_high_only_before_break), n),
                   "状態遷移させていない（§3）"),
    ]

    warned = [e for e in broke if e.warnings]
    first_offsets = [
        float(e.warnings[0].day_offset - (e.upper_close_break_day_offset or 0))
        for e in warned
    ]
    first_gains = [e.warnings[0].unrealized_pct_at_warning for e in warned]
    resolutions: dict[str, int] = {}
    total_warnings = 0
    for e in entered:
        for w in e.warnings:
            total_warnings += 1
            resolutions[w.resolution] = resolutions.get(w.resolution, 0) + 1
    rows += [
        SummaryRow("WARNING", "上限突破後にWARNINGが発生した件数", _rate(len(warned), len(broke)),
                   f"警戒足の総数 {total_warnings} 本"
                   f"（前回は ENTRY 直後からの全陰線で 175 本）"),
        SummaryRow("WARNING", "1イベントあたりの警戒足本数（中央値）",
                   f"{_median([float(e.warning_count) for e in warned]):.1f} 本"
                   if warned else "－"),
        SummaryRow("WARNING", "上限突破からWARNING発生までの営業日数（中央値）",
                   f"{_median(first_offsets):.0f} 日" if first_offsets else "－",
                   f"最短 {min(first_offsets):.0f} 日 / 最長 {max(first_offsets):.0f} 日"
                   if first_offsets else ""),
        SummaryRow("WARNING", "WARNING発生時の含み益率（中央値）",
                   f"{_median(first_gains):+.2f}%" if first_gains else "－",
                   "最初の警戒足の終値ベース。仮想ENTRY価格基準"),
        SummaryRow("WARNING", "warning_low を先に割った警戒足",
                   _rate(resolutions.get("low_break", 0), total_warnings)),
        SummaryRow("WARNING", "reference_high を先に更新した警戒足",
                   _rate(resolutions.get("rehigh", 0), total_warnings)),
        SummaryRow("WARNING", "同日に両方到達で順序不明の警戒足",
                   _rate(resolutions.get("ambiguous_both", 0), total_warnings),
                   "AMBIGUOUS_WARNING_ORDER。CASE2 の EXIT 有無がこの順序に依存する"),
        SummaryRow("WARNING", "決着前に active_stop へ到達した警戒足",
                   _rate(resolutions.get("stop", 0), total_warnings)),
        SummaryRow("WARNING", "追跡終端まで決着しなかった警戒足",
                   _rate(resolutions.get("open", 0), total_warnings)),
        SummaryRow("WARNING", "warning_low を寄りでギャップ割れした警戒足",
                   _rate(sum(1 for e in entered
                             for w in e.warnings if w.gap_through_warning_low),
                         sum(1 for e in entered
                             for w in e.warnings if w.low_break_date is not None)),
                   "分母は warning_low を割った警戒足。"
                   "warning_low での約定は仮定できない（§6A）"),
        SummaryRow("WARNING", "警戒足が上限突破の翌営業日に出た件数",
                   _rate(sum(1 for e in entered if "WARNING_TOO_EARLY" in e.flags), len(broke)),
                   "早すぎる警戒足の目安"),
    ]

    rehigh_events = [e for e in entered if e.rehigh_count >= 1]
    raised1 = [e for e in entered if e.stop_raise_count >= 1]
    raised2 = [e for e in entered if e.stop_raise_count >= 2]
    raise_pcts = [
        (e.max_active_stop - e.initial_stop) / e.initial_stop * 100.0 for e in raised1
    ]
    # 正 = 状態機械の方が早く押し安値を確定できた日数
    fractal_lags = [
        float(w.fractal_confirm_day_offset - w.rehigh_day_offset)
        for e in entered for w in e.warnings
        if w.fractal_confirm_day_offset is not None and w.rehigh_day_offset is not None
    ]
    fractal_missed = sum(
        1 for e in entered for w in e.warnings
        if w.new_swing_low_date is not None and not w.fractal_is_same_low
    )
    rows += [
        SummaryRow("REHIGH / TRAILING", "REHIGH_CONFIRMED が発生した件数",
                   _rate(len(rehigh_events), n),
                   f"再高値更新の総数 {sum(e.rehigh_count for e in entered)} 回"),
        SummaryRow("REHIGH / TRAILING", "trail stop を1回以上引き上げられた件数",
                   _rate(len(raised1), n),
                   "前回（既存 fractal 利用）は 参考A 2/32 / 参考B 10/32"),
        SummaryRow("REHIGH / TRAILING", "trail stop を2回以上引き上げられた件数",
                   _rate(len(raised2), n)),
        SummaryRow("REHIGH / TRAILING", "初期STOPからの引き上げ幅（中央値）",
                   f"{_median(raise_pcts):+.2f}%" if raise_pcts else "－",
                   f"最大 {max(raise_pcts):+.2f}%" if raise_pcts else ""),
        SummaryRow("REHIGH / TRAILING", "押し安値の確定が既存 fractal より何日早いか（中央値）",
                   (f"{_median(fractal_lags):.0f} 日" if fractal_lags else "－"),
                   ("正なら状態機械の方が早い／負なら fractal の方が早い。"
                    f"最小 {min(fractal_lags):.0f} 日 / 最大 {max(fractal_lags):.0f} 日"
                    f"（同じ安値を fractal も認めた {len(fractal_lags)} 件が分母）"
                    if fractal_lags else "") + " 比較用で状態機械には使っていない"),
        SummaryRow("REHIGH / TRAILING", "既存 fractal がそもそも押し安値と認めなかった件数",
                   _rate(fractal_missed,
                         sum(1 for e in entered for w in e.warnings
                             if w.new_swing_low_date is not None)),
                   "状態機械が確定した押し安値のうち、fractal の pivot 条件を満たさないもの"),
        SummaryRow("REHIGH / TRAILING", "同日にSTOP到達と再高値更新で順序不明",
                   _rate(sum(1 for e in entered if e.ambiguous_stop_days), n),
                   "当日安値が active_stop 以下なら押し安値も同水準以下になるため、"
                   "どちらの順でも撤退水準は変わらない"),
        SummaryRow("REHIGH / TRAILING", "warning_low を割ったのに WARNING に留まった件数",
                   _rate(sum(1 for e in entered if "STUCK_IN_WARNING" in e.flags), n),
                   "解釈(b) の副作用。CASE3 の構造的な弱点"),
    ]

    for c in CASES:
        rs = [e.cases[c] for e in entered]
        rets = [r.approximate_return_pct for r in rs if r.approximate_return_pct is not None]
        gains = [r.max_gain_pct for r in rs if r.max_gain_pct is not None]
        gives = [r.giveback_pct for r in rs if r.giveback_pct is not None]
        holds = [float(r.holding_days) for r in rs if r.holding_days is not None]
        rows += [
            SummaryRow("利益保持", f"{CASE_LABELS_JA[c]}: 仮想EXIT件数",
                       _rate(sum(1 for r in rs if not r.still_open), len(rs)),
                       "残りは追跡終端で保有継続"),
            SummaryRow("利益保持", f"{CASE_LABELS_JA[c]}: 仮想リターン中央値",
                       f"{_median(rets):+.2f}%" if rets else "－",
                       "仮想ENTRY価格（翌日始値）基準。約定価格は保証されない"),
            SummaryRow("利益保持", f"{CASE_LABELS_JA[c]}: 最大含み益の中央値",
                       f"{_median(gains):+.2f}%" if gains else "－"),
            SummaryRow("利益保持", f"{CASE_LABELS_JA[c]}: 吐き出し幅の中央値",
                       f"{_median(gives):.2f}pt" if gives else "－",
                       "最大含み益 − 最終リターン"),
            SummaryRow("利益保持", f"{CASE_LABELS_JA[c]}: 保有日数の中央値",
                       f"{_median(holds):.0f} 日" if holds else "－"),
            SummaryRow("利益保持", f"{CASE_LABELS_JA[c]}: +5%以上まで上昇後に損失",
                       _rate(sum(1 for r in rs if r.rose5_then_lost), len(rs))),
            SummaryRow("利益保持", f"{CASE_LABELS_JA[c]}: +10%以上から利益の過半を失った",
                       _rate(sum(1 for r in rs if r.gave_back_most), len(rs)),
                       "「大半＝過半」を数式にしただけの集計定義。売買閾値ではない"),
        ]

    exit_types: dict[str, dict[str, int]] = {}
    for e in entered:
        for c in CASES:
            exit_types.setdefault(c, {})
            t = e.cases[c].exit_type
            exit_types[c][t] = exit_types[c].get(t, 0) + 1
    for c in CASES:
        for t, cnt in sorted(exit_types.get(c, {}).items(), key=lambda kv: -kv[1]):
            rows.append(
                SummaryRow("EXIT種別", f"{CASE_LABELS_JA[c]}: {t}", _rate(cnt, n))
            )

    path_counts: dict[str, int] = {}
    for e in events:
        path_counts[e.path_label] = path_counts.get(e.path_label, 0) + 1
    for key in ("P0_NO_BREAKOUT", "P1_BREAKOUT_NO_WARNING", "P2_WARNING_LOW_BREAK",
                "P3_REHIGH_ONCE", "P4_REHIGH_MULTI", "P5_WARNING_UNRESOLVED", "NO_ENTRY"):
        if key in path_counts:
            rows.append(
                SummaryRow("経路の分類", PATH_LABELS_JA[key],
                           _rate(path_counts[key], len(events)))
            )

    flag_counts: dict[str, int] = {}
    for e in events:
        for fl in e.flags:
            flag_counts[fl] = flag_counts.get(fl, 0) + 1
    for fl, c in sorted(flag_counts.items(), key=lambda kv: -kv[1]):
        rows.append(
            SummaryRow("フラグ", FLAG_LABELS_JA.get(fl, fl), _rate(c, len(events)))
        )

    rows.append(
        SummaryRow("解釈の感度", "上限突破日・再高値更新日そのものが陰線だった回数",
                   str(sum(e.same_day_bearish_at_trend_entry for e in entered)),
                   "解釈(a)で同日採用にしていたら、この回数だけ警戒足が増えていた")
    )
    return rows


def write_summary_csv(rows: list[SummaryRow], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(
            "# EXIT 状態機械の集計。CASE 比較は「どの CASE が儲かるか」ではなく"
            "「文章ルールをどこまで機械で再現できたか」を見るためのもの。"
            " 分母 32 件と小さいので率は参考程度に留める。\n"
        )
        w = csv.DictWriter(f, fieldnames=["section", "metric", "value", "note"])
        w.writeheader()
        for r in rows:
            w.writerow(asdict(r))
    return path
