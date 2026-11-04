# -*- coding: utf-8 -*-
"""服务商增删改（2026-11-04，作者第 8 条“还他妈不能自定义，怎么反而还倒退了”）。

## 要治的病

界面上只有两张只读卡片 + “填入 Key”，`providers` 段只能手改 yaml ——
作者要的“自己加一家 OpenAI 兼容服务商”根本没有入口。

## 三条不能破的纪律（每条都在这套用例里）

1. **注释不许丢**：`system.yaml` 有 170+ 行解释（止烧口径、思考模式实测记录、
   白名单理由）。写入必须是**定点块替换**，不是整份 dump。判据：写前写后
   注释行集合一致。
2. **别人的配置逐字不变**：改 A 不能碰 B（比对 B 那个块的行）。
3. **在用不许删**：删了下一次运行找不到 provider，报错在很远的地方。

用法：python tests/unit/test_provider_store.py
"""
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from utils import provider_store as PS                              # noqa: E402
from utils.config_io import load_config_yaml                        # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("" if cond else "  → %s" % (detail,))))


SYS_YAML = """# =========================================================
# system.yaml（夹具）
# =========================================================
engine: direct

providers:
  tokenhub:
    type: openai-compat
    base_url: https://tokenhub.example.com/v1
    api_key_env: TOKENHUB_API_KEY
    timeout: 600
    retries: 5
    # ⚠️ 这条注释属于 tokenhub：改别人的 provider 时它必须原样留着
    available_models:
    - model-a
    - model-b
    fallback: []
  longcat:
    type: openai-compat
    base_url: https://longcat.example.com/v1
    api_key_env: LONGCAT_API_KEY
    available_models: []
    fallback: []

model:
  writer:
    provider: tokenhub
    id: model-a
  checker:
    provider: longcat
    id: model-c

budget:
  limit_yuan: 300
"""


def make_root():
    root = Path(tempfile.mkdtemp(prefix="nf_prov_"))
    (root / "config").mkdir(parents=True)
    (root / "config" / "system.yaml").write_text(SYS_YAML, encoding="utf-8")
    return root


def comments_of(text):
    return [ln for ln in text.splitlines() if ln.lstrip().startswith("#")]


def block_of(text, pid):
    """取某个 provider 块的行（从 `  pid:` 到下一个同级键）。"""
    lines = text.splitlines()
    a = None
    for i, ln in enumerate(lines):
        if ln.rstrip() == "  " + pid + ":":
            a = i
            break
    if a is None:
        return []
    b = len(lines)
    for j in range(a + 1, len(lines)):
        ln = lines[j]
        if ln.strip() and not ln.startswith("    ") and not ln.startswith("  #"):
            b = j
            break
    return lines[a:b]


def main():
    print("=" * 66)
    print("服务商增删改（定点块写入）")
    root = make_root()
    sysfile = root / "config" / "system.yaml"
    try:
        before = sysfile.read_text(encoding="utf-8")
        before_comments = comments_of(before)
        before_tokenhub = block_of(before, "tokenhub")

        print("\n【P1】新增一家自定义服务商")
        ok, msg, item = PS.save_provider(
            root, "myvendor", "https://api.myvendor.com/v1", "MYVENDOR_API_KEY",
            models=["mv-large", "mv-small"])
        check("P1 返回成功", ok, msg)
        text = sysfile.read_text(encoding="utf-8")
        check("P1 块写进去了", "  myvendor:" in text, text[:200])
        cfg = load_config_yaml(sysfile) or {}
        got = (cfg.get("providers") or {}).get("myvendor") or {}
        check("P1 字段对得上",
              got.get("base_url") == "https://api.myvendor.com/v1"
              and got.get("api_key_env") == "MYVENDOR_API_KEY"
              and got.get("available_models") == ["mv-large", "mv-small"], got)
        check("P1 默认字段补齐（type/timeout/retries/fallback）",
              got.get("type") == "openai-compat" and got.get("timeout") == 600
              and got.get("retries") == 5 and got.get("fallback") == [], got)

        print("\n【P2】别人的配置与全部注释逐字不变")
        check("P2 tokenhub 块一个字没动",
              block_of(text, "tokenhub") == before_tokenhub, block_of(text, "tokenhub"))
        check("P2 注释集合不变（含那条 ⚠️ 注释）",
              comments_of(text) == before_comments,
              set(before_comments) ^ set(comments_of(text)))
        check("P2 其余顶层段还在（engine/budget 没被吃掉）",
              "engine: direct" in text and "limit_yuan: 300" in text)

        print("\n【P3】更新已有 provider：只覆盖四项，手调字段保留")
        ok3, msg3, _ = PS.save_provider(
            root, "tokenhub", "https://tokenhub.example.com/v2", "TOKENHUB_API_KEY")
        text3 = sysfile.read_text(encoding="utf-8")
        cfg3 = load_config_yaml(sysfile) or {}
        th = (cfg3.get("providers") or {}).get("tokenhub") or {}
        check("P3 地址已更新", ok3 and th.get("base_url") == "https://tokenhub.example.com/v2",
              (msg3, th.get("base_url")))
        check("P3 原有模型清单保留（没传 models 就不该清空）",
              th.get("available_models") == ["model-a", "model-b"], th.get("available_models"))
        check("P3 那条 ⚠️ 注释还在", "这条注释属于 tokenhub" in text3)
        check("P3 id 没有重复出现",
              text3.count("\n  tokenhub:") == 1, text3.count("\n  tokenhub:"))

        print("\n【P4】非法输入一律拒，且文件一个字节不动")
        snap = sysfile.read_bytes()
        bad = [
            ("大写 id", PS.save_provider(root, "MyVendor", "https://a.com/v1", "A_API_KEY")),
            ("单字符 id", PS.save_provider(root, "x", "https://a.com/v1", "A_API_KEY")),
            ("裸域名", PS.save_provider(root, "ok1", "api.a.com/v1", "A_API_KEY")),
            ("带 /chat/completions",
             PS.save_provider(root, "ok2", "https://a.com/v1/chat/completions", "A_API_KEY")),
            ("env 用小写", PS.save_provider(root, "ok3", "https://a.com/v1", "my_key")),
            ("env 太短", PS.save_provider(root, "ok4", "https://a.com/v1", "K")),
            ("模型名空串", PS.save_provider(root, "ok5", "https://a.com/v1", "A_OK_KEY", [""])),
        ]
        for label, (okb, msgb, _it) in bad:
            check("P4 拒收：%s" % label, okb is False, msgb)
        check("P4 被拒时文件零改动", sysfile.read_bytes() == snap)

        print("\n【P5】删除：在用的拒，没用的删干净")
        ok5, msg5 = PS.remove_provider(root, "tokenhub")
        check("P5 在用（model.writer）时拒绝删除", ok5 is False and "正在被" in msg5, msg5)
        ok6, msg6 = PS.remove_provider(root, "myvendor")
        text6 = sysfile.read_text(encoding="utf-8")
        check("P5 没在用的能删", ok6, msg6)
        check("P5 删完不留残块", "  myvendor:" not in text6, text6[:300])
        check("P5 删完注释仍全在", comments_of(text6) == before_comments)
        ok7, msg7 = PS.remove_provider(root, "nope")
        check("P5 删不存在的 → 明确拒绝", ok7 is False, msg7)

        print("\n【P6】界面视图：不吐明文 Key，标出谁在用")
        os.environ["TOKENHUB_API_KEY"] = "sk-abcdefghijklmnop"
        os.environ.pop("LONGCAT_API_KEY", None)   # 本机可能真配了，断言要可控
        view = PS.list_providers(root)
        items = {it["id"]: it for it in view["items"]}
        check("P6 两个 provider 都在", set(items) == {"tokenhub", "longcat"}, set(items))
        check("P6 has_key 反映环境变量", items["tokenhub"]["has_key"] is True
              and items["longcat"]["has_key"] is False, items)
        check("P6 掩码只给前 3 后 4", items["tokenhub"]["key_mask"].startswith("sk-")
              and items["tokenhub"]["key_mask"].endswith("mnop")
              and "sk-abcdefghijklmnop" not in items["tokenhub"]["key_mask"],
              items["tokenhub"]["key_mask"])
        check("P6 标出谁在用", items["tokenhub"]["in_use_by"] == ["writer"],
              items["tokenhub"]["in_use_by"])
        check("P6 视图里没有 key 明文",
              "sk-abcdefghijklmnop" not in str(view))

        print("\n【P7】预设清单：数量够、字段合法、不预填模型名")
        presets = view["presets"]
        check("P7 至少 10 家", len(presets) >= 10, len(presets))
        badp = [p for p in presets
                if PS._validate(p["id"], p["base_url"], p["api_key_env"], None)]
        check("P7 每条预设都过校验（不会一键加进坏配置）", not badp, badp)
        ids = [p["id"] for p in presets]
        check("P7 id 不重复且不含已存在的两家",
              len(ids) == len(set(ids)) and "tokenhub" not in ids and "longcat" not in ids,
              ids)
        check("P7 预设不带模型名（模型名会过期，写死必 404）",
              all("models" not in p for p in presets))
        print("\n【P8】接线：三个端点 + GUI-only + 前端用双因子")
        m = (ROOT / "scripts" / "nf_api_domains" / "models.py").read_text(encoding="utf-8")
        check("P8 ROUTES 含三条 provider 端点",
              all(k in m for k in ('"/providers"', '"/providers/save"', '"/providers/remove"')),
              [k for k in ('"/providers"', '"/providers/save"', '"/providers/remove"') if k not in m])
        check("P8 两个写端点调 gui_only", m.count("gui_only(h,") >= 2, m.count("gui_only(h,"))
        nav = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
        check("P8 nf_api 有 GET 转发", 'p == "/providers"' in nav)
        check("P8 nf_api 有两条 POST 转发",
              'p == "/providers/save"' in nav and 'p == "/providers/remove"' in nav)
        ag = (ROOT / "scripts" / "utils" / "agent_guard.py").read_text(encoding="utf-8")
        check("P8 写端点在 Agent 禁用名单里",
              '"/providers/save"' in ag and '"/providers/remove"' in ag)
        check("P8 只读的 GET 不在禁用名单（Agent 可盘点）",
              ag.count('"/providers"') == 0, ag.count('"/providers"'))
        app = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
        check("P8 前端写请求走 guiPostJSON（双因子，手拼 fetch 会静默 403）",
              'guiPostJSON("/providers/save"' in app
              and 'guiPostJSON("/providers/remove"' in app)
        check("P8 前端有新增/编辑/删除入口",
              "＋ 新增服务商" in app and "openProviderForm(pid)" in app
              and "removeProvider(pid)" in app)
        check("P8 前端提供预设下拉", "providerPresets" in app and "pickPreset" in app)

        print("\n【P9】端点级：无头 403 / 双因子打桩后真写盘")
        import nf_api
        from nf_api_domains import models as dm
        from utils import gui_guard as _guard

        class H:
            def __init__(self, headers=None):
                self.headers = headers or {}

        old_root = nf_api.ROOT
        nf_api._set_root(root)
        # gui_only 现在走 gui_refusal（三种情形分说），所以打桩点改成“签发令牌”
        # —— 不能再打桩 is_trusted_gui（那条路已不经过）。
        saved_tok = _guard.agent_guard.issued_gui_token
        try:
            code, payload = dm.handle_providers_save(H(), {
                "id": "hooked", "base_url": "https://hooked.example.com/v1",
                "api_key_env": "HOOKED_API_KEY"})
            check("P9 无 GUI 双因子 → 403", code == 403, (code, payload))
            check("P9 403 时没写盘", "  hooked:" not in sysfile.read_text(encoding="utf-8"))

            _guard.agent_guard.issued_gui_token = lambda: "test-tok"
            GUI_H = H({"X-Mofang-Source": "gui", "X-Mofang-Token": "test-tok"})
            try:
                code2, payload2 = dm.handle_providers_save(GUI_H, {
                    "id": "hooked", "base_url": "https://hooked.example.com/v1",
                    "api_key_env": "HOOKED_API_KEY", "models": "h-1, h-2"})
                check("P9 双因子通过 → 200", code2 == 200 and payload2.get("ok"),
                      (code2, payload2))
                check("P9 真写进临时 root 的 system.yaml",
                      "  hooked:" in sysfile.read_text(encoding="utf-8"))
                code3, payload3 = dm.handle_providers_save(GUI_H, {
                    "id": "BAD", "base_url": "nope", "api_key_env": "Y"})
                check("P9 非法参数 → 400 且说清", code3 == 400 and payload3.get("error"),
                      (code3, payload3))
                code4, payload4 = dm.handle_providers(H())
                check("P9 只读端点不受双因子限制（Agent 也能盘点）",
                      code4 == 200 and any(i["id"] == "hooked"
                                           for i in payload4.get("items") or []),
                      (code4, payload4))
                check("P9 只读视图不吐完整明文 Key（变量名照常显示）",
                      "sk-abcdefghijklmnop" not in str(payload4)
                      and "HOOKED_API_KEY" in str(payload4), str(payload4)[:200])
            finally:
                _guard.agent_guard.issued_gui_token = saved_tok
        finally:
            nf_api._set_root(old_root)
    finally:
        os.environ.pop("TOKENHUB_API_KEY", None)
        shutil.rmtree(root, ignore_errors=True)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
