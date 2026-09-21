# -*- coding: utf-8 -*-
"""本轮新增端点的 HTTP 端到端自检（校对 / 预估 / 文风 / 拆书 / 模型切换 / 导出）。

**不碰真实仓库数据**：把 scripts/ config/ prompts/ 复制到临时目录当 ROOT 起
nf_api（--allow-fake），所有读写都落在临时目录。

覆盖：
  GET  /estimate（全流程 / 单阶段 / 多阶段 / 强制字符折算）—— 字段完整、口径标注
  GET  /proofread/report  无报告 → 404 + 可行动提示（不是 500）
  POST /proofread/run     确定性校对 → job → 报告落盘、finding 带 suggested_action
  POST /style/analyze     source=path / chapter / chapter+compare / 非法 source / 短文本
  GET  /book/pacing       ?source=current 实时算本书；无拆书结果 → 404 + 提示
  POST /book/split        拆外部文本 → 落盘 → GET /book/pacing 回读一致
  POST /models/switch     写回 config/system.yaml；未知角色 → 400
  POST /export/markdown   不再是 404（GET 版曾因 do_GET 内引用未定义变量而 500）
  AST 静态检查：do_GET / do_POST 内不得有本地名遮蔽模块级名（防 UnboundLocalError）

用法：python tests/test_new_endpoints_api_http.py
"""
import ast
import io
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
PORT = 8931
BASE = "http://127.0.0.1:%d" % PORT

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def req(method, path, body=None, timeout=60):
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if method == "POST" else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:
            payload = {}
        return e.code, payload


def wait_job(job_id, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        code, d = req("GET", "/jobs/" + job_id)
        if code == 200 and d.get("state") != "running":
            return d
        time.sleep(0.4)
    return {"state": "timeout"}


# 刻意埋入确定性缺陷：省略号不统一 / 半角逗号 / 破折号单写 / 错字 / 缺句末标点
# 后半段是干净正文，把章节撑到 >100 字符（style_analyzer 的特征下限），
# 否则文风对比会因"样本不足"被跳过，测不到 drift 分支。
REFINED = """## 第{n}章 测试章节

他做为长子，既使心里不服，也只能按耐着性子点头。

夜色很深，仿佛一切都没有发生过....

风从门缝里钻进来,带着潮气,冷得出奇。

她没有回答—只是看着窗外

“回来了？”她问。

他应了一声，把伞靠在门边，走近炉火，伸手烤了烤，才慢慢说起路上遇见的那些人和事。

炉火噼啪地响，屋里的影子在墙上晃来晃去。夜很长，他们谁也没有急着把话说完。
"""

STYLE_SAMPLE = (
    "他推门进来，把伞靠在门边。\n\n"
    "“回来了？”她问。\n\n"
    "他没有回答，只是看着窗外的雨，看着雨丝斜斜地落下来，落在巷口的灯上。\n\n"
    "灯在雨里晃，晃成一片模糊的黄。他忽然想起很多年前的一个黄昏，"
    "那时他还小，站在同样的巷口等一个不会回来的人。\n\n"
    "“吃饭了。”她说。\n\n"
    "他应了一声，转身进了屋，把雨关在门外。\n"
)

BOOK_TEXT = "".join(
    "第%s章 参考章节%d\n\n%s\n\n" % ("一二三四五六七"[i - 1] if i <= 7 else "八", i,
                                  "他抬头看了看灰蒙蒙的天。\n\n“走吧。”他说。\n\n"
                                  "雨丝斜斜地落下来，街上的人渐渐少了。\n\n" * (i * 2))
    for i in range(1, 8)
)

# /outline/trend 的对比夹具。key = 问题数 + 碎片数（越小越好）：
#   BAD  0 节点 + 0 规划，预计章节数 1 → issues=2 → key=2
#   GOOD 1 节点 + 1 规划（够长且含事件/冲突/场景词）→ thin=0, issues=0 → key=0
OUTLINE_BAD = """# 《测试书》整体大纲

## 起
开端。

## 承
发展。

## 转
高潮。

## 合
结局。

## 关键节点
（无）

## 预计章节数
1

## 章节规划
（无）
"""

OUTLINE_GOOD = """# 《测试书》整体大纲

## 起
露汐在沙都医院发现匿名信，决定追查。

## 承
与罗霄对峙，冲突升级。

## 转
调查触及高层，她被停职。

## 合
真相揭开，她选择留下。

## 关键节点
- 节点1：第1章 露汐在沙都的医院里发现那封匿名信，与罗霄在回廊发生对峙，冲突迅速升级，她决定追查真相并封锁消息。

## 预计章节数
1

## 章节规划
- 第1章（铺垫）：露汐在沙都医院发现匿名信，与罗霄对峙后冲突升级，她决定追查并封锁消息，交代学院与沙都的关系。
"""


def build_project():
    tmp = Path(tempfile.mkdtemp(prefix="nf_newapi_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    (tmp / "materials" / "raw").mkdir(parents=True)
    (tmp / "data" / "outline").mkdir(parents=True)
    refined = tmp / "data" / "chapters" / "refined"
    refined.mkdir(parents=True)
    (tmp / "data" / "setting").mkdir(parents=True)
    (tmp / "data" / "setting" / "setting.json").write_text(json.dumps({
        "characters": [{"name": "露汐", "aliases": ["小汐"], "description": "女主"}],
        "world": [], "plot_fragments": [], "timeline": [],
    }, ensure_ascii=False), encoding="utf-8")
    for n in (1, 2, 3):
        (refined / ("%02d.md" % n)).write_text(REFINED.format(n=n), encoding="utf-8")
    # 供 style/analyze source=path 与 book/split 使用
    (tmp / "style_sample.md").write_text(STYLE_SAMPLE, encoding="utf-8")
    (tmp / "book.txt").write_text(BOOK_TEXT, encoding="utf-8")
    (tmp / "short.txt").write_text("太短了。", encoding="utf-8")
    # 供 /outline/trend：一个「改前（差）」的历史版本 + 一个「改后（好）」的当前版本。
    # 两者 key（问题数+碎片数）分别为 2 与 0，于是 trend 应判 improved、
    # 并给出 has_backup=true。放在这里是为了让端点有真实可对比的数据。
    (tmp / "data" / "outline" / "history").mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "outline" / "history" / "global_v1.md").write_text(
        OUTLINE_BAD, encoding="utf-8")
    (tmp / "data" / "outline" / "global.md").write_text(OUTLINE_GOOD, encoding="utf-8")
    return tmp


def ast_shadow_check():
    """do_GET / do_POST 内不得有本地名遮蔽模块级名（2026-09-14 UnboundLocalError 复盘）。"""
    src = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    mod_names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                mod_names.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                mod_names.add(a.asname or a.name)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            mod_names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    mod_names.add(t.id)
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name in ("do_GET", "do_POST")):
            continue
        local = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Import):
                for a in n.names:
                    local.add(a.asname or a.name.split(".")[0])
            elif isinstance(n, ast.ImportFrom):
                for a in n.names:
                    local.add(a.asname or a.name)
            elif isinstance(n, ast.Assign):
                for t in n.targets:
                    for sub in ast.walk(t):
                        if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                            local.add(sub.id)
        hit = sorted(local & mod_names)
        if hit:
            bad.append((node.name, hit))
    return bad


def main():
    print("\n=== 0. AST 静态检查（名遮蔽）===")
    bad = ast_shadow_check()
    check("do_GET/do_POST 内无本地名遮蔽模块级名", not bad, bad)

    tmp = build_project()
    print("临时项目根:", tmp)
    proc = subprocess.Popen([str(PY), str(tmp / "scripts" / "nf_api.py"),
                             "--port", str(PORT), "--allow-fake"],
                            cwd=str(tmp), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                code, _h = req("GET", "/health")
                if code == 200:
                    break
            except Exception:
                time.sleep(0.25)
        else:
            print("  [FAIL] 服务未起来")
            return 1

        print("\n=== 1. GET /estimate（生成前预估）===")
        code, d = req("GET", "/estimate")
        check("全流程预估 → 200 且含 stages/totals/budget/disclaimer",
              code == 200 and d.get("stages") and "totals" in d and "budget" in d
              and d.get("disclaimer"), (code, list(d)[:8]))
        check("7 个阶段都在（含 stage7 零 token）",
              len(d.get("stages") or []) == 7
              and (d["stages"][-1]["stage"] == 7 and d["stages"][-1]["tokens_in"] == 0),
              [s["stage"] for s in d.get("stages", [])])
        tot = d.get("totals") or {}
        check("totals 自洽（tokens_in == 各阶段之和）",
              tot.get("tokens_in") == sum(s["tokens_in"] for s in d["stages"]),
              (tot.get("tokens_in"), [s["tokens_in"] for s in d["stages"]]))
        check("标注了估算口径 basis_source", d.get("basis_source") in ("history", "heuristic", "mixed"),
              d.get("basis_source"))
        check("每阶段都带 basis 依据", all(s.get("basis") for s in d["stages"]),
              [s["stage"] for s in d["stages"] if not s.get("basis")])
        code, d2 = req("GET", "/estimate?stage=4")
        check("?stage=4 → 只返回阶段4", code == 200 and len(d2["stages"]) == 1
              and d2["stages"][0]["stage"] == 4, [s["stage"] for s in d2.get("stages", [])])
        code, d3 = req("GET", "/estimate?stages=1,2")
        check("?stages=1,2 → 返回 1 和 2",
              code == 200 and [s["stage"] for s in d3["stages"]] == [1, 2],
              [s["stage"] for s in d3.get("stages", [])])
        code, d4 = req("GET", "/estimate?stage=6&no_history=1")
        check("?no_history=1 → 走字符折算（heuristic）",
              code == 200 and d4["stages"][0]["source"] == "heuristic",
              d4["stages"][0].get("source"))

        print("\n=== 2. 校对：报告缺失时可行动，运行后落盘 ===")
        code, d = req("GET", "/proofread/report")
        check("无报告 → 404 + hint（不是 500）",
              code == 404 and d.get("hint"), (code, str(d)[:180]))
        code, d = req("POST", "/proofread/run", {"llm": False, "dry_run": False})
        check("POST /proofread/run → 202 + job_id", code == 202 and d.get("job_id"),
              (code, str(d)[:180]))
        job = wait_job(d.get("job_id"))
        check("校对 job 成功结束", job.get("state") == "ok", str(job)[:200])
        check("job 汇报问题条数", "问题" in str(job.get("result")), job.get("result"))
        check("proofread_report.json 已生成",
              (tmp / "data" / "outline" / "proofread_report.json").exists())
        check("proofread_report.md 已生成",
              (tmp / "data" / "outline" / "proofread_report.md").exists())
        code, d = req("GET", "/proofread/report")
        check("GET /proofread/report → 200 且含 summary/rhythm/chapters",
              code == 200 and d.get("summary") and "rhythm" in d and d.get("chapters"),
              (code, list(d)[:8]))
        check("summary 统计到 3 章", d["summary"]["chapters"] == 3, d["summary"])
        check("真的抓到埋入的缺陷（错误 >= 1）", d["summary"]["error"] >= 1, d["summary"])
        allf = [f for c in d["chapters"] for f in (c.get("findings") or [])]
        check("finding 带 suggested_action（可被 batch_refine/审稿 UI 复用）",
              allf and all("suggested_action" in f for f in allf), allf[:1])
        check("finding 带 id / type / severity / detail",
              allf and all(all(k in f for k in ("id", "type", "severity", "detail")) for f in allf))
        types = {f["type"] for f in allf}
        check("覆盖标点与错字两类", "punct" in types and "typo" in types, types)
        check("节奏指标含对话占比与离群判定",
              "dialogue_ratio" in d["rhythm"] and "outliers" in d["rhythm"],
              list(d["rhythm"])[:8])
        code, d = req("POST", "/proofread/run", {"scope": "nope"})
        check("非法 scope → 400（不是静默当自动）", code == 400, (code, str(d)[:160]))

        print("\n=== 3. 文风分析 ===")
        code, d = req("POST", "/style/analyze",
                      {"source": "path", "path": str(tmp / "style_sample.md")})
        check("source=path → 200 且有特征",
              code == 200 and d.get("ok") and d.get("features"), (code, str(d)[:160]))
        ft = d.get("features") or {}
        for k in ("avg_sentence_len", "dialogue_ratio", "vocabulary", "sentiment",
                  "connectives", "punctuation", "person"):
            check("特征含 %s" % k, k in ft, list(ft)[:6])
        check("对话占比在 [0,1]", 0 <= (ft.get("dialogue_ratio") or 0) <= 1, ft.get("dialogue_ratio"))
        code, d = req("POST", "/style/analyze",
                      {"source": "path", "path": str(tmp / "short.txt")})
        check("短文本 → ok 但 sufficient=false（有 hint）",
              code == 200 and d.get("ok") and d.get("sufficient") is False and d.get("hint"),
              (code, str(d)[:160]))
        code, d = req("POST", "/style/analyze",
                      {"source": "chapter", "n": 1, "compare": {"n": 1}})
        check("source=chapter + compare → 有 drift 与 drift_report",
              code == 200 and d.get("ok") and d.get("drift") and d.get("drift_report"),
              (code, str(d)[:160]))
        check("drift 带 threshold / compared / drifts / matches",
              all(k in (d.get("drift") or {}) for k in ("threshold", "compared", "drifts", "matches")),
              list(d.get("drift") or {})[:8])
        code, d = req("POST", "/style/analyze", {"source": "chapter", "n": 99})
        check("不存在的章节 → ok=false + 可行动 error",
              code == 400 and not d.get("ok") and "不存在" in str(d.get("error")), (code, str(d)[:160]))
        code, d = req("POST", "/style/analyze", {"source": "bogus"})
        check("非法 source → 400", code == 400 and not d.get("ok"), (code, str(d)[:160]))
        code, d = req("POST", "/style/analyze", {"source": "path"})
        check("source=path 缺 path → 400", code == 400, (code, str(d)[:160]))

        print("\n=== 4. 章节节奏 / 拆书 ===")
        code, d = req("GET", "/book/pacing?source=current")
        check("?source=current → 200 且实时算出 3 章",
              code == 200 and d.get("ok") and len(d.get("chapters") or []) == 3,
              (code, len(d.get("chapters") or [])))
        check("含 summary（均值/σ/CV/趋势/离群）",
              all(k in (d.get("summary") or {})
                  for k in ("word_count", "dialogue_ratio", "trend", "outliers")),
              list(d.get("summary") or {})[:8])
        code, d = req("GET", "/book/pacing")
        check("尚未拆书 → 404 + hint", code == 404 and d.get("hint"), (code, str(d)[:180]))
        code, d = req("POST", "/book/split", {"path": str(tmp / "book.txt")})
        check("POST /book/split → 200 且识别章节",
              code == 200 and d.get("ok") and (d.get("result") or {}).get("chapters"),
              (code, str(d)[:200]))
        res = d.get("result") or {}
        check("拆出 7 章且标题模式为中文回目",
              len(res.get("chapters") or []) == 7 and res.get("pattern") == "中文回目",
              (len(res.get("chapters") or []), res.get("pattern")))
        check("book_pacing.json 已落盘", (tmp / "data" / "state" / "book_pacing.json").exists())
        code, d = req("GET", "/book/pacing")
        check("回读一致（章节数相同）",
              code == 200 and len(d.get("chapters") or []) == 7,
              (code, len(d.get("chapters") or [])))
        check("节奏趋势被判定", d["summary"].get("trend") in ("递增", "递减", "波动", "平稳"),
              d["summary"].get("trend"))
        code, d = req("POST", "/book/split", {"path": str(tmp / "nope.txt")})
        check("文件不存在 → 400", code == 400 and not d.get("ok"), (code, str(d)[:160]))
        code, d = req("POST", "/book/split", {})
        check("缺 path → 400", code == 400, (code, str(d)[:160]))

        print("\n=== 5. 模型切换 / 导出（此前缺失或写在 do_GET）===")
        cfg_file = tmp / "config" / "system.yaml"
        before = cfg_file.read_text(encoding="utf-8")
        code, d = req("POST", "/models/switch", {"role": "writer", "model": "glm-5.3"})
        check("POST /models/switch → 200（此前 404）", code == 200 and d.get("ok"),
              (code, str(d)[:180]))
        after = cfg_file.read_text(encoding="utf-8")
        check("config/system.yaml 里 writer 已被改写",
              "id: glm-5.3" in after and before != after,
              [l for l in after.splitlines() if "glm-5.3" in l][:2])
        code, d = req("POST", "/models/switch", {"role": "不存在的角色", "model": "x"})
        check("未知角色 → 400 + 列出可用角色",
              code == 400 and "可用" in str(d.get("error")), (code, str(d)[:180]))
        code, d = req("POST", "/models/switch", {"role": "writer"})
        check("缺 model → 400", code == 400, (code, str(d)[:160]))
        code, d = req("POST", "/export/markdown", {"per_vol": 2})
        check("POST /export/markdown 不再 404/500（此前只在 do_GET 里且会 500）",
              code in (200, 400) and "ok" in d, (code, str(d)[:180]))

        print("\n=== 5b. 本轮修好的两个断线端点 ===")
        GOOD_CH = "# 第1章 测试大纲\n\n- 核心事件：主角收到一封旧信\n- 涉及角色：露汐（女主）\n"
        code, d = req("POST", "/outline/chapters/save", {"n": 1, "content": GOOD_CH})
        check("POST /outline/chapters/save → 200（此前 404，ChapterBlueprint 保存全废）",
              code == 200 and d.get("ok"), (code, str(d)[:180]))
        check("单章大纲落到 data/outline/chapters/01.md",
              (tmp / "data" / "outline" / "chapters" / "01.md").read_text(encoding="utf-8") == GOOD_CH)
        code, d = req("POST", "/outline/chapters/save", {"n": 1, "content": GOOD_CH + "\n- 补一段\n"})
        baks = list((tmp / "data" / "outline" / "chapters").glob("01_v*.bak"))
        check("重复保存会先备份旧版（破坏性操作可回退）",
              code == 200 and len(baks) == 1, [p.name for p in baks])
        code, d = req("POST", "/outline/chapters/save", {"n": 2, "content": "没有必需字段"})
        check("缺「核心事件」→ 400", code == 400 and "核心事件" in str(d.get("error")),
              (code, str(d)[:180]))
        code, d = req("POST", "/outline/chapters/save", {"n": 0, "content": GOOD_CH})
        check("非法章号 → 400", code == 400, (code, str(d)[:160]))

        code, d = req("POST", "/models/add", {"name": "my-custom-model"})
        check("POST /models/add → 200（此前错写在 do_GET：GET 500 / POST 404）",
              code == 200 and d.get("ok"), (code, str(d)[:180]))
        cache = tmp / "data" / "state" / "fetched_models.json"
        saved = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
        check("手动模型写进缓存 _manual", "my-custom-model" in (saved.get("_manual") or []),
              saved.get("_manual"))
        code, d = req("POST", "/models/add", {})
        check("缺 name → 400", code == 400, (code, str(d)[:160]))
        code, d = req("POST", "/models/add", {"name": "../evil"})
        check("含路径分隔符的模型名被拒", code == 400, (code, str(d)[:160]))
        code, d = req("GET", "/models/cache")
        check("GET /models/cache 仍可用（读缓存，含手动项）",
              code == 200 and "my-custom-model" in json.dumps(d, ensure_ascii=False),
              (code, str(d)[:160]))

        print("\n=== 5.5 GET /outline/trend（迭代收敛视图）===")
        code, d = req("GET", "/outline/trend")
        check("→ 200 且含 series/status/note/compare",
              code == 200 and "series" in d and "status" in d and "note" in d
              and "compare" in d, (code, list(d)[:8]))
        check("series 按版本序（v1 在前、当前在末）",
              [e["label"] for e in d.get("series", [])] == ["v1（第1轮前）", "当前"],
              [e.get("label") for e in d.get("series", [])])
        check("series 带可比较的 key（问题数+碎片数）",
              [e["key"] for e in d.get("series", [])] == [2, 0],
              [e.get("key") for e in d.get("series", [])])
        check("has_backup 为真（有历史版本可对比）", d.get("has_backup") is True)
        check("status 反映最新版已达标（key=0 → done）",
              d.get("status") == "done", d.get("status"))
        check("note 是可读结论（含「开工」提示）", "开工" in str(d.get("note")),
              str(d.get("note"))[:80])
        cmp_ = d.get("compare") or {}
        check("compare.verdict 为 improved（问题 2 → 0）",
              cmp_.get("verdict") == "improved", cmp_.get("verdict"))
        check("compare.deltas 含 issues=-2",
              (cmp_.get("deltas") or {}).get("issues") == -2,
              cmp_.get("deltas"))
        check("compare.entries 记录了新增条目",
              bool((cmp_.get("entries") or {}).get("added")),
              cmp_.get("entries"))
        code, d2 = req("GET", "/outline/trend?window=2")
        check("?window=2 → window 生效", code == 200 and d2.get("window") == 2,
              (code, d2.get("window")))
        code, d3 = req("GET", "/outline/trend?window=abc")
        check("?window 非法值 → 回落默认 3（不 500）",
              code == 200 and d3.get("window") == 3, (code, d3.get("window")))

        print("\n=== 5.6 GET /outline/advise（开工方向建议）===")
        code, d = req("GET", "/outline/advise")
        check("→ 200 且含 options/recommended/verdict/metrics",
              code == 200 and "options" in d and "recommended" in d
              and "verdict" in d and "metrics" in d, (code, list(d)[:8]))
        ids = [o["id"] for o in d.get("options", [])]
        check("方案池含 start_writing（用户可坚持开工）", "start_writing" in ids, ids)
        check("推荐项在方案池内", d.get("recommended") in ids, d.get("recommended"))
        check("推荐项排在首位（按严重度排序）",
              ids and ids[0] == d.get("recommended"), ids)
        check("每个方案都带 why 与 tradeoff（不是干巴巴的标题）",
              all(o.get("why") and o.get("tradeoff") for o in d.get("options", [])),
              [(o.get("id"), bool(o.get("why")), bool(o.get("tradeoff")))
               for o in d.get("options", [])])
        check("停机点：建议不自动执行（metrics 反映当前体检）",
              isinstance(d.get("metrics"), dict) and "thin" in d["metrics"],
              d.get("metrics"))
        code, d2 = req("GET", "/outline/advise?window=2")
        check("?window 被接受", code == 200 and "options" in d2, code)

        print("\n=== 5.7 GET /sandbox/queue（沙盒审核队列）===")
        code, d = req("GET", "/sandbox/queue")
        check("→ 200 且含 items/stats/orphans/sandbox_dir",
              code == 200 and "items" in d and "stats" in d
              and "orphans" in d and "sandbox_dir" in d, (code, list(d)[:8]))
        check("默认只看待审（filter=pending）", d.get("filter") == "pending",
              d.get("filter"))
        check("stats 含三种状态计数",
              all(k in (d.get("stats") or {}) for k in ("pending", "approved", "rejected")),
              d.get("stats"))
        check("工程无产物时 items 为空（不报错）", d.get("items") == [], d.get("items"))
        code, d2 = req("GET", "/sandbox/queue?all=1")
        check("?all=1 → filter=all", code == 200 and d2.get("filter") == "all",
              (code, d2.get("filter")))

        print("\n=== 6. 未知路径未被破坏 ===")
        code, _d = req("GET", "/nope")
        check("未知路径仍 404", code == 404)
        code, d = req("GET", "/nope")
        check("404 提示里列出了新端点", "/estimate" in str(d.get("error")),
              str(d.get("error"))[:200])

        print("=" * 62)
        print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
        if FAIL:
            print("失败项: " + "、".join(FAIL))
        print("=" * 62)
        return 1 if FAIL else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
