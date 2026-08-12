"""ChatGPT 分析用データの書き出し（swing chatgpt-export）と日次自動実行の検査。

このテストが守りたいのは 1 点に尽きる。

    **export は本番判定を写すだけで、判定をやり直さない。**

そのため「CSV の各値が本番 `ScreenResult` と一致すること」「export モジュールが
レンジ検出・ENTRY 判定・レンジ内位置ガードを import すらしていないこと」を
固定する。ネットワークには触れず、合成日足だけで完結させる。
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml
from tests.conftest import SeriesBuilder, make_stock, uptrend_with_range
from typer.testing import CliRunner

from swing_screener import chatgpt_export as export_mod
from swing_screener.cli import app
from swing_screener.config import load_config, load_experimental
from swing_screener.data.cache import save_prices
from swing_screener.models import STATUS_ENTRY, STATUS_NEAR, STATUS_OUT, STATUS_RANGE
from swing_screener.screener import run_screening

runner = CliRunner()

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "daily-screening.yml"


# --- 合成プロジェクト ------------------------------------------------------------

# ENTRY / NEAR / RANGE / OUT が 1 件ずつ出る組み合わせ（tests/test_status.py と同じ形）。
# 全銘柄を同じ 76 本（= 同じ最終営業日）に揃える。実データでも全銘柄の
# 最終足は同じ日になるので、そこを合わせておかないと as_of の検査が現実と食い違う。
SERIES_BUILDERS = {
    "0002": lambda: uptrend_with_range(trend_days=70, range_days=6, touch_days=(1, 4), code="0002"),
    "0004": lambda: uptrend_with_range(trend_days=70, range_days=6, touch_days=(1, 5), code="0004"),
    "0001": lambda: uptrend_with_range(trend_days=68, range_days=8, touch_days=(0, 1), code="0001"),
    "0003": lambda: SeriesBuilder(code="0003").downtrend_to(76, 3000, 15),
}
TOTAL_BARS = 76

STOCK_ROWS = (
    ("0002", "ENTRY銘柄", "電気機器", "A"),
    ("0004", "NEAR銘柄", "機械", "B"),
    ("0001", "RANGE銘柄", "輸送用機器", "C"),
    ("0003", "OUT銘柄", "銀行業", "B"),
)


def _write_project(tmp_path: Path, codes) -> Path:
    """config.yaml / 銘柄マスター / 株価キャッシュが揃った一時プロジェクトを作る。"""
    cfg_data = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    cfg_data["universe"] = {
        "watchlist_csv": "data/watchlist.csv",
        "stocks_csv": "data/stocks.csv",
        "stock_themes_csv": "data/stock_themes.csv",
        "asset_type_overrides": {},
    }
    cfg_data["data"]["cache_dir"] = "cache/prices"
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(cfg_data, allow_unicode=True), encoding="utf-8"
    )
    (tmp_path / "experimental.yaml").write_text(
        (ROOT / "experimental.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )

    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    rows = [r for r in STOCK_ROWS if r[0] in codes]
    data_dir.joinpath("stocks.csv").write_text(
        "code,name,sector,asset_type,enabled\n"
        + "".join(f"{c},{n},{s},stock,true\n" for c, n, s, _p in rows),
        encoding="utf-8",
    )
    data_dir.joinpath("stock_themes.csv").write_text(
        "code,theme,is_leader,watch_priority\n"
        + "".join(f"{c},テーマ{c},true,{p}\n" for c, _n, _s, p in rows),
        encoding="utf-8",
    )

    cfg = load_config(tmp_path / "config.yaml")
    for code in codes:
        save_prices(SERIES_BUILDERS[code]().build(), cfg)
    return tmp_path


@pytest.fixture
def project(tmp_path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return _write_project(tmp_path, ["0002", "0004", "0001", "0003"])


@pytest.fixture
def empty_project(tmp_path, monkeypatch) -> Path:
    """候補が 1 件も出ないプロジェクト（OUT のみ）。"""
    monkeypatch.chdir(tmp_path)
    return _write_project(tmp_path, ["0003"])


def invoke(*args):
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output + str(result.exception)
    return result


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def header_of(path: Path) -> list[str]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f).fieldnames or [])


def bundle_of(project: Path) -> Path:
    dirs = sorted((project / "output/chatgpt").iterdir())
    assert len(dirs) == 1, [p.name for p in dirs]
    return dirs[0]


def production_run(project: Path):
    """同じキャッシュに対する本番スクリーニング結果（比較の基準）。"""
    from swing_screener import screener
    from swing_screener.universe import load_universe

    cfg = load_config(project / "config.yaml")
    exp = load_experimental(project / "experimental.yaml")
    stocks = load_universe(cfg)
    price_map, _ = screener.load_price_map(stocks, cfg)
    return cfg, exp, price_map, run_screening(stocks, price_map, cfg, exp)


# --- candidates.csv --------------------------------------------------------------


def test_chatgpt_exportが3ファイルを作る(project):
    result = invoke("chatgpt-export")

    directory = bundle_of(project)
    assert directory.name == date.fromisoformat(directory.name).isoformat()
    for name in ("candidates.csv", "daily_bars.csv", "manifest.txt"):
        assert (directory / name).exists(), name
        assert f"output/chatgpt/{directory.name}/{name}" in result.output


def test_candidates_csvの列は契約どおり(project):
    invoke("chatgpt-export")
    header = header_of(bundle_of(project) / "candidates.csv")

    assert header == list(export_mod.CANDIDATE_COLUMNS)
    # CODEX の指定した必須項目が落ちていないこと
    for column in (
        "as_of_date", "code", "name", "sector", "theme", "watch_priority", "status",
        "open", "high", "low", "close", "volume",
        "ma25", "ma25_slope_pct", "close_above_ma25", "higher_high", "higher_low",
        "trend_is_uptrend", "trend_reason",
        "range_start", "range_end", "range_days", "range_lower", "range_upper",
        "range_width_pct", "range_position", "lower_distance_pct",
        "lower_reaction_count", "range_found", "range_reason",
        "previous_day_high", "entry_trigger", "entry_trigger_margin_pct",
        "initial_stop", "stop_distance_pct", "entry_reason",
        "volume_ratio", "volume_evaluation", "volume_reason",
    ):
        assert column in header, column


def test_OUTは含まれずENTRY_NEAR_RANGEだけが出る(project):
    invoke("chatgpt-export")
    rows = read_csv(bundle_of(project) / "candidates.csv")

    assert [r["code"] for r in rows] == ["0002", "0004", "0001"]
    assert [r["status"] for r in rows] == [STATUS_ENTRY, STATUS_NEAR, STATUS_RANGE]
    assert STATUS_OUT not in {r["status"] for r in rows}
    assert "0003" not in {r["code"] for r in rows}


def test_ENTRYとNEARはPRIMARY_RANGEはSECONDARY(project):
    invoke("chatgpt-export")
    groups = {r["status"]: r["candidate_group"] for r in read_csv(bundle_of(project) / "candidates.csv")}

    assert groups == {
        STATUS_ENTRY: "PRIMARY",
        STATUS_NEAR: "PRIMARY",
        STATUS_RANGE: "SECONDARY",
    }


def test_並び順は本番スクリーニングのものをそのまま使う(project):
    """新しい「おすすめ度」を作らず、`ScreenResult.sort_key` の順を保つこと。"""
    invoke("chatgpt-export")
    rows = read_csv(bundle_of(project) / "candidates.csv")

    _cfg, _exp, _price_map, run = production_run(project)
    expected = [r.stock.code for r in run.results if r.status != STATUS_OUT]

    assert [r["code"] for r in rows] == expected


def test_銘柄の重複がない(project):
    invoke("chatgpt-export")
    codes = [r["code"] for r in read_csv(bundle_of(project) / "candidates.csv")]

    assert len(codes) == len(set(codes))


def test_as_of_dateは全行同じ(project):
    invoke("chatgpt-export")
    rows = read_csv(bundle_of(project) / "candidates.csv")

    assert len({r["as_of_date"] for r in rows}) == 1
    assert rows[0]["as_of_date"] == bundle_of(project).name


def test_reason列は既存の判定理由をそのまま持つ(project):
    """explain / Judgement の文言を短縮も再解釈もしないこと。"""
    invoke("chatgpt-export")
    rows = {r["code"]: r for r in read_csv(bundle_of(project) / "candidates.csv")}

    _cfg, _exp, _price_map, run = production_run(project)
    entry = next(r for r in run.results if r.stock.code == "0002")

    for key_prefix, column in (
        ("trend", "trend_reason"),
        ("range", "range_reason"),
        ("volume", "volume_reason"),
    ):
        for j in entry.judgements:
            if j.key.split(".")[0] == key_prefix:
                assert f"{j.label}: {j.detail}" in rows["0002"][column]

    status_j = next(j for j in entry.judgements if j.key == "status.result")
    assert rows["0002"]["status_reason"] == status_j.detail
    assert status_j.detail in rows["0002"]["entry_reason"]
    # 改行を含む理由文が壊れずに往復すること（CSV の quote）
    assert "\n" in rows["0002"]["trend_reason"]


def test_entry_trigger_margin_pctは終値と前日高値の比(project):
    invoke("chatgpt-export")
    row = next(r for r in read_csv(bundle_of(project) / "candidates.csv") if r["code"] == "0002")

    close, prev_high = float(row["close"]), float(row["previous_day_high"])
    assert row["entry_trigger"] == row["previous_day_high"]
    assert float(row["entry_trigger_margin_pct"]) == pytest.approx(
        (close / prev_high - 1) * 100, abs=0.01
    )


# --- 本番判定との一致（§24 single source of truth） ---------------------------------


def test_csvの値は本番ScreenResultと一致する(project):
    invoke("chatgpt-export")
    rows = {r["code"]: r for r in read_csv(bundle_of(project) / "candidates.csv")}

    _cfg, _exp, price_map, run = production_run(project)
    for result in run.results:
        if result.status == STATUS_OUT:
            continue
        row = rows[result.stock.code]
        bar = price_map[result.stock.code].bars[-1]

        assert row["status"] == result.status
        assert float(row["close"]) == pytest.approx(result.latest_close)
        assert float(row["open"]) == pytest.approx(bar.open)
        assert float(row["high"]) == pytest.approx(bar.high)
        assert float(row["low"]) == pytest.approx(bar.low)
        assert int(row["volume"]) == bar.volume
        assert float(row["initial_stop"]) == pytest.approx(result.stop_price, abs=0.005)
        assert float(row["lower_distance_pct"]) == pytest.approx(
            result.distance_to_lower_pct, abs=0.005
        )
        assert float(row["ma25"]) == pytest.approx(result.trend.ma, abs=0.005)
        assert row["close_above_ma25"] == ("true" if result.trend.close_above_ma else "false")
        assert row["ma25_direction"] == result.trend.ma_direction
        assert row["trend_is_uptrend"] == ("true" if result.trend.is_uptrend else "false")
        assert float(row["range_lower"]) == pytest.approx(result.range_.lower, abs=0.005)
        assert float(row["range_upper"]) == pytest.approx(result.range_.upper, abs=0.005)
        assert int(row["range_days"]) == result.range_.days
        assert row["range_start"] == result.range_.start_date.isoformat()
        assert row["range_end"] == result.range_.end_date.isoformat()
        assert int(row["lower_reaction_count"]) == result.range_.lower_touch_count
        assert row["rebound_confirmed"] == ("true" if result.rebound.confirmed else "false")
        assert float(row["previous_day_high"]) == pytest.approx(result.rebound.prev_high)
        assert row["volume_evaluation"] == result.volume.state


def test_export側でレンジやENTRYを再判定していない():
    """export モジュールがルール実装を import していないことを静的に検査する。"""
    from tests.test_production_isolation import code_tokens, imported_modules

    path = ROOT / "src" / "swing_screener" / "chatgpt_export.py"
    imports = imported_modules(path)

    assert not any("rules" in m.split(".") for m in imports), sorted(imports)
    for forbidden in ("detect_range", "evaluate_range", "evaluate_trend", "evaluate_rebound",
                      "classify", "screen_one", "run_screening", "summarize_volume"):
        assert not any(forbidden in m for m in imports), forbidden

    # しきい値の再実装も禁止（本番の値をコピーして持たない）
    tokens = code_tokens(path)
    for forbidden in ("max_position_in_range", "lower_threshold_pct", "min_lower_touches",
                      "break_tolerance_pct", "min_quality", "buffer_pct", "contract_ratio"):
        assert not any(forbidden in t for t in tokens), forbidden


def test_本番の確定パラメータが変わっていない():
    """v1 を固定したままの自動化であること（config / experimental を触っていない）。"""
    cfg = load_config(ROOT / "config.yaml")
    exp = load_experimental(ROOT / "experimental.yaml")

    assert (float(cfg.price_filter.min), float(cfg.price_filter.max)) == (2000.0, 7000.0)
    assert int(cfg.ma.period) == 25
    assert (int(cfg.range.min_days), int(cfg.range.max_days)) == (3, 10)
    assert int(cfg.range.min_lower_touches) == 2
    assert float(cfg.stop.buffer_pct) == 0.005
    assert float(exp.get("near.max_position_in_range")) == 0.65


def test_exportしても本番スクリーニング結果は変わらない(project):
    """export の前後で判定が一致すること（読み取り専用であることの確認）。"""
    _cfg, _exp, _price_map, before = production_run(project)
    invoke("chatgpt-export")
    _cfg2, _exp2, _price_map2, after = production_run(project)

    assert [(r.stock.code, r.status, r.stop_price) for r in before.results] == [
        (r.stock.code, r.status, r.stop_price) for r in after.results
    ]


# --- daily_bars.csv --------------------------------------------------------------


def test_daily_barsはlong_formatで候補全銘柄を含む(project):
    invoke("chatgpt-export")
    rows = read_csv(bundle_of(project) / "daily_bars.csv")

    assert header_of(bundle_of(project) / "daily_bars.csv") == list(export_mod.BAR_COLUMNS)
    candidates = [r["code"] for r in read_csv(bundle_of(project) / "candidates.csv")]
    assert {r["code"] for r in rows} == set(candidates)
    assert "0003" not in {r["code"] for r in rows}  # OUT の日足は出さない

    # 銘柄は candidates.csv の並び、各銘柄は日付昇順
    order: list[str] = []
    for r in rows:
        if not order or order[-1] != r["code"]:
            order.append(r["code"])
    assert order == candidates
    for code in candidates:
        dates = [r["date"] for r in rows if r["code"] == code]
        assert dates == sorted(dates)


def test_daily_barsは各銘柄70営業日まで(project):
    invoke("chatgpt-export")
    rows = read_csv(bundle_of(project) / "daily_bars.csv")

    per_code: dict[str, int] = {}
    for r in rows:
        per_code[r["code"]] = per_code.get(r["code"], 0) + 1
    assert set(per_code.values()) == {export_mod.DEFAULT_BAR_LOOKBACK_DAYS}

    # 最新の足は as_of で days_ago=0
    as_of = bundle_of(project).name
    latest = [r for r in rows if r["days_ago"] == "0"]
    assert {r["date"] for r in latest} == {as_of}
    assert len(latest) == len(per_code)


def test_足が70本未満なら取得できる全期間を出す(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_project(tmp_path, ["0001"])
    cfg = load_config(tmp_path / "config.yaml")
    short = uptrend_with_range(trend_days=59, range_days=6, touch_days=(0, 1), code="0001").build()
    assert len(short.bars) == 65
    save_prices(short, cfg)

    invoke("chatgpt-export")
    rows = read_csv(bundle_of(tmp_path) / "daily_bars.csv")
    assert len(rows) == 65


def test_daily_barsは生の日足のままで判定結果を含まない(project):
    """当時存在しなかった情報（現在のレンジ判定など）を過去行へ入れないこと。"""
    invoke("chatgpt-export")
    header = header_of(bundle_of(project) / "daily_bars.csv")

    for forbidden in ("status", "range_lower", "range_upper", "range_position",
                      "initial_stop", "lower_distance_pct", "candidate_group"):
        assert forbidden not in header, forbidden


def test_daily_barsのma25は本番と同じ計算(project):
    from swing_screener.indicators.ma import calc_ma_series

    invoke("chatgpt-export")
    rows = [r for r in read_csv(bundle_of(project) / "daily_bars.csv") if r["code"] == "0002"]

    _cfg, _exp, price_map, _run = production_run(project)
    bars = list(price_map["0002"].bars)
    ma = calc_ma_series(bars, 25)
    # MA25 未確定の足は空欄。推定値で埋めない。
    expected = ["" if v is None else f"{v:.2f}" for v in ma[-70:]]

    assert [r["ma25"] for r in rows] == expected
    assert "" in expected and any(e for e in expected)


def test_daily_barsの日付は未来にならない(project):
    invoke("chatgpt-export")
    as_of = date.fromisoformat(bundle_of(project).name)
    rows = read_csv(bundle_of(project) / "daily_bars.csv")

    assert all(date.fromisoformat(r["date"]) <= as_of for r in rows)


# --- manifest.txt ----------------------------------------------------------------


def test_manifestが件数と出所を持つ(project):
    invoke("chatgpt-export")
    directory = bundle_of(project)
    text = (directory / "manifest.txt").read_text(encoding="utf-8")
    meta = dict(
        line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#")
    )

    assert meta["market_data_as_of"] == directory.name
    assert meta["candidate_count"] == "3"
    assert meta["entry_candidate_count"] == "1"
    assert meta["near_count"] == "1"
    assert meta["range_count"] == "1"
    assert meta["out_count"] == "1"
    assert meta["universe_count"] == "4"
    assert meta["bars_count"] == str(len(read_csv(directory / "daily_bars.csv")))
    assert meta["bar_lookback_days"] == "70"
    assert meta["config_sha256"] and meta["experimental_sha256"]
    assert "git_commit_sha" in meta
    assert meta["generated_at"]

    assert export_mod.DISCLAIMER_EN in text
    assert export_mod.DISCLAIMER_JA in text


def test_manifestのgit_shaは環境変数を使う(project, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "0123456789abcdef")
    invoke("chatgpt-export")
    text = (bundle_of(project) / "manifest.txt").read_text(encoding="utf-8")

    assert "git_commit_sha=0123456789abcdef" in text


# --- 候補 0 件 ---------------------------------------------------------------------


def test_候補0件でもヘッダー付きで生成する(empty_project):
    """0 件は異常ではない。条件を緩めて候補を作ってはいけない。"""
    result = invoke("chatgpt-export")

    directory = bundle_of(empty_project)
    assert header_of(directory / "candidates.csv") == list(export_mod.CANDIDATE_COLUMNS)
    assert header_of(directory / "daily_bars.csv") == list(export_mod.BAR_COLUMNS)
    assert read_csv(directory / "candidates.csv") == []
    assert read_csv(directory / "daily_bars.csv") == []

    text = (directory / "manifest.txt").read_text(encoding="utf-8")
    assert "candidate_count=0" in text
    assert "bars_count=0" in text
    assert "候補 0件" in result.output

    # 検査も通ること（0件は失敗ではない）
    invoke("chatgpt-validate")


# --- 市場休日 / 冪等性 --------------------------------------------------------------


def test_market_checkは新しい営業日を検出する(project):
    result = invoke("market-check", "--format", "github")
    lines = dict(line.split("=", 1) for line in result.output.strip().splitlines())

    assert lines["has_new_data"] == "true"
    assert lines["last_export_date"] == ""
    assert lines["market_date"]


def test_market_checkは書き出し済みなら新しいデータなしと言う(project):
    """市場休日は株価の日付が進まないので has_new_data=false になる。"""
    invoke("chatgpt-export")
    result = invoke("market-check", "--format", "github")
    lines = dict(line.split("=", 1) for line in result.output.strip().splitlines())

    assert lines["has_new_data"] == "false"
    assert lines["market_date"] == lines["last_export_date"] == bundle_of(project).name

    human = invoke("market-check")
    assert "No new market data" in human.output


def test_skip_existingは既存bundleを上書きしない(project):
    invoke("chatgpt-export")
    directory = bundle_of(project)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}

    result = invoke("chatgpt-export", "--skip-existing")

    assert "既にあります" in result.output
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before


def test_同じ日に再実行しても内容が変わらない(project):
    invoke("chatgpt-export")
    directory = bundle_of(project)
    first_candidates = (directory / "candidates.csv").read_bytes()
    first_bars = (directory / "daily_bars.csv").read_bytes()
    first_manifest = (directory / "manifest.txt").read_text(encoding="utf-8")

    invoke("chatgpt-export")

    assert (directory / "candidates.csv").read_bytes() == first_candidates
    assert (directory / "daily_bars.csv").read_bytes() == first_bars
    # manifest は生成時刻の行だけが動く（UTC 表記と市場時間帯表記の 2 行）
    def without_timestamp(text: str) -> list[str]:
        return [ln for ln in text.splitlines() if not ln.startswith("generated_at")]

    assert without_timestamp((directory / "manifest.txt").read_text(encoding="utf-8")) == (
        without_timestamp(first_manifest)
    )
    assert len(list((project / "output/chatgpt").iterdir())) == 1


def test_dailyを二度実行しても記録が二重にならない(project):
    """journal / signals は自動実行の永続対象。重複させない。"""
    invoke("daily")
    invoke("chatgpt-export")
    invoke("daily")
    invoke("chatgpt-export")

    snapshots = list((project / "data/journal/daily").glob("*.csv"))
    assert len(snapshots) == 1
    signals = read_csv(project / "data/journal/signals.csv")
    assert [r["code"] for r in signals] == ["0002"]
    assert len(list((project / "output/chatgpt").iterdir())) == 1


def test_date指定で過去の営業日を書き出せる(project):
    """`--date` は系列を切ってから本番判定へ渡す（未来の足を混ぜない）。"""
    _cfg, _exp, price_map, _run = production_run(project)
    bars = price_map["0002"].bars
    target = bars[-3].date

    invoke("chatgpt-export", "--date", target.isoformat())

    directory = project / "output/chatgpt" / target.isoformat()
    assert directory.exists()
    rows = read_csv(directory / "candidates.csv")
    assert all(r["as_of_date"] == target.isoformat() for r in rows)
    bar_rows = read_csv(directory / "daily_bars.csv")
    assert all(date.fromisoformat(r["date"]) <= target for r in bar_rows)

    # 本番 screen_one を同じ切り方で呼んだ結果と一致すること
    from swing_screener.models import PriceSeries
    from swing_screener.screener import screen_one

    cfg = load_config(project / "config.yaml")
    exp = load_experimental(project / "experimental.yaml")
    for row in rows:
        sliced = PriceSeries(
            code=row["code"],
            bars=tuple(b for b in price_map[row["code"]].bars if b.date <= target),
        )
        stock = make_stock(code=row["code"], name=row["name"], sector=row["sector"])
        expected = screen_one(stock, sliced, cfg, exp)
        assert row["status"] == expected.status
        assert float(row["close"]) == pytest.approx(expected.latest_close)


# --- 検査（validation） -------------------------------------------------------------


def test_validateは正常なbundleを通す(project):
    invoke("chatgpt-export")
    result = invoke("chatgpt-validate")
    assert "検査OK" in result.output


def _tamper(path: Path, transform) -> None:
    rows = read_csv(path)
    header = header_of(path)
    rows = transform(rows)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def _errors(project: Path) -> list[str]:
    return export_mod.validate_bundle(bundle_of(project), run=production_run(project)[3])


def test_validateはOUTの混入を検出する(project):
    invoke("chatgpt-export")

    def add_out(rows):
        row = dict(rows[0])
        row.update({"code": "0003", "status": STATUS_OUT})
        return rows + [row]

    _tamper(bundle_of(project) / "candidates.csv", add_out)
    errors = _errors(project)
    assert any("想定外の status" in e for e in errors)


def test_validateは重複コードを検出する(project):
    invoke("chatgpt-export")
    _tamper(bundle_of(project) / "candidates.csv", lambda rows: rows + [dict(rows[0])])

    assert any("重複コード" in e for e in _errors(project))


def test_validateはas_of_dateの不一致を検出する(project):
    invoke("chatgpt-export")

    def shift(rows):
        rows[1]["as_of_date"] = (date.fromisoformat(rows[1]["as_of_date"]) - timedelta(days=1)).isoformat()
        return rows

    _tamper(bundle_of(project) / "candidates.csv", shift)
    assert any("as_of_date が揃っていない" in e for e in _errors(project))


def test_validateはレンジ上下の逆転を検出する(project):
    invoke("chatgpt-export")

    def swap(rows):
        rows[0]["range_lower"], rows[0]["range_upper"] = (
            rows[0]["range_upper"],
            rows[0]["range_lower"],
        )
        return rows

    _tamper(bundle_of(project) / "candidates.csv", swap)
    assert any("range_lower" in e and "range_upper" in e for e in _errors(project))


def test_validateは初期STOPの不一致を検出する(project):
    """本番計算と違う値が CSV に入っていたら落とす（single source of truth）。"""
    invoke("chatgpt-export")

    def bump(rows):
        rows[0]["initial_stop"] = "1.00"
        return rows

    _tamper(bundle_of(project) / "candidates.csv", bump)
    assert any("initial_stop が本番と違う" in e for e in _errors(project))


def test_validateは並び順の崩れを検出する(project):
    invoke("chatgpt-export")
    _tamper(bundle_of(project) / "candidates.csv", lambda rows: list(reversed(rows)))

    errors = _errors(project)
    assert any("ENTRY_CANDIDATE → NEAR → RANGE" in e for e in errors)


def test_validateはヘッダーの違いを検出する(project):
    invoke("chatgpt-export")
    path = bundle_of(project) / "candidates.csv"
    path.write_text("code,status\n0002,NEAR\n", encoding="utf-8")

    assert any("ヘッダーが想定と違う" in e for e in _errors(project))


def test_validateは候補にない銘柄の日足を検出する(project):
    invoke("chatgpt-export")

    def add_unknown(rows):
        row = dict(rows[0])
        row["code"] = "9999"
        return rows + [row]

    _tamper(bundle_of(project) / "daily_bars.csv", add_unknown)
    assert any("candidates.csv にないコード" in e for e in _errors(project))


def test_validateは70本超と未来日付とOHLC破綻を検出する(project):
    invoke("chatgpt-export")
    path = bundle_of(project) / "daily_bars.csv"

    def break_bars(rows):
        extra = dict(rows[0])
        extra["date"] = "2099-01-04"
        rows[1]["low"] = str(float(rows[1]["high"]) + 100)
        return rows + [extra]

    _tamper(path, break_bars)
    errors = _errors(project)
    assert any("未来の日付" in e for e in errors)
    assert any("より大きい" in e for e in errors)
    assert any("上限 70本 を超えている" in e for e in errors)


def test_validateは候補の日足欠落を検出する(project):
    invoke("chatgpt-export")
    _tamper(
        bundle_of(project) / "daily_bars.csv",
        lambda rows: [r for r in rows if r["code"] != "0002"],
    )

    assert any("日足がない候補" in e for e in _errors(project))


def test_validateはmanifestの件数ずれを検出する(project):
    invoke("chatgpt-export")
    manifest = bundle_of(project) / "manifest.txt"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("candidate_count=3", "candidate_count=99"),
        encoding="utf-8",
    )

    assert any("candidate_count" in e for e in _errors(project))


def test_validateはファイル欠落を検出する(project):
    invoke("chatgpt-export")
    (bundle_of(project) / "daily_bars.csv").unlink()

    assert any("daily_bars.csv がない" in e for e in _errors(project))


def test_検査に落ちたらCLIは失敗する(project):
    """中途半端な CSV を「その日の正常な最新データ」として残さない。"""
    invoke("chatgpt-export")
    _tamper(bundle_of(project) / "candidates.csv", lambda rows: rows + [dict(rows[0])])

    result = runner.invoke(app, ["chatgpt-validate"])
    assert result.exit_code == 1
    assert "検査に失敗しました" in result.output


def test_株価キャッシュがないと失敗する(tmp_path, monkeypatch):
    """取得が全滅した日を「市場休日」として静かに成功させない。"""
    monkeypatch.chdir(tmp_path)
    _write_project(tmp_path, ["0002"])
    for p in (tmp_path / "cache/prices").glob("*.csv"):
        p.unlink()

    result = runner.invoke(app, ["chatgpt-export"])
    assert result.exit_code == 1
    assert "株価キャッシュ" in result.output

    check = runner.invoke(app, ["market-check", "--format", "github"])
    assert check.exit_code == 1
    assert "has_new_data" not in check.output


# --- GitHub Actions workflow --------------------------------------------------------


@pytest.fixture(scope="module")
def workflow() -> dict:
    assert WORKFLOW.exists(), f"{WORKFLOW} がない"
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # YAML 1.1 では `on:` が真偽値キーとして読まれる
    data["on"] = data.get("on", data.get(True))
    return data


def test_workflowは平日16時10分JSTに動く(workflow):
    schedules = workflow["on"]["schedule"]
    assert [s["cron"] for s in schedules] == ["10 7 * * 1-5"]  # 07:10 UTC = 16:10 JST


def test_workflowは手動実行できる(workflow):
    assert "workflow_dispatch" in workflow["on"]


def test_workflowの権限は最小限(workflow):
    # data branch への push にだけ contents: write が要る
    assert workflow["permissions"] == {"contents": "write"}


def test_workflowは同時実行しない(workflow):
    assert workflow["concurrency"]["group"]
    assert workflow["concurrency"]["cancel-in-progress"] is False


def test_workflowは市場休日をcronへ書かない():
    """祝日カレンダーを焼き込むと毎年壊れる。判定は market-check に任せる。"""
    text = WORKFLOW.read_text(encoding="utf-8")
    for holiday_ish in ("01-01", "12-31", "golden", "obon", "holiday_dates"):
        assert holiday_ish not in text.lower()
    assert "market-check" in text


def test_workflowはfetchとdailyとexportをこの順で実行する(workflow):
    steps = workflow["jobs"]["screen"]["steps"]
    commands = [s.get("run", "") for s in steps]

    order = [
        next(i for i, c in enumerate(commands) if "swing fetch" in c),
        next(i for i, c in enumerate(commands) if "market-check" in c),
        next(i for i, c in enumerate(commands) if "swing daily" in c),
        next(i for i, c in enumerate(commands) if "swing chatgpt-export" in c),
        next(i for i, c in enumerate(commands) if "swing chatgpt-validate" in c),
    ]
    assert order == sorted(order)


def test_新しい営業日のときだけ生成する(workflow):
    """市場休日に重複 bundle を作らないこと。"""
    steps = workflow["jobs"]["screen"]["steps"]
    guarded = [
        s for s in steps
        if "swing daily" in s.get("run", "")
        or "chatgpt-export" in s.get("run", "")
        or s.get("uses", "").startswith("actions/upload-artifact")
    ]
    assert len(guarded) == 3
    for step in guarded:
        assert step["if"] == "steps.market.outputs.has_new_data == 'true'"


def test_artifactに3ファイルが入る(workflow):
    steps = workflow["jobs"]["screen"]["steps"]
    upload = next(s for s in steps if s.get("uses", "").startswith("actions/upload-artifact"))

    assert upload["with"]["name"] == (
        "chatgpt-market-data-${{ steps.market.outputs.market_date }}"
    )
    assert upload["with"]["path"] == "output/chatgpt/${{ steps.market.outputs.market_date }}/"
    assert upload["with"]["if-no-files-found"] == "error"


def test_journalとbundleがdata_branchへ永続化される(workflow):
    """runner は使い捨て。artifact だけを唯一の保存先にしない。"""
    steps = workflow["jobs"]["screen"]["steps"]
    restore = next(s for s in steps if "Restore" in s.get("name", ""))
    persist = next(s for s in steps if "Persist" in s.get("name", ""))

    for step in (restore, persist):
        assert "data/journal" in step["run"]
        assert "output/chatgpt" in step["run"]
        assert "DATA_BRANCH" in step["run"]

    assert workflow["env"]["DATA_BRANCH"]
    assert workflow["env"]["DATA_BRANCH"] != "main"
    assert "git push" in persist["run"]
    # default branch を日次 commit で汚さない
    assert "origin main" not in persist["run"]
    assert "origin \"$DATA_BRANCH\"" in persist["run"]


def test_workflowはtrades_csvを触らない(workflow):
    """実際の売買は手入力データ。自動処理と混ぜない。"""
    steps = workflow["jobs"]["screen"]["steps"]
    persist = next(s for s in steps if "Persist" in s.get("name", ""))
    assert "trades" not in persist["run"].replace("trades.csv は入れない", "")

    guard = next(s for s in steps if "trades.csv" in s.get("name", ""))
    assert "exit 1" in guard["run"]

    text = WORKFLOW.read_text(encoding="utf-8")
    for command in ("swing buy", "swing sell", "forward-export"):
        assert command not in text


def test_workflowはチャートを作らない():
    text = WORKFLOW.read_text(encoding="utf-8")
    for command in ("swing chart", "trade-chart", ".png"):
        assert command not in text


def test_workflowはActions_cacheを正本にしない():
    """cache を履歴の唯一の正本にしない（消えても復元できる形にする）。"""
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["screen"]["steps"]
    assert not any(s.get("uses", "").startswith("actions/cache") for s in steps)
