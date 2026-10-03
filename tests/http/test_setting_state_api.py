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

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:200]) if detail else ""))


def req(method, path, body=None, timeout=20):
    data = (json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
            if method == "POST" else None)
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json",
                                        "X-Mofang-Source": "test"})
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
    proc = subprocess.Popen([str(PY), str(tmp / "scripts" / "nf_api.py"),
                             "--port", str(PORT), "--allow-fake"],
                            cwd=str(tmp),
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
        check("T1 checked=false（**没查**，不是「一致」）", d.get("checked") is False, d)
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
