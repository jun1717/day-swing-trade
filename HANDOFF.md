# HANDOFF.md — 新しいスレッド / エージェントへの引き継ぎ

**まずこれを読んでから作業を始めること。** 5 分で読める分量にしてある。

---

## 1. このツールは何か

日本株の **日足ベース短期スイング**（保有 数日〜2週間）のスクリーニングツール。

目的はただ 1 つ:

> **98 銘柄の監視リストを、人間が毎日全部見る必要をなくすこと。**

```text
監視銘柄 98
  ↓ 価格帯 2,000〜7,000円
  ↓ 上昇トレンド（close > MA25）
  ↓ 短期レンジ（3〜10営業日・総合品質で成立。下限反応2回は満点の目標値）
  ↓ 下限付近（レンジ内位置 ≦ 0.65）
  ↓ 反発確認（終値 > 前日高値）
ENTRY_CANDIDATE
  ↓
人間が日足チャートを確認して最終判断
```

**買い銘柄を自動決定するツールではない。ENTRY 候補が 0 件の日があってよい。**
候補数を増やすことは成功条件ではない。

---

## 2. 現在のフェーズ（重要）

**2026-08 に研究フェーズを終了した。今はフォワード運用フェーズ。**

| やること | やらないこと |
|---|---|
| 毎日 `swing daily` を実行してデータを貯める | 新しい閾値探索 |
| 実際の売買を `swing buy` / `swing sell` で記録する | 過去 32 件への追加最適化 |
| UI・記録・使い勝手の改善 | 新しい EXIT ロジックの研究 |
| バグ修正 | ENTRY 条件・`0.65`・初期STOP の変更 |

**ユーザーから明示的な指示が無い限り、過去データでの最適化を勝手に始めないこと。**

理由: ENTRY イベントが 32 件しかない。この母数で条件を詰めると過剰適合になる。
5 種類の EXIT 研究を行い、**どの方向へ動かしても別のどこかが悪化する**ことが
確認済み（`RESEARCH_SUMMARY.md` §8）。

---

## 3. 読む順番

| ファイル | 内容 |
|---|---|
| **`TRADING_RULES.md`** | **v1 の正式ルール。CONFIRMED / PROVISIONAL / RESEARCH_ONLY の区別** |
| `README.md` | 毎日の操作・CLI・画面・ファイル構成 |
| `RESEARCH_SUMMARY.md` | 5 種類の研究で何が分かり、なぜ採用しなかったか |
| `DESIGN.md` | 実装の設計（モジュール構成・データモデル） |
| `CODEX_HANDOFF.md` | 発端の要件定義（v0.1）。ルールの正本は TRADING_RULES.md |
| `docs/RESEARCH_DESIGN.md` | 研究コードの設計（look-ahead 対策など） |

---

## 4. 何が確定していて、何が暫定か

### CONFIRMED（変えるなら TRADING_RULES.md を改訂する）

```text
価格帯 2,000〜7,000円 / MA25 / close > MA25
短期レンジ 3〜10営業日・境界は zone・総合品質で成立を判定
下限反応 2回は「満点になる目標値」であって必須条件ではない（TRADING_RULES §3.3）
ENTRYトリガー: 終値 > 前日高値
初期STOP: range_lower × 0.995
STOPは最大損失を保証しない（ギャップ割れ 10/31件・中央値 -1.94%）
ポジションサイズは自動化しない
EXIT は自動化しない（人間判断）
出来高は補助評価のみ
新規エントリーとして上限ブレイクを探さない
```

### PROVISIONAL（実際の日足を見たフィードバックで調整してよい）

```text
near.max_position_in_range = 0.65   ← 最適値ではない。上側ENTRYを除外する安全ガード
その他 experimental.yaml のすべて
```

`0.65` の根拠: これを超えるとレンジ上側からの遅い ENTRY が混ざる
（0.70 で 13件、0.80 で 74件）。0.65 だと 32 件すべてが下限反発の形になる。
**成績で選んだ値ではない。**

### RESEARCH_ONLY（本番コードに入れてはいけない）

```text
警戒陰線の機械判定（VARIANT A/B/C）
warning_low 割れの扱い（LOW / CLOSE / STRUCTURAL）
reference_high の定義（RH-A 〜 RH-E）
押し安値の機械確定 / trail stop の自動引き上げ
```

これらは `src/swing_screener/research/` の中だけに存在する。
`tests/test_production_isolation.py` が混入を検査している。

---

## 5. EXIT を自動化しない理由（聞かれたら）

```text
WARNING を早くする           → 正常な調整で早く降りる（突破翌日に警戒足 64%）
WARNING を遅くする           → WARNING が出ないまま初期STOPへ戻る（27〜36%）
warning_low 割れを日中判定    → 寄りでギャップ割れ 61%。その価格で約定できない
reference_high を緩める      → trail は増えるが利益は残らない。早降りが増える
                              （trail EXIT 後に +3% 以上上昇したのが 86〜100%）
reference_high を厳しくする   → そもそも REHIGH しない（27%）
```

最大含み益 +10% 以上に到達した 16 件で「残せた割合」の中央値は、
**reference_high の 5 案すべてで 1.0〜1.4%**。つまり緩めても厳しくしても
ほぼ全部吐き出していた。

代わりに **`review.py` が「今日チャートを見るべき理由」だけを 3 段階で出す**
（SCENARIO_RISK / CAUTION / REVIEW）。これは売買指示ではない。

---

## 6. コードの構造

```text
src/swing_screener/
  ├─ 【本番】cli.py  screener.py  models.py  config.py  charting.py  explain.py
  │          universe.py  portfolio.py  journal.py  review.py
  │          indicators/  rules/  data/  web/
  └─ 【研究】research/          ← 本番から一切 import されない
```

- 本番 CLI: `swing ...`（`pyproject.toml: [project.scripts]`）
- 研究 CLI: `python -m swing_screener.research.cli ...`（別入口。日常運用では使わない）

**研究コードは削除しない。** 過去の検証を再現可能な状態で保存する。

---

## 7. データの保存先

| 何 | どこ | 性質 |
|---|---|---|
| 株価キャッシュ | `cache/prices/` | 唯一ネットワークに触れる先 |
| 日次スナップショット | `data/journal/daily/YYYY-MM-DD.csv` | **後から書き換えない** |
| ENTRY候補履歴 | `data/journal/signals.csv` | 買わなかったものも残す。追記のみ |
| トレード台帳 | `data/trades.csv` | 保有中も決済済みも同じ行 |
| フォワード検証用 | `data/journal/forward_review.csv` | `swing forward-export` で生成 |
| スクリーニング結果 | `output/screening_YYYY-MM-DD.json` | |
| トレードのチャート | `output/trades/` | ENTRY時点・EXIT時点の PNG |

**日次スナップショットと ENTRY候補履歴を後から再計算値で上書きしないこと。**
株価は配当調整で遡って変わり、レンジ検出も新しい足が付くたびに動く。
上書きすると「その日に見えていたもの」が失われ、フォワード検証の意味がなくなる。

---

## 8. 作業する前のチェック

- [ ] `TRADING_RULES.md` を読んだか
- [ ] その変更は CONFIRMED を変えていないか（変えるなら文書の改訂が要る）
- [ ] 研究コードのロジックを本番へ持ち込んでいないか
- [ ] `config.yaml` / `experimental.yaml` を勝手に変えていないか
- [ ] `.venv/bin/python -m pytest` が全部通るか

---

## 9. よくある依頼と、その前に確認すべきこと

| 依頼 | 確認 |
|---|---|
| 「候補が少ないので条件を緩めて」 | 0 件の日があってよい設計。緩めると上側 ENTRY が混ざる（§4） |
| 「利確ルールを作って」 | v1 の設計判断として自動化していない。`RESEARCH_SUMMARY.md` §8 を読んでもらう |
| 「もっと成績の良い閾値を探して」 | 母数 32 件。フォワードで貯めてからにする |
| 「バックテストして」 | これはイベントスタディであって収益バックテストではない |

いずれも**ユーザーが理由を理解したうえで指示するなら実施してよい。**
黙って始めないこと。
