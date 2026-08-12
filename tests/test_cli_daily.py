"""毎日の運用フロー（swing daily / buy / sell / forward-export）の結合テスト。

合成株価だけで完結する一時プロジェクトを組み、**ネットワークに触れずに**
1 日分の流れを通す。ここで守りたいのは次の 3 点:

    1. `swing daily` が記録まで一度に済ませること
    2. 二度実行しても履歴が二重にならないこと
    3. `swing buy` が ENTRY候補履歴から初期STOP・レンジを補完すること
       （手入力させると「買ったときの根拠」がすぐ失われる）
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml
from tests.conftest import SeriesBuilder, uptrend_with_range
from typer.testing import CliRunner

from swing_screener.cli import app
from swing_screener.data.cache import save_prices

runner = CliRunner()

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path, monkeypatch):
    """config.yaml / 銘柄マスター / 株価キャッシュ が揃った一時プロジェクト。"""
    monkeypatch.chdir(tmp_path)

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
    data_dir.mkdir()
    (data_dir / "stocks.csv").write_text(
        "code,name,sector,asset_type,enabled\n"
        "0002,ENTRY銘柄,電気機器,stock,true\n"
        "0003,OUT銘柄,機械,stock,true\n",
        encoding="utf-8",
    )
    (data_dir / "stock_themes.csv").write_text(
        "code,theme,is_leader,watch_priority\n"
        "0002,テストテーマ,true,A\n"
        "0003,テストテーマ,false,B\n",
        encoding="utf-8",
    )

    from swing_screener.config import load_config

    cfg = load_config(tmp_path / "config.yaml")
    # 株価は 2,000〜7,000円 の売買対象レンジに収まるよう組む
    save_prices(
        uptrend_with_range(trend_end=5000.0, range_days=6, touch_days=(1, 4), code="0002").build(),
        cfg,
    )
    save_prices(SeriesBuilder(code="0003").downtrend_to(80, 3000, 15).build(), cfg)
    return tmp_path


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def invoke(*args):
    result = runner.invoke(app, list(args))
    assert result.exit_code == 0, result.output + str(result.exception)
    return result


# --- daily ----------------------------------------------------------------------


def test_daily_screens_and_records(project):
    result = invoke("daily")

    assert "ENTRY_CANDIDATE: 1" in result.output
    assert "0002 ENTRY銘柄" in result.output
    assert "保有銘柄の登録はありません" in result.output

    snapshots = list((project / "data/journal/daily").glob("*.csv"))
    assert len(snapshots) == 1
    assert {r["code"] for r in read_csv(snapshots[0])} == {"0002", "0003"}

    signals = read_csv(project / "data/journal/signals.csv")
    assert [r["code"] for r in signals] == ["0002"]
    assert float(signals[0]["initial_stop"]) > 0

    assert (project / "output").glob("screening_*.json")


def test_daily_is_idempotent(project):
    invoke("daily")
    second = invoke("daily")

    assert "上書きしません" in second.output
    assert "ENTRY候補の履歴追加: なし" in second.output
    assert len(read_csv(project / "data/journal/signals.csv")) == 1


def test_daily_does_not_touch_the_network(project, monkeypatch):
    """`daily` は必ずキャッシュだけで動くこと。"""
    import swing_screener.data.yfinance_provider as provider

    def boom(*_args, **_kwargs):
        raise AssertionError("daily がネットワークへアクセスした")

    monkeypatch.setattr(provider.YfinanceProvider, "fetch", boom)
    invoke("daily")


# --- buy / sell -------------------------------------------------------------------


def test_buy_fills_stop_and_range_from_signal_history(project):
    invoke("daily")
    signal = read_csv(project / "data/journal/signals.csv")[0]

    invoke("buy", "0002", "--price", "5000", "--qty", "100")

    trade = read_csv(project / "data/trades.csv")[0]
    assert trade["code"] == "0002"
    assert trade["name"] == "ENTRY銘柄"
    assert trade["quantity"] == "100"
    # 台帳は末尾の 0 を落として書くので、値として一致していればよい
    assert float(trade["initial_stop"]) == float(signal["initial_stop"])
    assert float(trade["original_range_lower"]) == float(signal["range_lower"])
    assert float(trade["original_range_upper"]) == float(signal["range_upper"])
    assert trade["signal_date"] == signal["signal_date"]


def test_buy_explicit_values_win_over_history(project):
    invoke("daily")
    invoke("buy", "0002", "--price", "5000", "--stop", "4800", "--lower", "4850",
           "--upper", "5200", "--reason", "手入力")

    trade = read_csv(project / "data/trades.csv")[0]
    assert trade["initial_stop"] == "4800"
    assert trade["original_range_lower"] == "4850"
    assert trade["entry_reason"] == "手入力"


def test_buy_rejects_second_open_position(project):
    invoke("daily")
    invoke("buy", "0002", "--price", "5000")

    result = runner.invoke(app, ["buy", "0002", "--price", "5100"])
    assert result.exit_code == 1
    assert "既に保有中" in result.output
    assert len(read_csv(project / "data/trades.csv")) == 1


def test_daily_lists_holdings_with_review_level(project):
    invoke("daily")
    invoke("buy", "0002", "--price", "5000", "--qty", "100", "--stop", "999999")

    result = invoke("daily")
    assert "[SCENARIO_RISK] 0002" in result.output
    assert "初期STOP以下" in result.output
    assert "売り判定" not in result.output  # 断定しない


def test_sell_records_exit_and_saves_charts(project):
    invoke("daily")
    invoke("buy", "0002", "--price", "5000", "--qty", "100")
    result = invoke("sell", "0002", "--price", "5200", "--reason", "profit_protection")

    trade = read_csv(project / "data/trades.csv")[0]
    assert trade["exit_price"] == "5200"
    assert trade["exit_reason"] == "profit_protection"
    assert "+4.0%" in result.output

    charts = sorted(p.name for p in (project / "output/trades").glob("*.png"))
    assert len(charts) == 2
    assert any(c.endswith("_entry.png") for c in charts)
    assert any(c.endswith("_exit.png") for c in charts)


def test_sell_unknown_code_fails_cleanly(project):
    result = runner.invoke(app, ["sell", "9999", "--price", "100"])
    assert result.exit_code == 1
    assert "保有中ではありません" in result.output


# --- holdings / forward-export -------------------------------------------------------


def test_holdings_never_says_sell(project):
    invoke("daily")
    invoke("buy", "0002", "--price", "5000", "--qty", "100")

    result = invoke("holdings")
    assert "保有 1件" in result.output
    assert "チャートを見るべき理由" in result.output
    for word in ("SELL", "TAKE PROFIT", "売却してください", "利確しろ"):
        assert word not in result.output


def test_forward_export_marks_purchased(project):
    invoke("daily")
    result = invoke("forward-export")
    rows = read_csv(project / "data/journal/forward_review.csv")
    assert len(rows) == 1
    assert rows[0]["purchased"] == "false"
    assert "購入済み 0件" in result.output

    invoke("buy", "0002", "--price", "5000")
    invoke("forward-export")
    rows = read_csv(project / "data/journal/forward_review.csv")
    assert rows[0]["purchased"] == "true"
