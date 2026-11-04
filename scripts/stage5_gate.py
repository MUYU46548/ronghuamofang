# -*- coding: utf-8 -*-
"""stage5 改稿裁定闸（2026-11-04，作者拍板 N1=B）。

问题
----
stage5 原先由模型直接改写并写进 `data/chapters/checked/`：**改完的稿直接变成成品**，
作者只能看 diff，不能逐条裁定。判定器即使判死，成品链仍由模型决定。

改法
----
模型产物先落 `data/chapters/checked_pending/NN.md`（暂存），按**段落**与 raw 对比
生成裁定项，逐条等作者勾"采用/不采用"；全部裁定完才合成 `checked/NN.md`。

判据（确定性，零 token）
------------------------
· 段落切分：空行分段（与全文其它模块一致）。
· 变化项：`difflib.SequenceMatcher` 的 replace / insert / delete，
  **equal 段不产生裁定项**——没改的地方不需要作者点头。
· 采用 = 用模型的新段；不采用 = 保留 raw 原段。
· 没有变化项 → 视为"无需裁定"，直接合成（合成结果与 raw 一致）。
· raw 或暂存稿在裁定后被改动 → 记录 `stale`，拒绝合成（防拿旧裁定套新稿）。

文件
----
    data/chapters/checked_pending/NN.md     模型改稿（暂存，不是成品）
    data/review/stage5/NN.json              裁定项与状态
    data/chapters/checked/NN.md             合成后才是成品

CLI
---
    python scripts/stage5_gate.py list
    python scripts/stage5_gate.py show 3
    python scripts/stage5_gate.py accept 3 --all
    python scripts/stage5_gate.py accept 3 --item 1 --item 3
    python scripts/stage5_gate.py apply 3
    python scripts/stage5_gate.py apply --all
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PENDING_REL = "data/chapters/checked_pending"
REVIEW_REL = "data/review/stage5"
CHECKED_REL = "data/chapters/checked"
RAW_REL = "data/chapters/raw"


def _root(root=None) -> Path:
    return Path(root) if root else Path(".")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def paragraphs(text: str) -> list[str]:
    import re
    parts = re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n"))
    return [p.strip() for p in parts if p.strip()]


def _plan(raw_text: str, proposed_text: str) -> tuple[list[dict], list[dict]]:
    """生成裁定项与重建计划（确定性）。

    为什么要有 blocks：`items` 是给人看的（id/kind/old/new），`blocks` 是给
    合成用的（按原文顺序记录"这一段怎么办"）。两者同一份数据，避免合成时
    再跑一次 SequenceMatcher 得到不同分组。
    """
    a, b = paragraphs(raw_text), paragraphs(proposed_text)
    sm = SequenceMatcher(None, a, b, autojunk=False)
    items, blocks, n = [], [], 0

    def add(kind: str, old: str, nw: str) -> int:
        nonlocal n
        n += 1
        items.append({"id": n, "kind": kind, "old": old, "new": nw, "accepted": None})
        blocks.append({"kind": "item", "item": n})
        return n

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            blocks.append({"kind": "equal", "old": [i1, i2], "new": [j1, j2]})
        elif tag == "replace":
            lo, ln = i2 - i1, j2 - j1
            m = min(lo, ln)
            for k in range(m):                       # 逐段配对：一段一项，便于逐条裁定
                add("replace", a[i1 + k], b[j1 + k])
            for k in range(m, ln):                   # 多出来的新段 = 插入
                add("insert", "", b[j1 + k])
            for k in range(m, lo):                   # 多出来的旧段 = 删除
                add("delete", a[i1 + k], "")
        elif tag == "insert":
            add("insert", "", "\n\n".join(b[j1:j2]))
        elif tag == "delete":
            add("delete", "\n\n".join(a[i1:i2]), "")
    return items, blocks


def diff_items(raw_text: str, proposed_text: str) -> list[dict]:
    return _plan(raw_text, proposed_text)[0]


def compose(raw_text: str, proposed_text: str, meta: dict) -> str:
    """按 blocks 与裁定结果合成正文（accepted=True 采用模型改法）。

    一条都没采用（含"模型没改"）时**原样返回 raw** —— 连末尾换行都不动，
    保证"拒绝"的产物与原文逐字节一致。
    """
    items = {int(it["id"]): it for it in meta.get("items") or []}
    if not any(it.get("accepted") for it in items.values()):
        return raw_text
    a, b = paragraphs(raw_text), paragraphs(proposed_text)
    out = []
    for blk in meta.get("blocks") or []:
        if blk.get("kind") == "equal":
            i1, i2 = blk["old"]
            out.extend(a[i1:i2])
            continue
        it = items.get(int(blk.get("item")))
        if not it:
            continue
        acc = it.get("accepted")
        if it["kind"] == "replace":
            out.append(it["new"] if acc else it["old"])
        elif it["kind"] == "insert":
            if acc:
                out.append(it["new"])
        elif it["kind"] == "delete":
            if not acc:
                out.append(it["old"])
    return "\n\n".join(x for x in out if x).strip() + "\n"


# ---------------------------------------------------------------- 存储

def _paths(chapter: int, root=None) -> tuple[Path, Path, Path, Path]:
    r = _root(root)
    return (r / RAW_REL / ("%02d.md" % chapter),
            r / PENDING_REL / ("%02d.md" % chapter),
            r / REVIEW_REL / ("%02d.json" % chapter),
            r / CHECKED_REL / ("%02d.md" % chapter))


def load(chapter: int, root=None) -> dict | None:
    _, _, meta_p, _ = _paths(chapter, root)
    if not meta_p.exists():
        return None
    try:
        return json.loads(meta_p.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return None


def save(meta: dict, root=None) -> None:
    _, _, meta_p, _ = _paths(int(meta["chapter"]), root)
    meta_p.parent.mkdir(parents=True, exist_ok=True)
    meta["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def write_proposal(chapter: int, root=None) -> dict:
    """把暂存稿与 raw 对比，落一条裁定记录。返回 meta。

    没有暂存稿 → 视为"模型没产出"，抛 FileNotFoundError（调用方按失败处理）。
    """
    raw_p, pending_p, _, _ = _paths(chapter, root)
    raw = raw_p.read_text(encoding="utf-8")
    prop = pending_p.read_text(encoding="utf-8")
    items, blocks = _plan(raw, prop)
    meta = {
        "chapter": chapter,
        "raw_sha": _sha(raw),
        "proposed_sha": _sha(prop),
        "proposed_path": str(pending_p),
        "status": "ready" if items else "noop",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "items": items,
        "blocks": blocks,
    }
    save(meta, root)
    return meta


def decide(chapter: int, decisions: dict[int, bool], root=None):
    """写入裁定。decisions = {item_id: True/False}。返回 (ok, msg, meta)。"""
    meta = load(chapter, root)
    if not meta:
        return False, "没有待裁定的改稿（第 %d 章）" % chapter, None
    if meta.get("status") == "stale":
        return False, "第 %d 章的稿子在裁定期间变过（stale），先重新生成裁定项" % chapter, meta
    ids = {int(it["id"]) for it in meta.get("items") or []}
    for k, v in decisions.items():
        if int(k) not in ids:
            return False, "裁定项不存在：%s（本章只有 %s 项）" % (k, sorted(ids)), meta
        if not isinstance(v, bool):
            return False, "裁定值必须是 true/false", meta
    for it in meta["items"]:
        if int(it["id"]) in {int(k) for k in decisions}:
            it["accepted"] = bool(decisions[int(it["id"])])
    meta["status"] = "decided" if all(it.get("accepted") is not None for it in meta["items"]) \
        else "pending"
    if meta["status"] == "pending" and not meta["items"]:
        meta["status"] = "noop"
    save(meta, root)
    left = [it["id"] for it in meta["items"] if it.get("accepted") is None]
    return True, ("已记 %d 项；还剩 %d 项未定" % (len(decisions), len(left)) if left
                  else "本章裁定完成，可以合成"), meta


def apply_chapter(chapter: int, root=None):
    """合成 `checked/NN.md`。要求全部裁定完成且稿子未变。返回 (ok, msg, path)。"""
    raw_p, pending_p, _, checked_p = _paths(chapter, root)
    meta = load(chapter, root)
    if not meta:
        return False, "没有裁定记录（第 %d 章）" % chapter, None
    if meta.get("status") == "approved":
        return True, "第 %d 章已合成过（幂等）" % chapter, checked_p
    if meta.get("status") == "stale":
        return False, "第 %d 章的稿子变过（stale），不能拿旧裁定合成" % chapter, None
    if any(it.get("accepted") is None for it in meta.get("items") or []):
        left = [it["id"] for it in meta["items"] if it.get("accepted") is None]
        return False, "第 %d 章还有 %d 项未裁定：%s" % (chapter, len(left), left), None
    if not raw_p.exists():
        return False, "缺 raw 原文：%s" % raw_p.as_posix(), None
    if not pending_p.exists():
        return False, "缺暂存改稿：%s" % pending_p.as_posix(), None

    raw = raw_p.read_text(encoding="utf-8")
    prop = pending_p.read_text(encoding="utf-8")
    if _sha(raw) != meta.get("raw_sha"):
        meta["status"] = "stale"
        save(meta, root)
        return False, "第 %d 章 raw 在裁定后变过（stale）" % chapter, None
    text = compose(raw, prop, meta)
    checked_p.parent.mkdir(parents=True, exist_ok=True)
    checked_p.write_text(text, encoding="utf-8")
    meta["status"] = "approved"
    meta["applied_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta["applied_sha"] = _sha(text)
    save(meta, root)
    return True, "第 %d 章已合成 → %s" % (chapter, checked_p.as_posix()), checked_p


def list_all(root=None) -> list[dict]:
    r = _root(root)
    d = r / REVIEW_REL
    out = []
    if not d.exists():
        return out
    for p in sorted(d.glob("*.json")):
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                      # noqa: BLE001
            continue
        items = m.get("items") or []
        out.append({
            "chapter": m.get("chapter"),
            "status": m.get("status"),
            "items": len(items),
            "decided": sum(1 for it in items if it.get("accepted") is not None),
            "pending": [it["id"] for it in items if it.get("accepted") is None],
        })
    return out


def pending_chapters(root=None) -> list[int]:
    return [x["chapter"] for x in list_all(root)
            if x["status"] in ("pending", "ready")]


def apply_all(root=None) -> tuple[int, list[str]]:
    done, msgs = 0, []
    for x in list_all(root):
        if x["status"] in ("pending", "ready", "decided", "noop"):
            ok, msg, _ = apply_chapter(int(x["chapter"]), root)
            msgs.append(msg)
            if ok:
                done += 1
    return done, msgs


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="stage5 改稿裁定闸")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list")
    p_list.add_argument("--json", action="store_true")
    p_show = sub.add_parser("show")
    p_show.add_argument("chapter", type=int)
    p_acc = sub.add_parser("accept")
    p_acc.add_argument("chapter", type=int)
    p_acc.add_argument("--all", action="store_true", help="全部采用模型的改法")
    p_acc.add_argument("--reject-all", action="store_true", help="全部不采用（保留 raw）")
    p_acc.add_argument("--item", type=int, action="append", default=[],
                       help="只采用这些项（可重复）")
    p_ap = sub.add_parser("apply")
    p_ap.add_argument("chapter", type=int, nargs="?")
    p_ap.add_argument("--all", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        rows = list_all()
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            if not rows:
                print("[gate] 没有待裁定的改稿")
            for x in rows:
                print("  第 %2d 章  %-8s  %d/%d 项已定%s"
                      % (x["chapter"], x["status"], x["decided"], x["items"],
                         ("  未定：" + str(x["pending"])) if x["pending"] else ""))
        return 0

    if args.cmd == "show":
        meta = load(args.chapter)
        if not meta:
            print("[gate] 第 %d 章没有裁定记录" % args.chapter)
            return 1
        print("第 %d 章 · %s · raw=%s" % (meta["chapter"], meta["status"], meta["raw_sha"]))
        for it in meta["items"]:
            mark = {True: "采用", False: "不采用", None: "未定"}[it.get("accepted")]
            print("\n[%d] %s · %s" % (it["id"], it["kind"], mark))
            if it.get("old"):
                print("  − " + it["old"].replace("\n", "\n    ")[:400])
            if it.get("new"):
                print("  + " + it["new"].replace("\n", "\n    ")[:400])
        return 0

    if args.cmd == "accept":
        meta = load(args.chapter)
        if not meta:
            print("[gate] 第 %d 章没有裁定记录" % args.chapter)
            return 1
        ids = [it["id"] for it in meta["items"]]
        if args.all:
            dec = {i: True for i in ids}
        elif args.reject_all:
            dec = {i: False for i in ids}
        elif args.item:
            dec = {i: True for i in ids if i in set(args.item)}
            dec.update({i: False for i in ids if i not in set(args.item)})
        else:
            print("[gate] 需要 --all / --reject-all / --item N")
            return 2
        ok, msg, _ = decide(args.chapter, dec)
        print(("[gate] " if ok else "[gate] 失败：") + msg)
        return 0 if ok else 1

    if args.cmd == "apply":
        if args.all:
            n, msgs = apply_all()
            for m in msgs:
                print("[gate] " + m)
            print("[gate] 合成 %d 章" % n)
            return 0
        if args.chapter is None:
            print("[gate] 需要章号或 --all")
            return 2
        ok, msg, _ = apply_chapter(args.chapter)
        print(("[gate] " if ok else "[gate] 失败：") + msg)
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
