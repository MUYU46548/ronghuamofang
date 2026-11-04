# -*- coding: utf-8 -*-
"""设定集状态域 HTTP 端到端自检（/setting/conflicts | /setting/canon | /setting/status）。

**不碰真实仓库数据**：把 `scripts/ config/ prompts/` 复制到临时目录当 ROOT 起 nf_api，
所有读写都落在临时目录，真实 `data/` 与 `materials/` 完全不受影响。

## 为什么必须真机起服务

这三条端点是**外部 Agent 经 MCP 调用的入口**（`nf_get_setting_conflicts` /
`nf_get_canon` / `nf_set_material_status`）。只测 handler 函数测不出：
`api.ROOT` 有没有被拷死、`do_POST` 有没有把 body 传进去、路由有没有真的接上。
所以这里走完整 HTTP 链路。

用法：python tests/http/test_setting_state_api.py
"""
import io
import json
import os
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
PY = sys.executable
PORT = 8918
BASE = "http://127.0.0.1:%d" % PORT
# GUI 双因子的第二因子：核对 /setting/stale/review 只认可信 GUI 来源。
# 不注入时那条端点永远 403，就测不出"带对了 token 就能核对"。
GUI_TOKEN = "setting-state-selftest-token"

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def req(method, path, body=None, timeout=20, gui=False):
    data = (json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
            if method == "POST" else None)
    headers = {"Content-Type": "application/json", "X-Mofang-Source": "test"}
    if gui:
        headers["X-Mofang-Source"] = "gui"
        headers["X-Mofang-Token"] = GUI_TOKEN
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:                                     # noqa: BLE001
            payload = {}
        return e.code, payload


def build_project(with_setting=False):
    tmp = Path(tempfile.mkdtemp(prefix="setting_state_api_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    raw = tmp / "materials" / "raw"
    raw.mkdir(parents=True)
    (tmp / "data" / "setting").mkdir(parents=True)
    (tmp / "data" / "state").mkdir(parents=True, exist_ok=True)

    # 两张**同名同字段、值不同**的卡 → 机器裁不了 → review（报人）
    def card(fname, name, ident):
        (raw / fname).write_text(
            "# 角色：%s\n\n## 基本信息\n\n- 身份：%s\n\n## 正文\n\n测试用。\n" % (name, ident),
            encoding="utf-8")

    card("甲_角色卡.md", "甲", "法师")
    card("甲_旧卡.md", "甲", "战士")
    card("乙_角色卡.md", "乙", "猎人")

    if with_setting:
        sp = tmp / "data" / "setting" / "setting.json"
        sp.write_text(json.dumps({
            "characters": [
                {"id": "c001", "name": "甲", "source": "甲_角色卡",
                 "role": "", "traits": []},
                {"id": "c002", "name": "乙", "source": "乙_角色卡",
                 "role": "", "traits": []},
            ],
            "world": {"locations": [], "factions": [], "magic_system": [], "items": []},
            "plot_fragments": [], "timeline": [],
            "_meta": {"version": 1},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    return tmp


def main():
    tmp = build_project(with_setting=False)
    print("临时项目根:", tmp)
    env = dict(os.environ)
    env["NF_GUI_TOKEN"] = GUI_TOKEN
    proc = subprocess.Popen([str(PY), str(tmp / "scripts" / "nf_api.py"),
                             "--port", str(PORT), "--allow-fake"],
                            cwd=str(tmp), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        up = False
        for _ in range(80):
            try:
                if req("GET", "/health")[0] == 200:
                    up = True
                    break
            except Exception:                                 # noqa: BLE001
                time.sleep(0.25)
        if not up:
            check("nf_api 起服务", False, "健康检查超时")
            return

        setting_p = tmp / "data" / "setting" / "setting.json"
        tomb_p = tmp / "data" / "setting" / "tombstones.json"
        audit_p = tmp / "data" / "setting" / "merge_audit.json"
        canon_p = tmp / "data" / "setting" / "canon.json"

        # ---------------- T1 冲突清单：checked=false ≠ 没有冲突 ----------------
        print("\n[T1] GET /setting/conflicts（设定集缺失 → checked=false）")
        code, d = req("GET", "/setting/conflicts")
        check("T1 HTTP 200", code == 200, code)
        check("T1 checked=false（**没查**，不是\u300c一致\u300d）", d.get("checked") is False, d)
        check("T1 reason 说明原因（设定集不存在）",
              "设定集不存在" in (d.get("reason") or ""), d.get("reason"))
        names = [c["name"] for c in (d.get("review_conflicts") or [])]
        check("T1 仍报出素材侧 review 分歧（甲 身份 法师 vs 战士）",
              "甲" in names, d.get("review_conflicts"))
        check("T1 冲突带双方出处",
              all(v.get("file") for c in d.get("review_conflicts") or []
                  for v in c["values"]), d.get("review_conflicts"))

        # ---------------- T2 canon：未冻结 ----------------
        print("\n[T2] GET /setting/canon（尚未冻结）")
        code, d = req("GET", "/setting/canon")
        check("T2 HTTP 200", code == 200, code)
        check("T2 exists=false + 给出冻结方式",
              d.get("exists") is False and "approve" in (d.get("hint") or ""), d)

        # ---------------- T3 reject：无条目也必须成功（墓碑先落） ----------------
        print("\n[T3] POST /setting/status reject（条目不存在）")
        code, d = req("POST", "/setting/status",
                      {"name": "甲", "action": "reject", "reason": "作者明确废弃该设定"})
        check("T3 HTTP 200（墓碑按名字生效，不依赖条目）", code == 200, (code, d))
        check("T3 tombstones.json 落盘且含该名",
              tomb_p.exists() and "甲" in json.loads(tomb_p.read_text(encoding="utf-8"))
              .get("entries", {}), tomb_p.exists())
        check("T3 返回值带回墓碑清单", "甲" in (d.get("tombstones") or []), d.get("tombstones"))

        # ---------------- T4 reject 必填 reason ----------------
        print("\n[T4] reject 无 reason → 400")
        code, d = req("POST", "/setting/status", {"name": "乙", "action": "reject"})
        check("T4 HTTP 400", code == 400, (code, d))
        check("T4 错误文案要求填 reason", "reason" in (d.get("error") or ""), d)

        # ---------------- T5 非法 action ----------------
        print("\n[T5] 非法 action → 400")
        code, d = req("POST", "/setting/status", {"name": "乙", "action": "删掉"})
        check("T5 HTTP 400", code == 400, (code, d))

        # ---------------- T6 adopt 需要条目存在 ----------------
        print("\n[T6] adopt：条目不存在 → 404")
        code, d = req("POST", "/setting/status",
                      {"name": "乙", "action": "adopt", "reason": "已确认"})
        check("T6 HTTP 404（状态只能挂在条目上）", code == 404, (code, d))
        check("T6 提示可改用 reject", "reject" in (d.get("hint") or ""), d)

        # ---------------- T7 adopt 成功 + 落盘 + 人工优先 ----------------
        print("\n[T7] 造 setting.json 后 adopt → 落盘且带 human 标记")
        setting_p.write_text(json.dumps({
            "characters": [
                {"id": "c001", "name": "甲", "source": "甲_角色卡"},
                {"id": "c002", "name": "乙", "source": "乙_角色卡"},
            ],
            "world": {"locations": [], "factions": [], "magic_system": [], "items": []},
            "plot_fragments": [], "timeline": [], "_meta": {"version": 1},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        code, d = req("POST", "/setting/status",
                      {"name": "乙", "action": "adopt", "reason": "作者确认无冲突"})
        check("T7 HTTP 200", code == 200, (code, d))
        after = json.loads(setting_p.read_text(encoding="utf-8"))
        e = [x for x in after["characters"] if x["name"] == "乙"][0]
        check("T7 setting.json 里 status=adopted", e.get("status") == "adopted", e)
        check("T7 带 status_source=human（人工决定可压过机器判定）",
              e.get("status_source") == "human", e)
        check("T7 entry_hit=1", d.get("entry_hit") == 1, d)

        # ---------------- T8 拍板后冲突清单刷新 ----------------
        print("\n[T8] 拍板后 merge_audit.json 落盘 + 墓碑可见")
        code, d = req("GET", "/setting/conflicts")
        check("T8 checked=true（现在有设定集了）", d.get("checked") is True, d)
        check("T8 tombstones 含 T3 登记的名字", "甲" in (d.get("tombstones") or []),
              d.get("tombstones"))
        check("T8 merge_audit.json 已落盘",
              audit_p.exists() and json.loads(audit_p.read_text(encoding="utf-8"))
              .get("audited_at"), audit_p.exists())

        # ---------------- T9 canon 冻结后可读 ----------------
        print("\n[T9] canon 冻结 → 元信息可读（默认不含全文）")
        canon_p.write_text(json.dumps({
            "characters": [{"name": "甲"}, {"name": "乙"}],
            "world": {"locations": [{"name": "某地"}]},
            "plot_fragments": [], "timeline": [],
            "_meta": {"canon_frozen_at": "2026-10-03T12:00:00", "canon_source": "x"},
        }, ensure_ascii=False), encoding="utf-8")
        code, d = req("GET", "/setting/canon")
        check("T9 exists=true", d.get("exists") is True, d)
        check("T9 frozen_at 透出", d.get("frozen_at") == "2026-10-03T12:00:00", d)
        check("T9 默认**不含全文**（含 content 就算失败）", "content" not in d, list(d))
        check("T9 counts 汇总正确", d.get("counts", {}).get("characters") == 2, d.get("counts"))
        code, d2 = req("GET", "/setting/canon?full=1")
        check("T9 ?full=1 才给全文", isinstance(d2.get("content"), dict), list(d2))

        # ---------------- T10 越权/坏参数不 500 ----------------
        print("\n[T10] 空 name → 400（不 500）")
        code, d = req("POST", "/setting/status", {"action": "adopt"})
        check("T10 HTTP 400", code == 400, (code, d))

        # ---------------- T11 设定引用：还没有章节 ----------------
        # 这一段刻意换成**两个字以上**的条目名：单字名（甲/乙）在正文里必然误报，
        # utils/setting_stale 不拿它去匹配（只计入 unlocatable）—— 用它测 link
        # 会得到"登记 0 条"的假失败。
        print("\n[T11] GET /setting/stale（有设定集，还没有章节正文）")
        setting_p.write_text(json.dumps({
            "characters": [{"id": "c101", "name": "甲某"}, {"id": "c102", "name": "乙某"}],
            "world": {"locations": [], "factions": [], "magic_system": [], "items": []},
            "plot_fragments": [], "timeline": [], "_meta": {"version": 2},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        code, d = req("GET", "/setting/stale")
        check("T11 HTTP 200", code == 200, code)
        check("T11 checked=true（设定集可读）", d.get("checked") is True, d)
        check("T11 没进正文的条目只计数、不进待核清单",
              d.get("items") == [] and d.get("counts", {}).get("absent") == 2, d.get("counts"))
        code, d = req("POST", "/setting/stale/link", {})
        check("T11 没有章节时 link 明确失败（不静默空跑）",
              code == 400 and "章节" in (d.get("error") or ""), (code, d))

        # ---------------- T12 扫描登记来源 ----------------
        print("\n[T12] 写一章 → new；link → 登记章号 + 版本")
        ch_dir = tmp / "data" / "chapters" / "raw"
        ch_dir.mkdir(parents=True)
        (ch_dir / "01.md").write_text("## 第1章\n\n甲某与乙某同行。\n", encoding="utf-8")
        code, d = req("GET", "/setting/stale")
        check("T12 未登记引用记 new（不冒充 stale）",
              d.get("counts", {}).get("new") == 2, d.get("counts"))
        code, d = req("POST", "/setting/stale/link", {})
        check("T12 link HTTP 200 且登记 2 条",
              code == 200 and d.get("added_pairs") == 2, (code, d))
        code, d = req("GET", "/setting/stale")
        check("T12 登记后 new=0 / stale=0",
              d.get("counts", {}).get("new") == 0 and d.get("counts", {}).get("stale") == 0,
              d.get("counts"))

        # ---------------- T13 改章 → 待核 ----------------
        print("\n[T13] 改第 1 章 → 引用它的条目待核")
        (ch_dir / "01.md").write_text("## 第1章\n\n甲某一个人走了。\n", encoding="utf-8")
        code, d = req("GET", "/setting/stale")
        check("T13 2 条待核", d.get("counts", {}).get("stale") == 2, d.get("counts"))
        names = {it["name"] for it in d.get("items") or [] if it["state"] == "stale"}
        check("T13 待核清单点名甲某 / 乙某", names == {"甲某", "乙某"}, names)
        row = [it for it in d.get("items") or [] if it["name"] == "甲某"][0]["sources"][0]
        check("T13 来源行给出改过原因与前后版本",
              "改过" in row.get("reason", "")
              and row.get("recorded_version") != row.get("current_version"), row)

        # ---------------- T14 核对是 GUI-only（双因子） ----------------
        print("\n[T14] POST /setting/stale/review：非 GUI 403，双因子 200")
        code, d = req("POST", "/setting/stale/review",
                      {"name": "甲某", "chapters": [1], "action": "confirm"})
        check("T14 非 GUI 来源 → 403（Agent 不得把待核洗成已核对）", code == 403, (code, d))
        code, d = req("GET", "/setting/stale")
        check("T14 403 之后待核数没变（真的没写）",
              d.get("counts", {}).get("stale") == 2, d.get("counts"))
        code, d = req("POST", "/setting/stale/review",
                      {"name": "甲某", "chapters": [1], "action": "confirm"}, gui=True)
        check("T14 双因子 GUI → 200", code == 200 and d.get("ok") is True, (code, d))
        code, d = req("GET", "/setting/stale")
        check("T14 核对后只剩 1 条待核（逐条核对，不连带）",
              d.get("counts", {}).get("stale") == 1, d.get("counts"))
        setting_after = json.loads(setting_p.read_text(encoding="utf-8"))
        jia = [x for x in setting_after["characters"] if x["name"] == "甲某"][0]
        check("T14 sources 落盘并带 confirmed_at",
              bool((jia.get("sources") or [{}])[0].get("confirmed_at")), jia.get("sources"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:                                     # noqa: BLE001
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 66)
    print("合计: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    print("=" * 66)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
