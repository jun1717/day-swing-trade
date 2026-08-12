# RESEARCH_DESIGN.md — 過去データ検証（イベントスタディ）設計書

> **2026-08 に研究フェーズは終了した。** 結果の総括は `RESEARCH_SUMMARY.md`、
> v1 の売買ルールは `TRADING_RULES.md` にある。
>
> 本書と `src/swing_screener/research/` は**再現可能な状態で保存する**が、
> 新しい探索を始めるための入口ではない。母数は 32 イベントしかなく、
> これ以上詰めると過剰適合になる。再開の条件は `TRADING_RULES.md` §10。

本書は `max_position_in_range` の役割を理解するための**検証機能**の契約書である。

---

## 0. この検証の目的と非目的

### 目的
現在の売買ルールを機械化した結果、`max_position_in_range` の違いによって
**何を拾い、何を捨てるのか**を理解する。

### 非目的（やってはいけないこと）
- **パラメータ最適化ではない。** 過去データで最も成績の良い閾値を探さない
- **収益バックテストではない。** イベントスタディ（シグナル後の値動きの観察）である
- **ENTRY件数を増やすことを成功条件にしない**
- **平均リターンだけで「0.60が最適」のような結論を出さない**
- レンジ上限付近／上限ブレイクを新規ENTRYとして拾う戦略には変更しない

### 絶対的な制約
- `config.yaml` / `experimental.yaml` を**変更しない**
- `src/swing_screener/` の**本番ロジックを変更しない**（`rules/`, `screener.py`, `indicators/` 等）
- `output/screening_*.json`（本番スクリーニング結果）を**上書きしない**
- 検証コードは `src/swing_screener/research/` に**完全に分離**する
- 検証結果を理由に設定値を自動変更しない。**提示したら止まる**

---

## 1. ディレクトリと出力先

```
src/swing_screener/research/     # 検証コード（本番から分離）
├── __init__.py
├── replay.py      # 過去日次リプレイ（look-ahead bias 対策の中核）
├── events.py      # ENTRY イベント記録
├── forward.py     # シグナル後の値動き
├── classify.py    # 形状分類 A〜D / 転帰分類
├── sweep.py       # 閾値スイープ
├── charts.py      # 注釈付きチャート
├── report.py      # HTML / CSV 出力
├── config.py      # 検証用パラメータ（本番 experimental.yaml とは別）
└── cli.py         # python -m swing_screener.research.cli

research/                        # 検証成果物（本番 output/ とは別）
├── events.csv                   # 全 ENTRY イベント（閾値非依存の生データ）
├── events_pos<閾値>.csv         # 閾値別 ENTRY イベント
├── summary.csv                  # 閾値別の集計
├── report.html                  # 比較レポート
└── charts/<code>_<date>.png     # 代表例チャート
```

---

## 2. Look-ahead bias の防止（最重要）

### 方針
各営業日 D について、`bars[0..i]`（D を含む、D より後を一切含まない）だけを
`screen_one()` に渡す。`screen_one` は「系列の最終足＝当日」として動作するため、
**スライスするだけで構造的に未来を遮断できる。**

```python
for i in range(warmup, len(bars)):
    sliced = PriceSeries(code=code, bars=bars[: i + 1])
    result = screen_one(stock, sliced, cfg, exp)   # D = bars[i].date 時点の判定
```

### 禁止事項
- MA25・swing・レンジ判定・ENTRY判定に `bars[i+1:]` を使わない
- 「シグナル日の翌日始値」は**判定には使わず、記録のみ**に使う
- forward return の計算だけが `bars[i+1:]` を参照してよい（観察であって判定ではない）

### 必須テスト（`tests/test_research_replay.py`）
1. **未来足を追加しても過去の判定が変わらない**
   同じ系列の末尾に大きな上昇/下落を付け足しても、日付 D の ScreenResult
   （status, range_lower, range_upper, position, ma25 など）が完全に一致すること
2. **リプレイ結果 == truncated 系列への直接 screen_one**
   `replay()` が返す日付 D の結果が、`screen_one(stock, PriceSeries(bars[:i+1]))` と一致すること
3. **warmup 未満の日は判定しない**（データ不足を ENTRY にしない）
4. forward 計算が `bars[i+1:]` のみを参照し、シグナル日の足を二重計上しないこと

---

## 3. 閾値スイープの等価性（計算量削減の根拠）

`near.max_position_in_range` は `rules/status.py` の**最終分類段階にしか影響しない**。
トレンド判定・レンジ検出・出来高・反発確認の計算結果は閾値に依存しない。

したがってリプレイは **`max_position_in_range: null`（制限なし）で1回だけ**実行し、
各閾値の status は事後導出する:

```python
def derive_status(base_status, position, threshold):
    if threshold is None:
        return base_status
    if base_status in ("ENTRY_CANDIDATE", "NEAR") and position > threshold:
        return "RANGE"
    return base_status
```

これにより全閾値が**完全に同一のレンジ検出結果**を共有することが保証される。

### 必須テスト（`tests/test_research_sweep.py`）
上記の導出が、実際に `max_position_in_range` を設定して `screen_one` を
呼んだ結果と**全サンプルで一致する**こと。一致しない場合は導出を使わず
実際に再計算する実装へフォールバックすること（等価性を仮定しない）。

---

## 4. 検証期間と対象

- 既定は直近 **6ヶ月**。`--months` で変更可（12ヶ月まで拡張できる設計）
- warmup は `max(ma.period, swing.lookback_bars, range.max_days) + 余裕`。
  現行設定では 60 本程度。実際に使った warmup を出力に記録する
- 現キャッシュは 2025-08-12〜2026-08-10 の 243 本 ≈ 8ヶ月が検証可能
- **12ヶ月へ拡張する場合**: `research.cli fetch-history --years 2` を用意する
  （本番 `config.yaml` の `fetch_period` は変更しない。research 側で period を上書きして
  同じ `cache/prices/` を更新する）
- 対象は `enabled=true` の全監視銘柄（現在98銘柄）。ETF も含める（asset_type で識別可能に）
- forward 10営業日を確保できないシグナルは `forward_complete=false` として
  **件数には含めるが forward 統計からは除外**する

---

## 5. 比較する閾値

```
0.50 / 0.60 / 0.65（現行） / 0.70 / 0.80 / null（制限なし）
```

他のパラメータは現行 `experimental.yaml` の値に固定する。
検証実行時は `experimental.yaml` を読み込み、`near.max_position_in_range` のみ
メモリ上で差し替える（**ファイルは書き換えない**）。

---

## 6. 【契約】ENTRY イベントのスキーマ

`research/events.csv` の列。閾値非依存の生データとして全 ENTRY を1度だけ記録し、
閾値別ファイルはこれをフィルタして生成する。

### シグナル情報
| 列 | 内容 |
|---|---|
| `date` | シグナル日（判定に使った最終足の日付） |
| `code` / `name` / `sector` / `asset_type` | 銘柄 |
| `themes` | テーマ（複数は `;` 区切り） |
| `watch_priority` / `is_leader` | 監視属性（売買条件ではない） |
| `entry_reason` | ENTRY 判定理由（`explain.py` の status 判定文） |
| `range_start_date` / `range_end_date` / `range_days` | レンジ期間 |
| `range_lower` / `range_upper` / `range_width_pct` | レンジ |
| `signal_close` | シグナル日終値 |
| `position_in_range` | `(close - lower) / (upper - lower)` |
| `last_lower_touch_date` | 直近のレンジ下限接触日 |
| `days_from_touch_to_signal` | 下限接触からシグナルまでの営業日数 |
| `lower_touch_count` | 下限反応回数 |
| `ma25` / `ma_direction` / `ma_slope_pct` / `ma_deviation_pct` | MA25 |
| `higher_highs` / `higher_lows` | 高値・安値切り上げ判定（`true/false/unknown`） |
| `volume_state` / `volume_range_vs_pre_ratio` | 出来高評価 |
| `prev_high` | 前日高値 |
| `breakout_pct_vs_prev_high` | `(signal_close - prev_high) / prev_high * 100` |
| `initial_stop` | `range_lower * 0.995` |
| `stop_distance_pct_from_close` | `(signal_close - initial_stop) / signal_close * 100` |

### シグナル日終値を基準とした forward（**約定可能価格ではない**）
`base_close` を基準価格として明示する。列名に `_from_close` を付けて基準を明示。

| 列 | 内容 |
|---|---|
| `fwd5_max_gain_pct_from_close` / `fwd10_max_gain_pct_from_close` | 期間中の高値ベース最大上昇率 |
| `fwd5_max_loss_pct_from_close` / `fwd10_max_loss_pct_from_close` | 期間中の安値ベース最大下落率（負値） |
| `fwd5_reached_range_upper` / `fwd10_reached_range_upper` | 高値が `range_upper` 以上に到達したか |
| `fwd5_broke_range_upper` / `fwd10_broke_range_upper` | 終値が `range_upper` を超えたか |
| `fwd5_hit_stop` / `fwd10_hit_stop` | 安値が `initial_stop` 以下に到達したか |
| `days_to_stop` | 損切り到達までの営業日数（未到達は空） |
| `fwd5_days_to_max_gain` / `fwd10_days_to_max_gain` | 最大上昇までの営業日数 |
| `forward_complete` / `forward_bars_available` | 10日分揃っているか |

### 翌営業日始値を基準とした forward（実運用との乖離を見る参考データ）
**「翌日始値で必ず買う」という新ルールにはしない。** あくまで参考。

| 列 | 内容 |
|---|---|
| `next_open` | シグナル翌営業日の始値（無ければ空） |
| `gap_pct` | `(next_open - signal_close) / signal_close * 100` |
| `fwd5_max_gain_pct_from_next_open` / `fwd10_max_gain_pct_from_next_open` | |
| `fwd5_max_loss_pct_from_next_open` / `fwd10_max_loss_pct_from_next_open` | |
| `stop_distance_pct_from_next_open` | `(next_open - initial_stop) / next_open * 100` |
| `fwd5_hit_stop_from_next_open` / `fwd10_hit_stop_from_next_open` | 翌日始値以降で損切り到達したか |

**注意**: 終値基準の forward は「シグナル日終値で買えた」という**実際には不可能な仮定**を
含む。CSV・HTML の両方にこの注記を必ず表示すること。

---

## 7. 分類

形状（シグナル時点）と転帰（シグナル後）を**別軸**として記録する。
1つのバケットに押し込めない（同じ形状でも転帰は分かれるため）。

### 形状分類 `shape`（ユーザー指定の A〜D）
| ラベル | 条件（研究用パラメータ、`research/config.py` に定義） |
|---|---|
| `A_ideal` | `position < 0.65` かつ `days_from_touch_to_signal <= 3` — 理想的な下限反発 |
| `A_slow_touch` | `position < 0.65` かつ `days_from_touch_to_signal > 3` — 位置は良いが接触が古い |
| `B_late` | `0.65 <= position < 0.80` — 反発は明確だが既にレンジ上側 |
| `C_near_upper` | `0.80 <= position < 0.95` — 実質レンジ上限付近を買う形 |
| `D_upper_zone` | `position >= 0.95` または 終値が `upper_zone_low` 以上 — 実質的な上限ブレイク買い |

`range_upper = max(high)` は当日高値を含むため `position > 1.0` は原理上発生しない。
したがって「上限ブレイク」は zone 到達で判定する。

### 転帰分類 `outcome`（forward 10日で評価、E ダマシに対応）
| ラベル | 条件 |
|---|---|
| `stopped_out` | 10日以内に `initial_stop` へ到達（**E: ダマシ**） |
| `range_breakdown` | 損切り未到達だが10日以内の終値が `range_lower` を下回った（**E: レンジ崩壊**） |
| `reached_upper` | 損切り未到達で `range_upper` に到達 |
| `neutral` | 上記いずれでもない |
| `incomplete` | forward データ不足 |

---

## 8. 集計（`research/summary.csv`）

閾値ごとに以下を出す。**件数だけで優劣を判断しない。**

- ENTRY 件数 / forward 完全な件数
- 形状分類の内訳（A/B/C/D の件数と比率）
- 転帰分類の内訳（stopped_out 率＝損切り到達率、range_breakdown 率など）
- `position_in_range` の分布（min / 25% / 中央値 / 75% / max）
- `days_from_touch_to_signal` の分布
- forward 5/10日の最大上昇率・最大下落率の分布（**平均だけでなく中央値と四分位数**）
- 終値基準と翌日始値基準の両方
- **各閾値で「増える分」＝1つ緩い閾値との差分イベント**を明示（何が追加されるかが本題）

---

## 9. 可視化（`research/report.html`）

- 閾値別の比較表（上記集計）
- 分布は**ヒストグラム**で比較（平均値の点比較にしない）
  - 5日/10日 最大上昇率、5日/10日 最大下落率
  - ENTRY 時のレンジ内位置
  - 下限接触から ENTRY までの日数
- 形状分類 × 転帰分類のクロス集計（どの形状がどう転ぶか）
- **閾値を緩めたときに追加されるイベントの一覧**（0.65→0.70→0.80→null の差分）
- 代表チャートへのリンク
- 冒頭に**この検証の非目的**（最適化ではない旨）と**終値基準の非約定性**を明記
- 外部CDN不使用・自己完結HTML

## 10. 代表チャート（`research/charts/`）

各カテゴリから数件ずつ。チャートには以下を必ず表示:
- レンジ上限 / レンジ下限 / **ENTRY判定日（縦線などで明示）** / 前日高値 / 初期損切りライン / MA25
- シグナル日以降の値動きも見えるよう、シグナル日の**前後**を表示する
  （検証用チャートなので未来を表示してよい。判定には使っていない）

カテゴリ:
1. うまく機能した例（`reached_upper`）
2. 損切りになった例（`stopped_out`）
3. ENTRYが遅かった例（`B_late`）
4. `max_position_in_range` によって除外された例（0.65 で落ち、0.80 で拾われる）
5. 制限なしにすると拾われる上限付近の例（`C_near_upper` / `D_upper_zone`）

---

## 11. CLI

```bash
python -m swing_screener.research.cli run [--months 6] [--out research/]
python -m swing_screener.research.cli fetch-history --years 2   # 12ヶ月検証用
```

本番 `swing` CLI には**サブコマンドを追加しない**（分離を維持するため）。
