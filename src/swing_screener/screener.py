"""スクリーニングのパイプライン統合（DESIGN.md §12.5）。

ここはネットワークに触れない。株価はキャッシュから読むだけなので、
パラメータを変えて何度でも再実行できる（これが調整の反復速度を決める）。
"""

from __future__ import annotations

import dataclasses
import json
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

from .explain import explain_lines
from .indicators.volume import summarize_volume
from .models import (
    STATUS_OUT,
    PriceSeries,
    ScreenResult,
    ScreeningRun,
    Stock,
)
from .rules import status as status_rules
from .rules.range_detect import detect_range
from .rules.rebound import evaluate_rebound
from .rules.trend import evaluate_trend


def load_price_map(
    stocks: Sequence[Stock], cfg
) -> tuple[dict[str, PriceSeries], list[str]]:
    """キャッシュから株価を読む。ネットワークには触れない。

    data.cache は別モジュールなので遅延 import する（ロジックのテストが
    キャッシュ実装に依存しないようにするため）。
    """
    from .data import cache  # noqa: PLC0415  遅延 import

    price_map: dict[str, PriceSeries] = {}
    warnings: list[str] = []
    for stock in stocks:
        try:
            series = cache.load_prices(stock.code, cfg)
        except Exception as exc:  # キャッシュ破損で全体を止めない
            warnings.append(f"{stock.code} {stock.name}: 株価キャッシュ読み込み失敗 ({exc})")
            continue
        if series is None or not series.bars:
            warnings.append(
                f"{stock.code} {stock.name}: 株価キャッシュがない（swing fetch を実行）"
            )
            continue
        price_map[stock.code] = series
    return price_map, warnings


def screen_one(stock: Stock, series: PriceSeries | None, cfg, exp) -> ScreenResult:
    """1銘柄を判定する。OUT でも理由と全判定を残す。"""
    guard = status_rules.check_data(stock, series, cfg)
    if guard is not None:
        latest = series.latest if series is not None else None
        return ScreenResult(
            stock=stock,
            status=guard.status,
            as_of=latest.date if latest else None,
            latest_close=latest.close if latest else None,
            price_filter_ok=False,
            out_reason=guard.out_reason,
            judgements=guard.judgements,
        )

    assert series is not None  # check_data が None を弾いている
    bars = list(series.bars)

    trend = evaluate_trend(bars, cfg, exp)
    best_range, candidates = detect_range(bars, cfg, exp)
    # 「前日までのレンジ」。当日足を含む window では下限が当日安値に張り付くため、
    # レンジ崩壊（昨日まで支持されていた下限を今日割った）はこちらで判定する。
    prev_range, _ = detect_range(bars[:-1], cfg, exp)
    volume = summarize_volume(
        bars, best_range.start_index if best_range else None, exp
    )
    rebound = evaluate_rebound(bars, exp, volume)

    outcome = status_rules.classify(
        bars=bars,
        trend=trend,
        range_=best_range,
        candidates=candidates,
        volume=volume,
        rebound=rebound,
        cfg=cfg,
        exp=exp,
        prev_range=prev_range,
    )

    rejected = tuple(c for c in candidates if best_range is None or c is not best_range)

    return ScreenResult(
        stock=stock,
        status=outcome.status,
        as_of=bars[-1].date,
        latest_close=bars[-1].close,
        price_filter_ok=outcome.price_filter_ok,
        trend=trend,
        range_=best_range,
        volume=volume,
        rebound=rebound,
        distance_to_lower_pct=outcome.distance_to_lower_pct,
        touched_lower_recently=outcome.touched_lower_recently,
        days_since_lower_touch=outcome.days_since_lower_touch,
        stop_price=outcome.stop_price,
        out_reason=outcome.out_reason,
        judgements=outcome.judgements,
        rejected_ranges=rejected,
    )


def run_screening(
    stocks: Sequence[Stock],
    price_map: dict[str, PriceSeries],
    cfg,
    exp,
) -> ScreeningRun:
    """全銘柄を判定し ScreenResult.sort_key で並べた ScreeningRun を返す。"""
    results: list[ScreenResult] = []
    warnings: list[str] = []
    for stock in stocks:
        series = price_map.get(stock.code)
        try:
            results.append(screen_one(stock, series, cfg, exp))
        except Exception as exc:  # 1銘柄の失敗で全体を止めない
            warnings.append(f"{stock.code} {stock.name}: 判定に失敗 ({exc})")
            results.append(
                ScreenResult(
                    stock=stock,
                    status=STATUS_OUT,
                    as_of=None,
                    latest_close=None,
                    price_filter_ok=False,
                    out_reason=f"判定中にエラー: {exc}",
                )
            )

    results.sort(key=lambda r: r.sort_key)
    as_of_dates = [r.as_of for r in results if r.as_of is not None]

    return ScreeningRun(
        as_of=max(as_of_dates) if as_of_dates else None,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        results=results,
        config_snapshot=cfg.as_dict(),
        experimental_snapshot=exp.as_dict(),
        warnings=warnings,
    )


# --- JSON 化 ----------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    """dataclass / date / tuple を素直に JSON へ落とす。"""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def result_to_dict(result: ScreenResult) -> dict:
    data = _jsonable(result)
    # dataclass のフィールド名 range_ は JSON では range として扱いやすくする
    data["range"] = data.get("range_")
    data["sort_key"] = _jsonable(list(result.sort_key))
    data["explain"] = explain_lines(result)
    data["display_priority"] = result.stock.display_priority
    data["is_leader_any"] = result.stock.is_leader_any
    return data


def run_to_dict(run: ScreeningRun) -> dict:
    """Web UI が読む JSON。config / experimental も埋め込み再現性を担保する。"""
    return {
        "as_of": run.as_of.isoformat() if run.as_of else None,
        "generated_at": run.generated_at,
        "counts": run.counts(),
        "warnings": list(run.warnings),
        "config": _jsonable(run.config_snapshot),
        "experimental": _jsonable(run.experimental_snapshot),
        "results": [result_to_dict(r) for r in run.results],
    }


def save_run(run: ScreeningRun, cfg, path: Path | None = None) -> Path:
    """output/screening_YYYY-MM-DD.json に保存する。"""
    if path is None:
        out_dir = Path(str(cfg.get("output.dir", "output")))
        stamp = (run.as_of or date.today()).isoformat()
        path = out_dir / f"screening_{stamp}.json"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(run_to_dict(run), f, ensure_ascii=False, indent=2)
    return path
