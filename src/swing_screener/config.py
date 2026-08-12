"""【契約】設定ロード。

config.yaml（確定ルール）と experimental.yaml（未確定パラメータ）を読む。

このツールでは「パラメータを変えて再スクリーニングする」ことが主作業になるため、
YAML にキーを足すだけでコード変更なしに参照できる形にしている（dataclass の
再定義を強制しない）。

    cfg = load_config()
    cfg.price_filter.min          # 2000
    exp = load_experimental()
    exp.near.lower_threshold_pct  # 2.0
    exp.get("near.lookback_days", 3)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config.yaml")
DEFAULT_EXPERIMENTAL_PATH = Path("experimental.yaml")


class Params:
    """ネストした dict へドットアクセスするラッパー。

    存在しないキーへのアクセスは AttributeError にする（typo を早く気付くため）。
    既定値が欲しい場合は get("a.b", default) を使う。
    """

    def __init__(self, data: dict[str, Any], _path: str = "") -> None:
        self._data = data or {}
        self._path = _path

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in self._data:
            where = f"{self._path}.{name}" if self._path else name
            raise AttributeError(
                f"設定キー '{where}' が見つかりません。YAML を確認してください。"
            )
        return self._wrap(self._data[name], name)

    def _wrap(self, value: Any, name: str) -> Any:
        if isinstance(value, dict):
            path = f"{self._path}.{name}" if self._path else name
            return Params(value, path)
        return value

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self._data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return Params(cur, dotted) if isinstance(cur, dict) else cur

    def __contains__(self, name: str) -> bool:
        return name in self._data

    def as_dict(self) -> dict[str, Any]:
        """出力JSONへ埋め込むためのスナップショット。"""
        import copy

        return copy.deepcopy(self._data)

    def __repr__(self) -> str:
        return f"Params({self._path or 'root'}: {list(self._data)})"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"設定ファイルが見つかりません: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} のトップレベルはマッピングである必要があります")
    return data


# config.yaml に必ず存在すべきキー（確定ルール）
REQUIRED_CONFIG_KEYS = (
    "price_filter.min",
    "price_filter.max",
    "ma.period",
    "range.min_days",
    "range.max_days",
    "range.min_lower_touches",
    "stop.buffer_pct",
)

# experimental.yaml に必ず存在すべきキー（未確定パラメータ）
REQUIRED_EXPERIMENTAL_KEYS = (
    "ma_slope.method",
    "ma_slope.lookback",
    "swing.method",
    "swing.pivot_window",
    "range_zone.lower_tolerance_pct",
    "range_zone.upper_tolerance_pct",
    "range_quality.min_quality",
    "near.lower_threshold_pct",
    "near.lookback_days",
    "volume.contract_ratio",
)


def _validate(params: Params, required: tuple[str, ...], label: str) -> None:
    missing = [k for k in required if params.get(k, _MISSING) is _MISSING]
    if missing:
        raise ValueError(f"{label} に必須キーがありません: {', '.join(missing)}")


class _Missing:
    pass


_MISSING = _Missing()


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> Params:
    params = Params(_load_yaml(Path(path)))
    _validate(params, REQUIRED_CONFIG_KEYS, "config.yaml")
    return params


def load_experimental(path: Path | str = DEFAULT_EXPERIMENTAL_PATH) -> Params:
    params = Params(_load_yaml(Path(path)))
    _validate(params, REQUIRED_EXPERIMENTAL_KEYS, "experimental.yaml")
    return params


def load_all(
    config_path: Path | str = DEFAULT_CONFIG_PATH,
    experimental_path: Path | str = DEFAULT_EXPERIMENTAL_PATH,
) -> tuple[Params, Params]:
    return load_config(config_path), load_experimental(experimental_path)
