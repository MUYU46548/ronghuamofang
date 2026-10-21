# -*- coding: utf-8 -*-
"""作品级多套写作预设的自检（2026-10-21，roadmap 10.3）。

## 为什么这些断言值得写

预设是"可切换的层"，切换 = 改写 project.yaml（全流水线的创作口径来源）。
判据钉死的是三件事：
1. **身份字段不进预设**（书名/章数混进来 = 切换把 A 书改成 B 书，五连炸同族）
2. **未知键拒绝**（静默忽略 = 用户以为存了其实没存）
3. **切换真的生效 + 切回有据**（写而不验 = 配了不生效的假功能）

用法：python tests/unit/test_writing_presets.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PASS, FAIL = [], []


def check(name, ok, detail=""):
    if ok:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("" if detail == "" else "  -> %r" % (detail,)))


PROJ_YAML = """book:
  name: "测试书"
  genre: "奇幻"
  chapters: 12
  style: "冷峻克制"
  language: "zh-CN"
  form: "narrative"
  style_notes: "多用短句"
"""


def main():
    print("=" * 62)
    tmp = Path(tempfile.mkdtemp(prefix="nf_wp_"))
    try:
        (tmp / "config").mkdir()
        (tmp / "config" / "project.yaml").write_text(PROJ_YAML, encoding="utf-8")
        # 备份目录必须留在 tmp（否则测试会往真实仓库 config/history/ 写垃圾）
        from utils import writing_presets as WP

        print("\n[P1] 保存：键的选择与拒绝")
        ok, msg = WP.save_preset("noir", {
            "style": "冷硬 noir，雨夜霓虹，短句快节奏",
            "style_notes": "少心理描写，多动作",
            "target_words": [4000, 6000],
            "form": "narrative",
        }, root=tmp)
        check("P1a 合法预设保存成功", ok, msg)
        ok, msg = WP.save_preset("bad", {"name": "换书名"}, root=tmp)
        check("P1b 身份键（name）→ 拒绝并点名", not ok and "name" in msg, msg)
        ok, msg = WP.save_preset("bad2", {"chapters": 99}, root=tmp)
        check("P1c 身份键（chapters）→ 拒绝", not ok and "chapters" in msg, msg)
        ok, msg = WP.save_preset("bad3", {"style": "x"}, root=tmp)
        check("P1d style 过短 → 拒绝", not ok and "太短" in msg, msg)
        ok, msg = WP.save_preset("bad4", {"target_words": [9000, 1000]}, root=tmp)
        check("P1e 字数区间倒挂 → 拒绝", not ok and "target_words" in msg, msg)
        ok, msg = WP.save_preset("bad5", {"form": "poem"}, root=tmp)
        check("P1f 未知文体 → 拒绝并列出可选", not ok and "narrative" in msg, msg)
        ok, msg = WP.save_preset("Bad ID!", {"style": "合法内容但 id 非法"}, root=tmp)
        check("P1g 非法 id → 拒绝", not ok and "id" in msg, msg)
        ok, msg = WP.save_preset("", {"style": "合法内容但 id 空"}, root=tmp)
        check("P1h 空 id → 拒绝", not ok, msg)

        print("\n[P2] 清单与当前值")
        lst = WP.list_presets(root=tmp)
        check("P2a 清单只有合法预设", [x["id"] for x in lst] == ["noir"], lst)
        cur = WP.current_values(root=tmp)
        check("P2b 当前值来自 project.yaml", cur.get("style") == "冷峻克制", cur)
        check("P2c 当前值只含预设键", set(cur) <= set(WP.PRESET_KEYS), cur)

        print("\n[P3] 切换：真的生效 + 切回有据")
        ok, msg = WP.save_preset("gentle", {
            "style": "温和明亮，日常流，允许大段抒情",
            "form": "essay",
            "genre": "散文",
        }, root=tmp)
        check("P3a 第二套预设保存", ok, msg)
        ok, msg = WP.apply_preset("gentle", root=tmp)
        check("P3b 切换成功", ok, msg)
        from utils.config_io import load_config_yaml
        proj = load_config_yaml(str(tmp / "config" / "project.yaml"))
        book = proj.get("book") or {}
        check("P3c 读回 project.yaml 真的变了", book.get("form") == "essay"
              and "温和明亮" in str(book.get("style")), book)
        check("P3d 未涉及的身份字段没动", book.get("name") == "测试书"
              and book.get("chapters") == 12, book)
        hist = WP.history(root=tmp)
        check("P3e 切换留 history", len(hist) == 1 and hist[0]["preset"] == "gentle", hist)
        check("P3f history 记了上一套值", hist[0]["prev"].get("style") == "冷峻克制"
              or "冷硬" in str(hist[0]["prev"].get("style")), hist[0]["prev"])
        # 切回 noir
        ok, msg = WP.apply_preset("noir", root=tmp)
        proj2 = load_config_yaml(str(tmp / "config" / "project.yaml"))
        book2 = proj2.get("book") or {}
        check("P3g 切回上一套真生效", ok and book2.get("form") == "narrative"
              and "noir" in str(book2.get("style")), book2)
        check("P3h history 累积两条", len(WP.history(root=tmp)) == 2)

        print("\n[P4] 切换的拒绝条件")
        ok, msg = WP.apply_preset("ghost", root=tmp)
        check("P4a 不存在的预设 → 拒绝并列出可选", not ok and "noir" in msg, msg)
        # 预设文件被手工改坏
        p = WP.preset_path("noir", root=tmp)
        p.write_text("{ broken json", encoding="utf-8")
        ok, msg = WP.apply_preset("noir", root=tmp)
        check("P4b 文件损坏 → 拒绝", not ok and "损坏" in msg, msg)
        # 预设文件被塞进未知键（绕过保存校验的手工编辑）
        p.write_text(json.dumps({"style": "内容没问题", "word_template": "x.dotx"},
                                ensure_ascii=False), encoding="utf-8")
        ok, msg = WP.apply_preset("noir", root=tmp)
        check("P4c 手工塞的未知键 → 应用时也拒", not ok and "word_template" in msg, msg)

        print("\n[P5] 根重定向：不写错书、备份不落真实仓库")
        real_yaml = Path(__file__).resolve().parents[2] / "config" / "project.yaml"
        real_before = real_yaml.read_bytes() if real_yaml.exists() else b""
        p.write_text(json.dumps({"style": "临时根测试内容"}, ensure_ascii=False),
                     encoding="utf-8")
        ok, msg = WP.apply_preset("noir", root=tmp)
        real_after = real_yaml.read_bytes() if real_yaml.exists() else b""
        check("P5a 真实仓库 project.yaml 一个字节没动", real_before == real_after)
        check("P5b tmp 根写入成功", ok, msg)
        check("P5c 备份落在 tmp 根的 config/history/（不污染真实仓库）",
              (tmp / "config" / "history").is_dir()
              and any((tmp / "config" / "history").glob("project_*.yaml")))

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 62)
    print("通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  FAILED: " + f)
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
