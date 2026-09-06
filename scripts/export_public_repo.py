"""Export a privacy-scrubbed public tree of this repository.

The local runnable checkout stays untouched. This script copies allowlisted paths
into a sibling directory, redacts personal identifiers (option B: keep brand YCH,
replace the real-name signature with 维护者), and refuses to finish if forbidden
patterns remain.

Usage (from repo root):

    .\\.venv\\Scripts\\python.exe scripts\\export_public_repo.py
    .\\.venv\\Scripts\\python.exe scripts\\export_public_repo.py --out D:\\path\\ych-bot-public
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = PROJECT_ROOT.parent / "ych-bot-public"

# Stable fictional QQ numbers so tests that int()-cast still work.
OWNER_QQ = "2000000001"
BOT_QQ = "2000000002"
FRIEND_QQ = "2000000003"
GROUP_QQ = "2000000004"

COPY_ROOTS = (
    ".cursor",
    ".githooks",
    "backend",
    "docs",
    "frontend",
    "migration",
    "openspec",
    "scripts",
    "storage",
)

COPY_FILES = (
    ".env.example",
    ".gitattributes",
    ".gitignore",
    "pyproject.toml",
    "README.md",
    "uv.lock",
)

SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "dist",
    "build",
    ".vite",
    "ych_bot.egg-info",
}

SKIP_FILE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".lnk",
    ".sqlite3",
    ".sqlite3-wal",
    ".sqlite3-shm",
    ".log",
}

# Drop local operational narrative that is hard to scrub into something useful.
DROP_RELATIVE = {
    "HANDOFF_TO_CURSOR.md",
    "docs/history",
    "docs/NEXT_PHASE.md",
    "docs/NAPCAT_UPDATE.md",
    "frontend/mockups",
}

TEXT_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".json",
    ".md",
    ".mdc",
    ".toml",
    ".yaml",
    ".yml",
    ".txt",
    ".example",
    ".bat",
    ".ps1",
    ".css",
    ".html",
    ".svg",
    ".gitignore",
    ".gitattributes",
}

REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("YCH（姚铖颢）", "YCH（维护者）"),
    ("YCH(姚铖颢)", "YCH（维护者）"),
    ("创造者为姚铖颢", "创造者为维护者"),
    ("创造者姚铖颢", "创造者维护者"),
    ("开发者、创造者：姚铖颢", "开发者、创造者：维护者"),
    ("姚铖颢", "维护者"),
    ("2580508026", OWNER_QQ),
    ("3336425098", BOT_QQ),
    ("2892917255", FRIEND_QQ),
    ("928557213", GROUP_QQ),
)

# Absolute local paths that must not ship.
PATH_SCRUBS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"D:\\\\My_code\\\\NapCat\.Shell\.Windows\.Node\\\\ych-bot", re.I), "<repo-root>"),
    (re.compile(r"D:/My_code/NapCat\.Shell\.Windows\.Node/ych-bot", re.I), "<repo-root>"),
    (
        re.compile(r"D:\\\\My_code\\\\napcat-rollback-[^\s`\"']+", re.I),
        "<napcat-rollback-outside-repo>",
    ),
    (re.compile(r"D:/My_code/napcat-rollback-[^\s`\"']+", re.I), "<napcat-rollback-outside-repo>"),
)

FORBIDDEN_LITERALS = (
    "姚铖颢",
    "2580508026",
    "3336425098",
    "2892917255",
    "928557213",
    "BEGIN PRIVATE KEY",
)

FORBIDDEN_PATTERNS = (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),)

PUBLIC_HANDOFF = """# Handoff (public template)

This tree is a **privacy-scrubbed public export** of YCH Bot. It is not a runnable
copy of anyone's production machine.

## Identity (template)

- Brand: YCH
- Creator/developer display name in this export: 维护者
- Example owner QQ in docs/tests: `2000000001`
- Example bot QQ in docs/tests: `2000000002`

Replace these with your own values in `.env` after copy. Do not commit real tokens.

## Before coding

1. Read `openspec/config.yaml` and `openspec/specs/`.
2. Read `docs/README.md` and `docs/operator/` for operator surfaces.
3. Copy `.env.example` to `.env` and fill only local secrets.

## Safety defaults

Outbound QQ, model network, Qzone publish, privacy-job execution, search, image
generation, and OCR stay off until you configure and authorize them. Readiness
is necessary but never sufficient.

## Not included

- Real `.env`, SQLite runtime data, imports, exports, privacy archives
- Local operational handoff history and NapCat rollback paths
- Any live chat bodies
"""

PUBLIC_NEXT_PHASE = """# Next phase (public template)

This public export does not ship a private production timeline.

Start new work with an OpenSpec change under `openspec/changes/`, implement against
temporary databases and fake transports, then archive into `openspec/specs/`.

Keep search, image generation, privacy-job execution, and OCR closed until you
explicitly want those capabilities.
"""

PUBLIC_README_BANNER = """# YCH Bot

> Public template export. Brand stays **YCH**; the creator display name in this
> tree is **维护者**. Example QQ numbers are fictional placeholders
> (`2000000001` owner / `2000000002` bot). Do not treat them as a live account.

"""


def should_skip_dir(name: str) -> bool:
    return name in SKIP_DIR_NAMES or name.startswith(".")


def should_skip_file(path: Path) -> bool:
    if path.name == ".env":
        return True
    if path.suffix.lower() in SKIP_FILE_SUFFIXES:
        return True
    return path.name.endswith(".sqlite3")


def drop_path(relative: Path) -> bool:
    text = relative.as_posix()
    return any(text == item or text.startswith(item.rstrip("/") + "/") for item in DROP_RELATIVE)


def is_text_file(path: Path) -> bool:
    if path.name in {".gitignore", ".gitattributes", ".env.example"}:
        return True
    return path.suffix.lower() in TEXT_SUFFIXES


def scrub_text(content: str) -> str:
    for old, new in REPLACEMENTS:
        content = content.replace(old, new)
    for pattern, repl in PATH_SCRUBS:
        content = pattern.sub(repl, content)
    return content


def copy_file(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if is_text_file(source):
        try:
            text = source.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            shutil.copy2(source, dest)
            return
        dest.write_text(scrub_text(text), encoding="utf-8", newline="\n")
    else:
        shutil.copy2(source, dest)


def copy_tree(source_root: Path, dest_root: Path, relative_root: str) -> None:
    source = source_root / relative_root
    if not source.exists():
        return
    for path in source.rglob("*"):
        relative = path.relative_to(source_root)
        if drop_path(relative):
            continue
        if path.is_dir():
            if should_skip_dir(path.name):
                # rglob still descends; skip by not copying children via continue on files
                continue
            continue
        # Skip files under skipped directories
        if any(part in SKIP_DIR_NAMES for part in relative.parts):
            continue
        if should_skip_file(path):
            continue
        # Keep only .gitkeep / README under storage and migration legacy dumps
        posix = relative.as_posix()
        if posix.startswith("storage/") and path.name not in {".gitkeep", "README.md"}:
            continue
        if posix.startswith("migration/legacy-data/") and path.name != ".gitkeep":
            continue
        if posix.startswith("migration/legacy-config/") and path.name != ".gitkeep":
            continue
        copy_file(path, dest_root / relative)


def write_public_overlays(out: Path) -> None:
    (out / "HANDOFF_TO_CURSOR.md").write_text(PUBLIC_HANDOFF, encoding="utf-8", newline="\n")
    (out / "docs" / "NEXT_PHASE.md").write_text(PUBLIC_NEXT_PHASE, encoding="utf-8", newline="\n")
    readme = out / "README.md"
    body = readme.read_text(encoding="utf-8") if readme.exists() else ""
    body = scrub_text(body)
    # Prefer the public banner over a scrubbed private status dump when present.
    if body.lstrip().startswith("# YCH Bot"):
        rest = body.split("\n", 1)[1] if "\n" in body else ""
        body = PUBLIC_README_BANNER + rest
    else:
        body = PUBLIC_README_BANNER + "\n" + body
    readme.write_text(body, encoding="utf-8", newline="\n")
    (out / "PUBLIC_EXPORT.md").write_text(
        "\n".join(
            [
                "# Public export notes",
                "",
                "Generated by `scripts/export_public_repo.py` (option B).",
                "",
                "- Brand: YCH",
                "- Creator display name: 维护者",
                f"- Placeholder owner QQ: `{OWNER_QQ}`",
                f"- Placeholder bot QQ: `{BOT_QQ}`",
                f"- Placeholder friend QQ: `{FRIEND_QQ}`",
                f"- Placeholder group QQ: `{GROUP_QQ}`",
                "- Dropped: private handoff, docs/history, local NapCat path notes",
                "- Not copied: .env, runtime SQLite, imports/exports, .venv, node_modules",
                "",
                "Push this directory as its own git repo.",
                "Do not force-push your private local history here.",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )


def verify(out: Path) -> list[str]:
    findings: list[str] = []
    for path in out.rglob("*"):
        if not path.is_file() or not is_text_file(path):
            continue
        if any(part in SKIP_DIR_NAMES for part in path.relative_to(out).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        relative = path.relative_to(out).as_posix()
        for token in FORBIDDEN_LITERALS:
            if token in text:
                if relative == "scripts/export_public_repo.py":
                    continue
                findings.append(f"{relative}: still contains {token!r}")
        if relative != "scripts/export_public_repo.py":
            for pattern in FORBIDDEN_PATTERNS:
                if pattern.search(text):
                    findings.append(f"{relative}: still contains secret-shaped token")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--force", action="store_true", help="Delete existing output directory first"
    )
    args = parser.parse_args()
    out: Path = args.out.resolve()

    if out == PROJECT_ROOT or PROJECT_ROOT in out.parents:
        print(f"Refusing to write inside the private repo: {out}", file=sys.stderr)
        return 2

    if out.exists():
        if not args.force:
            print(f"Output exists. Re-run with --force to replace: {out}", file=sys.stderr)
            return 2
        shutil.rmtree(out)
    out.mkdir(parents=True)

    for name in COPY_FILES:
        source = PROJECT_ROOT / name
        if source.exists():
            copy_file(source, out / name)
    for root in COPY_ROOTS:
        copy_tree(PROJECT_ROOT, out, root)

    write_public_overlays(out)

    findings = verify(out)
    if findings:
        print(f"Export incomplete: {len(findings)} forbidden leftovers", file=sys.stderr)
        for item in findings[:40]:
            print(f"  - {item}", file=sys.stderr)
        return 1

    file_count = sum(1 for p in out.rglob("*") if p.is_file())
    print(f"Public export ready: {out}")
    print(f"Files: {file_count}")
    print("Next: cd into that directory, git init, create a GitHub repo, push.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
