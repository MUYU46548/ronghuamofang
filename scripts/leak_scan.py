# -*- coding: utf-8 -*-
"""泄露门禁扫描器 —— 把「私人词 → 发布树」挡住，且**不许静默通过**。

## 为什么需要它

发布树里曾经混进真名、真实作品词、本机绝对路径和私人 vault 的目录结构。
靠人工过目不可靠（实测同一批文件扫两遍结论都不一样）。所以做成**可复跑的门禁**：

- **fail-closed**：词表缺失 / 为空 / 条目数不足 → 直接失败（exit 2）。
  这条比"扫了但没有"重要得多 —— 一个悄悄退化成空名单的门禁等于没有门禁。
- **零真值随发布树**：仓库里只放 `ci/blacklist.example`（占位），真词表走
  CI secret 或本地未跟踪文件。

## 划层判据（哪些词该进词表）

> **「换个用户这条规则还成立吗？」** 成立 → 入库层（通用规则，随发布树走）；
> 不成立 → 敏感层（真名 / 作品词 / 私人目录结构 / 本机路径），只进词表。

⚠️ **仓库 owner 的 GitHub 句柄不进词表**：它出现在 git remote、Release URL、
`package.json`、`AboutDialog.vue` 里，是发布物的**必需组成**。把它列成敏感词只能
靠路径白名单放行，制造的是"既暴露又假装敏感"的假红 —— 假红会训练人忽略红灯。

## 用法

    python scripts/leak_scan.py --list ci/blacklist.txt
    python scripts/leak_scan.py --list ci/blacklist.txt --json
    python scripts/leak_scan.py --list path/to/words.txt --root <other-repo>

退出码：0 无命中 · 1 有命中 · 2 词表不可用（fail-closed）
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

MIN_ENTRIES = 20
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".docx", ".dotx",
              ".xlsx", ".zip", ".gz", ".exe", ".dll", ".so", ".dylib", ".pkl",
              ".pyc", ".woff", ".woff2", ".ttf", ".otf", ".mp4", ".m4a"}


def load_words(path):
    """读词表。返回 (words, allow, error)。

    支持两种行：
      - `词`            → 敏感词
      - `allow: 路径`   → **整文件豁免**（必须带 `# 理由`）
    `#` 开头为注释，空行忽略。
    """
    p = Path(path)
    if not p.exists():
        return [], [], f"词表不存在：{p}"
    try:
        raw = p.read_text(encoding="utf-8")
    except Exception as e:                                    # noqa: BLE001
        return [], [], f"词表读取失败：{e!r}"
    words, allow = [], []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.lower().startswith("allow:"):
            target = s[len("allow:"):].split("#")[0].strip()
            if target and target not in allow:
                allow.append(target)
            continue
        if s not in words:
            words.append(s)
    if not words:
        return [], allow, f"词表为空（{p}）—— fail-closed，不放行"
    if len(words) < MIN_ENTRIES:
        return [], allow, (f"词表只有 {len(words)} 条，少于下限 {MIN_ENTRIES} 条 —— "
                           "疑似被截断/部分丢失，fail-closed，不放行")
    return words, allow, ""


def tracked_files(root=".", use_git=True):
    root = Path(root)
    if use_git:
        try:
            out = subprocess.run(["git", "ls-files"], cwd=str(root),
                                 capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=60)
            if out.returncode == 0:
                return [root / ln for ln in (out.stdout or "").splitlines() if ln.strip()], "git"
        except Exception:                                     # noqa: BLE001
            pass
    files = [p for p in root.rglob("*")
             if p.is_file() and ".git" not in p.parts and "node_modules" not in p.parts]
    return files, "walk"


def _rel_norm(path, root):
    """文件 → 相对 root 的规范路径（正斜杠）。用于豁免匹配。"""
    import os
    try:
        rel = os.path.relpath(str(path), str(root))
    except Exception:                                         # noqa: BLE001
        rel = str(path)
    return rel.replace("\\", "/")


def _match_allow(rel_norm, allow_norm):
    """豁免匹配 —— **按仓库相对路径精确匹配**，目录用结尾 `/`。

    ⚠️ 这里刻意不做"以 `/名字` 结尾"的宽松匹配：那样 `allow: README.md` 会连
    `materials/raw/README.md`、`tests/e2e/fixture_project/README.md` 一起放行
    （实测踩到）—— 豁免面被无声放大，正是"门禁开后门"的典型形态。
    """
    for a in allow_norm:
        a = a.strip()
        if not a:
            continue
        if a.endswith("/"):
            if rel_norm.startswith(a.rstrip("/") + "/"):
                return True
        elif rel_norm == a:
            return True
    return False


def scan(files, words, skip=(), allow=(), root="."):
    skip = set(str(s).replace("\\", "/") for s in skip)
    allow_norm = {str(a).replace("\\", "/") for a in allow}
    hits = []
    skipped_bin = 0
    exempted = []
    for f in files:
        fs = str(f)
        norm = fs.replace("\\", "/")
        if norm in skip:
            continue
        if _match_allow(_rel_norm(f, root), allow_norm):
            exempted.append(fs)
            continue
        if f.suffix.lower() in BINARY_EXT:
            skipped_bin += 1
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:                                     # noqa: BLE001
            skipped_bin += 1
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for w in words:
                if w in line:
                    hits.append({"path": fs, "line": i, "word": w,
                                 "text": line.strip()[:160]})
    return hits, skipped_bin, exempted


def main():
    ap = argparse.ArgumentParser(description="发布前泄露门禁扫描（fail-closed）")
    ap.add_argument("--list", required=True, help="敏感词表（# 注释，一行为一词）")
    ap.add_argument("--root", default=".", help="扫描根（默认当前目录）")
    ap.add_argument("--no-git", action="store_true", help="不用 git ls-files，直接遍历")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--max-print", type=int, default=50, help="终端最多打印多少条命中")
    args = ap.parse_args()

    words, allow, err = load_words(args.list)
    if err:
        print("[leak_scan] ❌ " + err)
        return 2

    files, how = tracked_files(args.root, use_git=not args.no_git)
    # 词表文件自身必须跳过（它里面全是敏感词，扫自己必然满屏命中）。
    # 遍历模式给的是绝对路径、git 模式给的是相对路径，两种形态都放进去。
    lp = Path(args.list)
    skip = {str(lp).replace("\\", "/"), str(lp.resolve()).replace("\\", "/"), lp.name}
    hits, skipped_bin, exempted = scan(files, words, skip=skip, allow=allow,
                                       root=args.root)

    if args.json:
        print(json.dumps({
            "words": len(words), "files": len(files), "mode": how,
            "hits": hits, "skipped_binary": skipped_bin,
            "exempted": exempted,
        }, ensure_ascii=False, indent=2))
    else:
        print(f"[leak_scan] 词表 {len(words)} 条 · 扫描 {len(files)} 个文件"
              f"（{how}，跳过二进制 {skipped_bin}）")
        if exempted:
            # 豁免必须**可见** —— 静默豁免就是给门禁开后门
            print(f"[leak_scan] 已豁免 {len(exempted)} 个文件（词表里的 allow: 行）："
                  + "、".join(exempted))
        if not hits:
            print("[leak_scan] ✅ 零命中")
        else:
            by_file = {}
            for h in hits:
                by_file.setdefault(h["path"], []).append(h)
            print(f"[leak_scan] ❌ 命中 {len(hits)} 处 / {len(by_file)} 个文件")
            shown = 0
            for path in sorted(by_file):
                print(f"  {path}")
                for h in by_file[path]:
                    if shown >= args.max_print:
                        break
                    print(f"    L{h['line']:<5} [{h['word']}]  {h['text'][:110]}")
                    shown += 1
                if shown >= args.max_print:
                    print("  …（还有更多，用 --json 看全量）")
                    break
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
