# -*- coding: utf-8 -*-
"""索引与实物对账端点自检（GET /materials/reconcile，HTTP 端到端）。

**不碰真实仓库数据**：把 scripts/ config/ prompts/ 复制到临时目录当 ROOT 起 nf_api，
素材全部在临时目录里造。

覆盖：
  - 登记表声称 10 篇 / 实物 3 篇 → verdict=mismatch、posts_delta=-7、人话摘要；
  - 碎片登记表放在 raw 里，登记的日期在 scraps 目录里 → 必须命中（跨目录比对，
    否则会假报“无一重合”）；
  - 移除登记表 → verdict=consistent / no_meta，而不是把“没东西可比”说成“对上了”；
  - 端点只读：请求前后 materials/ 下文件一个不变。

用法：python tests/http/test_materials_reconcile_api_http.py
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
PORT = 8946
BASE = "http://127.0.0.1:%d" % PORT

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def req(method, path, body=None, timeout=20):
    data = (json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
            if method == "POST" else None)
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:                                   # noqa: BLE001
            payload = {}
        return e.code, payload


def build_project():
    tmp = Path(tempfile.mkdtemp(prefix="reconcile_api_"))
    shutil.copytree(ROOT / "scripts", tmp / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "config", tmp / "config",
                    ignore=shutil.ignore_patterns("history"))
    if not (tmp / "config" / "project.yaml").exists():
        shutil.copy2(ROOT / "config" / "project.yaml.example",
                     tmp / "config" / "project.yaml")
    shutil.copytree(ROOT / "prompts", tmp / "prompts",
                    ignore=shutil.ignore_patterns("history", "reference"))
    raw = tmp / "materials" / "raw"
    scraps = tmp / "materials" / "original_scraps"
    raw.mkdir(parents=True)
    scraps.mkdir(parents=True)
    (tmp / "data").mkdir()

    (raw / "00_大纲收束索引.md").write_text(
        "# 雾港 · 大纲收束索引 v2（自动生成，未改动任何源文件）\n\n"
        "本批素材共 10 篇，按 9 维聚类，9 维齐全。\n", encoding="utf-8")
    (raw / "碎片填充计划.md").write_text(
        "# 说明\n\n已使用的碎片在此登记，避免重复使用。\n\n# 登记列表\n"
        "2099-05-01\n2099-05-02\n", encoding="utf-8")
    (raw / "角色卡_巡道工.md").write_text(
        "# 角色卡\n\n- 名称：巡道工甲\n- 身份：夜班巡道\n", encoding="utf-8")
    (raw / "角色卡_记账员.md").write_text(
        "# 角色卡\n\n- 名称：记账员乙\n- 身份：码头账房\n", encoding="utf-8")
    (raw / "地点卡_旧码头.md").write_text(
        "# 地点卡\n\n旧码头，雾天停摆。\n", encoding="utf-8")
    # 碎片：文件名带日期，供跨目录比对
    (scraps / "碎片2099-05-01.md").write_text("雾起时没有船进港。\n", encoding="utf-8")
    (scraps / "碎片2099-05-02.md").write_text("账房那盏灯亮到天亮。\n", encoding="utf-8")
    return tmp


def _snapshot(d: Path):
    return sorted((p.name, p.stat().st_size) for p in d.rglob("*") if p.is_file())


def main():
    tmp = build_project()
    print("临时项目根:", tmp)
    raw = tmp / "materials" / "raw"
    before = _snapshot(tmp / "materials")
    proc = subprocess.Popen([str(PY), str(tmp / "scripts" / "nf_api.py"),
                             "--port", str(PORT), "--allow-fake"],
                            cwd=str(tmp),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(80):
            try:
                code, _h = req("GET", "/health")
                if code == 200:
                    break
            except Exception:                               # noqa: BLE001
                time.sleep(0.25)
        else:
            print("  [FAIL] 服务未起来")
            return 1

        print("\n=== 1. GET /materials/reconcile（有差异） ===")
        code, d = req("GET", "/materials/reconcile")
        check("200 且 ok", code == 200 and d.get("ok") is True, str(d)[:200])
        check("verdict=mismatch", d.get("verdict") == "mismatch", d.get("verdict"))
        check("实物 raw=3 篇", (d.get("actual") or {}).get("posts", {}).get("raw") == 3,
              d.get("actual"))
        check("实物 scraps=2 条", (d.get("actual") or {}).get("posts", {}).get("scraps") == 2,
              d.get("actual"))
        metas = {m["name"]: m for m in d.get("meta_files") or []}
        check("两份登记表都在结果里", set(metas) == {"00_大纲收束索引.md", "碎片填充计划.md"},
              list(metas))
        idx = metas.get("00_大纲收束索引.md", {})
        check("收束索引：声称 10 / 实物 3 / 差 -7",
              idx.get("claims", {}).get("posts") == 10
              and idx.get("diff", {}).get("posts_actual") == 3
              and idx.get("diff", {}).get("posts_delta") == -7, idx.get("diff"))
        check("收束索引判为有差异", idx.get("consistent") is False, idx.get("issues"))
        plan = metas.get("碎片填充计划.md", {})
        check("碎片登记：跨目录日期 2/2 命中",
              plan.get("diff", {}).get("dates_matched") == 2
              and not plan.get("diff", {}).get("dates_missing"), plan.get("diff"))
        check("摘要是一句人话", "不一致" in (d.get("summary") or ""), d.get("summary"))

        print("\n=== 2. 端点只读（素材文件一个不变） ===")
        after = _snapshot(tmp / "materials")
        check("materials/ 未被改动", before == after, "%d → %d" % (len(before), len(after)))

        print("\n=== 3. 移除差异源 → 不再报不一致 ===")
        (raw / "00_大纲收束索引.md").unlink()
        code, d2 = req("GET", "/materials/reconcile")
        check("200", code == 200, code)
        check("verdict=consistent", d2.get("verdict") == "consistent", d2.get("verdict"))
        check("只剩碎片登记表一份", len(d2.get("meta_files") or []) == 1,
              [m["name"] for m in d2.get("meta_files") or []])

        print("\n=== 4. 没有登记表 → no_meta ===")
        (raw / "碎片填充计划.md").unlink()
        code, d3 = req("GET", "/materials/reconcile")
        check("verdict=no_meta（不是“对上了”）", d3.get("verdict") == "no_meta",
              d3.get("verdict"))
        check("no_meta 仍有实物统计", (d3.get("actual") or {}).get("posts", {}).get("raw") == 3,
              d3.get("actual"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
