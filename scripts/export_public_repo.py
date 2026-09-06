"""Export a slim, privacy-scrubbed public tree.

Local private checkout stays untouched. Public output keeps runnable code,
tests, and a short README. Narrative docs, OpenSpec archives, Cursor rules,
and private handoff files stay local only.

Usage (from private repo root):

    .\\.venv\\Scripts\\python.exe scripts\\export_public_repo.py --force
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = PROJECT_ROOT.parent / "ych-bot-public"

OWNER_QQ = "2000000001"
BOT_QQ = "2000000002"
FRIEND_QQ = "2000000003"
GROUP_QQ = "2000000004"

COPY_ROOTS = (
    ".githooks",
    "backend",
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

# Public remote does not need local narrative / process docs.
DROP_RELATIVE = {
    ".cursor",
    "HANDOFF_TO_CURSOR.md",
    "docs",
    "frontend/mockups",
    "frontend/INFORMATION_ARCHITECTURE.md",
    "frontend/OPS_DASHBOARD.md",
    "frontend/VISUAL_LANGUAGE.md",
    "openspec/changes",
    "migration/MANIFEST.md",
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
    ("YCH（维护者）", "YCH（维护者）"),
    ("YCH（维护者）", "YCH（维护者）"),
    ("创造者为维护者", "创造者为维护者"),
    ("创造者维护者", "创造者维护者"),
    ("开发者、创造者：维护者", "开发者、创造者：维护者"),
    ("维护者", "维护者"),
    ("2000000001", OWNER_QQ),
    ("2000000002", BOT_QQ),
    ("2000000003", FRIEND_QQ),
    ("2000000004", GROUP_QQ),
)

PATH_SCRUBS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"D:\\\\My_code\\\\NapCat\.Shell\.Windows\.Node\\\\ych-bot", re.I), "<repo-root>"),
    (re.compile(r"D:/My_code/NapCat\.Shell\.Windows\.Node/ych-bot", re.I), "<repo-root>"),
    (
        re.compile(r"D:\\\\My_code\\\\napcat-rollback-[^\s`\"']+", re.I),
        "<napcat-rollback-outside-repo>",
    ),
    (re.compile(r"<napcat-rollback-outside-repo>`\"']+", re.I), "<napcat-rollback-outside-repo>"),
)

FORBIDDEN_LITERALS = (
    "维护者",
    "2000000001",
    "2000000002",
    "2000000003",
    "2000000004",
    "BEGIN PRIVATE KEY",
)

FORBIDDEN_PATTERNS = (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),)

PUBLIC_README = f"""# YCH Bot

基于 NapCat / OneBot 的本机 QQ 助手（公开模板）。

- 品牌：**YCH**
- 公开树里的创造者显示名：**维护者**
- 示例 QQ（占位，不是真实账号）：主号 `{OWNER_QQ}`，机器人 `{BOT_QQ}`

这是脱敏后的代码模板，不是任何人的本机生产环境。密钥、聊天记录、运行库都不会进仓库。

## 本地运行

```powershell
uv sync --extra dev
Copy-Item .env.example .env
# 填写你自己的 QQ、令牌和模型配置；默认外发与模型网络关闭
uv run ych-bot
```

前端：

```powershell
npm --prefix frontend install
npm --prefix frontend run dev
```

观察模式启动（不打开外发）：

```powershell
.\\scripts\\start-observe.bat
```

## 仓库里有什么

- `backend/`：Python 后端与分层测试
- `frontend/`：操作仪表盘
- `openspec/specs/`：当前行为规格（变更归档只留在私有仓）
- `scripts/`：启动与导出辅助
- `.env.example`：可提交的空配置样例

## 安全默认

外发 QQ、模型网络、空间发布、隐私任务、搜索、文生图、OCR 默认关闭。  
就绪检查通过也不等于已经授权真实效果。

## 私有内容

完整交接文档、阶段历史、OpenSpec 变更归档、Cursor 规则等
只保留在维护者本机私有仓，不推送到此公开仓库。
"""


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
        if not path.is_file():
            continue
        relative = path.relative_to(source_root)
        if drop_path(relative):
            continue
        if any(part in SKIP_DIR_NAMES for part in relative.parts):
            continue
        if should_skip_file(path):
            continue
        posix = relative.as_posix()
        if posix.startswith("storage/") and path.name not in {".gitkeep", "README.md"}:
            continue
        if posix.startswith("migration/legacy-data/") and path.name != ".gitkeep":
            continue
        if posix.startswith("migration/legacy-config/") and path.name != ".gitkeep":
            continue
        copy_file(path, dest_root / relative)


def write_public_overlays(out: Path) -> None:
    (out / "README.md").write_text(PUBLIC_README, encoding="utf-8", newline="\n")
    # Keep a tiny frontend readme if the long ones were dropped.
    frontend_readme = out / "frontend" / "README.md"
    if not frontend_readme.exists():
        frontend_readme.write_text(
            "# Frontend\n\nYCH operator dashboard. `npm install` then `npm run dev`.\n",
            encoding="utf-8",
            newline="\n",
        )


def verify(out: Path) -> list[str]:
    findings: list[str] = []
    banned_paths = (
        "docs/",
        "openspec/changes/",
        ".cursor/",
        "HANDOFF_TO_CURSOR.md",
        "PUBLIC_EXPORT.md",
    )
    for path in out.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(out).as_posix()
        if any(relative == b.rstrip("/") or relative.startswith(b) for b in banned_paths):
            findings.append(f"{relative}: should not exist in slim public export")
            continue
        if any(part in SKIP_DIR_NAMES for part in path.relative_to(out).parts):
            continue
        if not is_text_file(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if relative == "scripts/export_public_repo.py":
            continue
        for token in FORBIDDEN_LITERALS:
            if token in text:
                findings.append(f"{relative}: still contains {token!r}")
        for pattern in FORBIDDEN_PATTERNS:
            if pattern.search(text):
                findings.append(f"{relative}: still contains secret-shaped token")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--force", action="store_true")
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
        print(f"Export incomplete: {len(findings)} problems", file=sys.stderr)
        for item in findings[:40]:
            print(f"  - {item}", file=sys.stderr)
        return 1

    file_count = sum(1 for p in out.rglob("*") if p.is_file())
    print(f"Slim public export ready: {out}")
    print(f"Files: {file_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
