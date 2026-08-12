"""監視銘柄マスターの正規化・ロード (DESIGN.md §4 / §12.5)。

`data/watchlist.csv`（人間が編集する唯一の銘柄ファイル。同一銘柄が複数テーマに
重複登場する）を読み、以下2ファイルへ正規化する。

- stocks.csv       : code でユニークな銘柄マスター
- stock_themes.csv : 銘柄×テーマの多対多（is_leader / watch_priority はテーマ単位）

重要な契約: code は "200A" "464A" のような英数字コードがあるため、
**常に文字列として扱う**（int キャストは絶対にしない。先頭ゼロ落ち・型崩れの原因）。
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from swing_screener.models import Stock, ThemeTag

_STOCKS_FIELDNAMES = ["code", "name", "sector", "asset_type", "enabled"]
_THEMES_FIELDNAMES = ["code", "theme", "is_leader", "watch_priority"]


# --- 補助関数 -----------------------------------------------------------


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    return text in ("true", "1", "yes")


def _bool_to_csv(value: bool) -> str:
    return "true" if value else "false"


def _overrides_dict(cfg) -> dict[str, str]:
    """cfg.universe.asset_type_overrides を code(str) -> asset_type(str) の
    plain dict にして返す（未設定なら空dict）。YAMLでキーを数値として
    unquoted 記述された場合でも str に揃える。"""
    raw = cfg.universe.get("asset_type_overrides", {})
    if hasattr(raw, "as_dict"):
        raw = raw.as_dict()
    return {str(k): str(v) for k, v in dict(raw).items()}


def _detect_asset_type(name: str, sector: str, code: str, overrides: dict[str, str]) -> str:
    """asset_type ("stock" | "etf") を判定する（DESIGN.md §4）。"""
    if code in overrides:
        return overrides[code]
    if sector == "ETF" or "ETF" in name or "上場投信" in name:
        return "etf"
    return "stock"


def _read_watchlist_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"監視銘柄CSVが見つかりません: {path}")
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def _read_existing_enabled(path: Path) -> dict[str, bool]:
    """既存 stocks.csv から人間が編集した enabled 値を読み取る（無ければ空dict）。

    normalize_watchlist の再実行時にこれとマージすることで、人間が
    無効化した銘柄の設定を上書きしないようにする。
    """
    if not path.exists():
        return {}
    enabled: dict[str, bool] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = str(row.get("code", "")).strip()
            if not code:
                continue
            enabled[code] = _parse_bool(row.get("enabled"), default=True)
    return enabled


def _write_stocks_csv(stocks: list[Stock], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_STOCKS_FIELDNAMES)
        for s in stocks:
            writer.writerow([s.code, s.name, s.sector, s.asset_type, _bool_to_csv(s.enabled)])


def _write_stock_themes_csv(stocks: list[Stock], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_THEMES_FIELDNAMES)
        for s in stocks:
            for t in s.themes:
                writer.writerow([s.code, t.theme, _bool_to_csv(t.is_leader), t.watch_priority])


def _read_stocks_csv(path: Path) -> tuple[list[str], dict[str, Stock]]:
    order: list[str] = []
    stocks: dict[str, Stock] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = str(row.get("code", "")).strip()
            if not code:
                continue
            order.append(code)
            stocks[code] = Stock(
                code=code,
                name=str(row.get("name", "")).strip(),
                sector=str(row.get("sector", "")).strip(),
                asset_type=str(row.get("asset_type", "")).strip() or "stock",
                enabled=_parse_bool(row.get("enabled"), default=True),
                themes=(),
            )
    return order, stocks


def _read_stock_themes_csv(path: Path) -> dict[str, list[ThemeTag]]:
    themes: dict[str, list[ThemeTag]] = {}
    if not path.exists():
        return themes
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = str(row.get("code", "")).strip()
            if not code:
                continue
            themes.setdefault(code, []).append(
                ThemeTag(
                    theme=str(row.get("theme", "")).strip(),
                    is_leader=_parse_bool(row.get("is_leader"), default=False),
                    watch_priority=str(row.get("watch_priority", "")).strip().upper() or "C",
                )
            )
    return themes


# --- 契約インターフェース (DESIGN.md §12.5) ------------------------------


def normalize_watchlist(cfg) -> tuple[list[Stock], list[str]]:
    """watchlist.csv を読み、stocks.csv / stock_themes.csv を書き出す。

    戻り値は (銘柄一覧, 警告メッセージ一覧)。
    """
    watchlist_path = Path(cfg.universe.watchlist_csv)
    stocks_path = Path(cfg.universe.stocks_csv)
    themes_path = Path(cfg.universe.stock_themes_csv)

    overrides = _overrides_dict(cfg)
    # 人間が編集済みの enabled は書き換える前に読んでおく（マージ用）。
    existing_enabled = _read_existing_enabled(stocks_path)

    rows = _read_watchlist_rows(watchlist_path)

    warnings: list[str] = []
    order: list[str] = []
    canonical: dict[str, dict[str, str]] = {}  # code -> {"name":..., "sector":...}（最初の出現）
    themes_by_code: dict[str, list[ThemeTag]] = {}
    seen_themes: dict[str, set[str]] = {}

    for row in rows:
        code = str(row.get("code", "")).strip()
        if not code:
            continue
        name = str(row.get("name", "")).strip()
        sector = str(row.get("sector", "")).strip()
        theme = str(row.get("theme", "")).strip()
        is_leader = _parse_bool(row.get("is_leader"), default=False)
        watch_priority = str(row.get("watch_priority", "")).strip().upper() or "C"

        if code not in canonical:
            canonical[code] = {"name": name, "sector": sector}
            order.append(code)
            themes_by_code[code] = []
            seen_themes[code] = set()
        else:
            first = canonical[code]
            if name and first["name"] and name != first["name"]:
                warnings.append(
                    f"{code}: name不一致。'{first['name']}' を採用し '{name}' を無視しました。"
                )
            if sector and first["sector"] and sector != first["sector"]:
                warnings.append(
                    f"{code}: sector不一致。'{first['sector']}' を採用し '{sector}' を無視しました。"
                )

        if not theme:
            continue
        if theme in seen_themes[code]:
            warnings.append(f"{code}: テーマ '{theme}' が重複行として存在します。最初の行を採用しました。")
            continue
        seen_themes[code].add(theme)
        themes_by_code[code].append(
            ThemeTag(theme=theme, is_leader=is_leader, watch_priority=watch_priority)
        )

    stocks: list[Stock] = []
    for code in order:
        name = canonical[code]["name"]
        sector = canonical[code]["sector"]
        asset_type = _detect_asset_type(name, sector, code, overrides)
        enabled = existing_enabled.get(code, True)
        stocks.append(
            Stock(
                code=code,
                name=name,
                sector=sector,
                asset_type=asset_type,
                enabled=enabled,
                themes=tuple(themes_by_code[code]),
            )
        )

    _write_stocks_csv(stocks, stocks_path)
    _write_stock_themes_csv(stocks, themes_path)

    return stocks, warnings


def load_universe(cfg) -> list[Stock]:
    """stocks.csv + stock_themes.csv を読む。無ければ normalize_watchlist を実行する。"""
    stocks_path = Path(cfg.universe.stocks_csv)
    themes_path = Path(cfg.universe.stock_themes_csv)

    if not stocks_path.exists():
        normalize_watchlist(cfg)

    order, stocks_by_code = _read_stocks_csv(stocks_path)
    themes_by_code = _read_stock_themes_csv(themes_path)

    result: list[Stock] = []
    for code in order:
        base = stocks_by_code[code]
        result.append(
            Stock(
                code=base.code,
                name=base.name,
                sector=base.sector,
                asset_type=base.asset_type,
                enabled=base.enabled,
                themes=tuple(themes_by_code.get(code, ())),
            )
        )
    return result
