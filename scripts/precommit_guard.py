"""Block commits that would leak local secrets, runtime data, or unformatted code.

Runs from the repository root. Reports rule names and locations only, never the
matched text. Use `git commit --no-verify` only when YCH has decided the finding
is wrong, and fix the rule afterwards.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

Reader = Callable[[str], str | None]

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DENIED_PATHS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("env-file", re.compile(r"(^|/)\.env($|\.(?!example))")),
    ("sqlite-data", re.compile(r"\.sqlite3(-wal|-shm)?$")),
    (
        "runtime-storage",
        re.compile(r"^storage/(runtime|backups|imports|exports|generated)/(?!\.gitkeep)"),
    ),
    ("privacy-storage", re.compile(r"^storage/privacy-backups/(?!\.gitkeep)")),
    ("legacy-migration", re.compile(r"^migration/legacy-(data|config)/(?!\.gitkeep)")),
    ("log-file", re.compile(r"\.log$")),
    ("build-output", re.compile(r"^frontend/dist/")),
    ("python-cache", re.compile(r"(^|/)__pycache__/")),
)

CONTENT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("provider-key-literal", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
)

ASSIGNED_SECRET = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|onebot[_-]?token|client[_-]?secret)"
    r"\s*[:=]\s*[\"']?(?P<value>[A-Za-z0-9_\-]{16,})"
)

# A secret is a random string. Readable placeholders and constant references are not.
PLACEHOLDER_WORDS = re.compile(
    r"(?i)must[-_]not|fake|dummy|placeholder|example|sample|test|invalid|change[-_]?me"
)
HYPHENATED_WORDS = re.compile(r"^[a-z0-9]+([-_][a-z0-9]+)+$")
CONSTANT_REFERENCE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def looks_like_secret(value: str) -> bool:
    if PLACEHOLDER_WORDS.search(value):
        return False
    if CONSTANT_REFERENCE.match(value):
        return False
    return not HYPHENATED_WORDS.match(value)


TEXT_SUFFIXES = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".json",
        ".md",
        ".toml",
        ".yaml",
        ".yml",
        ".ps1",
        ".bat",
        ".cfg",
        ".ini",
        ".txt",
        ".html",
        ".css",
        ".example",
    }
)


def run(*args: str) -> str:
    result = subprocess.run(
        args,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0 and not result.stdout:
        return ""
    return result.stdout


def read_staged(path: str) -> str | None:
    """Read what the commit would contain, not what the working tree happens to hold."""
    result = subprocess.run(
        ("git", "show", f":{path}"),
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def read_worktree(path: str) -> str | None:
    absolute = PROJECT_ROOT / path
    if not absolute.is_file():
        return None
    try:
        return absolute.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def staged_paths() -> list[str]:
    output = run("git", "diff", "--cached", "--name-only", "--diff-filter=ACMR")
    return [line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()]


def tracked_paths() -> list[str]:
    output = run("git", "ls-files")
    return [line.strip().replace("\\", "/") for line in output.splitlines() if line.strip()]


def scan_paths(paths: list[str]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        for rule, pattern in DENIED_PATHS:
            if pattern.search(path):
                findings.append(f"{path}: 命中禁止路径规则 {rule}")
    return findings


def scan_content(paths: list[str], read: Reader) -> list[str]:
    findings: list[str] = []
    for path in paths:
        if Path(path).suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = read(path)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for rule, pattern in CONTENT_RULES:
                if pattern.search(line):
                    findings.append(f"{path}:{number}: 命中敏感内容规则 {rule}")
            match = ASSIGNED_SECRET.search(line)
            if match and looks_like_secret(match.group("value")):
                findings.append(f"{path}:{number}: 命中敏感内容规则 assigned-secret")
    return findings


def check_python_style(paths: list[str], read: Reader) -> list[str]:
    """Lint the staged text through stdin, so staging a partial fix cannot pass."""
    findings: list[str] = []
    for path in paths:
        if not path.endswith(".py"):
            continue
        source = read(path)
        if source is None:
            continue
        for command, label in (
            (("format", "--check"), "ruff format --check"),
            (("check",), "ruff check"),
        ):
            result = subprocess.run(
                [sys.executable, "-m", "ruff", *command, "--stdin-filename", path, "-"],
                cwd=PROJECT_ROOT,
                input=source,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if result.returncode != 0:
                findings.append(f"{path}: {label} 未通过。修好并重新 git add。")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--all",
        action="store_true",
        help="扫描全部已跟踪文件，而不是暂存区。用于定期自检。",
    )
    arguments = parser.parse_args()

    paths = tracked_paths() if arguments.all else staged_paths()
    read = read_worktree if arguments.all else read_staged
    if not paths:
        print("precommit-guard: 没有需要检查的文件。")
        return 0

    findings = scan_paths(paths) + scan_content(paths, read)
    if not arguments.all:
        findings += check_python_style(paths, read)

    if findings:
        print(f"precommit-guard: 发现 {len(findings)} 个问题，提交已阻止。")
        for finding in findings:
            print(f"  - {finding}")
        print("确认是误报时可用 git commit --no-verify，并同时修正规则。")
        return 1

    print(f"precommit-guard: 通过，检查了 {len(paths)} 个文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
