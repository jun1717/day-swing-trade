# DESIGN.md — 日足短期スイング スクリーナー 設計書

本書は売買ルールを実装に落とすための**契約書**である。
実装者（人間・エージェント問わず）は本書のインターフェースを変更してはならない。
変更が必要な場合は、まず本書を更新する。

> **売買ルールの正本は `TRADING_RULES.md`。** 本書は実装側の契約だけを扱う。
>
> v1 で追加したモジュール（本書の §6〜§12 の契約は変更していない）:
>
> | モジュール | 役割 |
> |---|---|
> | `portfolio.py` | トレード台帳（`data/trades.csv`）。判定を持たない |
> | `journal.py` | 日次スナップショット・ENTRY候補履歴。**過去を上書きしない** |
> | `review.py` | 保有銘柄の日次レビュー。**売買判定はしない** |
> | `charting.render_holding_chart` | 保有銘柄チャート（ENTRY価格 / 初期STOP / 保有後最高値） |
>
> Web UI は `/holdings`・`/holdings/{code}`・`/signals` を追加した。
> スクリーニングの判定ロジック（`screener.py` / `rules/` / `indicators/`）は
> v0.1 から変更していない。

---

## 0. 大原則

1. **このツールは買い銘柄を自動決定しない。** 人間が日足を見るべき銘柄を減らすだけ。
2. **未確定パラメータをコードに固定しない。** すべて `experimental.yaml` に置く。
3. **すべての判定は理由を持つ。** 判定結果だけを返す関数を作らない。必ず `Judgement` を伴う。
4. **OUT銘柄も捨てない。** なぜ落ちたかを保持する。これが精度改善の唯一の入力である。

---

## 1. 技術スタック

- Python 3.11+ / uv
- pandas（OHLCV処理）
- yfinance（株価取得、`.T` サフィックス）
- FastAPI + Jinja2（Web UI）
- matplotlib（詳細チャートPNG生成、`Agg` バックエンド）
- typer（CLI）
- pytest（ロジック回帰テスト）

チャートを PNG にする理由：詳細画面の目的が「スクリーンショットして ChatGPT に貼る」ことなので、
対話的JSチャートより確定画像のほうが用途に合う。PNG は直接ダウンロードもできる。

---

## 2. ディレクトリ構成

```
.
├── CODEX_HANDOFF.md         # 売買ルールの原典（変更しない）
├── DESIGN.md                # 本書
├── README.md
├── pyproject.toml
├── config.yaml              # 確定済みルールのみ
├── experimental.yaml        # 未確定パラメータのみ
├── data/
│   ├── watchlist.csv        # 人間が編集する唯一の銘柄ファイル
│   ├── stocks.csv           # 自動生成（正規化された銘柄マスター）
│   └── stock_themes.csv     # 自動生成（銘柄×テーマ）
├── cache/prices/{code}.csv  # OHLCVキャッシュ
├── output/
│   ├── screening_YYYY-MM-DD.json
│   └── charts/{code}.png
├── src/swing_screener/
│   ├── models.py            # 【契約】データモデル
│   ├── config.py            # 【契約】設定ロード
│   ├── universe.py          # watchlist正規化・ロード
│   ├── data/
│   │   ├── provider.py      # DataProvider Protocol
│   │   ├── yfinance_provider.py
│   │   └── cache.py         # CSVキャッシュ read/write
│   ├── indicators/
│   │   ├── ma.py            # MA25・傾き（method切替可）
│   │   ├── swing.py         # swing high/low検出（method切替可）
│   │   └── volume.py        # 出来高集計
│   ├── rules/
│   │   ├── trend.py         # 上昇トレンド判定
│   │   ├── range_detect.py  # 3〜10日レンジ検出
│   │   ├── rebound.py       # 反発確認
│   │   └── status.py        # OUT/RANGE/NEAR/ENTRY_CANDIDATE 分類＋並び順
│   ├── screener.py          # パイプライン統合
│   ├── market_session.py    # 当日セッションの確定判定（運用設定・売買ルールではない）
│   ├── chatgpt_export.py    # ChatGPT 分析用 CSV の書き出し（判定はしない）
│   ├── charting.py          # 詳細チャートPNG
│   ├── explain.py           # 判定理由テキスト生成
│   ├── cli.py               # typer
│   └── web/
│       ├── app.py
│       ├── templates/{base,list,detail}.html
│       └── static/style.css
└── tests/
```

---

## 3. データフロー

```
data/watchlist.csv
   │  swing normalize
   ▼
data/stocks.csv + data/stock_themes.csv
   │  swing fetch            ← ネットワークアクセスはここだけ
   ▼
cache/prices/{code}.csv
   │  swing screen           ← オフラインで何度でも再実行可
   ▼
output/screening_YYYY-MM-DD.json
   │  swing serve
   ▼
一覧画面 → 詳細画面（チャートPNG + 判定理由）
```

`screen` がネットワークに触れないことが重要。パラメータ調整の反復速度を最優先する。

---

## 4. 監視銘柄CSVの管理

`data/watchlist.csv`（列: `code,name,sector,theme,is_leader,watch_priority`）が人間の編集対象。
`normalize` で以下2ファイルへ正規化する。

- **stocks.csv**: `code,name,sector,asset_type,enabled` — code でユニーク
- **stock_themes.csv**: `code,theme,is_leader,watch_priority` — 銘柄×テーマの多対多

ルール:
- 重複 code は1行に集約。`name` / `sector` は最初の出現を採用し、後続で不一致があれば警告を出す（落とさない）。
- `asset_type`: `sector == "ETF"` または銘柄名に `ETF` / `上場投信` を含む → `etf`、それ以外 `stock`。
  `config.yaml` の `universe.asset_type_overrides` で code 単位に上書きできる。
- `enabled` は既定 `true`。`false` の銘柄はスクリーニング対象外（マスターからは削除しない）。
- 表示用 priority = その銘柄が持つ全テーマ中の最上位（A > B > C）。**売買条件ではない。**
- 既に `stocks.csv` が存在し、人間が `enabled` を編集している場合、`normalize` は
  `enabled` 列の既存値を保持する（マージ）。

---

## 5. 設定の管理

### config.yaml — 確定値のみ
CODEX_HANDOFF §28 の確定値を置く。ここの値を実装者が勝手に変えない。

末尾の `--- 以下はルールではなく実行環境の設定 ---` 以降（`data` / `output` /
`chart` / `market_session`）は**売買ルールではない**。特に `market_session`
（Asia/Tokyo・大引け 15:30・データ確定待ち 16:00）は「当日セッション終了前の
未確定日足を正式 bundle にしない」ための運用設定で、売買パラメータではない。

### experimental.yaml — 未確定値のみ
冒頭に「これは確定した売買ルールではない」と明記。全項目にコメントで
「何を意味するか」「上げると/下げるとどうなるか」を書く。

アクセスは `cfg.range.min_days` / `exp.near.lower_threshold_pct` のようなドットアクセス。
**YAMLにキーを足すだけで新パラメータが使えること**を要件とする（dataclass再定義を強制しない）。
これはパラメータ変更が主作業になるツールだからである。

出力JSONには使用した config / experimental の全内容を埋め込む（結果の再現性確保）。

---

## 6. 【契約】データモデル (`models.py`)

`models.py` に定義済み。実装者は**フィールドを削除・改名しない**。追加は可。

中核は `Judgement`（1つの判定 = ok + ラベル + 具体的な数値説明）。
トレンド・レンジ・反発の各ルールは必ず `judgements: tuple[Judgement, ...]` を返す。
UI の「なぜその判定なのか」表示はこれを描画するだけで成立する。

---

## 7. 指標 (`indicators/`)

### ma.py
- `calc_ma_series(bars, period) -> list[float | None]`
- `ma_slope(ma_series, exp) -> (direction, slope_pct, detail)`
  - `method: vs_n_days_ago` … `(MA[-1] - MA[-1-lookback]) / MA[-1-lookback] * 100`
  - `method: linreg` … 直近 lookback 本の線形回帰の傾きを % 換算
  - `slope_pct > min_slope_pct` なら `up`、`< -min_slope_pct` なら `down`、他は `flat`

### swing.py
- `detect_swings(bars, exp) -> (highs, lows)` 各要素 `SwingPoint(index, date, price)`
  - `method: fractal` … 左右 `pivot_window` 本より高い(安い)高値(安値)を pivot とする
  - `method: zigzag` … `zigzag_pct` 以上の反転で pivot を確定
- **未確定アルゴリズムなので、method を増やせる registry 形式にする。**

### volume.py
- `summarize_volume(bars, range_start_idx, exp) -> VolumeInfo`
- 当日 / 5日平均 / 20日平均 / レンジ期間平均 / レンジ前平均（レンジと同じ日数分の直前区間）
- `state`: `contracting`（レンジ中減少傾向）/ `neutral` / `expanding`
  - `range_avg / pre_range_avg <= exp.volume.contract_ratio` → contracting
  - `>= exp.volume.expand_ratio` → expanding
- 出来高は**単独で売買判定に使わない**。表示と並び順の補助のみ。

---

## 8. ルール (`rules/`)

### trend.py — 上昇トレンド判定
判定項目（すべて Judgement 化）:
1. `close > MA25`
2. MA25 の向き（up/flat/down）
3. 高値切り上げ（直近2つの swing high 比較）
4. 安値切り上げ（直近2つの swing low 比較）

**必須項目は experimental で切替**（`trend.require_*`）。初期値は 1 と 2 を必須、3・4 は
参考表示（swing検出アルゴリズムが未確定なため、これを必須にすると取りこぼしが出る）。

`strength`（トレンド強度、並び順用）= MA25乖離率と MA25傾きから算出。内訳を必ず表示する。

### range_detect.py — 短期レンジ検出
`range.min_days`〜`range.max_days`（3〜10）の各 window（直近 w 本）について評価する。

各 window の算出値:
- `upper = max(high)`, `lower = min(low)`
- `upper_zone = [upper*(1-t), upper*(1+t)]`, `lower_zone = [lower*(1-t), lower*(1+t)]`
  （t = `exp.range_zone.upper_tolerance_pct` / `lower_tolerance_pct`）
- `width_pct = (upper - lower) / lower * 100`
- `lower_touch_count` = window内で `low <= lower_zone_high` を満たす**日数**。
  ただし連続日は1回の反応としてまとめる（2日連続で下限を這うのは1反応と数える）。
- `volatility_change` = window後半の平均日中値幅 / 前半の平均日中値幅（<1 が収縮＝良）
- `volume_change` = window平均出来高 / window直前同日数の平均出来高

**除外条件**（1つでも該当したらその window は不採用、理由を残す）:
- `width_pct > exp.range_quality.max_width_pct`（値幅が広すぎる＝レンジでない）
- `width_pct < exp.range_quality.min_width_pct`（動きがなさすぎる）
- 安値が `exp.range_quality.reject_consecutive_lower_lows` 本連続で切り下がる
- 大陰線＋出来高急増（実体下落率 >= `big_bearish_body_pct` かつ 出来高が20日平均の
  `big_bearish_volume_ratio` 倍以上）が window 内に存在
- `volatility_change > exp.range_quality.max_volatility_change`（値幅拡大中）

**品質スコア** `quality`（0〜1、重みは experimental）:
- 幅の狭さ / 下限反応 / ボラ収縮 / 出来高減少 / 日数の長さ
- 下限反応の得点は `min(lower_touch_count / range.min_lower_touches, 1.0)`。
  つまり **`min_lower_touches` は「満点になる目標値」であって除外条件ではない**
  （上の除外条件の一覧に下限反応回数が入っていないのはそのため）。
  1回でも総合スコアが `min_quality` を超えればレンジは成立する。
  詳細と経緯は `TRADING_RULES.md` §3.3。

全 window のうち quality 最大のものを採用。`quality < exp.range_quality.min_quality`
なら「良いレンジなし」として OUT。

**採用 window の全候補とその不採用理由も結果に残す**（UI で「なぜこのレンジになったか」を示すため）。

### rebound.py — 反発確認
- `rebound_confirmed = latest.close > prev.high`（**確定ルール**）
- 加点材料（表示のみ、単独では判定しない）: 陽線 / 長い下ヒゲ / 出来高回復

### status.py — 状態分類
順に評価し、最初に該当した理由で OUT する:

| 判定 | 条件 |
|---|---|
| OUT | データ不足 / `enabled=false` |
| OUT | `latest_close` が `[price_filter.min, price_filter.max]` の外 |
| OUT | 上昇トレンド条件を満たさない |
| OUT | 品質を満たすレンジがない |
| OUT | レンジ下限を `exp.near.break_tolerance_pct` 超で下抜け（レンジ崩壊） |
| RANGE | 上昇トレンド + レンジあり、下限から遠い |
| NEAR | RANGE + （`distance_to_lower_pct <= exp.near.lower_threshold_pct`<br>または直近 `exp.near.lookback_days` 日以内に下限zoneへ接触）<br>**かつ** レンジ内位置 `<= exp.near.max_position_in_range` |
| ENTRY_CANDIDATE | NEAR + `rebound_confirmed` |

`distance_to_lower_pct = (current_price - range_lower) / range_lower * 100`

`stop_price = range_lower * (1 - config.stop.buffer_pct)` = レンジ下限の0.5%下。

**NEAR の lookback について**: 反発すると価格は下限から離れるため、当日距離だけで判定すると
「下限付近 かつ 反発確認」が同時成立しにくい。そこで直近数日の下限接触も NEAR とみなす。
`lookback_days: 0` にすれば CODEX_HANDOFF の厳密定義に戻る。**これは experimental である。**

**レンジ内位置ガード（`near.max_position_in_range`）**: 上の lookback だけでは、
下限zoneに触れたあと上限まで走り抜けた銘柄まで NEAR/ENTRY に入ってしまう。
実データ（2026-08-10、商船三井・日本郵船・川崎汽船）でレンジ内位置 0.87〜0.92 の銘柄が
ENTRY_CANDIDATE に分類される事象が実際に発生した。これは CODEX_HANDOFF §21 が新規
エントリーに使わないと明記した「レンジ上限ブレイク」そのもので、最も買ってはいけない位置である。

```text
position = (close - range_lower) / (range_upper - range_lower)   # 0=下限, 1=上限
```

距離(%)のキャップでは代用できない。レンジ幅が狭いと上限付近でも距離が小さくなるためで、
幅3%のレンジでは位置0.95でも下限から +2.7% しか離れていない。position は幅に自動で
スケールするので、幅によらず一貫した意味を持つ。両方のガードを持つ理由がこれである。
（回帰テスト: `tests/test_status.py::test_位置ガードは距離キャップでは代用できない`）

### 並び順
status（ENTRY_CANDIDATE > NEAR > RANGE > OUT）で第一ソート。
同一 status 内は以下の順（すべて UI に列として出し、順位理由が見えるようにする）:
1. `distance_to_lower_pct` 昇順
2. `trend.strength` 降順
3. `range.quality` 降順
4. `volume` 評価（contracting を上位）
5. `watch_priority`（A > B > C）… 最後の微調整のみ

**ブラックボックスな総合スコアで順位を決めない。**

---

## 9. 一覧画面 (`/`)

- ヘッダ: データ基準日、銘柄数、status別件数バッジ、`swing fetch` 実行日時
- ワンクリックフィルタ: **「NEAR + ENTRY_CANDIDATE のみ」**（最重要）
- 絞り込み: status / theme / sector / watch_priority / is_leader / stock・ETF・すべて
- テーブル列（CODEX_HANDOFF §24 準拠、全項目必須）:
  code / 銘柄名 / sector / theme / watch_priority / is_leader / asset_type / 現在値 /
  status / MA25 / MA25乖離率 / MA25方向 / 高値切上 / 安値切上 / range_days /
  range_lower / range_upper / range_width_pct / lower_touch_count /
  distance_to_lower_pct / volume_state / 前日高値 / rebound_confirmed / stop_price
- status はバッジで色分け（ENTRY_CANDIDATE=赤、NEAR=橙、RANGE=青、OUT=灰）
- 行クリックで詳細画面へ
- OUT 銘柄は既定で折りたたみ、展開すると**落選理由**列付きで表示

フィルタ・ソートはサーバー往復なしのクライアントJSで完結させる（100銘柄なら十分）。

## 10. 詳細画面 (`/stock/{code}`)

左（または上）: 日足チャートPNG
- ローソク足（既定120日、60/120/250切替）
- MA25
- 検出レンジの矩形（上限zone・下限zoneを帯で表示）
- 前日高値の水平線
- 損切りライン（`stop_price`）
- 出来高サブプロット（レンジ期間をハイライト）

右（または下）: 判定理由パネル。CODEX_HANDOFF §27 の書式をそのまま出す:

```
上昇トレンド：OK
25日線：上向き (+1.8% / 5日)
株価 > MA25：OK (5,580 > 5,412, +3.1%)
高値切り上げ：OK
安値切り上げ：OK

レンジ：6営業日 (08/01〜08/08)
下限：5,120円
上限：5,480円
下限反応：2回 (08/04, 08/07)
値幅：7.0%

下限まで：+1.2%

出来高：レンジ中減少傾向 (レンジ平均/レンジ前平均 = 0.72)

反発確認：未成立 (終値5,580 <= 前日高値5,610)

状態：NEAR
損切り候補：5,094円
```

加えて PNG ダウンロードリンク（ChatGPT へ貼る用）を置く。

---

## 11. CLI

```
swing normalize              # watchlist.csv → stocks.csv / stock_themes.csv
swing fetch [--code 5803] [--force]   # 株価取得 → cache/prices/
swing screen [--json out.json]        # スクリーニング（オフライン）
swing chart <code> [--days 120]       # 単体チャートPNG生成
swing serve [--port 8000]             # Web UI（起動時に screen を実行）
```

すべて `--config` / `--experimental` でファイル差し替え可能。

---

## 12. テスト方針

合成OHLCVを組み立てて**ルールの意図**を固定する。株価APIには依存させない。

- 上昇トレンド判定（close>MA25、MA25の向き）
- swing high/low 検出（fractal）
- レンジ検出：理想的なレンジ / 幅が広すぎる / 値幅拡大 / 大陰線+出来高急増 / 1〜2日
- lower_touch_count（連続日を1回にまとめる挙動）
- status 分類 4状態それぞれ
- 価格フィルタ境界（2000 / 7000 ちょうどは通す）
- universe 正規化（重複銘柄・複数テーマ・ETF判定・priority集約）

---

## 12.5 【契約】モジュール間インターフェース

以下のシグネチャは固定。実装者は変更しないこと。

```python
# universe.py
def normalize_watchlist(cfg) -> tuple[list[Stock], list[str]]:
    """watchlist.csv を読み、stocks.csv / stock_themes.csv を書き出す。
    戻り値は (銘柄一覧, 警告メッセージ一覧)。"""

def load_universe(cfg) -> list[Stock]:
    """stocks.csv + stock_themes.csv を読む。無ければ normalize_watchlist を実行する。"""

# data/provider.py
class DataProvider(Protocol):
    def fetch(self, code: str) -> PriceSeries: ...

# data/yfinance_provider.py
class YfinanceProvider:
    def __init__(self, cfg) -> None: ...
    def fetch(self, code: str) -> PriceSeries: ...

# data/cache.py
def price_path(code: str, cfg) -> Path: ...
def save_prices(series: PriceSeries, cfg) -> Path: ...
def load_prices(code: str, cfg) -> PriceSeries | None: ...
def cached_codes(cfg) -> list[str]: ...
def last_fetch_at(cfg) -> str | None:
    """cache/prices/.meta.json に記録した最終取得日時（ISO文字列）。"""
def record_fetch(cfg) -> None: ...

# screener.py
def load_price_map(stocks: list[Stock], cfg) -> tuple[dict[str, PriceSeries], list[str]]:
    """キャッシュから読む。戻り値は (code -> PriceSeries, 警告)。ネットワークに触れない。"""

def screen_one(stock: Stock, series: PriceSeries | None, cfg, exp) -> ScreenResult: ...

def run_screening(stocks, price_map, cfg, exp) -> ScreeningRun:
    """全銘柄を判定し sort_key で並べた ScreeningRun を返す。"""

def save_run(run: ScreeningRun, cfg, path: Path | None = None) -> Path:
    """output/screening_YYYY-MM-DD.json に保存。config/experimental も埋め込む。"""

def run_to_dict(run: ScreeningRun) -> dict:
    """JSON化。Web からも使う。"""

# explain.py
def explain_lines(result: ScreenResult) -> list[str]:
    """DESIGN.md §10 の書式で判定理由テキストを返す。"""

def judgement_groups(result: ScreenResult) -> list[tuple[str, list[Judgement]]]:
    """("上昇トレンド", [...]), ("レンジ", [...]), ("出来高", [...]), ("反発", [...]) の順。"""

# charting.py
def render_daily_chart(
    series: PriceSeries, result: ScreenResult, cfg, exp,
    output_path: Path, days: int = 120,
) -> Path: ...

# web/app.py
def create_app(config_path: str = "config.yaml",
               experimental_path: str = "experimental.yaml") -> FastAPI: ...
```

---

## 13. v0.1 で作らないもの

CODEX_HANDOFF §31 準拠。特に **レンジ上限ブレイクによる新規エントリー探索は実装しない。**
自動売買・自動ポジションサイズ・AI自動BUY判定も対象外。
