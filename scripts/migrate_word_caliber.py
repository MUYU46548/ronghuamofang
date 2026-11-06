# -*- coding: utf-8 -*-
"""把既有工作区的单章字数区间从“含标点口径”折算到“只计汉字口径”（2026-11-05）。

## 为什么需要它

2026-11-05 起字数口径改为**只计汉字**（`utils/word_count.py`）。代码里的出厂预设
已经同步折算成 `[4000, 7200]`，但**已经在用的工作区，配置里写死的仍是
`[5000, 9000]`** —— 不改的话，同一个“5000”在新口径下变严了约 19.6%，
实际要求被悄悄提高，用户会发现“以前能过的稿现在过不了”。

所以给一次性迁移：只把**恰好等于旧出厂值 `[5000, 9000]`** 的配置改成 `[4000, 7200]`。
其它值（用户自己调过的）一律不动 —— 那是创作决策，不是本次口径变更的范围。

## 用法

    python scripts/migrate_word_caliber.py            # 预演（默认，只读）
    python scripts/migrate_word_caliber.py --yes      # 执行
    python scripts/migrate_word_caliber.py --root <工作区> --yes

写盘走 `utils.config_io.set_section_scalars`（定向改写 + 自动备份到
`config/history/`），不整份 dump，注释不会丢。
"""
import argparse
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from utils.config_io import load_config_yaml, set_section_scalars  # noqa: E402

OLD = [5000, 9000]
NEW = [4000, 7200]


def main() -> int:
    ap = argparse.ArgumentParser(description="字数口径折算：含标点 → 只计汉字")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]),
                    help="项目根（默认本仓库/工作区）")
    ap.add_argument("--yes", action="store_true", help="真写盘（默认只预演）")
    args = ap.parse_args()

    root = Path(args.root)
    cfg_path = root / "config" / "system.yaml"
    if not cfg_path.is_file():
        print("找不到 %s" % cfg_path)
        return 2

    cfg = load_config_yaml(cfg_path)
    cur = ((cfg.get("chapter") or {}).get("target_words"))
    print("配置：%s" % cfg_path)
    print("当前 chapter.target_words = %r" % (cur,))

    if cur == NEW:
        print("已经是新口径 [%d, %d]，无需迁移。" % tuple(NEW))
        return 0
    if cur != OLD:
        print("不是旧出厂值 [%d, %d]，不动（用户自己调过的值属创作决策）。" % tuple(OLD))
        return 0

    print("将改为 [%d, %d]（同等实际篇幅；写前自动备份到 config/history/）。"
          % tuple(NEW))
    if not args.yes:
        print("这是预演。确认后加 --yes 执行。")
        return 0

    ok, msg = set_section_scalars(("chapter",), {"target_words": NEW}, path=cfg_path)
    if not ok:
        print("写入失败：%s" % msg)
        return 1
    back = ((load_config_yaml(cfg_path).get("chapter") or {}).get("target_words"))
    print("已写入，回读 = %r" % (back,))
    return 0 if back == NEW else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.exit(main())
