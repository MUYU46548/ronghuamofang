# -*- coding: utf-8 -*-
"""暂停文案诚实性（2026-11-02）。

## 为什么有这个测试

界面上那句\u300c已暂停输出，可修改提示词后点恢复\u300d是**三重谎**：

1. `/stream/pause` 只 `set()` 一个 Event，该 Event 只在包装器的 `_push` 里被查
   —— 模型调用照跑，服务端 token 照产生照计费；
2. 本次请求的 prompt 在开跑时就发出去了，此刻改它不影响这一轮；
3. 那个\u300c修改提示词\u300d输入框**从来没有发送方**（`streamPromptOverride` 全项目无消费），
   恢复时还补一句\u300c新提示词将在后续输出中生效\u300d。

结果：用户以为按了暂停就能改方向，实际只是看不见。

## 判据（每条都能变红）

- P1 前端不再出现那三句假话
- P2 假控件彻底删除（无 `streamPromptOverride` 残留）
- P3 pause 分支的响应带 `model_running: True`（界面别只靠文案，能拿到事实）
- P4 **pause 分支不含任何中断调用** —— 这是"模型没停"的结构证据；
      将来若真做成暂停，必须同时改文案，这条会先红
- P5 前端提示语必须同时说清\u300c仍在生成\u300d与\u300c计费\u300d
- P6 后端文案不得再写\u300c已暂停流式输出\u300d

用法：python tests/unit/test_pause_honesty.py
"""
import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "console" / "src" / "App.vue"
API = ROOT / "scripts" / "nf_api.py"
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           ("  → " + str(detail)[:220]) if detail else ""))


def strip_comments(text, kind):
    """去掉注释，只留"会被用户看到/被执行"的部分。

    注释里引用旧文案做溯源是应该的（不然下次没人知道为什么改），
    所以判据只对**活代码**生效 —— 否则修注释也算修复。
    """
    if kind == "vue":
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
        text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
        return re.sub(r"\s//\s.*$", "", text, flags=re.M)
    return re.sub(r"^\s*#.*$", "", text, flags=re.M)


def pause_block(src):
    """截出 `/stream/pause` 那个 elif 分支的源码（到下一个 elif 为止）。"""
    i = src.find('p == "/stream/pause"')
    if i < 0:
        return ""
    j = src.find('elif p ==', i)
    return src[i:j if j > 0 else i + 2000]


def main():
    print("=" * 66)
    print("暂停文案诚实性")
    print("=" * 66)

    app_raw = APP.read_text(encoding="utf-8")
    api_raw = API.read_text(encoding="utf-8")
    app = strip_comments(app_raw, "vue")
    api = strip_comments(api_raw, "py")

    # ---- P1 三句假话必须消失 ----
    lies = [
        "可修改提示词后点恢复",
        "修改提示词（可选，恢复后注入）",
        "新提示词将在后续输出中生效",
    ]
    for lie in lies:
        check("P1 前端无假话\u300c%s\u300d" % lie, lie not in app,
              "仍在 App.vue 的活代码里" if lie in app else "")

    # ---- P2 假控件彻底删除 ----
    check("P2 无 streamPromptOverride 残留", "streamPromptOverride" not in app,
          "变量/输入框还在" if "streamPromptOverride" in app else "")

    # ---- P3 响应带事实字段 ----
    blk = pause_block(api)
    check("P3 能截到 pause 分支", bool(blk), "没找到 /stream/pause")
    check("P3 pause 响应带 model_running=True", '"model_running": True' in blk,
          blk[:160])
    check("P6 后端不再写\u300c已暂停流式输出\u300d", "已暂停流式输出" not in api,
          "旧文案还在 nf_api.py")

    # ---- P4 结构证据：pause 分支没有中断调用 ----
    if blk:
        # 合法的只有 streamer["pause"].set()。出现 stop 索引 / abort / close / cancel
        # 就说明它真去掐模型了 —— 那时文案与 P5 必须一起改。
        banned = [w for w in ("abort", ".close()", "cancel", '["stop"]', "['stop']")
                  if w in blk]
        check("P4 pause 分支不中断模型", not banned,
              "出现中断调用：%s（若真做成暂停，请同时改文案与 P5）" % banned)
        check("P4b pause 分支只置 Event",
              re.search(r'pause"\]\s*=\s*threading\.Event', blk) is not None
              or 'streamer["pause"].set()' in blk, blk[:200])

    # ---- P5 提示语说清真相 ----
    m = re.search(r'say\("(已暂停[^"]*)"\)', app)
    text = m.group(1) if m else ""
    check("P5 暂停提示语存在", bool(text), "没找到 say(\"已暂停…\")")
    check("P5 提到模型仍在生成", "仍在生成" in text, text)
    check("P5 提到仍在计费", "计费" in text, text)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
