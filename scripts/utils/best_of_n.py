# -*- coding: utf-8 -*-
"""Best-of-N 择优（B2）：同一章生成 N 个改写候选 → 确定性打分 → 留最好的那版。

## 它解决的问题

阶段 4 / 自动重写此前只有“一次机会”：模型写坏了就打回、再写一次，好坏全凭运气，
而且**看不出为什么这版更差**。Best-of-N 把“多写几版再挑”变成一条可复算的流程：

1. 用同一份修订意见跑 N 次（N 默认 3）；
2. 每个候选都存到 `data/state/candidates/chNN/cand{i}.md`（**留证**，不是只留赢家）；
3. 用 `utils/candidate_score`（零 token、五维、每条都有理由）排序；
4. 胜者写回章节，落 `result.json`（含每个候选的分数与理由）供界面并排展示。

## 边界（不做的事）

- **不自动开**：`gates.best_of_n` 默认 0（关）。开一次 = N 倍 token，得作者自己点头；
- 不判断“文学好坏”：打分只用来排序，结论仍归判定引擎与作者；
- 失败不吞：某个候选跑挂就如实记 `status=failed` 并参与排序（分数为空），
  全都挂 → 返回失败且**不动正文**。

用法（CWD 必须是项目根；与 batch_refine 同一前提）：
    python scripts/best_of_n.py --chapter 3 --n 3
    python scripts/best_of_n.py --chapter 3 --n 3 --dry-run     # 只算不算（不调模型）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# batch_refine 住在 scripts/（不在 utils/）：本模块被 nf_api / auto_rewrite 以
# `utils.best_of_n` 导入时，scripts/ 未必在 sys.path 上 —— 自己补，别靠调用方。
_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import batch_refine as br                                        # noqa: E402
from utils.file_io import read_text, write_text                  # noqa: E402

CAND_ROOT = "data/state/candidates"
REPORT_REL = "data/state/candidates/index.json"


@contextmanager
def _in_root(root: str | Path):
    """临时把 CWD 切到项目根。

    为什么不用 `Path(root)/...` 全量改写：`batch_refine` 的 HISTORY_DIR / OUTLINE_DIR
    都是模块级相对路径，它自己就在 CWD 上跑。与其把人家整条链改成 root 参数，
    不如在这里进出一次 CWD —— 前提写死在模块 docstring 里，行为与既有脚本一致。
    """
    old = os.getcwd()
    os.chdir(str(root or "."))
    try:
        yield
    finally:
        os.chdir(old)


def _cand_dir(chapter: int) -> Path:
    return Path(CAND_ROOT) / ("ch%02d" % chapter)


def load_result(chapter: int) -> Optional[Dict[str, Any]]:
    """读某一章的候选结果（界面用）；没有返回 None。"""
    p = _cand_dir(chapter) / "result.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _target_words(cfg: Dict[str, Any]) -> List[int]:
    try:
        tw = ((cfg.get("chapter") or {}).get("target_words")) or []
        if isinstance(tw, (list, tuple)) and len(tw) >= 2:
            return [int(tw[0]), int(tw[1])]
    except (TypeError, ValueError):
        pass
    return []


def _cliche_terms(root: str | Path):
    """病灶词表（界面表 > config > 内置）。拿不到就返回 None = 该项未评。"""
    try:
        from utils import wordlists as wl
        return wl.load_cliche(root)["terms"]
    except Exception:                                            # noqa: BLE001
        return None


def run_best_of_n(cfg, proj, chapter: int, feedback: str, n: int = 3,
                  client=None, task_dir: Optional[str] = None,
                  dry_run: bool = False, root: str | Path = ".",
                  use_client=None) -> Dict[str, Any]:
    """生成 N 个候选并择优落盘。返回结果字典（也写进 result.json）。

    返回结构：`{ok, chapter, n, chosen, candidates[], message, dry_run}`。
    `ok` 只在“至少一个候选成功且胜者已写回”时为 True。
    """
    from utils import candidate_score as cs

    n = max(2, int(n or 3))
    out_dry = {"ok": False, "chapter": chapter, "n": n, "dry_run": True,
               "chosen": None, "candidates": [],
               "message": "dry-run：未调用模型，没有生成候选"}
    with _in_root(root):
        chap_path = br.pick_chapter_path(chapter)
        if chap_path is None:
            return {"ok": False, "chapter": chapter, "n": n, "chosen": None,
                    "candidates": [], "message": "第 %d 章不存在（raw/checked/refined 均无）" % chapter,
                    "dry_run": dry_run}
        if dry_run:
            out_dry["message"] = ("dry-run：将生成 %d 个候选（预计 %d 次模型调用），"
                                  "当前章 %s" % (n, n, chap_path))
            return out_dry

        original = read_text(chap_path)
        cdir = _cand_dir(chapter)
        if cdir.exists():
            shutil.rmtree(cdir)          # 只清这一章的旧候选，别的章不动
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "original.md").write_text(original, encoding="utf-8", newline="\n")

        cands: List[Dict[str, Any]] = []
        for i in range(1, n + 1):
            if i > 1:
                # 每一轮都从原文出发：否则第 2 轮是在第 1 轮改过的稿子上再改，
                # 三个候选就不是“同一道题的三份答卷”，排序失去意义。
                write_text(chap_path, original)
            sink_err = ""
            try:
                ok, msg, _backup = br.run_single_refine(
                    cfg, proj, chapter, feedback, client=client,
                    task_dir=task_dir or "data/state/tasks")
            except Exception as e:                               # noqa: BLE001
                ok, msg = False, "%s: %s" % (type(e).__name__, str(e)[:160])
            text = read_text(chap_path) if chap_path.exists() else ""
            if not ok or not (text or "").strip():
                cands.append({"id": "cand%d" % i, "status": "failed",
                              "error": (msg or "精修失败")[:200], "text": ""})
                (cdir / ("cand%d.md" % i)).write_text(
                    "（候选 %d 生成失败：%s）\n" % (i, msg), encoding="utf-8")
                continue
            p = cdir / ("cand%d.md" % i)
            p.write_text(text, encoding="utf-8", newline="\n")
            cands.append({"id": "cand%d" % i, "status": "ok", "path": str(p),
                          "text": text, "msg": msg})

        # —— 打分（只用成功的候选；失败项照实列出，不参与排名）——
        good = [c for c in cands if c.get("status") == "ok"]
        if not good:
            write_text(chap_path, original)     # 全挂：正文一个字都不动
            return {"ok": False, "chapter": chapter, "n": n, "chosen": None,
                    "candidates": [{"id": c["id"], "status": c["status"],
                                    "error": c.get("error")} for c in cands],
                    "message": "全部 %d 个候选都失败，正文未改动" % n, "dry_run": False}

        rows = cs.score_candidates(
            [{"id": c["id"], "text": c["text"]} for c in good],
            target_words=_target_words(cfg) or None,
            cliche_terms=_cliche_terms(root))
        by_id = {r["id"]: r for r in rows}
        ranked = []
        for c in cands:
            r = by_id.get(c["id"])
            ranked.append({
                "id": c["id"],
                "status": c["status"],
                "path": c.get("path", ""),
                "score": (r or {}).get("score"),
                "words": (r or {}).get("words"),
                "dims": (r or {}).get("dims"),
                "reasons": (r or {}).get("reasons") or ([c.get("error")] if c.get("error") else []),
            })
        winner_id = rows[0]["id"]
        winner = next(c for c in good if c["id"] == winner_id)

        # 胜者写回（先留一份“择优前”的版本，出事能回滚）
        version = br.next_version(chapter)
        br.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        backup = br.HISTORY_DIR / ("ch%02d_v%d.md" % (chapter, version))
        shutil.copy2(chap_path, backup)
        write_text(chap_path, winner["text"])

        result = {
            "ok": True,
            "chapter": chapter,
            "n": n,
            "dry_run": False,
            "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "target_words": _target_words(cfg),
            "chosen": winner_id,
            "chosen_score": rows[0]["score"],
            "candidates": ranked,
            "backup": str(backup),
            "message": "已择优落盘：%s（%s 分）；候选 %d 个，失败 %d 个"
                       % (winner_id, rows[0]["score"], n, n - len(good)),
        }
        write_text(cdir / "result.json", json.dumps(result, ensure_ascii=False, indent=2))
        _update_index(chapter, result)
        return result


def _update_index(chapter: int, result: Dict[str, Any]) -> None:
    """在 `data/state/candidates/index.json` 里记一笔（界面列“哪些章做过择优”）。"""
    p = Path(REPORT_REL)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[str(chapter)] = {
        "chapter": chapter,
        "generated_at": result.get("generated_at"),
        "chosen": result.get("chosen"),
        "chosen_score": result.get("chosen_score"),
        "n": result.get("n"),
    }
    write_text(p, json.dumps(data, ensure_ascii=False, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser(description="Best-of-N：同一章多写几版，按确定性打分挑最好的")
    ap.add_argument("--chapter", type=int, required=True)
    ap.add_argument("--n", type=int, default=3, help="候选数（默认 3；最少 2）")
    ap.add_argument("--feedback", default="",
                    help="修订意见（默认用 auto_rewrite 的同款模板：不改情节，收敛文风）")
    ap.add_argument("--dry-run", action="store_true", help="只算不跑：不调用模型")
    ap.add_argument("--root", default=".")
    args = ap.parse_args()

    from utils.config_io import load_config_yaml
    from utils.project_config import load_project_yaml
    cfg = load_config_yaml(str(Path(args.root) / "config" / "system.yaml")) or {}
    try:
        proj = load_project_yaml(str(Path(args.root) / "config" / "project.yaml")) or {}
    except Exception:                                            # noqa: BLE001
        proj = {}
    thr = int((cfg.get("chapter") or {}).get("quality_threshold", 6) or 6)
    feedback = args.feedback or (
        "本章 quality 自评低于阈值 %d。请在**不改变情节与结构**的前提下重写："
        "① 收敛冗余形容与比喻连缀；② 补充具体动作与感官细节；③ 视角统一；"
        "④ 与大纲及设定 locked 条目一致；⑤ 保留 `<!-- summary: -->` 与 `<!-- quality: -->` 注释。" % thr)

    res = run_best_of_n(cfg, proj, args.chapter, feedback, n=args.n,
                        dry_run=args.dry_run, root=args.root)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") or res.get("dry_run") else 1


if __name__ == "__main__":
    sys.exit(main())
