# 日足短期スイング スクリーナー v1

日本株の「日足ベース短期スイング」用スクリーニングツール。

**このツールは売買を自動判定しない。** 事前に選んだ監視銘柄から

```
監視銘柄 98 → 価格帯 → 上昇トレンド → 短期レンジ → レンジ下限付近 → 反発確認
```

の順に候補を絞り、**「今日、人間が日足チャートを確認すべき銘柄」を減らす**のが目的。
ENTRY も EXIT も、最終判断は日足チャートを見て人間が行う。

| 文書 | 内容 |
|---|---|
| **[TRADING_RULES.md](TRADING_RULES.md)** | **v1 の売買ルール（正本）。確定値 / 暫定値 / 研究のみの区別** |
| [RESEARCH_SUMMARY.md](RESEARCH_SUMMARY.md) | 過去データ検証で何が分かり、なぜ大半を採用しなかったか |
| [HANDOFF.md](HANDOFF.md) | 新しいスレッド・エージェント向けの引き継ぎ |
| [DESIGN.md](DESIGN.md) | 実装の設計 |
| [CODEX_HANDOFF.md](CODEX_HANDOFF.md) | 発端の要件定義（v0.1・履歴） |

---

# 毎日の基本操作

引け後に、上から順に 6 ステップ。

```bash
# 1. 株価を更新する（ネットワークに触れるのはここだけ。98銘柄で約1分）
.venv/bin/python -m swing_screener.cli fetch

# 2. スクリーニングして記録する（オフライン。数秒）
.venv/bin/python -m swing_screener.cli daily

# 3. 画面を開く
.venv/bin/python -m swing_screener.cli serve      # → http://127.0.0.1:8000
```

```
4. ENTRY_CANDIDATE / NEAR を確認する        画面上部の「NEAR + ENTRY_CANDIDATE のみ」
5. 保有銘柄を確認する                        画面上部の「保有銘柄」タブ
6. 売買したら記録する                        swing buy / swing sell
```

`daily` はスクリーニング・日次スナップショットの保存・ENTRY候補履歴への追記・
保有銘柄のレビューを一度に行い、結果をターミナルにも出す。

```
■ データ基準日: 2026-08-10
ENTRY_CANDIDATE: 0 / NEAR: 3 / RANGE: 10 / OUT: 85
日次スナップショット: data/journal/daily/2026-08-10.csv  98件を保存しました。
ENTRY候補の履歴追加: なし

■ 今日の候補
--- ENTRY_CANDIDATE (0件) ---
  なし
--- NEAR (3件) ---
  9513 J-POWER  3,828円  下限まで+0.8%  初期STOP 3,778円
  9503 関西電力  2,361円  下限まで+1.1%  初期STOP 2,324円
  7203 トヨタ  2,981円  下限まで+2.2%  初期STOP 2,903円

■ 保有銘柄
  [SCENARIO_RISK] 7203 トヨタ  2,981円 (-2.3%)  初期STOP 3,000円 (あと-0.6%)
      ・初期STOP以下: 終値 2,981円 ≦ 初期STOP 3,000円。…
```

**ENTRY_CANDIDATE が 0 件の日は珍しくない。** 候補数を増やすことは成功条件ではない。

### 売買したときの記録

```bash
# 買ったとき（レンジ・初期STOPは ENTRY候補履歴から自動で補完される）
.venv/bin/python -m swing_screener.cli buy 9513 --price 3800 --qty 100 --reason "レンジ下限反発"

# 売ったとき（ENTRY時点と EXIT時点のチャートPNGも保存される）
.venv/bin/python -m swing_screener.cli sell 9513 --price 3950 --reason profit_protection
```

---

# 自動運用

日々の主運用は **GitHub Actions → CSV → ChatGPT 分析** に移した。
手元で `fetch` / `daily` を回さなくても、平日の引け後に候補データが揃う。
Web UI は残してあるので、チャートを見たいときはこれまでどおり `serve` を使う。

### GitHub Actions daily workflow

`.github/workflows/daily-screening.yml` が **平日 16:10 JST**（cron は 07:10 UTC）に

```
swing fetch  →  swing daily  →  swing chatgpt-export
```

を実行し、その日の候補を ChatGPT へ渡せる CSV にして artifact へ置く。
Actions の画面から `Run workflow`（`workflow_dispatch`）で手動実行もできる。

* **日本の祝日は cron に書いていない。** 平日は毎日起動し、`fetch` 成功後に
  `market-check` で「前回書き出した日より新しい株価が来ているか」を見る。
  来ていなければ `No new market data` として **何も作らずに正常終了**する
  （前回の bundle も上書きしない）。`fetch` 自体が失敗した場合は休日扱いにせず
  workflow を失敗させる。
* 定期実行と手動実行が重なっても同じ日次データを同時に更新しないよう
  `concurrency` で直列化している。
* 権限は `contents: write` のみ（生成データ用ブランチへの push に必要）。
  新しい Secret は要らない。
* **`data/trades.csv` は自動処理では絶対に触らない**（実際に売買した記録は手入力）。
  変更されていないことを workflow 内で検査している。

失敗させる条件: 依存インストール / `fetch` / `daily` / CSV 書き出し / 検査 /
永続化のいずれかが失敗したとき。**中途半端な CSV を「その日の最新データ」として残さない。**

### ChatGPT への渡し方

1. GitHub の **Actions → daily-screening** を開き、最新の成功 run を選ぶ。
2. run 画面下部の Artifacts から `chatgpt-market-data-YYYY-MM-DD` をダウンロードする。
3. 展開すると `candidates.csv` / `daily_bars.csv` / `manifest.txt` の 3 ファイルが入っている。
4. その 3 ファイルをそのまま ChatGPT へアップロードする。
5. こう依頼する:

   > 今日の候補です。現在の短期スイングルールで、買う / まだ待つ / 見送るを判断してください。

チャート画像は入れない（入力はテキストと CSV のみ）。`daily_bars.csv` に
候補全銘柄の直近 70 営業日の日足が入っているので、MA25・高値/安値の切り上げ・
3〜10 営業日のレンジ・下限反応・ローソク足と下ヒゲ・出来高推移は
チャートなしで再確認できる。

**最終判断は人間が行う。** このワークフローは CSV を作るところまでで、
ChatGPT を呼び出しもしないし、注文も出さない。

### 手元で同じ CSV を作る

Actions と同じものをローカルでも作れる（ロジックは workflow ではなく CLI にある）。

```bash
.venv/bin/python -m swing_screener.cli fetch
.venv/bin/python -m swing_screener.cli daily
.venv/bin/python -m swing_screener.cli chatgpt-export          # → output/chatgpt/YYYY-MM-DD/
.venv/bin/python -m swing_screener.cli chatgpt-export --date 2026-08-10   # 過去日を指定する場合
```

出力される 3 ファイル:

| ファイル | 中身 |
|---|---|
| `candidates.csv` | その日の候補 1 銘柄 = 1 行。ENTRY_CANDIDATE / NEAR / RANGE のみ（**OUT は入れない**）。ENTRY・NEAR が `PRIMARY`、RANGE が `SECONDARY` |
| `daily_bars.csv` | 候補全銘柄 × 直近 70 営業日の生の日足（date, code, OHLCV, ma25, days_ago） |
| `manifest.txt` | 生成日時・データ基準日・commit sha・件数・config のハッシュなど出所の記録 |

**この書き出しは売買判定をしない。** 本番スクリーニング結果をそのまま CSV に
写すだけで、レンジ再判定も ENTRY 再判定もレンジ内位置ガードの再評価もしない
（`tests/test_chatgpt_export.py` が本番結果との一致を固定している）。
並び順も本番の並び（ENTRY_CANDIDATE → NEAR → RANGE、同 status 内は下限までの
距離順）をそのまま使い、新しい「おすすめ度」は作らない。

候補が 0 件の日も異常ではない。ヘッダーだけの CSV と `candidate_count=0` の
manifest を正しく作る。**候補を出すために条件を緩めることはしない。**

---

# セットアップ

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"
```

以降のコマンドは `.venv/bin/python -m swing_screener.cli <command>`
（`source .venv/bin/activate` 後は `swing <command>` でも可）。

初回は監視銘柄の正規化から:

```bash
.venv/bin/python -m swing_screener.cli normalize   # data/watchlist.csv → stocks.csv / stock_themes.csv
.venv/bin/python -m swing_screener.cli fetch
```

---

# CLI

## 毎日使うもの

| コマンド | 用途 |
|---|---|
| `fetch` | 株価取得 → `cache/prices/`。当日取得済みはスキップ（`--force` で強制） |
| `daily` | スクリーニング + 記録 + 保有レビュー。**通常はこれ 1 つ** |
| `chatgpt-export` | ChatGPT 分析用 CSV を `output/chatgpt/YYYY-MM-DD/` へ書き出す（`--date` `--lookback-days` `--skip-existing`） |
| `serve` | Web UI 起動（`--port 8000`） |
| `holdings` | 保有銘柄の当日レビュー（`--closed` で決済済み一覧） |
| `buy CODE --price P` | 購入を記録。`--qty` `--date` `--stop` `--lower` `--upper` `--reason` `--memo` |
| `sell CODE --price P` | 売却を記録。`--reason` `--memo` `--date`。チャートPNGも保存 |

## ときどき使うもの

| コマンド | 用途 |
|---|---|
| `normalize` | `data/watchlist.csv` → `stocks.csv` / `stock_themes.csv` を再生成 |
| `screen` | スクリーニングのみ（**記録しない**）。パラメータ比較用 |
| `chart CODE` | 候補銘柄の日足チャートPNG（`--days 120`） |
| `trade-chart CODE` | 保有銘柄のチャートPNG（`--as-of YYYY-MM-DD` で当時の形を再現） |
| `forward-export` | フォワード検証用の素データを 1 枚の CSV に書き出す |
| `chatgpt-validate` | 書き出し済みの ChatGPT 用 CSV を検査する（本番判定との一致も見る） |
| `market-check` | 新しい営業日の株価が来ているか（`--format github` で `key=value` 出力） |

全コマンドで `--config` / `--experimental` を指定でき、設定違いの結果を比較できる。

```bash
# 例: パラメータを変えた場合の候補数を比較する（記録に影響しない screen を使う）
.venv/bin/python -m swing_screener.cli screen --experimental experimental_loose.yaml
```

### 監視銘柄を変えるとき

`data/watchlist.csv`（列: `code,name,sector,theme,is_leader,watch_priority`）を編集して
`normalize` → `fetch`。同じ銘柄を複数テーマに書いてよい（三菱重工＝重工・防衛＋宇宙・衛星など）。
内部で銘柄とテーマに分離される。

一時的に監視から外したい銘柄は `data/stocks.csv` の `enabled` を `false` にする。
この値は `normalize` を再実行しても保持される。

---

# 画面

上部のタブで 3 画面を行き来する。

```
候補一覧  |  保有銘柄  |  ENTRY候補履歴
```

## 候補一覧 `/`

- status 別の件数バッジ・データ基準日・最終株価取得日時
- **「NEAR + ENTRY_CANDIDATE のみ」ボタン**（日々の主用途はこれ）
- 絞り込み: status / テーマ / 業種 / watch_priority / is_leader / 個別株・ETF
- 列は CODEX_HANDOFF §24 の全項目に加え、**並び順の根拠**（トレンド強度・レンジ品質）も表示。
  総合スコアで順位を隠さず、なぜその順位かが見えるようにしている
- **OUT 銘柄は既定で非表示**。展開すると落選理由が付く（「精度改善の回し方」で使う）
- 「再スクリーニング」ボタンはキャッシュ済み株価から再計算するだけでネットワークに触れない。
  `experimental.yaml` を書き換えてこれを押すと即座に結果が変わる

## 候補の詳細 `/stock/{code}`

- 日足チャート（60 / 120 / 250日切替）: ローソク足、MA25、レンジ上限zone・下限zone（帯）、
  検出レンジ枠、下限反応マーカー、前日高値、損切りライン、出来高
- 判定理由パネル: **なぜその判定になったかを全項目分**表示。コピーボタンで ChatGPT に貼れる

```
上昇トレンド：OK
25日線：上向き — MA25 3,753円 > 5日前 3,715円 (+1.0%)
株価 > MA25：OK — 3,828円 > MA25 3,753円 (+2.0%)

レンジ：5営業日 (08/04〜08/10)
下限：3,797円 (zone 3,770円〜3,824円)
上限：3,945円 (zone 3,917円〜3,973円)
下限反応：2回 (08/05, 08/10)

下限まで：+0.8%
反発確認：未成立 (終値 3,828円 <= 前日高値 3,926円 (-2.5%))

状態：NEAR
損切り候補：3,778円（レンジ下限の0.5%下）

レンジ候補の検討：3日=不採用(値幅が拡大中) 4日=可(0.71) 5日=採用(0.83) ...
```

## 保有銘柄 `/holdings`

**この画面は「売れ」と言わない。** 出すのは *今日チャートを見るべき理由* だけ。

| 段階 | 意味 |
|---|---|
| `SCENARIO_RISK` | 買った理由が残っているか確認する（初期STOP以下 / 元レンジ下限割れ / MA25割れ） |
| `CAUTION` | 形に注意が要る（大陰線 / 陰線＋出来高急増 / 直近の局所安値割れ） |
| `REVIEW` | 一度チャートを見る（直近陰線の安値割れ / 元レンジ上限に到達・突破済み） |
| `OK` | 目立った変化なし |

一覧では損益率・初期STOPまでの距離・元レンジ上限の到達/突破・保有後最高値・
MA25・直近陰線安値・直近の局所安値を横に並べる。

## 保有銘柄の詳細 `/holdings/{code}`

- 日足チャート: **ENTRY価格・ENTRY日・初期STOP・買ったときのレンジ・保有後最高値**、
  決済済みなら EXIT 日と価格。「ENTRY時点の形」「EXIT時点の形」ボタンで当時の足だけで再描画できる
- **買った理由がまだ残っているか**（シナリオ確認欄）:
  上昇トレンド維持 / MA25維持 / 元レンジ上限突破済み / 重要支持帯を保っている /
  大きな陰線が出ていない / 出来高が急増していない / 直近の局所安値を保っている
  — **✗ が付いても自動の売り条件にはならない**
- 今日チャートを見るべき理由（上の 3 段階、根拠の数値つき）
- ChatGPT へのコピペ用テキスト

**trail stop も利確ラインも描かない。** v1 で自動化していないものを、あるように見せないため。

## ENTRY候補履歴 `/signals`

ENTRY_CANDIDATE が出た日を、**実際に買わなかったものも含めて**すべて残す。
これがフォワード検証の母数になる。購入したかどうかはトレード台帳との結合で表示する
（履歴そのものは書き換えない）。

---

# 状態の意味

| status | 条件 |
|---|---|
| `ENTRY_CANDIDATE` | 上昇トレンド＋短期レンジ＋下限付近＋**反発確認**（終値 > 前日高値） |
| `NEAR` | 上昇トレンド＋短期レンジ＋下限付近。反発確認はまだ＝**最重要監視候補** |
| `RANGE` | 上昇トレンド＋短期レンジ。まだ下限から遠い |
| `OUT` | 条件外（価格帯外／トレンド外／良いレンジなし／レンジ崩壊） |

**`ENTRY_CANDIDATE` は「買え」ではない。** 必ず日足チャートを人間が確認する。

---

# v1 の売買ルール（要約）

正本は **[TRADING_RULES.md](TRADING_RULES.md)**。ここは要約。

## 確定（CONFIRMED）

```text
価格帯                 2,000 〜 7,000円
上昇トレンドの必須条件  close > MA25
短期レンジ             3〜10営業日 / 下限反応 2回以上 / 境界は zone
ENTRYトリガー          終値 > 前日高値
初期STOP               range_lower × 0.995
ポジションサイズ        自動化しない
EXIT                   自動化しない（人間判断）
```

### 初期STOP は最大損失を保証しない

過去検証で、初期STOP に到達した 31 件のうち **10 件（32%）が寄付でギャップ割れ**していた
（割れ幅の中央値 -1.94%）。日足の終値ベースで判断する運用である以上、STOP 価格での
約定は仮定できない。ポジションサイズを考えるときはこのリスクを織り込むこと。

## 暫定（PROVISIONAL）

`near.max_position_in_range = 0.65` をはじめ `experimental.yaml` の全項目。

`0.65` は **最適値として証明された値ではない**。これを超えるとレンジ上側からの
遅い ENTRY が混ざる（0.70 で 13件、0.80 で 74件）ため、上側を拾わないための
安全ガードとして暫定維持している。

## 研究のみ（RESEARCH_ONLY）— 本番には入っていない

警戒陰線の機械判定 / `warning_low` 割れの扱い / `reference_high` の定義 /
押し安値の機械確定 / trail stop の自動引き上げ。

**なぜ EXIT を自動化しないのか**（詳細は [RESEARCH_SUMMARY.md](RESEARCH_SUMMARY.md)）:

```text
WARNING を早くする   → 正常な調整で早く降りる（突破翌日に警戒足 64%）
WARNING を遅くする   → WARNING が出ないまま初期STOPへ戻る（27〜36%）
reference_high を緩める → trail は増えるが利益は残らない、早降りが増える
reference_high を厳しくする → そもそも REHIGH しない
```

母数が 32 件しかなく、これ以上詰めると過剰適合になる。

---

# データの保存先

| 何 | どこ | 性質 |
|---|---|---|
| 株価キャッシュ | `cache/prices/{code}.csv` | `fetch` が書く。唯一ネットワークに触れる先 |
| スクリーニング結果 | `output/screening_YYYY-MM-DD.json` | 全判定の生データ |
| **日次スナップショット** | `data/journal/daily/YYYY-MM-DD.csv` | その日の全銘柄の判定。**上書きしない** |
| **ENTRY候補履歴** | `data/journal/signals.csv` | 買わなかったものも残す。**追記のみ** |
| **トレード台帳** | `data/trades.csv` | 保有中も決済済みも同じ行（`exit_date` が空なら保有中） |
| フォワード検証用 | `data/journal/forward_review.csv` | `forward-export` が生成 |
| トレードのチャート | `output/trades/{code}_{entry_date}_{entry,exit}.png` | `sell` 時に自動保存 |
| **ChatGPT 用データ** | `output/chatgpt/YYYY-MM-DD/{candidates,daily_bars}.csv` + `manifest.txt` | `chatgpt-export` が生成。1 営業日 1 ディレクトリ |

## GitHub Actions での永続化

runner は使い捨てなので、自動生成された記録が次の run で消えないようにしている。

| 何 | どこへ残るか |
|---|---|
| 日次スナップショット・ENTRY候補履歴・ChatGPT 用データ | 専用ブランチ **`automation-data`**（`data/journal/` と `output/chatgpt/` のみ） |
| その日の ChatGPT 用データ | Actions artifact `chatgpt-market-data-YYYY-MM-DD`（保持 90 日） |
| トレード台帳 `data/trades.csv` | **default branch のみ。自動処理は書き込まない** |

run のはじめに `automation-data` から作業ディレクトリへ復元し、終わりに新しい分を
そのブランチへ commit する。既存ファイルを消さずに上書きコピーするので過去の
スナップショットは失われない。default branch は日次 commit で汚れない。
artifact には保持期限があるため、**artifact を唯一の保存先にはしない**
（Actions cache も履歴の正本には使わない）。

## なぜ日次スナップショットを上書きしないのか

株価は配当調整で遡って変わり、レンジ検出も新しい足が付くたびに動く。
後日の再計算値で過去日を上書きすると「その日に見えていたもの」が失われ、
フォワード検証の意味がなくなる。同じ日付のファイルが既にあれば
`daily` は保存をスキップする（`--force-snapshot` で明示的に上書きできる）。

ENTRY候補履歴も同じ理由で、`(signal_date, code)` が既にあれば追記しない。
「実際に買ったか」は履歴側に書かず、トレード台帳との結合で導く。

## トレード台帳の列

```text
code, name, entry_date, entry_price, quantity,
original_range_lower, original_range_upper, initial_stop,
entry_reason, memo, signal_date,
exit_date, exit_price, exit_reason, exit_memo
```

`original_range_*` と `initial_stop` は **ENTRY 時点の値を固定して持つ**。
毎日再計算するとレンジが動き、「何を根拠に買ったのか」が失われるため。

CSV を直接編集してもよい。次のリクエストで画面に反映される。

`exit_reason` は自由記述。記入例:
`initial_stop` / `scenario_break` / `warning_candle` / `support_break` /
`profit_protection` / `discretionary` / `other`
— **これらは自動判定ルールではない。**

---

# パラメータ調整

**確定ルールと未確定ルールをファイルごと分離している。**

## `config.yaml` — 確定値

株価フィルタ 2,000〜7,000円 / MA25 / レンジ 3〜10営業日 / 下限反応 2回 /
損切り＝下限の0.5%下。**これらは確定した売買ルールなので通常は変えない。**

保存先を変えたい場合だけ、次の任意キーを足せる（省略時は既定値）。

```yaml
journal:
  dir: data/journal          # 日次スナップショット / ENTRY候補履歴
  trades_csv: data/trades.csv
```

## `experimental.yaml` — 未確定値

**ここの値は確定した売買ルールではない。** 実際の日足を見ながら調整する前提の暫定値。
全項目にコメントで「何を意味するか」「上げると/下げるとどうなるか」を書いてある。

| パラメータ | 既定 | 意味 |
|---|---|---|
| `ma_slope.method` / `lookback` | `vs_n_days_ago` / 5 | MA25 を「上向き」と判定する方法。`linreg` に切替可 |
| `swing.method` / `pivot_window` | `fractal` / 2 | swing high/low の検出方法。`zigzag` に切替可 |
| `trend.require_higher_highs` / `_lows` | `false` | 高値・安値切り上げを必須にするか（既定は参考表示のみ） |
| `range_zone.lower_tolerance_pct` | 0.7 | 下限zone の許容幅（%）。広げると下限反応回数が増える |
| `range_quality.max_width_pct` | 10.0 | これより広い値幅はレンジとみなさない |
| `range_quality.min_quality` | 0.45 | この品質未満なら「良いレンジなし」= OUT |
| `range_quality.big_bearish_body_pct` | 3.0 | 大陰線の目安。保有レビューの `CAUTION` でも使う |
| `range_quality.big_bearish_volume_ratio` | 1.8 | 出来高急増の目安。同上 |
| `near.lower_threshold_pct` | 2.0 | 「下限付近」とみなす距離（%） |
| `near.lookback_days` | 3 | 直近何日以内の下限接触を NEAR とみなすか |
| `near.max_position_in_range` | 0.65 | レンジ内位置ガード（後述） |
| `volume.contract_ratio` | 0.80 | 出来高「減少傾向」の判定比 |

変更後は Web UI の「再スクリーニング」ボタンを押すだけ（`fetch` 不要）。

保有レビューは **EXIT 用の新しい閾値を 1 つも持たない。** 使うのは上の
`big_bearish_*` だけで、これはレンジ検出が元から持っていた値である。

## アルゴリズムの差し替え

MA傾き判定と swing 検出は未確定なので registry 形式にしてある。
`experimental.yaml` の `method:` を変えるだけで実装が切り替わり、
新しい方式を追加する場合も `@register_slope_method` / `@register_swing_method` で登録するだけ。

---

# 実装上の重要な点

## レンジ内位置ガード

CODEX_HANDOFF の定義（下限付近＋反発確認）をそのまま実装すると、反発した銘柄は
価格が下限から離れるため両条件が同時に成立しにくい。そこで「直近 N 日以内の下限接触も
下限付近とみなす」（`near.lookback_days`）を入れた。

ところが**実データで、これだけでは商船三井・日本郵船・川崎汽船がレンジ内位置 0.87〜0.92、
つまりレンジ上限のすぐ下で `ENTRY_CANDIDATE` になった。** これは CODEX_HANDOFF §21 が
新規エントリーに使わないと明記した「レンジ上限ブレイク」そのもので、最も買ってはいけない位置になる。

```
position = (終値 - レンジ下限) / (レンジ上限 - レンジ下限)   # 0=下限, 1=上限
position <= near.max_position_in_range (既定 0.65) を NEAR/ENTRY の必須条件とする
```

距離(%)の上限では代用できない。レンジ幅が狭いと上限付近でも距離が小さくなるためで、
幅3%のレンジでは位置0.95でも下限から +2.7% しか離れていない。position はレンジ幅に
自動でスケールする。

`near.lookback_days: 0` かつ `near.max_position_in_range: null` にすれば
CODEX_HANDOFF の厳密な定義に戻せる。

## 株価は配当調整済み

yfinance の調整後株価を使っているため、過去の足は証券会社のチャートと僅かにずれる。
直近の足は一致する。MA25 と 3〜10日レンジの判定には実用上影響しない。

## 局所安値は後追いで確定する

保有レビューが出す「直近の局所安値」は fractal 検出（`swing.pivot_window: 2`）なので、
**右側の足が 2 本確定するまで出ない**。リアルタイムの押し安値ではない。

---

# 精度改善の回し方

**改善の入力は OUT 銘柄の落選理由。** 一覧画面で OUT を展開すると、なぜ落ちたかが1行で出る。

```
品質を満たすレンジがない（最良は8日 window（値幅が広すぎる（15.4% > 10.0%）
                          / 日々の値幅が拡大中（後半/前半 1.44 > 1.15）））
上昇トレンド条件を満たさない（MA25の向き: 下向き — MA25 4,707円 < 5日前 4,851円 (-3.0%)）
```

日足を見て違和感があったら、対応するパラメータを `experimental.yaml` で調整して再スクリーニング。

| 気づいたこと | 調整先 |
|---|---|
| 拾ってほしい銘柄がレンジなしで落ちた | `range_quality.max_width_pct` を広げる / `min_quality` を下げる |
| 不自然なレンジを拾った | `range_quality.min_quality` を上げる / `max_width_pct` を狭める |
| 下限判定がおかしい | `range_zone.lower_tolerance_pct` |
| 下限接近判定が早すぎる / 遅すぎる | `near.lower_threshold_pct` |
| 反発後に候補から消えるのが早い | `near.lookback_days` / `near.max_position_in_range` |
| 上限付近の銘柄を拾ってしまう | `near.max_position_in_range` を下げる |
| 高値・安値切り上げが「判定不能」ばかり | `swing.pivot_window` を下げる |

**ENTRY 条件そのもの・初期STOP・`0.65` は、この手順では変えない。**
変えるなら [TRADING_RULES.md](TRADING_RULES.md) §10 の手順に従う。

---

# ファイル構成

```
src/swing_screener/
│  【本番】
├── cli.py          CLI（fetch / daily / serve / holdings / buy / sell …）
├── models.py       データモデル（全判定は Judgement = 結果 + 具体的な根拠 を伴う）
├── config.py       config.yaml / experimental.yaml のロード
├── universe.py     watchlist の正規化・ロード
├── screener.py     スクリーニングのパイプライン統合
├── explain.py      判定理由テキスト生成
├── charting.py     日足チャートPNG（候補用・保有用）
├── portfolio.py    トレード台帳（保有・売買記録）
├── journal.py      日次スナップショット・ENTRY候補履歴
├── review.py       保有銘柄の日次レビュー（売買判定はしない）
├── chatgpt_export.py  ChatGPT 分析用CSV（本番結果を写すだけ。判定はしない）
├── data/           yfinance 取得と CSV キャッシュ
├── indicators/     MA25・swing・出来高（method 差し替え可）
├── rules/          トレンド / レンジ検出 / 反発 / 状態分類
├── web/            FastAPI + Jinja2（候補一覧・保有銘柄・ENTRY候補履歴）
│
│  【研究】本番からは一切 import されない
└── research/       過去データ検証（下記「研究コマンド」）
```

`fetch`（通信あり）と `daily` / `screen`（キャッシュのみ）を分離しているので、
パラメータ調整の再計算はオフラインで何度でも走る。

本番コードが研究コードを import していないことは
`tests/test_production_isolation.py` が検査している（依存は研究 → 本番の一方通行）。

---

# 開発

```bash
.venv/bin/python -m pytest -q     # 436 tests
```

テストは株価APIに依存せず、合成OHLCVでルールの意図を固定している。
パラメータを変えたときに何が壊れるかはテストが教えてくれる。

| テスト | 何を守っているか |
|---|---|
| `test_status.py` | 4状態の判定・価格フィルタ・`0.65` ガード |
| `test_range_detect.py` | レンジ検出 |
| `test_indicators.py` | MA / swing / 出来高 |
| `test_charting.py` | チャートPNG（候補用・保有用・`as_of` の切り出し） |
| `test_portfolio.py` | 台帳の往復・二重保有の拒否 |
| `test_journal.py` | 過去記録を書き換えないこと・二重記録しないこと |
| `test_review.py` | 「売れ」と言わないこと・新しい閾値を持たないこと |
| `test_cli_daily.py` | 毎日の運用フロー（daily / buy / sell / forward-export） |
| `test_chatgpt_export.py` | CSV が本番判定と一致すること・OUT を出さないこと・70営業日・0件・市場休日のスキップ・検査・workflow の設定 |
| `test_production_isolation.py` | 研究ロジックが本番に混入していないこと |
| `test_research_*.py` | 各研究の look-ahead 対策・prefix 不変性 |

---

---

# 研究コマンド（日常運用では使わない）

以下は **過去データ検証専用** で、本番ロジックとは完全に分離されている。
`config.yaml` / `experimental.yaml` / `output/` には一切書き込まない。

**2026-08 に研究フェーズは終了した。** 結論は [RESEARCH_SUMMARY.md](RESEARCH_SUMMARY.md)
にまとめてある。以下は結果を再現するためのコマンドであって、**新しい探索を始めるための
入口ではない**。母数は 32 イベントしかなく、これ以上詰めると過剰適合になる。

入口は本番と別:

```bash
.venv/bin/python -m swing_screener.research.cli --help
```

## 1. ENTRY 閾値スイープ

```bash
.venv/bin/python -m swing_screener.research.cli run --months 6
# → research/report.html

# 12ヶ月に拡張する場合（本番の fetch_period は変更しない）
.venv/bin/python -m swing_screener.research.cli fetch-history --years 2
```

各営業日について「その日までのデータだけ」でスクリーニングを再現し、ENTRY_CANDIDATE の
シグナルとその後の値動きを記録する。look-ahead bias が無いことは
`tests/test_research_replay.py` で固定している（未来足を追加/削除しても過去日の判定が変わらない）。

出力: `research/report.html` / `events.csv` / `events_pos*.csv` / `summary.csv` / `charts/`

## 2. ENTRY 後の追跡

```bash
.venv/bin/python -m swing_screener.research.cli exit-study
# → research/exit_study/report.html
```

仮想 ENTRY 価格は「シグナル翌営業日の始値」（**検証用であって売買ルールではない**）。
ポジションを閉じる機械判定に使うのは確定ルールの初期損切りだけ。

出力: `report.html` / `events.csv` / `timeline.csv` / `warning_candles.csv` /
`trail_candidates.csv` / `summary.csv` / `representative_charts/`

## 3. EXIT 状態機械

```bash
.venv/bin/python -m swing_screener.research.cli exit-state-machine
# → research/exit_state_machine/report.html
```

```
INITIAL_HOLD ──(終値 > 元レンジ上限)──> TREND_HOLD ──(最初の陰線)──> WARNING
                                            ↑                          │
                                            └──(reference_high 再突破)──┘
                                               押し安値確定 → STOP引き上げ
```

CODEX_HANDOFF §30 の文章ルールを状態機械にできるか確かめた。CASE1/2/3 の比較は
**「どの CASE が儲かるか」を決めるためのものではない。**

出力: `report.html` / `events.csv` / `state_timeline.csv` / `warnings.csv` /
`stop_updates.csv` / `daily_state.csv` / `case_comparison.csv` / `summary.csv` /
`representative_charts/`

## 4. WARNING をいつ有効化するか（VARIANT A/B/C）

```bash
.venv/bin/python -m swing_screener.research.cli warning-start-study
# → research/warning_start_study/report.html
```

| 案 | 警戒足を有効化する条件 |
|---|---|
| A | 元レンジ上限を終値突破した**翌営業日**から（比較基準） |
| B | 突破後に `high > breakout_day_high` を満たした日の翌営業日から |
| C | 突破後に `close > breakout_day_close` を満たした日の翌営業日から |

`reference_high` の定義・押し安値・トレーリング・初期STOP・ENTRY ロジック・
`0.65` は **3 案とも完全に同一**。VARIANT A は状態機械検証と同じ挙動なので
`research/exit_state_machine/` の CSV はバイト単位で変わらない。

## 5. `warning_low` を割ったあとの扱い（LOW / CLOSE / STRUCTURAL）

```bash
.venv/bin/python -m swing_screener.research.cli warning-break-study
# → research/warning_break_study/report.html
```

| 案 | 利確候補のトリガー |
|---|---|
| 参考 `HOLD_UNTIL_STOP` | 降りない（比較の基準） |
| V1 `LOW_BREAK` | `low < warning_low` |
| V2 `CLOSE_BREAK` | `close < warning_low` |
| V3 `STRUCTURAL_BREAK` | `close < warning_low` かつ `close < original_range_upper` |

3 つは入れ子（V1 ⊇ V2 ⊇ V3）。仮想EXITは 4 案とも**トリガー翌営業日の始値**に統一し、
`warning_low` に STOP を置いた場合の参考価格は別列に分けてある。

## 6. `reference_high` の決め方（RH-A 〜 RH-E）

```bash
.venv/bin/python -m swing_screener.research.cli reference-high-study
# → research/reference_high_study/report.html
```

| 案 | `reference_high` |
|---|---|
| RH-A `HOLDING_HIGH` | `max(high)` ENTRY〜警戒足**当日**（比較基準） |
| RH-B `WARNING_HIGH` | 警戒足の高値 |
| RH-C `PRE_WARNING_CLOSE_HIGH` | `max(close)` ENTRY〜警戒足**前日** |
| RH-D `WARNING_OPEN` | 警戒足の始値 |
| RH-E `PRE_WARNING_HIGH` | `max(high)` ENTRY〜警戒足**前日**（参考VARIANT） |

定義上 `RH-A = max(RH-B, RH-E)` なので、**RH-A は常に RH-B か RH-E のどちらか一方と一致する**。
同じ日に「終値で `warning_low` 割れ」と「`high > reference_high`」の両方が成立する日は
`AMBIGUOUS_REHIGH_EXIT_ORDER` として分離し、**REHIGH 優先 / EXIT 優先の両方を走らせて
差分を出す**。どちらが正しい順序かは決めていない。

## look-ahead bias を作らないための約束（テストで固定）

- 1 営業日ずつ前へ進み、その日までの足しか読まない
- `new_swing_low_candidate` は `reference_high` を再突破した日に初めて確定する
- 引き上げた STOP は**翌営業日から**有効（確定日の安値へ遡って適用しない）
- 利確候補のトリガーも**その営業日の足だけ**で判定する。仮想EXITの約定（翌営業日始値）は
  追跡ループを抜けたあとで初めて埋める
- 途中で打ち切った系列で走らせた結果が、全長で走らせた結果の先頭と一致すること（prefix 不変性）
- `reference_high` はどの案も**警戒足当日までの足だけ**で決まる。RH-C は当日の終値を、
  RH-E は当日の高値を含まない（running max の更新順で担保し、順序を入れ替える変異で
  テストが落ちることを確認済み）

## 読むときの注意

- これは**パラメータ最適化ではない**。過去成績が最良の閾値を選ぶための道具ではない
- 終値基準の forward は「シグナル日終値で買えた」という**実際には不可能な仮定**を含む
- **損切り到達率を閾値間で単純比較しない。** エントリー位置が高いほど損切りまでの距離が
  広がるため、率だけを見ると「緩いほど安全」と誤読する
- イベントは独立ではない。同一銘柄の同一レンジが連日シグナルを出す
- **ほぼ全件が最終的に初期STOPへ到達するのは、利確ルールが未確定でポジションを閉じる
  機械判定が初期STOPしか無いため。** これを「損切り失敗率」と読まない
- **件数の多い案を「良い案」と読まない。** `reference_high` の 5 案では 32 件のうち
  結果が変わるのは 5 件だけで、trail 成立件数が増えても利益吐き出しは改善せず、
  増えたのは「正常な調整で早く降りた」件の方だった

## 研究コードは削除しない

過去の検証を再現可能な状態で保存する。ただし**本番ツールのロジックには混入させない**
（`tests/test_production_isolation.py` が検査）。

---

# 次のフェーズ

**追加のバックテストではない。**

```text
フォワード運用（swing daily を毎日）
  → 実トレード記録（swing buy / sell）
  → 新規 ENTRY イベントが 10〜30件 貯まる
  → 困っている 1 点だけを対象に、必要な箇所だけ再研究
```

貯まったデータは `swing forward-export` で 1 枚の CSV にまとめられる。
再開の手順と、やってはいけないことは [TRADING_RULES.md](TRADING_RULES.md) §10 と
[RESEARCH_SUMMARY.md](RESEARCH_SUMMARY.md) §10 に書いてある。
