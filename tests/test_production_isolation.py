"""本番コードと研究コードが混ざっていないことの検査（TRADING_RULES.md §6.3）。

研究フェーズでは 5 種類の EXIT ロジックを比較した。そのどれも **正式ルールに
採用していない**。にもかかわらず本番の判定にそれらが紛れ込むと、
「ツールが何を根拠に表示しているか」が誰にも分からなくなる。

そこでこのテストは 2 方向を固定する。

    本番 → 研究   import してはいけない（一方通行）
    研究 → 本番   import してよい（研究は本番ロジックを読むだけ）

さらに、研究専用の語（VARIANT / reference_high / trail stop など）が
本番ソースに現れないことを検査する。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "swing_screener"
RESEARCH = SRC / "research"

PRODUCTION_FILES = sorted(
    p for p in SRC.rglob("*.py") if RESEARCH not in p.parents and p != RESEARCH
)


def imported_modules(path: Path) -> set[str]:
    """そのファイルが import しているモジュール名（相対 import も解決する）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    package_parts = path.relative_to(SRC.parent).parent.parts  # ("swing_screener", ...)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相対 import
                base = list(package_parts[: len(package_parts) - (node.level - 1)])
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            names.add(module)
            names.update(f"{module}.{a.name}" for a in node.names)
    return names


def test_production_files_are_found():
    """検査対象が空だと、このテストは何も守らない。"""
    assert len(PRODUCTION_FILES) >= 15
    assert any(p.name == "screener.py" for p in PRODUCTION_FILES)
    assert any(p.name == "review.py" for p in PRODUCTION_FILES)
    assert not any("research" in p.parts for p in PRODUCTION_FILES)


@pytest.mark.parametrize("path", PRODUCTION_FILES, ids=lambda p: p.name)
def test_production_never_imports_research(path: Path):
    leaked = {m for m in imported_modules(path) if "research" in m.split(".")}
    assert not leaked, f"{path.name} が研究コードを import している: {sorted(leaked)}"


# 研究でしか使わない語。本番の**コード**に現れたら、研究ロジックが漏れた可能性がある。
#
# 検査対象はコード（識別子・文字列リテラル）だけで、コメントと docstring は除く。
# 「なぜ採用しなかったか」を本番のコメントで説明することは、むしろ推奨したいため。
RESEARCH_ONLY_TERMS = (
    "reference_high",
    "warning_low",
    "REHIGH",
    "trail_stop",
    "VARIANT_A",
    "VARIANT_B",
    "VARIANT_C",
    "CLOSE_BREAK",
    "LOW_BREAK",
    "STRUCTURAL_BREAK",
    "STUCK_IN_WARNING",
    "AMBIGUOUS",
)


def code_tokens(path: Path) -> set[str]:
    """識別子と文字列リテラル。コメント・docstring は含めない。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))

    tokens: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            tokens.add(node.id)
        elif isinstance(node, ast.Attribute):
            tokens.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            tokens.add(node.name)
        elif isinstance(node, ast.arg):
            tokens.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            tokens.add(node.arg)
        elif isinstance(node, ast.alias):
            tokens.add(node.name)
            if node.asname:
                tokens.add(node.asname)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                tokens.add(node.value)
    return tokens


@pytest.mark.parametrize("path", PRODUCTION_FILES, ids=lambda p: p.name)
def test_no_research_only_identifiers_in_production(path: Path):
    tokens = code_tokens(path)
    found = sorted({t for t in RESEARCH_ONLY_TERMS for tok in tokens if t in tok})
    assert not found, f"{path.name} のコードに研究専用の語が含まれている: {found}"


def test_the_term_check_actually_catches_a_leak(tmp_path):
    """検査そのものが機能していることを確かめる（常に緑になる検査を防ぐ）。"""
    leak = tmp_path / "leak.py"
    leak.write_text("reference_high = 100\n", encoding="utf-8")
    tokens = code_tokens(leak)
    assert any(t in tok for t in RESEARCH_ONLY_TERMS for tok in tokens)

    doc_only = tmp_path / "doc_only.py"
    doc_only.write_text('"""reference_high は採用していない。"""\nx = 1\n', encoding="utf-8")
    tokens = code_tokens(doc_only)
    assert not any(t in tok for t in RESEARCH_ONLY_TERMS for tok in tokens)


def test_research_may_import_production():
    """依存の向きは一方通行。研究側から本番を読むのは正しい。"""
    sm = RESEARCH / "exit_state_machine.py"
    assert sm.exists()
    assert any(m.startswith("swing_screener") for m in imported_modules(sm))


def test_research_code_is_still_present():
    """研究コードは削除しない（再現可能な状態で保存する）。"""
    for name in (
        "exit_study.py",
        "exit_state_machine.py",
        "warning_start_study.py",
        "warning_break_study.py",
        "reference_high_study.py",
        "sweep.py",
        "replay.py",
    ):
        assert (RESEARCH / name).exists(), name


def test_production_cli_has_no_research_commands():
    """日常運用のユーザーが研究コマンドを知らなくても困らないこと。"""
    from swing_screener.cli import app

    names = {c.name or c.callback.__name__ for c in app.registered_commands}
    assert {"daily", "fetch", "holdings", "buy", "sell", "serve"} <= names
    assert not any(
        term in n for n in names for term in ("study", "state-machine", "sweep", "warning")
    )


def test_research_cli_is_a_separate_entry_point():
    """研究 CLI は `python -m swing_screener.research.cli` の別入口であること。"""
    import tomllib

    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as f:
        pyproject = tomllib.load(f)

    scripts = pyproject["project"]["scripts"]
    assert scripts == {"swing": "swing_screener.cli:app"}

    from swing_screener.research import cli as research_cli

    assert research_cli.app is not None
