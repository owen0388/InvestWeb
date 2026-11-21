"""把项目打包成可分发的源码压缩包（不含虚拟环境、依赖、构建产物与数据库）。

用法：
    python scripts/package_share.py
    python scripts/package_share.py -o D:/InvestWeb-share.zip
"""
from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 任意层级出现即跳过
EXCLUDE_DIRS = {
    ".venv", "venv", ".git", ".workbuddy", ".idea", ".vscode",
    "node_modules", "dist", "data", "__pycache__", ".pytest_cache",
}
# 不入包的敏感/临时文件（.env 可能含密钥，让对方从 .env.example 复制）
EXCLUDE_FILES = {".env", ".DS_Store", "Thumbs.db", "npm-debug.log"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".log"}


def iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(root)
        if any(part in EXCLUDE_DIRS for part in rel.parts[:-1]):
            continue
        if path.name in EXCLUDE_FILES or path.suffix in EXCLUDE_SUFFIX:
            continue
        yield path, rel


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--output", default=str(ROOT.parent / "InvestWeb-share.zip"))
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    files = list(iter_files(ROOT))
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path, rel in files:
            zf.write(path, rel.as_posix())

    size_mb = output.stat().st_size / 1024 / 1024
    print(f"输出：{output}")
    print(f"文件数：{len(files)}　大小：{size_mb:.2f} MB")
    print("\n包内顶层内容：")
    tops = sorted({rel.parts[0] for _, rel in files})
    for top in tops:
        print(f"  - {top}")
    print("\n已排除：.venv / node_modules / dist / data / __pycache__ / .pytest_cache / .workbuddy / .env")


if __name__ == "__main__":
    main()
