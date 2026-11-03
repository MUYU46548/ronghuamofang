# -*- coding: utf-8 -*-
"""段群进度进账：`_parts/NN/gK.md` 写了 10 段，系统里不许等于没写（2026-11-03）。

## 要治的病

加强模式（`chapter.enhanced_mode=true`）是**逐群落盘**：每写完一群就落一个
`data/chapters/_parts/NN/gK.md`，全部齐了才拼装成 `raw/NN.md`。

真机实测（2026-11-03 run）：第 1 章 4 群、约 5900 字已经在盘上，但
`progress.json` 里一个字都没记 —— 界面说"这章没写"，用户以为要去重写，
实际只差最后一群。**盘上有、系统里没有 = 等于没写。**

## 判据（每条都能变红）

- K1 有 4 个 gK.md → `{done: 4, words: >0, total: 传入值}`
- K2 `note_parts` 把它写进 `progress.stages["4"].parts["1"]`
- K3 拼装稿 `raw/01.md` 出现后，该条目**自动消失**（不再说"未拼装"）
- K4 一群都没有 → 不留空条目（不许制造"0/5 群"的噪声）
- K5 total 传 0 时也照记（至少 done/words 是真的）
- K6 接线：stage4 在群开始 / 文件进度 / 章结束 三处都记账；/state 把它交给界面；
      界面上有一条"未拼装 N 章"的提示

用法：python tests/unit/test_parts_progress.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from stage4_writing import parts_state, note_parts          # noqa: E402
from utils.progress_manager import ProgressManager           # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=None):
    if cond:
        PASS.append(name)
        print("  [PASS] " + name)
    else:
        FAIL.append(name)
        print("  [FAIL] " + name + ("  → %r" % (detail,) if detail is not None else ""))


def main():
    print("=" * 66)
    print("段群进度进账（parts）")

    tmp = Path(tempfile.mkdtemp())
    chapters = tmp / "data" / "chapters"
    raw = chapters / "raw"
    parts = chapters / "_parts"
    raw.mkdir(parents=True)
    d1 = parts / "01"
    d1.mkdir(parents=True)
    body = "他推开门，外头是雨。" * 40          # 约 400 字
    for g in range(1, 5):
        (d1 / ("g%d.md" % g)).write_text(body, encoding="utf-8")

    print("\n【1】parts_state：读盘数群与字数")
    st = parts_state(parts, 1, 5)
    check("K1 数到 4 群", st["done"] == 4, st)
    check("K1 字数 >0（不是只数文件）", st["words"] > 0, st)
    check("K1 total 来自调用方", st["total"] == 5, st)
    st0 = parts_state(parts, 9, 5)
    check("K4 没有目录 → 0 群 0 字（不留噪声）", st0 == {"done": 0, "total": 5, "words": 0}, st0)

    print("\n【2】note_parts：写进 progress，拼装后自动清掉")
    pm = ProgressManager(str(tmp / "data" / "state" / "progress.json"))
    info = note_parts(parts, raw, pm, 1, total_groups=5)
    bucket = (pm.data.get("stages", {}).get("4", {}) or {}).get("parts") or {}
    check("K2 写进了 stages.4.parts['1']", bucket.get("1", {}).get("done") == 4, bucket)
    check("K2 返回值就是写入的那条", info == bucket.get("1"), (info, bucket))

    (raw / "01.md").write_text(body, encoding="utf-8")
    info2 = note_parts(parts, raw, pm, 1, total_groups=5)
    bucket = (pm.data.get("stages", {}).get("4", {}) or {}).get("parts") or {}
    check("K3 拼装稿出现后条目被清掉", "1" not in bucket, bucket)
    check("K3 返回值 None（没有未拼装条目）", info2 is None, info2)

    print("\n【3】边界")
    pm2 = ProgressManager(str(tmp / "data" / "state" / "progress2.json"))
    note_parts(parts, raw, pm2, 7, total_groups=0)
    b2 = (pm2.data.get("stages", {}).get("4", {}) or {}).get("parts") or {}
    check("K4 没产物的章不建条目", "7" not in b2, b2)
    d7 = parts / "07"
    d7.mkdir(parents=True)
    (d7 / "g1.md").write_text(body, encoding="utf-8")
    note_parts(parts, raw, pm2, 7, total_groups=0)
    b2 = (pm2.data.get("stages", {}).get("4", {}) or {}).get("parts") or {}
    check("K5 total 未知（0）也照记 done/words",
          b2.get("7", {}).get("done") == 1 and b2.get("7", {}).get("words") > 0, b2)

    print("\n【4】接线")
    s4 = (ROOT / "scripts" / "stage4_writing.py").read_text(encoding="utf-8")
    check("K6 群开始/文件进度/章结束三处都记账",
          s4.count("_note_parts(") >= 3, s4.count("_note_parts("))
    check("K6 开跑前先对账一次（崩溃遗留也能看见）",
          "开跑前先对账一次" in s4)
    api = (ROOT / "scripts" / "nf_api.py").read_text(encoding="utf-8")
    check("K6 /state 把 parts 交给界面", 'out["parts"] = st["parts"]' in api)
    app = (ROOT / "console" / "src" / "App.vue").read_text(encoding="utf-8")
    check("K6 界面上有【未拼装 N 章】提示", "未拼装" in app)

    print("-" * 66)
    print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
    if FAIL:
        print("未通过：" + "、".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
