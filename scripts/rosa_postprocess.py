# -*- coding: utf-8 -*-
"""完书后 ROSA 后处理 CLI（P1.6）。

一键生成三件草稿到沙盒（E:/图书馆/ROSA/Obsidian_AI_Sandbox/10_Inbox/）：
1. 作品介绍页草稿（按 ROSA 官方书籍模板结构）
2. 已有 ROSA 词条角色的"官作出场记录"待粘贴段落
3. 无词条新角色的角色设定页草稿（按官方角色模板结构，源稀薄处"待补充"）

出场统计为确定性（scripts/appearances.py），LLM 仅做文本聚合（简介/事迹/剧情）。
ROSA 库本体只读——所有产物写入沙盒，人工审阅后自行发布。

用法：
  python scripts/rosa_postprocess.py [--books|--roles|--all] [--dry-run] [--no-llm]
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml

from utils.llm_client import make_client
from utils.file_io import read_text, write_text
from utils.template_loader import load_template

import appearances as app

SETTING = "data/setting/setting.json"
OUTLINE_DIR = "data/outline/chapters"
CHAPTER_DIRS = ("data/chapters/refined", "data/chapters/checked", "data/chapters/raw")
SUMMARY_RE = re.compile(r"<!--\s*summary:\s*(.+?)\s*-->", re.IGNORECASE | re.S)
TOC_RE = re.compile(r"^#+\s*(第\s*[\d一二三四五六七八九十百]+\s*章.*)$", re.MULTILINE)


def load_rosa_config():
    try:
        cfg = yaml.safe_load(read_text("config/templates_rosa.yaml")) or {}
    except Exception:
        cfg = {}
    rosa = cfg.get("rosa", {})
    defaults = {
        "sandbox_dir": "E:/图书馆/ROSA/Obsidian_AI_Sandbox/10_Inbox",
        "book_template": "E:/图书馆/ROSA/99 模板/官方书籍模板.md",
        "role_template": "E:/图书馆/ROSA/99 模板/官方角色介绍模板.md",
        "role_dirs": [
            "E:/图书馆/ROSA/03 设定/01 人物/01 旧作人物",
            "E:/图书馆/ROSA/03 设定/01 人物/02 新作人物",
            "E:/图书馆/ROSA/03 设定/01 人物/03 次要人物",
        ],
    }
    for k, v in defaults.items():
        rosa.setdefault(k, v)
    return rosa


def load_setting():
    try:
        return json.loads(read_text(SETTING))
    except Exception:
        return {}


def setting_entry(role_name):
    """从设定集取角色条目文本（供 LLM 提示）。"""
    setting = load_setting()
    for c in setting.get("characters", []):
        if isinstance(c, dict) and c.get("name") == role_name:
            return json.dumps(c, ensure_ascii=False, indent=1)[:2000]
    return "（设定集无此角色条目）"


def collect_chapter_summaries():
    """收集各章 summary 注释。返回 {章号: 摘要}。"""
    seen = {}
    for d in CHAPTER_DIRS:
        for p in sorted(Path(d).glob("*.md")):
            try:
                n = int(p.stem)
            except ValueError:
                continue
            if n in seen:
                continue
            m = SUMMARY_RE.search(read_text(p))
            if m:
                seen[n] = m.group(1).strip()
    return seen


def extract_toc():
    """从章节文件提取目录（章标题）。refined > checked > raw 优先级，每章一条。返回 markdown 列表或空串。"""
    found = {}
    for d in CHAPTER_DIRS:
        for p in sorted(Path(d).glob("*.md")):
            try:
                n = int(p.stem)
            except ValueError:
                continue
            if n in found:
                continue
            text = read_text(p)
            m = TOC_RE.search(text)
            found[n] = m.group(1).strip() if m else f"第{n}章"
    lines = [f"- {found[n]}" for n in sorted(found)]
    return "\n".join(lines) if lines else "（待补充）"


def has_rosa_entry(role_name, rosa_dirs):
    """判定角色是否已有 ROSA 词条（按文件名 stem 匹配）。"""
    for d in rosa_dirs:
        p = Path(d) / f"{role_name}.md"
        if p.exists():
            return True
    return False


def format_role_groups(stats, total_chapters):
    """按出场统计分组输出文本（供 LLM 提示 + 用户预览）。"""
    groups = {"major": [], "minor": [], "background": [], "absent": []}
    for name, st in stats.items():
        groups.get(st.get("level"), []).append((name, st))
    lines = []
    labels = {"major": "主要角色", "minor": "次要角色",
              "background": "背景与提及", "absent": "未出场"}
    for lvl in ("major", "minor", "background", "absent"):
        items = groups.get(lvl, [])
        if not items:
            continue
        names = ", ".join(f"{n}（第{st['first']}章起，{st['chapters']}章）"
                          for n, st in items)
        lines.append(f"{labels[lvl]}: {names}")
    return "\n".join(lines)


def role_hints(stats, summaries):
    """为每个出现角色生成一句事迹提示（基于其出场章摘要）。"""
    hints = []
    for name, st in stats.items():
        if st.get("level") == "absent":
            continue
        chs = sorted(st.get("per_chapter", {}))
        ctx = []
        for n in chs:
            s = summaries.get(n, "")
            if s:
                ctx.append(f"第{n}章: {s[:150]}")
        joined = " / ".join(ctx)
        hints.append(f"- {name}（第{st['first']}章起，{st['chapters']}章）: {joined[:400]}")
    return "\n".join(hints)


def build_book_page_task(proj, stats, summaries, toc, book_summary_text):
    """生成作品介绍页 LLM 任务。"""
    role_groups = format_role_groups(stats, len(summaries) or 1)
    hints = role_hints(stats, summaries)
    book = proj.get("book", {})
    today = datetime.now().strftime("%Y-%m-%d")
    _, body = load_template("rosa_book_page.md", {
        "book_name": book.get("name", "未命名"),
        "book_summary": book_summary_text or "（无全书摘要，请基于各章摘要概括）",
        "chapter_summaries": "\n".join(f"- 第{n}章: {s}" for n, s in sorted(summaries.items())),
        "role_groups": role_groups,
        "role_hints": hints,
        "toc": toc,
        "author": book.get("author", ""),
        "genre": book.get("genre", ""),
        "today": today,
        "path_output": "{OUT}",
    })
    return body


def build_role_task(proj, role_name, st, summaries, has_entry, rosa_cfg):
    """生成角色记录/设定页 LLM 任务。"""
    book = proj.get("book", {})
    today = datetime.now().strftime("%Y-%m-%d")
    # 出场章摘要（含前后各1章）
    chs = sorted(st.get("per_chapter", {}))
    ctx = {}
    for n in chs:
        ctx[n] = summaries.get(n, "")
    ctx_lines = "\n".join(f"- 第{n}章: {s}" for n, s in sorted(ctx.items()) if s)
    _, body = load_template("rosa_role_page.md", {
        "book_name": book.get("name", "未命名"),
        "role_name": role_name,
        "setting_entry": setting_entry(role_name),
        "appearance": json.dumps(st, ensure_ascii=False)[:800],
        "chapter_summaries": ctx_lines or "（无）",
        "has_rosa_entry": "true" if has_entry else "false",
        "level": st.get("level", ""),
        "year": str(datetime.now().year),
        "first": st.get("first"),
        "chapters": st.get("chapters", 0),
        "today": today,
        "path_output": "{OUT}",
    })
    return body


def run(proj, mode="all", client=None, task_dir="data/state/tasks",
        dry_run=False, no_llm=False, only_roles=None):
    rosa = load_rosa_config()
    sandbox = Path(rosa["sandbox_dir"])
    sandbox.mkdir(parents=True, exist_ok=True)

    # 1) 出场统计（确定性）
    stats, total_chapters, new_candidates = app.count_appearances()
    if not stats:
        return False, "未找到角色或章节（先跑 stage3/4）"
    print(f"[rosa] 出场统计：{len(stats)} 角色 / {total_chapters} 章")

    # 1.5) 精确选择（--roles）：只处理指定角色
    if only_roles:
        stats = {n: st for n, st in stats.items() if n in only_roles}
        print(f"[rosa] --roles 过滤后：{len(stats)} 角色（{('、'.join(stats))}）")

    # 2) 各章摘要（确定性）
    summaries = collect_chapter_summaries()
    toc = extract_toc()
    book = proj.get("book", {})
    book_name = book.get("name", "未命名")

    # 3) 已有 ROSA 词条判定
    rosa_dirs = rosa.get("role_dirs", [])
    with_entry = [n for n, st in stats.items()
                  if st.get("in_setting") and has_rosa_entry(n, rosa_dirs)]
    need_page = [n for n, st in stats.items()
                 if st.get("level") != "absent" and not st.get("in_setting")]
    print(f"[rosa] 已有词条角色：{len(with_entry)} 个（出出场记录）")
    print(f"[rosa] 无词条新角色：{len(need_page)} 个（出设定草稿）")

    plans = []
    if mode in ("books", "all"):
        plans.append("作品介绍页")
    if mode in ("roles", "all"):
        plans.append(f"角色出场记录（{len(with_entry)} 个）")
        if need_page:
            plans.append(f"新角色设定页（{len(need_page)} 个：{'、'.join(need_page)}）")

    if dry_run:
        print("[rosa] --dry-run：只统计与规划，不生成任务/不调 LLM")
        print("[rosa] 计划:", "、".join(plans))
        print(f"[rosa] 输出目录: {sandbox}")
        return True, "dry-run（仅统计与规划）"

    client = client or make_client(
        yaml.safe_load(read_text("config/system.yaml")), "default")
    generated = []

    # 4) 作品介绍页（LLM 聚合，no_llm 时降级骨架）
    if mode in ("books", "all"):
        out = sandbox / f"{book_name}_作品介绍页_草稿.md"
        if no_llm:
            skeleton = (f"# {book_name} 作品介绍页（骨架，未生成简介——--no-llm）\n\n"
                        f"## 登场角色分组\n\n{format_role_groups(stats, total_chapters)}\n\n"
                        f"## 目录\n\n{toc}\n")
            write_text(out, skeleton)
            generated.append(str(out))
            print(f"[rosa] 作品介绍页骨架（no-llm）→ {out}")
        else:
            book_summary_text = ""
            bs = Path(f"output/{book_name}_全书摘要.md")
            if bs.exists():
                book_summary_text = read_text(bs)[:3000]
            task = client.write_task(
                task_dir, "rosa_book_page.md",
                build_book_page_task(proj, stats, summaries, toc, book_summary_text)
                .replace("{OUT}", str(out)))
            result = client.run_task(task)
            if result["exit_code"] != 0:
                print(f"[rosa] 作品介绍页子会话失败（跳过）")
            elif out.exists():
                generated.append(str(out))
                print(f"[rosa] 作品介绍页 → {out}")

    # 5) 角色出场记录 + 新角色设定页
    if mode in ("roles", "all"):
        if no_llm:
            # 确定性降级：出场记录用出场统计 + 章摘要片段，不调 LLM
            rec_lines = []
            for name in with_entry:
                st = stats[name]
                frag = next((summaries[n] for n in sorted(st.get("per_chapter", {}))
                             if n in summaries), "（待补充）")
                rec_lines.append(
                    f"### {name}\n\n## 官作出场记录\n\n"
                    f"- {datetime.now().year}年\n"
                    f"    - `[[{book_name}]]`：{frag[:200]}"
                    f"（第{st['first']}章起，共{st['chapters']}章出场）\n")
            if rec_lines:
                out = sandbox / f"{book_name}_角色出场记录_草稿.md"
                header = (f"# 《{book_name}》角色出场记录草稿（--no-llm 骨架）\n\n"
                          f"> 由 NovelForge 自动生成（{datetime.now().strftime('%Y-%m-%d')}）。\n"
                          f"> 请将各角色段落粘贴到 ROSA 对应词条的「官作出场记录」节，并润色事迹。\n\n")
                write_text(out, header + "\n".join(rec_lines))
                generated.append(str(out))
                print(f"[rosa] 角色出场记录骨架（no-llm）→ {out}")
            for name in need_page:
                st = stats[name]
                out = sandbox / f"{name}_设定_草稿.md"
                skeleton = (f"# {name} 角色设定草稿（骨架，待人工补充——--no-llm）\n\n"
                            f"## 出场\n\n- 首出场：第{st['first']}章；共{st['chapters']}章；"
                            f"分级：{st.get('level')}\n\n"
                            f"## 角色信息\n\n- 正式名称：{name}\n"
                            f"- 别名：（待补充）\n- 称号：（待补充）\n"
                            f"- 种族：（待补充）\n- 能力：（待补充）\n"
                            f"- 所属世界：（待补充）\n\n"
                            f"## 角色介绍\n\n（待补充）\n\n"
                            f"## 官作出场记录\n\n- {datetime.now().year}年\n"
                            f"    - `[[{book_name}]]`：（待补充）"
                            f"（第{st['first']}章起，共{st['chapters']}章出场）\n")
                write_text(out, skeleton)
                generated.append(str(out))
                print(f"[rosa] 新角色设定骨架（no-llm）→ {out}")
        else:
            # 已有词条：出场记录段落（合并到一个文件）
            rec_lines = []
            for name in with_entry:
                st = stats[name]
                task = client.write_task(
                    task_dir, "rosa_role_page.md",
                    build_role_task(proj, name, st, summaries, True, rosa)
                    .replace("{OUT}", str(sandbox / f"_role_tmp_{name}.md")))
                result = client.run_task(task)
                tmp = sandbox / f"_role_tmp_{name}.md"
                try:
                    if result["exit_code"] == 0 and tmp.exists():
                        text = read_text(tmp)
                        m = re.search(r"## 官作出场记录.*?(?=\n## |\Z)", text, re.S)
                        if m:
                            rec_lines.append(f"### {name}\n\n{m.group(0).strip()}\n")
                finally:
                    tmp.unlink(missing_ok=True)
            if rec_lines:
                out = sandbox / f"{book_name}_角色出场记录_草稿.md"
                header = (f"# 《{book_name}》角色出场记录草稿\n\n"
                          f"> 由 NovelForge 自动生成（{datetime.now().strftime('%Y-%m-%d')}）。\n"
                          f"> 请将各角色段落粘贴到 ROSA 对应词条的「官作出场记录」节。\n\n")
                write_text(out, header + "\n".join(rec_lines))
                generated.append(str(out))
                print(f"[rosa] 角色出场记录 → {out}")
            elif with_entry:
                print("[rosa] 出场记录生成失败或为空")

            # 无词条新角色：设定页草稿（每个角色一个文件）
            for name in need_page:
                st = stats[name]
                out = sandbox / f"{name}_设定_草稿.md"
                task = client.write_task(
                    task_dir, "rosa_role_page.md",
                    build_role_task(proj, name, st, summaries, False, rosa)
                    .replace("{OUT}", str(out)))
                result = client.run_task(task)
                if result["exit_code"] == 0 and out.exists():
                    generated.append(str(out))
                    print(f"[rosa] 新角色设定草稿 → {out}")

    print(f"[rosa] 完成。生成 {len(generated)} 个文件：")
    for g in generated:
        print(f"  {g}")
    return True, f"完成（生成 {len(generated)} 个文件）"


def main():
    parser = argparse.ArgumentParser(description="NovelForge ROSA 后处理（完书后）")
    parser.add_argument("--books", action="store_true", help="只生成作品介绍页")
    parser.add_argument("--role-records", dest="role_records", action="store_true",
                        help="只生成角色记录/新角色设定")
    parser.add_argument("--all", dest="mode_all", action="store_true", help="两者（默认）")
    parser.add_argument("--dry-run", action="store_true", help="只统计与规划，不调 LLM")
    parser.add_argument("--no-llm", action="store_true", help="跳过 LLM 聚合（出场统计照出）")
    parser.add_argument("--roles", default=None,
                        help="只处理指定角色（逗号分隔，如 露汐,小林）")
    args = parser.parse_args()

    mode = "all"
    if args.books and not args.role_records:
        mode = "books"
    elif args.role_records and not args.books:
        mode = "roles"

    only_roles = None
    if args.roles:
        only_roles = {r.strip() for r in args.roles.split(",") if r.strip()}
        if not args.books:  # --roles 隐含只处理角色（除非同时显式 --books）
            mode = "roles"

    proj = yaml.safe_load(read_text("config/project.yaml"))
    ok, msg = run(proj, mode=mode, dry_run=args.dry_run, no_llm=args.no_llm,
                  only_roles=only_roles)
    print(f"[rosa] {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
