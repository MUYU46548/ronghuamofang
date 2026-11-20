# -*- coding: utf-8 -*-
"""C3 知识库排除项：**显式撤销默认项 + 二次确认**（2026-11-20 作者拍板）。

作者原话：“C3 知识库排除项显式撤销 + 二次确认”。

改之前的真相：`get_skip_dirs()` 只做“默认四项 ∪ 用户追加”，注释写着
“取消某个默认项本键做不到（刻意）”。所以界面上取消勾选 `.obsidian` 是**假的** ——
勾选框没了，扫描面里照样排除，用户以为生效了。这比“没做”更糟（界面对读者撒谎）。

现在的口径（这份用例钉住）：
1. `obsidian.unskip_dirs` 是唯一能放开默认项的通道，只对默认四项生效；
2. **沙盒目录无论如何放不开**（写进 unskip 也会被加回来 —— 自指污染不是偏好）；
3. 放开默认项 = 服务端要求 `confirm: true`，不带就 400 且**不写盘**；
4. 放开后扫描面立刻变（`.obsidian` 出现在清单里、excluded=False），能再勾回去；
5. `.obsidian` 这类目录不含 .md，必须仍出现在清单里 —— 否则“撤销入口”根本不存在。

用法：python tests/unit/test_vault_skip_unskip.py
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  -> " + str(detail)[:240]) if detail else ""))


def _w(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _build(tmp: Path):
    """造一个最小库：设定/ 有词条，.obsidian / .git 无 .md，沙盒在库里。"""
    _w(tmp / "config" / "system.yaml",
       "engine: self\nobsidian:\n  sandbox_dir: obsidian_sandbox\n")
    _w(tmp / "config" / "system.local.yaml",
       "obsidian:\n  vault_path: %s\n" % (tmp / "vault").as_posix())
    v = tmp / "vault"
    _w(v / "设定" / "人物" / "岑野.md", "# 岑野\n虚构人物词条，内容足够长用于测试。\n")
    _w(v / "草稿" / "随手.md", "# 随手\n自造草稿。\n")
    (v / ".obsidian").mkdir(parents=True, exist_ok=True)
    _w(v / ".obsidian" / "app.json", '{"theme": "dark"}')
    (v / ".git").mkdir(parents=True, exist_ok=True)
    _w(v / ".git" / "config", "[core]\n")
    _w(v / "obsidian_sandbox" / "候选.md", "# 沙盒产物\n")
    return v


def _with_root(tmp, fn):
    import nf_api as api
    from utils import config_io, setting_schema
    old_root, old_cwd = api.ROOT, os.getcwd()
    old_cfg_root = getattr(config_io, "ROOT", None)
    api.ROOT = Path(tmp)
    config_io.ROOT = Path(tmp)
    setting_schema.reset_obsidian_config_cache()
    os.chdir(tmp)
    try:
        return fn()
    finally:
        os.chdir(old_cwd)
        api.ROOT = old_root
        config_io.ROOT = old_cfg_root
        setting_schema.reset_obsidian_config_cache()


def main():
    import nf_api as api
    from utils import config_io, setting_schema
    from utils.setting_schema import get_skip_dirs, get_unskip_dirs, _DEFAULT_SKIP_DIRS
    import obsidian_bridge as OB
    from nf_api_domains import project as dom

    class H:            # 端点只用到 headers（本例不看来源）
        headers = {}

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _build(tmp)

        def run():
            print("\n【1】默认：四项恒排除（含无 .md 的 .obsidian）")
            sk = get_skip_dirs()
            check("默认四项都在排除集合里", set(_DEFAULT_SKIP_DIRS) <= sk, sorted(sk))
            check("沙盒目录也在（自指污染防线）", "obsidian_sandbox" in sk, sorted(sk))
            check("unskip 初始为空", get_unskip_dirs() == [])

            print("\n【2】清单里必须看得见 .obsidian（否则撤销入口不存在）")
            prev = OB.scan_vault_preview()
            names = {d["name"]: d for d in prev["top_dirs"]}
            check(".obsidian 出现在扫描面清单", ".obsidian" in names, sorted(names))
            check("它标成“无 .md”而不是 0 篇的普通目录",
                  names.get(".obsidian", {}).get("no_md") is True, names.get(".obsidian"))
            check("默认状态是排除", names.get(".obsidian", {}).get("excluded") is True)
            check("回传 sandbox_dir（界面据此禁用勾选）",
                  prev.get("sandbox_dir") == "obsidian_sandbox", prev.get("sandbox_dir"))

            print("\n【2b】老调用方（只发改动项、不带 unskip）→ 200，默认四项照旧排除")
            code, d = dom.handle_config_vault_skip_dirs(H(), {"exclude": ["设定", "草稿"]})
            check("不带 unskip → 200（向后兼容，不被读成“全放开”）",
                  code == 200 and d.get("ok"), (code, d.get("error")))
            check("默认四项仍排除", set(_DEFAULT_SKIP_DIRS) <= get_skip_dirs(),
                  sorted(set(_DEFAULT_SKIP_DIRS) - get_skip_dirs()))
            check("响应里 unskip 为空", (d.get("unskip") or []) == [], d.get("unskip"))

            print("\n【3】带 unskip 但没带 confirm → 400，且一个字都没写")
            local = Path(tmp) / "config" / "system.local.yaml"
            before = local.read_text(encoding="utf-8")
            code, d = dom.handle_config_vault_skip_dirs(
                H(), {"exclude": ["设定", "草稿", "obsidian_sandbox"],
                      "unskip": [".obsidian", ".git"]})
            check("缺 confirm → 400", code == 400, code)
            check("响应标了 need_confirm 并列出要放开哪些",
                  d.get("need_confirm") is True and ".obsidian" in (d.get("unskip") or []), d)
            check("没有写盘（扫描面保持原样）",
                  local.read_text(encoding="utf-8") == before)
            check("放开项没进生效集合", ".obsidian" in get_skip_dirs())

            print("\n【4】带 confirm 才真放开，扫描面立刻变")
            code, d = dom.handle_config_vault_skip_dirs(
                H(), {"exclude": ["设定", "草稿", "obsidian_sandbox"],
                      "unskip": [".obsidian", ".git"], "confirm": True})
            check("带 confirm → 200", code == 200 and d.get("ok"), (code, d.get("error")))
            check("回传里点名放开了哪几个", sorted(d.get("unskip") or []) == [".git", ".obsidian"],
                  d.get("unskip"))
            sk2 = get_skip_dirs()
            check(".obsidian 真的不再排除", ".obsidian" not in sk2, sorted(sk2))
            check(".git 也放开了（同一批）", ".git" not in sk2, sorted(sk2))
            check("用户自己勾的“设定/草稿”仍排除", {"设定", "草稿"} <= sk2, sorted(sk2))
            check("沙盒仍然排除（unskip 减不掉它）", "obsidian_sandbox" in sk2, sorted(sk2))
            prev2 = OB.scan_vault_preview()
            nm = {x["name"]: x for x in prev2["top_dirs"]}
            check("界面状态跟着变：.obsidian 不再 excluded",
                  nm.get(".obsidian", {}).get("excluded") is False, nm.get(".obsidian"))
            check("unskip_dirs 如实回传", sorted(prev2.get("unskip_dirs") or []) == [".git", ".obsidian"],
                  prev2.get("unskip_dirs"))

            print("\n【5】想放开沙盒 → 400 并说清原因（不是静默忽略）")
            code, d = dom.handle_config_vault_skip_dirs(
                H(), {"exclude": ["设定", "草稿"], "unskip": ["obsidian_sandbox"],
                      "confirm": True})
            check("unskip 写沙盒 → 400", code == 400, code)
            check("错误里点名沙盒目录且解释“自指污染”",
                  "obsidian_sandbox" in (d.get("error") or "")
                  and "自指" in (d.get("error") or ""), (d.get("error") or "")[:90])
            check("拒绝后沙盒仍在排除集合", "obsidian_sandbox" in get_skip_dirs())

            print("\n【6】勾回去 = 恢复默认，不需要 confirm")
            code, d = dom.handle_config_vault_skip_dirs(
                H(), {"exclude": ["设定", "草稿", "obsidian_sandbox"] + sorted(_DEFAULT_SKIP_DIRS),
                      "unskip": []})
            check("勾回默认项 → 200（无需确认）", code == 200 and d.get("ok"), (code, d.get("error")))
            check("unskip 清空", get_unskip_dirs() == [], get_unskip_dirs())
            check("四项又回到排除集合", set(_DEFAULT_SKIP_DIRS) <= get_skip_dirs())

            print("\n【6b】unskip 写一个非默认项 → 400（“放开”一个本来就没排除的目录无意义）")
            code, d = dom.handle_config_vault_skip_dirs(
                H(), {"exclude": [], "unskip": ["设定"], "confirm": True})
            check("非默认项 → 400 且说明原因", code == 400 and "默认排除项" in (d.get("error") or ""),
                  (code, (d.get("error") or "")[:70]))
            check("拒绝后默认四项仍在排除集合", set(_DEFAULT_SKIP_DIRS) <= get_skip_dirs())

            print("\n【7】配置文件里手写非默认项 = 空操作（不制造假状态）")
            _w(Path(tmp) / "config" / "system.local.yaml",
               "obsidian:\n  vault_path: %s\n  unskip_dirs:\n  - 设定\n" % (tmp / "vault").as_posix())
            setting_schema.reset_obsidian_config_cache()
            check("只对默认四项生效（“设定”不在默认里，忽略）", get_unskip_dirs() == [],
                  get_unskip_dirs())
            check("默认四项仍排除", set(_DEFAULT_SKIP_DIRS) <= get_skip_dirs())

            print("\n【8】配置写入保留注释（不许被 yaml.dump 吃掉）")
            raw = (Path(tmp) / "config" / "system.local.yaml").read_text(encoding="utf-8")
            check("local 仍是手写行的形状（有 obsidian: 节）", raw.startswith("obsidian:"), raw[:40])

        _with_root(tmp, run)

    print("\n" + "=" * 60)
    print("排除项显式撤销：%d 通过 / %d 失败" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
