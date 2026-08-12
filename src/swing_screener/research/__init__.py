"""過去データでのイベントスタディ検証（docs/RESEARCH_DESIGN.md）。

本パッケージは **検証専用** であり、本番スクリーニングのロジックからは完全に
分離されている。ここから `src/swing_screener/` 配下の本番モジュールは
**読むだけ**（import して呼ぶだけ）で、書き換えない。

この検証の目的:
    `near.max_position_in_range` の違いによって「何を拾い、何を捨てるのか」を
    理解する。

この検証の**非目的**（RESEARCH_DESIGN §0）:
    - パラメータ最適化ではない。最も成績の良い閾値を探さない
    - 収益バックテストではない。イベントスタディ（シグナル後の値動きの観察）である
    - ENTRY 件数を増やすことを成功条件にしない

構成:
    config.py   検証用パラメータ（本番 experimental.yaml とは独立）
    replay.py   過去日次リプレイ（look-ahead bias 対策の中核）
    events.py   ENTRY イベントの記録スキーマ
    forward.py  シグナル後の値動き（観察であって判定ではない）
    classify.py 形状分類 A〜D / 転帰分類
    sweep.py    閾値スイープと集計
    charts.py   注釈付きチャート（別担当）
    report.py   HTML 出力（別担当）
    cli.py      python -m swing_screener.research.cli
"""

from __future__ import annotations

__all__ = [
    "classify",
    "config",
    "events",
    "forward",
    "replay",
    "sweep",
]
