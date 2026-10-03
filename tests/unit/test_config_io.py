# -*- coding: utf-8 -*-
"""配置严格读取 / 定向写入自检（A 块，2026-10-03）。零 LLM、零网络。

## 为什么

PyYAML 对**重复键静默取后值**。本项目栽过两次（`project.yaml` 的 `user_outline`
覆盖真实大纲；`system.yaml` 的 `gates.agent_mode` 双写），而 2026-10-03 又把**止烧阈值**
（`budget.token_limit.*`）放进了 `system.yaml` —— 配置被静默覆盖 = 熔断闸门形同虚设。

判据一份：`utils/config_io`（读严格 + 写定向 + 回读校验），
默认预设与合法性由 `utils.cost_tracker` 管（语义不重复实现）。

用法：python tests/unit/test_config_io.py
"""
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils.config_io import (ConfigError, deep_merge,  # noqa: E402
                             load_config_yaml, load_pipeline_config,
                             set_section_scalar)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:240]) if detail else ""))


def tmpdir():
    return Path(tempfile.mkdtemp(prefix="cfg_io_"))


# ============================================================ 1. 严格读
def case_strict_read():
    print("\n【1】严格读：重复键必须报错（不再静默取后值）")
    tmp = tmpdir()
    try:
        dup = tmp / "system.yaml"
        dup.write_text("gates:\n  agent_mode: true\n  agent_mode: false\n", encoding="utf-8")
        try:
            load_config_yaml(dup)
            check("重复键 → 抛 ConfigError", False, "没抛异常（又静默取后值了）")
        except ConfigError as e:
            msg = str(e)
            check("重复键 → 抛 ConfigError", True)
            check("报错带键名", "agent_mode" in msg, msg)
            check("报错带**行号**（第 3 行是后一个）", "第 3 行" in msg, msg)
            check("报错说明「静默取后值」的危害", "静默" in msg, msg)
            check("报错给体检入口", "nfctl.py check" in msg, msg)

        good = tmp / "ok.yaml"
        good.write_text("gates:\n  agent_mode: true\nengine: hermes\n", encoding="utf-8")
        data = load_config_yaml(good)
        check("正常文件照读", data["gates"]["agent_mode"] is True and data["engine"] == "hermes", data)

        check("文件不存在 → 返回 default，不抛（未初始化是合法状态）",
              load_config_yaml(tmp / "nope.yaml") is None
              and load_config_yaml(tmp / "nope.yaml", default={}) == {})

        bad = tmp / "syntax.yaml"
        bad.write_text("gates:\n  agent_mode: true\n   bad_indent: 1\n", encoding="utf-8")
        try:
            load_config_yaml(bad)
            check("语法错 → 抛 ConfigError", False, "没抛")
        except ConfigError as e:
            check("语法错 → 抛 ConfigError（不让裸 YAMLError 冒出去）", True)
            check("语法错误信息可行动", "nfctl.py check" in str(e), str(e)[:120])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================ 2. 合并
def case_merge():
    print("\n【2】深合并语义（local 覆盖 system；列表整体替换）")
    check("嵌套 dict 合并，override 优先",
          deep_merge({"a": {"x": 1, "y": 2}}, {"a": {"y": 9}}) == {"a": {"x": 1, "y": 9}})
    check("列表整体替换（不是元素拼接）",
          deep_merge({"l": [1, 2, 3]}, {"l": [9]}) == {"l": [9]})
    check("新键追加", deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2})
    check("None 容错", deep_merge(None, {"a": 1}) == {"a": 1}
          and deep_merge({"a": 1}, None) == {"a": 1})


def case_pipeline_config():
    print("\n【3】流水线入口：三份配置全部严格（local 里的重复键也要报）")
    tmp = tmpdir()
    try:
        (tmp / "system.yaml").write_text("engine: hermes\nbudget:\n  limit_yuan: 300\n",
                                         encoding="utf-8")
        (tmp / "system.local.yaml").write_text("budget:\n  limit_yuan: 500\n", encoding="utf-8")
        (tmp / "project.yaml").write_text('book:\n  name: "测试书"\n', encoding="utf-8")
        cfg, proj = load_pipeline_config(tmp / "system.yaml", tmp / "system.local.yaml",
                                         tmp / "project.yaml")
        check("local 覆盖 system（深合并）", cfg["budget"]["limit_yuan"] == 500, cfg)
        check("system 里 local 没写的键保留", cfg["engine"] == "hermes", cfg)
        check("project 独立返回", proj["book"]["name"] == "测试书", proj)

        (tmp / "system.local.yaml").write_text(
            "budget:\n  limit_yuan: 500\n  limit_yuan: 800\n", encoding="utf-8")
        try:
            load_pipeline_config(tmp / "system.yaml", tmp / "system.local.yaml",
                                 tmp / "project.yaml")
            check("local 里的重复键也必须报错", False, "没抛（本地覆盖是静默覆盖的重灾区）")
        except ConfigError as e:
            check("local 里的重复键也必须报错", "limit_yuan" in str(e), str(e)[:120])

        (tmp / "system.local.yaml").write_text("budget:\n  limit_yuan: 500\n", encoding="utf-8")
        (tmp / "project.yaml").write_text('book:\n  name: "A"\n  name: "B"\n', encoding="utf-8")
        try:
            load_pipeline_config(tmp / "system.yaml", tmp / "system.local.yaml",
                                 tmp / "project.yaml")
            check("project 里的重复键也必须报错", False, "没抛")
        except ConfigError:
            check("project 里的重复键也必须报错", True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================ 4. 定向写
def case_targeted_write():
    print("\n【4】定向写入：只改一行，注释与其它内容逐字保留 + 备份 + 回读校验")
    tmp = tmpdir()
    try:
        cfg = tmp / "system.yaml"
        original = (
            "# 顶部说明（必须保留）\n"
            "engine: hermes\n"
            "budget:\n"
            "  limit_yuan: 300        # 金额阈值（hermes 下无效）\n"
            "  token_limit:\n"
            "    enabled: true\n"
            "    max_total_tokens: 3000000   # 单轮累计\n"
            "    per_request_max_tokens: 8000\n"
            "logging:\n"
            "  level: info\n"
        )
        cfg.write_text(original, encoding="utf-8")
        import os
        origin = os.getcwd()
        os.chdir(tmp)
        try:
            # 注意：set_section_scalar 的备份目录是相对的 config/history
            (tmp / "config").mkdir(exist_ok=True)
            ok, msg = set_section_scalar(("budget", "token_limit"), "max_total_tokens",
                                         10000000, path=cfg, comment="单轮累计（改过）")
            check("写入成功", ok, msg)
            text = cfg.read_text(encoding="utf-8")
            check("顶部注释保留", "# 顶部说明（必须保留）" in text)
            check("同级键的注释保留", "# 金额阈值（hermes 下无效）" in text)
            check("未动的键保留", "per_request_max_tokens: 8000" in text
                  and "level: info" in text)
            check("目标键已改", "max_total_tokens: 10000000" in text, text)
            check("目标键的新注释生效（不留矛盾说明）",
                  "单轮累计（改过）" in text and "单轮累计\n" not in text)
            check("回读校验通过（写后能解析且值一致）", "已更新" in msg, msg)
            check("写前有备份", (tmp / "config" / "history").exists()
                  and list((tmp / "config" / "history").glob("*.yaml")), msg)

            # 注意类型：传 bool 才会写成 `false`；传字符串 "false" 会被 YAML 引号保护成
            # `'false'`（那是**字符串**，不是布尔）—— 所以设置页必须先把表单值转成真类型，
            # 这正是 cost_tracker.validate_token_limit 的职责。
            ok2, msg2 = set_section_scalar(("budget", "token_limit"),
                                           "per_request_pause_hermes", False, path=cfg)
            check("缺键 → 插到块末尾（缩进跟随块内键）", ok2 and
                  "    per_request_pause_hermes: false" in cfg.read_text(encoding="utf-8"),
                  msg2)
            back = load_config_yaml(cfg)
            check("插入后整份文件仍是合法 YAML（严格 loader 再读一遍）",
                  back["budget"]["token_limit"]["per_request_pause_hermes"] is False, back)
            ok6, msg6 = set_section_scalar(("budget", "token_limit"),
                                           "per_request_pause_hermes", "false", path=cfg)
            check("传字符串 \"false\" → 写成 'false'（保住字符串类型，不静默变布尔）",
                  ok6 and load_config_yaml(cfg)["budget"]["token_limit"][
                      "per_request_pause_hermes"] == "false", msg6)

            ok3, msg3 = set_section_scalar(("no_such_section",), "x", 1, path=cfg,
                                           allow_insert=False)
            check("缺段且不允许插入 → 明确拒绝（不写坏文件）", ok3 is False, msg3)
            ok4, msg4 = set_section_scalar(("budget", "token_limit"), "enabled", True, path=cfg)
            check("布尔值也能写对（True 不是字符串 'True'）",
                  ok4 and load_config_yaml(cfg)["budget"]["token_limit"]["enabled"] is True,
                  msg4)
            # 顶层段（字符串形式）也要能用
            ok5, msg5 = set_section_scalar("logging", "level", "debug", path=cfg)
            check("顶层段（字符串 path）可用",
                  ok5 and load_config_yaml(cfg)["logging"]["level"] == "debug", msg5)
        finally:
            os.chdir(origin)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ============================================================ 5. 结构性护栏
# 允许残留的裸 safe_load（**每一条都要写理由**，与泄露门禁的 `allow:` 同一纪律：
# 静默豁免等于开后门）。新增文件若出现在这份名单外 → 本用例变红。
SAFE_LOAD_ALLOWED = {
    "scripts/utils/template_loader.py":
        "提示词模板 frontmatter（不是配置；且已包在 except yaml.YAMLError 里）",
    "scripts/nfctl.py":
        "只读诊断入口（_read_yaml 故意宽松：配置坏了更要用 status 看出来；"
        "重复键由 _find_duplicate_keys 专门报）",
    "scripts/utils/setting_schema.py":
        "设定集/素材数据文件（不是配置）",
}


def case_no_bare_config_safe_load():
    print("\n【5】结构性护栏：配置类 YAML 不许再有裸 safe_load（防回流）")
    offenders = []
    for p in sorted((ROOT / "scripts").rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        if "__pycache__" in p.parts:
            continue
        for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "yaml.safe_load(" in ln and not ln.strip().startswith("#"):
                offenders.append("%s:%d" % (rel, i))
    allowed_hits = [o for o in offenders if o.split(":")[0] in SAFE_LOAD_ALLOWED]
    real = [o for o in offenders if o.split(":")[0] not in SAFE_LOAD_ALLOWED]
    check("配置类裸 safe_load 已清零（A2 的机械替换）", real == [], real[:8])
    check("豁免名单里的残留确实存在（名单不许变成空话）",
          len(allowed_hits) >= 2, allowed_hits)
    for f, why in sorted(SAFE_LOAD_ALLOWED.items()):
        check("豁免条目指向真实文件：%s" % f, (ROOT / f).exists(), why)
    # 反证：把一处替换回裸 safe_load → 判据必须能抓住
    sample = (ROOT / "scripts" / "stage2_outline.py").read_text(encoding="utf-8")
    reverted = sample.replace('load_config_yaml("config/system.yaml")',
                              'yaml.safe_load(read_text("config/system.yaml"))')
    check("旧写法确实会被本条判据识别（反证）",
          "yaml.safe_load(" in reverted and reverted != sample)


def main():
    print("=" * 62)
    print("  配置严格读取 / 定向写入自检（A 块）")
    print("=" * 62)
    case_strict_read()
    case_merge()
    case_pipeline_config()
    case_targeted_write()
    case_no_bare_config_safe_load()
    print("\n" + "=" * 62)
    print("  通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("   ✗ " + f)
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
