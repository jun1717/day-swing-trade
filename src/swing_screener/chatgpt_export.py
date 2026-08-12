"""ChatGPT へ渡す分析用データの書き出し（日次自動運用の出力）。

出力は `output/chatgpt/YYYY-MM-DD/` 配下の 3 ファイル。

    candidates.csv   その日の候補銘柄（ENTRY_CANDIDATE / NEAR / RANGE）の横比較
    daily_bars.csv   候補銘柄の直近70営業日の生の日足（long format）
    manifest.txt     出所と件数（人間と ChatGPT がデータの素性を確認するため）

**このモジュールは売買判定をしない。**

ここが最も重要な設計上の約束である。レンジ再検出も ENTRY 再判定も
`near.max_position_in_range` の再評価も、このモジュールでは一切行わない。
やるのは本番スクリーニング結果（`ScreeningRun` / `ScreenResult` /
`Judgement`）を読んで CSV の列へ写すことだけで、本番判定が single source of
truth である（tests/test_chatgpt_export.py が本番結果との一致を固定する）。

例外は `entry_trigger_margin_pct`（終値が前日高値からどれだけ離れているか）
だけで、これは説明のための表示値であり売買条件には使われない。

daily_bars.csv には「そのとき存在しなかった情報」を入れない。列は生の OHLCV と
MA25 だけで、現在のレンジ判定を過去の行へコピーするようなことはしない。
MA25 はその日までの終値だけで決まるので過去行に入れてよい。
"""

from __future__ import annotations

import csv
import hashlib
import os
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from .indicators.ma import calc_ma_series
from .models import (
    STATUS_ENTRY,
    STATUS_NEAR,
    STATUS_RANGE,
    OHLCVBar,
    PriceSeries,
    ScreenResult,
    ScreeningRun,
)

# 書き出す状態。OUT は含めない（CODEX_HANDOFF の「候補」の定義そのもの）。
EXPORT_STATUSES: tuple[str, ...] = (STATUS_ENTRY, STATUS_NEAR, STATUS_RANGE)

# ENTRY_CANDIDATE / NEAR は「今すぐ日足を見るべき」候補、RANGE は「形は出来て
# いるがまだ下限から遠い」候補。新しいスコアではなく既存 status の言い換え。
CANDIDATE_GROUP: dict[str, str] = {
    STATUS_ENTRY: "PRIMARY",
    STATUS_NEAR: "PRIMARY",
    STATUS_RANGE: "SECONDARY",
}

# ChatGPT 側で MA25・高値/安値切り上げ・3〜10日レンジ・下限反応・出来高推移を
# 再確認できるだけの本数（README「GitHub Actions daily workflow」参照）。
DEFAULT_BAR_LOOKBACK_DAYS = 70

CANDIDATES_FILENAME = "candidates.csv"
DAILY_BARS_FILENAME = "daily_bars.csv"
MANIFEST_FILENAME = "manifest.txt"

DISCLAIMER_EN = (
    "This bundle is analysis input only. "
    "Production trading logic was not recalculated or modified by this export."
)
DISCLAIMER_JA = (
    "このデータは分析のための入力です。本番の売買判定は、この書き出しによって"
    "再計算も変更もされていません。"
)

CANDIDATE_COLUMNS: tuple[str, ...] = (
    # --- 基本 ---
    "as_of_date",
    "code",
    "name",
    "sector",
    "theme",
    "watch_priority",
    "status",
    "candidate_group",
    # --- 最新日足 ---
    "open",
    "high",
    "low",
    "close",
    "volume",
    # --- 上昇トレンド ---
    "ma25",
    "ma25_direction",
    "ma25_slope_pct",
    "ma25_deviation_pct",
    "close_above_ma25",
    "higher_high",
    "higher_low",
    "trend_is_uptrend",
    # --- レンジ ---
    "range_found",
    "range_start",
    "range_end",
    "range_days",
    "range_lower",
    "range_upper",
    "range_width_pct",
    "range_position",
    "lower_distance_pct",
    "lower_reaction_count",
    "lower_reaction_dates",
    "days_since_lower_touch",
    "range_lower_zone_low",
    "range_lower_zone_high",
    "range_quality",
    # --- ENTRY ---
    "previous_day_high",
    "entry_trigger",
    "entry_trigger_margin_pct",
    "rebound_confirmed",
    "bullish_candle",
    "long_lower_wick",
    "volume_recovered",
    "initial_stop",
    "stop_distance_pct",
    # --- 出来高 ---
    "volume_ratio",
    "volume_range_vs_pre_ratio",
    "volume_avg5",
    "volume_avg20",
    "volume_evaluation",
    "volume_evaluation_label",
    # --- 判定理由（既存 Judgement をそのまま連結したもの） ---
    "trend_reason",
    "range_reason",
    "entry_reason",
    "volume_reason",
    "status_reason",
)

BAR_COLUMNS: tuple[str, ...] = (
    "date",
    "code",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "ma25",
    "days_ago",
)


class ExportError(Exception):
    """書き出しを続けると不完全なデータが残る場合に投げる。"""


class ValidationError(Exception):
    """検査に落ちた。`errors` に全件を入れる（1件目で止めない）。"""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__("；".join(self.errors))


# --- 整形 ---------------------------------------------------------------------


def _fmt(value: Any, digits: int = 2) -> str:
    """CSV セル 1 個ぶんの文字列。None は空欄（推定で埋めない）。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _reason(result: ScreenResult, prefixes: tuple[str, ...]) -> str:
    """既存 Judgement を `ラベル: 詳細` の複数行にして返す。

    **短縮も言い換えもしない。** 本番が表示している detail をそのまま並べる。
    改行を含むので、書き出し側は csv モジュールに quote させること。
    """
    return "\n".join(
        f"{j.label}: {j.detail}"
        for j in result.judgements
        if j.key.split(".")[0] in prefixes
    )


def _judgement_detail(result: ScreenResult, key: str) -> str:
    for j in result.judgements:
        if j.key == key:
            return j.detail
    return ""


# --- 出力先 -------------------------------------------------------------------


def bundle_root(cfg: Any = None, out_dir: Path | str | None = None) -> Path:
    """ChatGPT 用データの置き場所（既定 `output/chatgpt`）。

    `output.chatgpt_dir` があればそれを使う。config.yaml には書かれていないので
    既定では `output.dir` から導く（config.yaml は v1 の確定ルールなので触らない）。
    """
    if out_dir is not None:
        return Path(out_dir)
    if cfg is not None:
        configured = cfg.get("output.chatgpt_dir", None)
        if configured:
            return Path(str(configured))
        return Path(str(cfg.get("output.dir", "output"))) / "chatgpt"
    return Path("output/chatgpt")


def bundle_dir(as_of: date, cfg: Any = None, out_dir: Path | str | None = None) -> Path:
    return bundle_root(cfg, out_dir) / as_of.isoformat()


def exported_dates(cfg: Any = None, out_dir: Path | str | None = None) -> list[date]:
    """書き出し済みの日付（昇順）。manifest.txt があるものだけを完成とみなす。"""
    root = bundle_root(cfg, out_dir)
    if not root.exists():
        return []
    dates: list[date] = []
    for child in root.iterdir():
        if not child.is_dir() or not (child / MANIFEST_FILENAME).exists():
            continue
        try:
            dates.append(date.fromisoformat(child.name))
        except ValueError:
            continue
    return sorted(dates)


def latest_exported_date(
    cfg: Any = None, out_dir: Path | str | None = None
) -> date | None:
    dates = exported_dates(cfg, out_dir)
    return dates[-1] if dates else None


# --- 株価の切り出し -------------------------------------------------------------


def latest_market_date(price_map: dict[str, PriceSeries]) -> date | None:
    """キャッシュ済み株価の最新日（= 最新の確定営業日）。"""
    dates = [s.bars[-1].date for s in price_map.values() if s.bars]
    return max(dates) if dates else None


def truncate_price_map(
    price_map: dict[str, PriceSeries], as_of: date
) -> dict[str, PriceSeries]:
    """as_of より後の足を落とす（`--date` 指定時に未来を渡さないため）。

    研究の replay と同じ考え方で、系列を切るだけで本番判定へ未来が入らない。
    判定そのものは本番 `screen_one` が行う（ここでは再実装しない）。
    """
    out: dict[str, PriceSeries] = {}
    for code, series in price_map.items():
        bars = tuple(b for b in series.bars if b.date <= as_of)
        if bars:
            out[code] = PriceSeries(code=code, bars=bars)
    return out


# --- candidates.csv -----------------------------------------------------------


def candidate_results(run: ScreeningRun) -> list[ScreenResult]:
    """書き出す候補。**並べ替えない。**

    `run.results` は既に本番の `ScreenResult.sort_key`（status →
    下限までの距離 → トレンド強度 → レンジ品質 → 出来高 → priority）で
    並んでいる。sort_key の第1キーが status なので、絞り込むだけで
    ENTRY_CANDIDATE → NEAR → RANGE の順が保たれる。ここで並べ替えると
    「おすすめ度」を新しく作ることになるのでしない。
    """
    return [r for r in run.results if r.status in EXPORT_STATUSES]


def _candidate_row(result: ScreenResult, as_of: date, bar: OHLCVBar | None) -> dict[str, str]:
    trend = result.trend
    range_ = result.range_
    rebound = result.rebound
    volume = result.volume

    close = result.latest_close
    prev_high = rebound.prev_high if rebound else None
    # 表示用の説明値。売買条件には使わない（反発確認は本番の rebound.confirmed）。
    trigger_margin = (
        (close / prev_high - 1.0) * 100.0
        if close is not None and prev_high
        else None
    )
    stop = result.stop_price
    stop_distance = (stop - close) / close * 100.0 if stop is not None and close else None

    lower = range_.lower if range_ else None
    upper = range_.upper if range_ else None
    position = None
    if range_ is not None and close is not None:
        span = range_.upper - range_.lower
        if span > 0:
            position = (close - range_.lower) / span

    return {
        "as_of_date": _fmt(as_of),
        "code": result.stock.code,
        "name": result.stock.name,
        "sector": result.stock.sector,
        "theme": "|".join(result.stock.theme_names),
        "watch_priority": result.stock.display_priority,
        "status": result.status,
        "candidate_group": CANDIDATE_GROUP.get(result.status, ""),
        "open": _fmt(bar.open if bar else None),
        "high": _fmt(bar.high if bar else None),
        "low": _fmt(bar.low if bar else None),
        "close": _fmt(close),
        "volume": _fmt(bar.volume if bar else None),
        "ma25": _fmt(trend.ma if trend else None),
        "ma25_direction": trend.ma_direction if trend else "",
        "ma25_slope_pct": _fmt(trend.ma_slope_pct if trend else None),
        "ma25_deviation_pct": _fmt(trend.ma_deviation_pct if trend else None),
        "close_above_ma25": _fmt(trend.close_above_ma if trend else None),
        "higher_high": _fmt(trend.higher_highs if trend else None),
        "higher_low": _fmt(trend.higher_lows if trend else None),
        "trend_is_uptrend": _fmt(trend.is_uptrend if trend else None),
        "range_found": _fmt(range_ is not None),
        "range_start": _fmt(range_.start_date if range_ else None),
        "range_end": _fmt(range_.end_date if range_ else None),
        "range_days": _fmt(range_.days if range_ else None),
        "range_lower": _fmt(lower),
        "range_upper": _fmt(upper),
        "range_width_pct": _fmt(range_.width_pct if range_ else None),
        "range_position": _fmt(position, digits=3),
        "lower_distance_pct": _fmt(result.distance_to_lower_pct),
        "lower_reaction_count": _fmt(range_.lower_touch_count if range_ else None),
        "lower_reaction_dates": (
            "|".join(d.isoformat() for d in range_.lower_touch_dates) if range_ else ""
        ),
        "days_since_lower_touch": _fmt(result.days_since_lower_touch),
        "range_lower_zone_low": _fmt(range_.lower_zone_low if range_ else None),
        "range_lower_zone_high": _fmt(range_.lower_zone_high if range_ else None),
        "range_quality": _fmt(range_.quality if range_ else None, digits=3),
        "previous_day_high": _fmt(prev_high),
        "entry_trigger": _fmt(prev_high),
        "entry_trigger_margin_pct": _fmt(trigger_margin),
        "rebound_confirmed": _fmt(rebound.confirmed if rebound else None),
        "bullish_candle": _fmt(rebound.bullish_candle if rebound else None),
        "long_lower_wick": _fmt(rebound.long_lower_wick if rebound else None),
        "volume_recovered": _fmt(rebound.volume_recovered if rebound else None),
        "initial_stop": _fmt(stop),
        "stop_distance_pct": _fmt(stop_distance),
        "volume_ratio": _fmt(volume.latest_vs_avg5_ratio if volume else None, digits=3),
        "volume_range_vs_pre_ratio": _fmt(
            volume.range_vs_pre_ratio if volume else None, digits=3
        ),
        "volume_avg5": _fmt(volume.avg5 if volume else None, digits=0),
        "volume_avg20": _fmt(volume.avg20 if volume else None, digits=0),
        "volume_evaluation": volume.state if volume else "",
        "volume_evaluation_label": volume.state_label if volume else "",
        "trend_reason": _reason(result, ("trend",)),
        "range_reason": _reason(result, ("range",)),
        "entry_reason": _reason(result, ("rebound", "status")),
        "volume_reason": _reason(result, ("volume",)),
        "status_reason": _judgement_detail(result, "status.result"),
    }


def build_candidate_rows(
    run: ScreeningRun,
    price_map: dict[str, PriceSeries],
    *,
    as_of: date | None = None,
) -> list[dict[str, str]]:
    """候補 1 銘柄 = 1 行。本番結果を写すだけで、判定はしない。"""
    stamp = as_of or run.as_of
    if stamp is None:
        return []
    rows: list[dict[str, str]] = []
    for result in candidate_results(run):
        series = price_map.get(result.stock.code)
        bar = series.bars[-1] if series is not None and series.bars else None
        rows.append(_candidate_row(result, stamp, bar))
    return rows


# --- daily_bars.csv -----------------------------------------------------------


def build_bar_rows(
    codes: Sequence[str],
    price_map: dict[str, PriceSeries],
    cfg: Any,
    *,
    lookback_days: int = DEFAULT_BAR_LOOKBACK_DAYS,
) -> list[dict[str, str]]:
    """候補銘柄の直近 `lookback_days` 営業日を long format で返す。

    `codes` の順（= candidates.csv の並び）で銘柄を並べ、各銘柄は日付昇順。
    足が lookback_days 未満なら取得できる全期間を出す（間引きも水増しもしない）。

    MA25 は本番と同じ `calc_ma_series` を系列全体に対して計算してから
    直近ぶんを切り出す。切り出してから計算すると先頭の 24 本が空になるため。
    """
    period = int(cfg.ma.period)
    rows: list[dict[str, str]] = []
    for code in codes:
        series = price_map.get(code)
        if series is None or not series.bars:
            continue
        bars = list(series.bars)
        ma_series = calc_ma_series(bars, period)
        start = max(0, len(bars) - lookback_days)
        last_index = len(bars) - 1
        for i in range(start, len(bars)):
            bar = bars[i]
            rows.append(
                {
                    "date": bar.date.isoformat(),
                    "code": code,
                    "open": _fmt(bar.open),
                    "high": _fmt(bar.high),
                    "low": _fmt(bar.low),
                    "close": _fmt(bar.close),
                    "volume": _fmt(bar.volume),
                    "ma25": _fmt(ma_series[i]),
                    "days_ago": str(last_index - i),
                }
            )
    return rows


# --- manifest.txt -------------------------------------------------------------


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "unknown"


def resolve_git_sha() -> str:
    """git commit sha。GitHub Actions では環境変数、ローカルでは git に聞く。"""
    env_sha = os.environ.get("GITHUB_SHA")
    if env_sha:
        return env_sha
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and sha else "unknown"


def _screener_version() -> str:
    try:
        from importlib.metadata import version

        return version("swing-screener")
    except Exception:  # 未インストール（pythonpath 実行）でも書き出しは続ける
        return "unknown"


def build_manifest_lines(
    *,
    as_of: date,
    run: ScreeningRun,
    candidate_rows: Sequence[dict[str, str]],
    bar_rows: Sequence[dict[str, str]],
    lookback_days: int,
    git_sha: str,
    config_path: Path | str | None,
    experimental_path: Path | str | None,
    generated_at: str | None = None,
) -> list[str]:
    counts = run.counts()
    by_status: dict[str, int] = {s: 0 for s in EXPORT_STATUSES}
    for row in candidate_rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1

    lines = [
        "# ChatGPT 分析用データ (swing-screener chatgpt-export)",
        f"generated_at={generated_at or datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"market_data_as_of={as_of.isoformat()}",
        f"git_commit_sha={git_sha}",
        f"screener_version={_screener_version()}",
        f"universe_count={len(run.results)}",
        f"candidate_count={len(candidate_rows)}",
        f"entry_candidate_count={by_status.get(STATUS_ENTRY, 0)}",
        f"near_count={by_status.get(STATUS_NEAR, 0)}",
        f"range_count={by_status.get(STATUS_RANGE, 0)}",
        f"out_count={counts.get('OUT', 0)}",
        f"bars_count={len(bar_rows)}",
        f"bar_lookback_days={lookback_days}",
        f"candidates_file={CANDIDATES_FILENAME}",
        f"daily_bars_file={DAILY_BARS_FILENAME}",
    ]
    for label, path in (("config", config_path), ("experimental", experimental_path)):
        if path is None:
            continue
        p = Path(path)
        lines.append(f"{label}_file={p.name}")
        lines.append(f"{label}_sha256={_sha256(p)}")
    if run.warnings:
        lines.append(f"screening_warnings={len(run.warnings)}")

    lines.extend(
        [
            "",
            "# このデータの性格",
            DISCLAIMER_EN,
            DISCLAIMER_JA,
            "candidates.csv の値と reason 列は、本番スクリーニング結果"
            "（ScreenResult / Judgement）をそのまま写したものです。"
            "この書き出しでレンジ判定・ENTRY判定・レンジ内位置の再評価は行っていません。",
            "daily_bars.csv は生の日足と MA25 だけです。"
            "その日には存在しなかった判定結果を過去の行へ埋め込んでいません。",
            "OUT の銘柄は含みません。候補が 0 件の日は candidate_count=0 で、"
            "CSV はヘッダーのみになります（条件を緩めて候補を作ることはしません）。",
        ]
    )
    return lines


# --- 書き出し -------------------------------------------------------------------


@dataclass(frozen=True)
class Bundle:
    """1 営業日ぶんの書き出し結果。"""

    as_of: date
    directory: Path
    candidates_path: Path
    bars_path: Path
    manifest_path: Path
    candidate_rows: list[dict[str, str]] = field(default_factory=list)
    bar_rows: list[dict[str, str]] = field(default_factory=list)
    lookback_days: int = DEFAULT_BAR_LOOKBACK_DAYS

    @property
    def candidate_count(self) -> int:
        return len(self.candidate_rows)

    def status_counts(self) -> dict[str, int]:
        out = {s: 0 for s in EXPORT_STATUSES}
        for row in self.candidate_rows:
            out[row["status"]] = out.get(row["status"], 0) + 1
        return out

    @property
    def files(self) -> tuple[Path, ...]:
        return (self.candidates_path, self.bars_path, self.manifest_path)


def _write_csv(path: Path, columns: Sequence[str], rows: Iterable[dict[str, str]]) -> None:
    """csv モジュールに quote させる（reason 列は改行を含む）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(columns))
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})


def write_bundle(
    run: ScreeningRun,
    price_map: dict[str, PriceSeries],
    cfg: Any,
    *,
    as_of: date | None = None,
    out_dir: Path | str | None = None,
    lookback_days: int = DEFAULT_BAR_LOOKBACK_DAYS,
    git_sha: str | None = None,
    config_path: Path | str | None = None,
    experimental_path: Path | str | None = None,
    generated_at: str | None = None,
) -> Bundle:
    """`output/chatgpt/YYYY-MM-DD/` へ 3 ファイルを書き出す。

    候補 0 件でもヘッダーだけの CSV を正しく作る（0 件は異常ではない）。
    """
    stamp = as_of or run.as_of
    if stamp is None:
        raise ExportError(
            "データ基準日が不明です（株価キャッシュが空。先に swing fetch を実行してください）。"
        )

    candidate_rows = build_candidate_rows(run, price_map, as_of=stamp)
    codes = [row["code"] for row in candidate_rows]
    bar_rows = build_bar_rows(codes, price_map, cfg, lookback_days=lookback_days)

    directory = bundle_dir(stamp, cfg, out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    candidates_path = directory / CANDIDATES_FILENAME
    bars_path = directory / DAILY_BARS_FILENAME
    manifest_path = directory / MANIFEST_FILENAME

    _write_csv(candidates_path, CANDIDATE_COLUMNS, candidate_rows)
    _write_csv(bars_path, BAR_COLUMNS, bar_rows)

    lines = build_manifest_lines(
        as_of=stamp,
        run=run,
        candidate_rows=candidate_rows,
        bar_rows=bar_rows,
        lookback_days=lookback_days,
        git_sha=git_sha if git_sha is not None else resolve_git_sha(),
        config_path=config_path,
        experimental_path=experimental_path,
        generated_at=generated_at,
    )
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return Bundle(
        as_of=stamp,
        directory=directory,
        candidates_path=candidates_path,
        bars_path=bars_path,
        manifest_path=manifest_path,
        candidate_rows=candidate_rows,
        bar_rows=bar_rows,
        lookback_days=lookback_days,
    )


# --- 検査 ---------------------------------------------------------------------


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        return header, list(reader)


def _float(text: str | None) -> float | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def validate_bundle(
    directory: Path | str,
    *,
    lookback_days: int = DEFAULT_BAR_LOOKBACK_DAYS,
    run: ScreeningRun | None = None,
    today: date | None = None,
) -> list[str]:
    """書き出した 3 ファイルを検査し、見つかった不整合を全部返す。

    `run` を渡すと本番スクリーニング結果との一致も検査する
    （status・銘柄集合・初期STOP）。中途半端な CSV を「その日の正しい最新
    データ」として残さないための最後の関門なので、1 件目で止めずに全部集める。
    """
    directory = Path(directory)
    errors: list[str] = []

    candidates_path = directory / CANDIDATES_FILENAME
    bars_path = directory / DAILY_BARS_FILENAME
    manifest_path = directory / MANIFEST_FILENAME

    for path in (candidates_path, bars_path, manifest_path):
        if not path.exists():
            errors.append(f"{path.name} がない")
    if errors:
        return errors

    # --- candidates.csv ---
    header, rows = _read_csv(candidates_path)
    if header != list(CANDIDATE_COLUMNS):
        errors.append(
            f"{CANDIDATES_FILENAME} のヘッダーが想定と違う（想定 {len(CANDIDATE_COLUMNS)}列 / "
            f"実際 {len(header)}列）"
        )
        return errors  # 列が違うと以降の検査が意味をなさない

    codes = [r["code"] for r in rows]
    duplicated = sorted({c for c in codes if codes.count(c) > 1})
    if duplicated:
        errors.append(f"{CANDIDATES_FILENAME} に重複コード: {', '.join(duplicated)}")

    bad_status = sorted({r["status"] for r in rows if r["status"] not in EXPORT_STATUSES})
    if bad_status:
        errors.append(
            f"{CANDIDATES_FILENAME} に想定外の status: {', '.join(bad_status)}"
            "（ENTRY_CANDIDATE / NEAR / RANGE のみ）"
        )

    as_of_values = sorted({r["as_of_date"] for r in rows})
    if len(as_of_values) > 1:
        errors.append(
            f"{CANDIDATES_FILENAME} の as_of_date が揃っていない: {', '.join(as_of_values)}"
        )
    as_of = date.fromisoformat(as_of_values[0]) if len(as_of_values) == 1 else None
    if as_of is not None and as_of.isoformat() != directory.name:
        errors.append(
            f"{CANDIDATES_FILENAME} の as_of_date {as_of} が出力先 {directory.name} と違う"
        )

    for r in rows:
        lower, upper = _float(r["range_lower"]), _float(r["range_upper"])
        if lower is not None and upper is not None and lower > upper:
            errors.append(f"{r['code']}: range_lower {lower} > range_upper {upper}")

    # 並び順は ENTRY_CANDIDATE → NEAR → RANGE のブロック順であること
    order = {s: i for i, s in enumerate(EXPORT_STATUSES)}
    ranks = [order.get(r["status"], 99) for r in rows]
    if ranks != sorted(ranks):
        errors.append(
            f"{CANDIDATES_FILENAME} の並びが ENTRY_CANDIDATE → NEAR → RANGE になっていない"
        )

    # --- daily_bars.csv ---
    bar_header, bar_rows = _read_csv(bars_path)
    if bar_header != list(BAR_COLUMNS):
        errors.append(
            f"{DAILY_BARS_FILENAME} のヘッダーが想定と違う（想定 {list(BAR_COLUMNS)}）"
        )
        return errors

    candidate_codes = set(codes)
    bar_codes = [r["code"] for r in bar_rows]
    unknown = sorted(set(bar_codes) - candidate_codes)
    if unknown:
        errors.append(
            f"{DAILY_BARS_FILENAME} に candidates.csv にないコード: {', '.join(unknown)}"
        )

    per_code: dict[str, list[dict[str, str]]] = {}
    for r in bar_rows:
        per_code.setdefault(r["code"], []).append(r)

    for code, items in per_code.items():
        if len(items) > lookback_days:
            errors.append(
                f"{code}: 日足が {len(items)}本で上限 {lookback_days}本 を超えている"
            )
        dates = [r["date"] for r in items]
        if len(set(dates)) != len(dates):
            errors.append(f"{code}: daily_bars.csv に同じ日付の行が複数ある")

    limit = today or date.today()
    for r in bar_rows:
        try:
            bar_date = date.fromisoformat(r["date"])
        except ValueError:
            errors.append(f"{r['code']}: 日付が読めない ({r['date']})")
            continue
        if bar_date > limit or (as_of is not None and bar_date > as_of):
            errors.append(f"{r['code']} {r['date']}: 未来の日付の足が含まれている")

        o, h, low, c = (_float(r["open"]), _float(r["high"]), _float(r["low"]), _float(r["close"]))
        if None in (o, h, low, c):
            errors.append(f"{r['code']} {r['date']}: OHLC に空欄がある")
            continue
        if low > min(o, h, c):
            errors.append(f"{r['code']} {r['date']}: low {low} が open/high/close より大きい")
        if h < max(o, low, c):
            errors.append(f"{r['code']} {r['date']}: high {h} が open/low/close より小さい")

    missing_bars = sorted(candidate_codes - set(bar_codes))
    if missing_bars:
        errors.append(
            f"{DAILY_BARS_FILENAME} に日足がない候補: {', '.join(missing_bars)}"
        )

    # --- manifest.txt ---
    manifest = manifest_path.read_text(encoding="utf-8")
    meta = dict(
        line.split("=", 1)
        for line in manifest.splitlines()
        if "=" in line and not line.startswith("#")
    )
    if meta.get("candidate_count") != str(len(rows)):
        errors.append(
            f"{MANIFEST_FILENAME} の candidate_count {meta.get('candidate_count')} が "
            f"実際の {len(rows)}件 と違う"
        )
    if meta.get("bars_count") != str(len(bar_rows)):
        errors.append(
            f"{MANIFEST_FILENAME} の bars_count {meta.get('bars_count')} が "
            f"実際の {len(bar_rows)}件 と違う"
        )
    if DISCLAIMER_EN not in manifest:
        errors.append(f"{MANIFEST_FILENAME} に分析用データである旨の注記がない")

    # --- 本番スクリーニング結果との一致 ---
    if run is not None:
        errors.extend(_compare_with_production(rows, run))

    return errors


def _compare_with_production(
    rows: Sequence[dict[str, str]], run: ScreeningRun
) -> list[str]:
    """CSV が本番判定をそのまま写しているかを検査する（§24 の single source of truth）。"""
    errors: list[str] = []
    production = {r.stock.code: r for r in candidate_results(run)}

    csv_codes = [r["code"] for r in rows]
    if csv_codes != list(production):
        errors.append(
            "candidates.csv の銘柄と並びが本番スクリーニング結果と一致しない"
            f"（CSV {csv_codes} / 本番 {list(production)}）"
        )

    for row in rows:
        result = production.get(row["code"])
        if result is None:
            errors.append(f"{row['code']}: 本番結果に存在しない銘柄が CSV にある")
            continue
        if row["status"] != result.status:
            errors.append(
                f"{row['code']}: status が本番と違う（CSV {row['status']} / 本番 {result.status}）"
            )
        expected_stop = _fmt(result.stop_price)
        if row["initial_stop"] != expected_stop:
            errors.append(
                f"{row['code']}: initial_stop が本番と違う"
                f"（CSV {row['initial_stop'] or '空'} / 本番 {expected_stop or '空'}）"
            )
    return errors
