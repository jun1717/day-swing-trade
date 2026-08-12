"""下限反応回数（`range.min_lower_touches = 2`）の v1 仕様を固定する回帰テスト。

## なぜこのファイルがあるか

2026-08 の運用開始直後、実際の `candidates.csv` に

    7203 トヨタ  status=ENTRY_CANDIDATE  range_days=3
                 lower_reaction_count=1  range_quality=0.688

が出た。`TRADING_RULES.md` が「下限反応 2回以上」と読める書き方をしていたため、
**これはルール違反ではないか**（= 実装のバグではないか）という疑いが立った。

監査の結論は「実装が正しく、文書の表現が誤解を招いていた」である。根拠:

* `CODEX_HANDOFF.md` §15「下限反応回数」は
  「レンジ下限付近で最低2回程度反応 **したものを高評価する**」
  「**目標**: `lower_touch_count >= 2`」「ただし zone 判定の誤差を考慮する」
  と書いている。除外条件ではなく評価軸として定義されている。
* `CODEX_HANDOFF.md` §12 は短期レンジの **除外条件を明示的に列挙**しているが、
  そこに下限反応回数は入っていない（安値の連続切り下がり / 大陰線＋出来高急増 /
  値幅の拡大 など）。
* `DESIGN.md` の range_detect 節も、除外条件の一覧と品質スコアの一覧を分けて書き、
  `lower_touch_count >= range.min_lower_touches` を **品質スコア側**に置いている。
* `experimental.yaml` の `range_quality.weights.lower_touches: 0.30` は
  「下限反応が多いほど高評価（最重要）」という **重み**である。
* `TRADING_RULES.md` §3.2 が 0.65 の根拠として挙げる過去検証（264件→32件）は、
  この実装（1回でも成立する）で得られた数字である。実際に
  `research/events_pos065.csv` の 32 件のうち **23 件が lower_touch_count=1**。
  hard filter にすると、正式ルールの根拠にしている表そのものが別の数字になる。

したがって v1 の仕様は次のとおり:

    2回は「満点になる目標値」であって、成立の必須条件ではない。
    1回でも、値幅・収縮・出来高・日数を含む総合品質が
    `range_quality.min_quality` を超えればレンジは成立し、
    下限付近＋反発確認が揃えば ENTRY_CANDIDATE になり得る。

**このテストを「2回未満は不成立」に書き換えるのは、v1 のルール変更である。**
その場合は `TRADING_RULES.md` を先に改訂し、0.65 の根拠データを取り直すこと。
"""

from __future__ import annotations

import pytest
from tests.conftest import make_stock, uptrend_with_range

from swing_screener.models import STATUS_ENTRY
from swing_screener.rules.range_detect import evaluate_window, range_judgements
from swing_screener.screener import screen_one

# 7203 トヨタ (2026-08-12) と同じ形: 3営業日レンジ・下限反応1回・品質は合格。
TOYOTA_SHAPE = dict(range_days=3, touch_days=(0, 1))


def toyota_like():
    return uptrend_with_range(**TOYOTA_SHAPE).build()


# --- レンジ成立 ---------------------------------------------------------------


def test_下限反応1回でも品質を満たせばレンジは成立する(cfg, exp):
    win = evaluate_window(toyota_like().bars, 3, cfg, exp)

    assert win.lower_touch_count == 1
    assert win.quality >= float(exp.range_quality.min_quality)
    assert win.accepted is True
    # 「下限反応が少ない」は不採用理由に **ならない**
    assert win.reject_reasons == ()


def test_下限反応は除外条件ではなく品質スコアの一項目(cfg, exp):
    win = evaluate_window(toyota_like().bars, 3, cfg, exp)

    touches = next(j for j in win.quality_breakdown if j.key == "range.quality.lower_touches")
    assert "1回 / 目標2回" in touches.detail
    assert "0.50" in touches.detail  # 1 / 2 = 0.50点
    # 満点ではないのに採用されている = スコア要素として効いている
    assert win.accepted is True


def test_下限反応の判定は表示専用で必須条件ではない(cfg, exp):
    """`required=False` であること。ここが True になると意味が変わる。"""
    win = evaluate_window(toyota_like().bars, 3, cfg, exp)
    judgement = next(
        j for j in range_judgements(win, [win], cfg, exp) if j.key == "range.lower_touches"
    )

    assert judgement.ok is False, "2回に届いていないことは表示される"
    assert judgement.required is False, "が、成立の必須条件ではない"
    assert "目標 2回" in judgement.detail


def test_下限反応が多いほど品質は上がる(cfg, exp):
    """目標値であることの裏返し。2回のほうが高く評価される。"""
    one = evaluate_window(uptrend_with_range(range_days=6, touch_days=(2, 3)).build().bars, 6, cfg, exp)
    two = evaluate_window(uptrend_with_range(range_days=6, touch_days=(1, 4)).build().bars, 6, cfg, exp)

    assert (one.lower_touch_count, two.lower_touch_count) == (1, 2)
    assert two.quality > one.quality


# --- ENTRY まで進めること -------------------------------------------------------


def test_下限反応1回でもENTRY_CANDIDATEになり得る(cfg, exp):
    """7203 トヨタで実際に起きたケース。v1 ではこれが正しい挙動。"""
    result = screen_one(make_stock(code="7203", name="トヨタ"), toyota_like(), cfg, exp)

    assert result.status == STATUS_ENTRY
    assert result.range_ is not None
    assert result.range_.days == 3
    assert result.range_.lower_touch_count == 1
    assert result.range_.quality >= float(exp.range_quality.min_quality)
    # ENTRY の必須条件（TRADING_RULES.md §3.1）はすべて満たしている
    assert result.trend.is_uptrend is True
    assert result.rebound.confirmed is True

    position = (result.latest_close - result.range_.lower) / (
        result.range_.upper - result.range_.lower
    )
    assert position <= float(exp.near.max_position_in_range)


def test_下限反応回数は必須判定の一覧に入っていない(cfg, exp):
    """`required=True` の判定に下限反応が混ざっていないこと。"""
    result = screen_one(make_stock(), toyota_like(), cfg, exp)

    required_keys = {j.key for j in result.judgements if j.required}
    assert "range.lower_touches" not in required_keys
    assert "range.found" in required_keys  # レンジ成立そのものは必須


# --- 設定値の意味 ---------------------------------------------------------------


def test_min_lower_touchesは満点の基準として使われる(cfg, exp):
    """値そのものは 2 のまま（CODEX_HANDOFF §28 の確定値）。使われ方が目標値。"""
    assert int(cfg.range.min_lower_touches) == 2

    win = evaluate_window(toyota_like().bars, 3, cfg, exp)
    detail = next(
        j.detail for j in win.quality_breakdown if j.key == "range.quality.lower_touches"
    )
    assert f"目標{int(cfg.range.min_lower_touches)}回" in detail


@pytest.mark.parametrize("touch_days, expected", [((0, 1), 1), ((1, 4), 2)])
def test_連続日は1回に集約されるという前提は変えない(cfg, exp, touch_days, expected):
    """1回になりやすいのは集約仕様のため。ここを緩めて回数を水増ししない。"""
    bars = uptrend_with_range(range_days=6, touch_days=touch_days).build().bars
    assert evaluate_window(bars, 6, cfg, exp).lower_touch_count == expected
