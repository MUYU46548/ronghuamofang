# -*- coding: utf-8 -*-
"""MCP 白名单补钥匙 + 服务未启动分层指引自检（件5/件6，2026-09-29）。

覆盖：
A. 件5：`tools/list` 暴露 nf_get_remedy（HTTP /remedy 诊断）与 nf_restore_snapshot
   （本进程内复用 snapshot.py），且总数与契约一致。
B. 件5：nf_restore_snapshot 的**预览/确认**两段式 —— 预览不得改文件；
   确认后文件被恢复，且恢复前自动留下 `pre_restore` 快照（可折返）。
C. 件6：8765 服务本体不可达时返回**带层号**的干净报错（8766 传输层 / 8765 服务本体），
   而不是裸 URLError 文本。

全程离线、零网络（用一个必然无人监听的端口模拟 8765 未启动）、零费用。
用法：python tests/unit/test_mcp_remedy_snapshot.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import nf_mcp                                                  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


DEAD_PORT = 59999          # 必然无人监听 → 模拟 8765 未启动


def test_tools_list():
    print("\n[A] 白名单暴露新工具")
    resp = nf_mcp.handle_rpc("tools/list", {}, "127.0.0.1", DEAD_PORT)
    names = [t["name"] for t in resp["result"]["tools"]]
    check("含 nf_get_remedy", "nf_get_remedy" in names)
    check("含 nf_restore_snapshot", "nf_restore_snapshot" in names)
    check("工具总数与 MCP_TOOLS 声明一致（数量勿写死）",
          len(names) == len(nf_mcp.MCP_TOOLS), len(names))
    check("tools/list 不泄漏内部字段（_http/_local）",
          all("_http" not in t and "_local" not in t for t in resp["result"]["tools"]))


def test_layer_guidance():
    print("\n[B] 8765 不可达 → 带层号的干净报错（件6）")
    resp = nf_mcp.handle_rpc("tools/call",
                             {"name": "nf_get_state", "arguments": {}},
                             "127.0.0.1", DEAD_PORT)
    result = resp.get("result") or {}
    text = (result.get("content") or [{}])[0].get("text", "")
    check("返回 isError（不是裸栈/挂死）", result.get("isError") is True, text[:160])
    check("文案标注 8766 = MCP 传输层", "8766" in text and "传输层" in text)
    check("文案标注 8765 = 服务本体", "8765" in text and "本体" in text)
    check("给出可执行启动指引", "nf_api" in text, text[:200])
    payload = json.loads(text)
    check("结构化带 layer 字段", payload.get("layer", "").startswith("8766"))


def test_snapshot_two_phase():
    print("\n[C] 快照回退：预览不动文件 / 确认才执行（件5）")
    cwd = os.getcwd()
    tmp = tempfile.mkdtemp(prefix="nf_mcp_snap_")
    try:
        os.chdir(tmp)
        snap_id = "20260101_000000_test"
        (Path("history") / snap_id / "data" / "state").mkdir(parents=True)
        (Path("history") / snap_id / "data" / "state" / "progress.json").write_text(
            '{"v":"snap"}', encoding="utf-8")
        (Path("data") / "state").mkdir(parents=True)
        cur = Path("data") / "state" / "progress.json"
        cur.write_text('{"v":"now"}', encoding="utf-8")

        # 只列快照（不传 id）
        r0 = nf_mcp._local_snapshot_restore({})
        check("不传 id → 列出可用快照", r0.get("ok") and snap_id in r0.get("snapshots", []), r0)

        # 预览（不 confirm）
        r1 = nf_mcp._local_snapshot_restore({"snapshot_id": snap_id})
        check("预览 ok 且未确认", r1.get("ok") is True and r1.get("confirmed") is False)
        check("预览明确标注 dry-run", any("dry-run" in m for m in r1.get("messages", [])), r1.get("messages"))
        check("预览**不改文件**", cur.read_text(encoding="utf-8") == '{"v":"now"}',
              cur.read_text(encoding="utf-8"))

        # 前缀匹配 + 找不存在的 id
        r_miss = nf_mcp._local_snapshot_restore({"snapshot_id": "29990101_nope"})
        check("不存在的 id → ok=false（不误执行）", r_miss.get("ok") is False, r_miss.get("error"))

        # 确认执行
        r2 = nf_mcp._local_snapshot_restore({"snapshot_id": "20260101", "confirm": True})
        check("确认后 ok=true", r2.get("ok") is True, r2.get("messages"))
        check("文件已恢复为快照内容", cur.read_text(encoding="utf-8") == '{"v":"snap"}',
              cur.read_text(encoding="utf-8"))
        pre = [p.name for p in Path("history").iterdir() if "pre_restore" in p.name]
        check("恢复前自动留下 pre_restore 快照（可折返）", bool(pre), pre)
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("===== MCP 补钥匙 + 分层指引自检（件5/件6）=====")
    test_tools_list()
    test_layer_guidance()
    test_snapshot_two_phase()
    print("\n===== 合计: %d passed, %d failed =====" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + " | ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
