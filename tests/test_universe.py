"""universe.py のテスト (DESIGN.md §4 / §12)。

重複銘柄の1レコードへの集約、複数テーマの保持、ETF判定（sector/name/override）、
display_priority集約（A>B>C）、既存enabledの保持を検証する。

code は文字列として扱う契約（"200A" のような英数字コードで int キャストされ
崩れないこと）が最重要なので、明示的に確認する。
"""

from __future__ import annotations

import csv
from pathlib import Path

from swing_screener.config import Params
from swing_screener.universe import load_universe, normalize_watchlist

# code, name, sector, theme, is_leader, watch_priority
WATCHLIST_ROWS = [
    ("7011", "三菱重工", "機械", "重工・防衛", "true", "A"),
    ("7011", "三菱重工", "機械", "宇宙・衛星", "true", "B"),
    ("6701", "NEC", "電気機器", "AIインフラ・通信", "false", "A"),
    ("6701", "NEC", "電気機器", "宇宙・衛星", "true", "B"),
    ("200A", "NEXT FUNDS 日経半導体株指数連動型上場投信", "ETF", "半導体・生成AI", "false", "A"),
    ("2644", "GX 半導体関連-日本株式 ETF", "その他", "半導体・生成AI", "false", "B"),
    ("1234", "テストファンド", "その他", "テスト", "false", "C"),
    ("9999", "サンプル工業", "機械", "テストB", "false", "B"),
    # 同一code内で name/sector が食い違うケース → 警告を出しつつ最初の出現を採用
    ("9999", "サンプル工業改", "機械化学", "テストC", "false", "C"),
]


def _write_watchlist(path: Path, rows=WATCHLIST_ROWS) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["code", "name", "sector", "theme", "is_leader", "watch_priority"])
        writer.writerows(rows)


def _make_cfg(tmp_path: Path, overrides: dict | None = None, rows=WATCHLIST_ROWS) -> Params:
    watchlist = tmp_path / "watchlist.csv"
    _write_watchlist(watchlist, rows)
    return Params(
        {
            "universe": {
                "watchlist_csv": str(watchlist),
                "stocks_csv": str(tmp_path / "stocks.csv"),
                "stock_themes_csv": str(tmp_path / "stock_themes.csv"),
                "asset_type_overrides": overrides or {},
            }
        }
    )


def test_normalize_merges_duplicate_codes_into_one_stock(tmp_path):
    cfg = _make_cfg(tmp_path)
    stocks, _warnings = normalize_watchlist(cfg)

    codes = [s.code for s in stocks]
    assert codes.count("7011") == 1
    assert codes.count("6701") == 1

    mitsubishi = next(s for s in stocks if s.code == "7011")
    assert {t.theme for t in mitsubishi.themes} == {"重工・防衛", "宇宙・衛星"}


def test_code_is_kept_as_string_not_int(tmp_path):
    cfg = _make_cfg(tmp_path)
    stocks, _warnings = normalize_watchlist(cfg)

    codes = {s.code for s in stocks}
    assert "200A" in codes
    assert all(isinstance(s.code, str) for s in stocks)

    # 書き出したCSVの生データも文字列のまま（先頭ゼロ等が落ちない）ことを確認
    content = Path(cfg.universe.stocks_csv).read_text(encoding="utf-8")
    assert "200A" in content


def test_asset_type_detected_from_sector_or_name(tmp_path):
    cfg = _make_cfg(tmp_path)
    stocks, _warnings = normalize_watchlist(cfg)
    by_code = {s.code: s for s in stocks}

    assert by_code["200A"].asset_type == "etf"  # sector == "ETF"
    assert by_code["2644"].asset_type == "etf"  # name に "ETF" を含む
    assert by_code["7011"].asset_type == "stock"
    assert by_code["1234"].asset_type == "stock"  # override なしなら通常判定


def test_asset_type_override_by_code(tmp_path):
    cfg = _make_cfg(tmp_path, overrides={"1234": "etf"})
    stocks, _warnings = normalize_watchlist(cfg)
    by_code = {s.code: s for s in stocks}

    assert by_code["1234"].asset_type == "etf"
    # override対象外は通常判定のまま
    assert by_code["7011"].asset_type == "stock"


def test_display_priority_takes_highest_across_themes(tmp_path):
    cfg = _make_cfg(tmp_path)
    stocks, _warnings = normalize_watchlist(cfg)
    by_code = {s.code: s for s in stocks}

    # 7011: A(重工・防衛) + B(宇宙・衛星) → A
    assert by_code["7011"].display_priority == "A"
    # 6701: A(AIインフラ・通信) + B(宇宙・衛星) → A
    assert by_code["6701"].display_priority == "A"


def test_name_sector_mismatch_keeps_first_occurrence_and_warns(tmp_path):
    cfg = _make_cfg(tmp_path)
    stocks, warnings = normalize_watchlist(cfg)
    by_code = {s.code: s for s in stocks}

    assert by_code["9999"].name == "サンプル工業"
    assert by_code["9999"].sector == "機械"
    assert any("9999" in w for w in warnings)


def test_enabled_is_preserved_on_re_normalize(tmp_path):
    cfg = _make_cfg(tmp_path)
    normalize_watchlist(cfg)

    # 人間が stocks.csv を直接編集して 7011 を無効化したと仮定する
    stocks_path = Path(cfg.universe.stocks_csv)
    with stocks_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        if row["code"] == "7011":
            row["enabled"] = "false"
    with stocks_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["code", "name", "sector", "asset_type", "enabled"])
        writer.writeheader()
        writer.writerows(rows)

    stocks, _warnings = normalize_watchlist(cfg)
    by_code = {s.code: s for s in stocks}

    assert by_code["7011"].enabled is False
    assert by_code["6701"].enabled is True  # 他の銘柄は既定値のまま


def test_load_universe_runs_normalize_when_missing(tmp_path):
    cfg = _make_cfg(tmp_path)
    assert not Path(cfg.universe.stocks_csv).exists()

    stocks = load_universe(cfg)

    assert Path(cfg.universe.stocks_csv).exists()
    assert Path(cfg.universe.stock_themes_csv).exists()
    codes = {s.code for s in stocks}
    assert {"7011", "6701", "200A"} <= codes


def test_load_universe_round_trip_preserves_themes_and_priority(tmp_path):
    cfg = _make_cfg(tmp_path)
    normalize_watchlist(cfg)

    stocks = load_universe(cfg)
    mitsubishi = next(s for s in stocks if s.code == "7011")

    assert {t.theme for t in mitsubishi.themes} == {"重工・防衛", "宇宙・衛星"}
    assert mitsubishi.display_priority == "A"
    assert mitsubishi.is_leader_any is True
