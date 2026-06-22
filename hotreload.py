from __future__ import annotations

from pathlib import Path

from main import main as gui_main


WATCHED_SUFFIXES = {".py", ".toml"}


def should_reload(_change: object, path: str) -> bool:
    candidate = Path(path)
    if candidate.name.startswith("."):
        return False
    ignored_dirs = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".setlog",
        ".venv",
        "__pycache__",
        "node_modules",
    }
    if any(part in ignored_dirs for part in candidate.parts):
        return False
    return candidate.suffix in WATCHED_SUFFIXES


def print_reload(changes: set[tuple[object, str]]) -> None:
    changed_paths = sorted(Path(path).name for _change, path in changes)
    if changed_paths:
        print("変更を検知しました。GUIを再起動します: " + ", ".join(changed_paths))


def main() -> int:
    try:
        from watchfiles import run_process
    except ImportError:
        print(
            "watchfiles が見つかりません。`uv sync --group dev` を実行してください。"
        )
        return 1

    print("hot reload を開始します。停止するには Ctrl+C を押してください。")
    return run_process(
        Path.cwd(),
        target=gui_main,
        callback=print_reload,
        watch_filter=should_reload,
        debounce=500,
    )
