# -*- coding: utf-8 -*-
"""产物闸：候选区 → 作者批准 → 才进正式路径。

## 为什么需要它

`engine: self` 下，外部执行方（agent）按任务书写产物。现状是**直接写正式路径**
（`data/setting/setting.json`、`data/chapters/raw/NN.md`），程序只看文件在不在
就判完成 —— 等于全程没有一个人能作主。用户 2026-11-01 的判词是"从按下开始到
拿到稿子，没有任何一个点是人能作主的"，2026-11-11 仍是同一句。

本模块把"作者批过没有"变成程序化判据：**未批准的产物，程序不承认。**

## 锁为什么在这里，不在壳上

在 GUI 里拦、在 CLI 里拦，都不算锁：换一个入口就绕过了。唯一在所有路径汇合点上
的判据是"这个文件有没有批准记录"（且内容 sha256 对得上）。所以锁放在这里，
任何调用方（GUI / CLI / agent / 阶段代码）都要过同一道门。

## 三段流程

1. 执行方写候选：`data/_pending/stageN/<正式相对路径>`
2. 作者批准（界面动作）：`record_approval()` 记下 `{相对路径: sha256}`
3. 程序合成：`promote()` 校验 sha 一致 → 备份旧正式文件 → 复制进正式路径

**sha 绑内容，不绑文件名**：批准之后执行方再改一版，sha 对不上 → 拒收。
这与 stage5 的 `stale` 判据同源（拿旧裁定套新稿是最容易犯的错）。

## 接入是逐阶段的，不是一次全开

`REGISTERED_STAGES` 里有的阶段才走这套；没登记的阶段维持原行为。
这样不会因为某个阶段还没改就把它拦死，也不会出现"登记了却没人批"的半截状态。

2026-11-12：**阶段 1~6 已全部登记**（2/3/5/6 一次补齐；此前只有 1 与 4 是真的）。
阶段 7/8 是本地导出（Word / Markdown），不经外部执行方，不在此列。
"""
import hashlib
import json
from pathlib import Path

#: 候选区根（相对项目根）。刻意不用 `data/state/`：那是程序自己的状态，
#: 而候选是**待作者过目**的东西，混进去会被清理脚本扫掉。
PENDING_ROOT = "data/_pending"

#: 批准记录与旧稿备份的根。与 stage5 的 `data/review/stage5/` 同一家族。
REVIEW_ROOT = "data/review"

#: 已接入本闸的阶段 → 该阶段的正式产物（相对项目根）。
#: **唯一真源**：orchestrator 的等待分支与 API 的批准清单都读这里，
#: 不许各写一份（本项目吃过“同一判据写两遍、只修一处”的亏）。
#: 新接一个阶段 = 加一行 + 该阶段 `self_done` 走 `promote()`。
STAGE_PRODUCTS = {
    1: ["data/setting/setting.json"],
    2: ["data/outline/global.md"],
}

#: 目录型产物（2026-11-11）：章数是作者配的，文件数写不死。
#: (目录, 模式) —— `product_rels()` 展开成具体文件清单，其余逻辑完全一样。
STAGE_PRODUCT_GLOBS = {
    3: ("data/outline/chapters", "*.md"),
    4: ("data/chapters/raw", "*.md"),
    5: ("data/chapters/checked", "*.md"),
    6: ("data/chapters/refined", "*.md"),
}

#: 已接入本闸的阶段（固定清单与目录型都算）。
#: 2026-11-12：2 / 3 / 5 / 6 一次补齐 —— 此前只有 1 与 4 是真的，其余阶段的锁是假的。
REGISTERED_STAGES = frozenset(set(STAGE_PRODUCTS) | set(STAGE_PRODUCT_GLOBS))

#: 阶段的人话名。**只给名字**，怎么说由界面决定（这里不写文案规则）。
STAGE_LABELS = {
    1: "设定集",
    2: "整体大纲",
    3: "分章大纲",
    4: "正文",
    5: "检查稿",
    6: "润色稿",
}


def stage_label(stage):
    return STAGE_LABELS.get(int(stage), "阶段%d" % int(stage))


def product_rels(root, stage):
    """该阶段**当前**的产物相对路径清单（固定清单 + 候选目录展开，去重保序）。

    目录型产物从**候选区**展开，不是从正式目录：待批的东西在候选区，
    正式目录里的是“已合成”的旧结果。走正式目录会得出“没有待批”的假结论。
    """
    out = list(STAGE_PRODUCTS.get(int(stage)) or [])
    spec = STAGE_PRODUCT_GLOBS.get(int(stage))
    if spec:
        base, pat = spec
        pdir = Path(root) / PENDING_ROOT / ("stage%d" % int(stage)) / base
        if pdir.is_dir():
            for p in sorted(pdir.glob(pat)):
                rel = base + "/" + p.name
                if rel not in out:
                    out.append(rel)
    return out


def is_registered_stage(stage):
    return int(stage) in REGISTERED_STAGES


def formal_rels(root, stage):
    """正式路径上**现在就有**的产物清单（升级迁移 / 批量搬进候选区用）。"""
    stage = int(stage)
    out = []
    for rel in STAGE_PRODUCTS.get(stage) or []:
        if (Path(root) / rel).exists():
            out.append(rel)
    spec = STAGE_PRODUCT_GLOBS.get(stage)
    if spec:
        base, pat = spec
        d = Path(root) / base
        if d.is_dir():
            for p in sorted(d.glob(pat)):
                rel = base + "/" + p.name
                if rel not in out:
                    out.append(rel)
    return out


def is_product_rel(root, stage, rel):
    """这个相对路径**是不是该阶段的产物**（固定清单直配，或命中目录型模式）。

    不用 `product_rels()` 判：那个只列"候选区里真有的"，
    而这里要回答的是"这个路径归不归这个阶段管"（作者手改时要按它留痕）。
    """
    stage = int(stage)
    rel = str(rel).replace("\\", "/").lstrip("/")
    if rel in (STAGE_PRODUCTS.get(stage) or []):
        return True
    spec = STAGE_PRODUCT_GLOBS.get(stage)
    if not spec:
        return False
    base, pat = spec
    if not rel.startswith(base + "/"):
        return False
    name = rel[len(base) + 1:]
    if not name or "/" in name:
        return False
    from fnmatch import fnmatch
    return fnmatch(name, pat)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_registered(stage):
    return int(stage) in REGISTERED_STAGES


def pending_rel(stage, rel):
    """正式相对路径 → 候选相对路径。`data/setting/setting.json` →
    `data/_pending/stage1/data/setting/setting.json`。

    保留原路径层级（而不是只留文件名）：两个阶段可能产出同名文件，
    拍平之后会互相覆盖。
    """
    return "%s/stage%d/%s" % (PENDING_ROOT, int(stage), str(rel).replace("\\", "/").lstrip("/"))


def pending_path(root, stage, rel):
    return Path(root) / pending_rel(stage, rel)


def approval_path(root, stage):
    return Path(root) / REVIEW_ROOT / ("stage%d" % int(stage)) / "approvals.json"


def load_approvals(root, stage):
    p = approval_path(root, stage)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def status(root, stage, rel):
    """候选/批准/正式三者的当前关系。只读，不改任何文件。

    返回 `{"state": ..., "pending": 相对路径, "sha": ..., "approved_sha": ..., "why": ...}`
    state 取值：
    - `no_pending`：执行方还没写候选
    - `pending`    ：候选在，没人批
    - `approved`   ：候选在且批准的就是这一版 —— 可以合成
    - `stale`      ：批过，但候选被改过（sha 变了）—— 必须重批
    """
    prel = pending_rel(stage, rel)
    p = Path(root) / prel
    out = {"state": "no_pending", "pending": prel, "sha": "", "approved_sha": "", "why": ""}
    if not p.exists():
        out["why"] = "候选区没有这个产物：%s" % prel
        return out
    out["sha"] = _sha256(p)
    rec = load_approvals(root, stage).get(str(rel))
    if not rec:
        out["state"] = "pending"
        out["why"] = "候选已就位，等作者批准（%s）" % prel
        return out
    if rec.get("rejected"):
        out["state"] = "rejected"
        out["why"] = "作者驳回了这一版（%s）" % (rec.get("note") or "未写理由")
        return out
    out["approved_sha"] = str(rec.get("sha256") or "")
    if out["approved_sha"] != out["sha"]:
        out["state"] = "stale"
        out["why"] = "批过之后候选又被改过（批准的是 %s…，现在是 %s…），需要重新批" % (
            out["approved_sha"][:8], out["sha"][:8])
        return out
    out["state"] = "approved"
    return out


def record_approval(root, stage, rel, by="gui", note=""):
    """记一条批准。返回 `(ok, 信息dict)`；候选不存在 → 拒绝（不许批空气）。

    `by` 只作留痕：谁批的（界面 / 命令行）。本模块不判断调用者身份 ——
    身份闸在端点层（`agent_guard`），这里只管"批的是哪一版"。
    """
    st = status(root, stage, rel)
    if st["state"] == "no_pending":
        return False, {"error": st["why"], "pending": st["pending"]}
    recs = load_approvals(root, stage)
    recs[str(rel)] = {"sha256": st["sha"], "by": str(by), "note": str(note)}
    p = approval_path(root, stage)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(recs, ensure_ascii=False, indent=2), encoding="utf-8")
    return True, {"rel": str(rel), "sha256": st["sha"], "file": str(p.as_posix())}


def import_legacy(root, stage, rel, by="legacy-import"):
    """把正式路径上**已有**的产物搬进候选区（**不自动批准**）。

    只解决"升级前的老项目"这一步：搬过去之后仍要作者批准一次。
    刻意不自动批准 —— 自动批准等于这把锁是假的，那还不如不做。
    """
    src = Path(root) / str(rel).replace("\\", "/")
    if not src.exists():
        return False, "正式路径上没有产物：%s" % rel, {}
    dst = pending_path(root, stage, rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    return True, "已搬进候选区：%s（仍需作者批准才会被采用）" % pending_rel(stage, rel), {
        "pending": pending_rel(stage, rel), "by": str(by)}


def record_rejection(root, stage, rel, note=""):
    """记一条驳回。候选文件**不删**（执行方可以改一版再来），但这一版不会被合成。

    驳回是合法终态：正式路径保持原样，程序不追问。
    """
    st = status(root, stage, rel)
    if st["state"] == "no_pending":
        return False, {"error": st["why"]}
    recs = load_approvals(root, stage)
    recs[str(rel)] = {"rejected": True, "sha256": st["sha"], "note": str(note)}
    p = approval_path(root, stage)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(recs, ensure_ascii=False, indent=2), encoding="utf-8")
    return True, {"rel": str(rel), "sha256": st["sha"], "rejected": True}


def promote(root, stage, rel, overwrite=True):
    """候选 → 正式。**唯一**被允许写正式产物的函数。

    返回 `(ok, msg, info)`：
    - 未批准 / 已过期 → `ok=False`，且**一个字节都不写**（合成本身可重试）
    - 已批准 → 备份旧正式文件到 `data/review/stageN/prev/`，再复制

    旧文件备份是硬要求：合成之后作者仍要能退回（拒绝是合法终态，
    合错也必须能退）。
    """
    st = status(root, stage, rel)
    if st["state"] != "approved":
        return False, st["why"] or ("未批准，不能写正式路径：%s" % rel), st
    src = Path(root) / st["pending"]
    dst = Path(root) / str(rel).replace("\\", "/")
    info = {"rel": str(rel), "sha256": st["sha"], "backup": ""}
    if dst.exists():
        bak = (Path(root) / REVIEW_ROOT / ("stage%d" % int(stage)) / "prev" / str(rel).replace("\\", "/"))
        bak.parent.mkdir(parents=True, exist_ok=True)
        bak.write_bytes(dst.read_bytes())
        info["backup"] = str(bak.relative_to(Path(root)).as_posix())
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    return True, "已合成 %s（批准版本 %s…）" % (rel, st["sha"][:8]), info


def approved_formal(root, stage, rel):
    """正式路径上**当前这一版**是不是作者批过的（sha 对得上，且不是驳回记录）。

    给"能不能直接复用已有产物"这类判据用。只说"文件在"就够了的地方
    （历史上就是那么写的）会让**绕过候选区直接写正式路径**变成合法捷径 ——
    这是本闸最容易漏的一个口子：锁在合成上，复用在旁边开了后门。
    """
    dst = Path(root) / str(rel).replace("\\", "/")
    if not dst.exists():
        return False
    rec = load_approvals(root, stage).get(str(rel)) or {}
    if rec.get("rejected") or not rec.get("sha256"):
        return False
    return str(rec.get("sha256")) == _sha256(dst)


def pending_note(root, stage):
    """候选已就位、只差作者批准的那一份（没有就返回 None）。

    给各阶段 `self_done` 用：没有这句话，作者会看到"还有 N 章没写"，
    而实际是"执行方已经交回了，在等你点头"—— 等于让作者等自己。
    """
    for rel in product_rels(root, stage):
        st = status(root, stage, rel)
        if st.get("state") in ("pending", "stale"):
            return st
    return None


def promote_approved(root, stage, rels=None):
    """把该阶段**已批准**的候选一次性合成，返回真正合成成功的相对路径。

    各阶段的 `self_done` 都调它（唯一实现）：漏一次就等于那个阶段的锁是假的。
    没批准/已过期的候选一律跳过 —— 它们留在候选区等作者，不影响其它章。
    """
    done = []
    for rel in (product_rels(root, stage) if rels is None else rels):
        try:
            if status(root, stage, rel).get("state") != "approved":
                continue
            ok, _msg, _info = promote(root, stage, rel)
            if ok:
                done.append(rel)
        except Exception:                                     # noqa: BLE001
            continue
    return done


def record_author_write(root, stage, rel, by="gui-edit", note="作者亲手改的"):
    """作者亲手把**正式路径**上的这一份写成现在这样 → 记一条批准。

    为什么需要：产物闸只认"作者批过的那一版"。作者在界面上直接改正文 / 大纲，
    是比"批准候选"更直接的动作 —— 若不留痕，下一次自任务运行会把它判成
    "正式路径上有产物但没批过"，逼他去 import_legacy（等于把作者自己的手稿
    当可疑物处理）。

    只写登记过、且路径确属该阶段产物的那一种；其余静默返回 False（不抛）。
    调用点必须是**可信 GUI** 的写入（agent 写的走候选区，不许在这里留痕）。
    """
    stage = int(stage)
    if not is_registered_stage(stage) or not is_product_rel(root, stage, rel):
        return False
    dst = Path(root) / str(rel).replace("\\", "/")
    if not dst.exists():
        return False
    recs = load_approvals(root, stage)
    recs[str(rel)] = {"sha256": _sha256(dst), "by": str(by), "note": str(note)}
    p = approval_path(root, stage)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(recs, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def legacy_hint(stage, rel):
    """正式路径上有产物、候选区却没有时给人话（升级前的老项目 / 绕过候选区两种可能）。

    刻意**不自动批准**：自动批准等于这把锁是假的。搬进候选区之后仍要作者批一次。
    """
    return ("正式路径上已有 %s，但候选区没有对应记录 —— 可能是升级前的旧产物，"
            "也可能是执行方绕过了候选区。请把它搬进候选区以备批准："
            "python -c \"import sys;sys.path.insert(0,'scripts');"
            "from utils import product_gate as p;p.import_legacy('.', %d, '%s')\""
            % (rel, int(stage), rel))
