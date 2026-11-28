<script setup>
/**
 * UiBadge —— 徽标（状态 / 计数 / 标签）的唯一封装（2026-11-28）。
 *
 * 为什么要有：现状并存两套 —— `.pill`（灰底）与 `.st-*`（七种状态色）。
 * `test_ui_tokens【10】` 已经要求两者几何一致（圆角 + 字号），说明它们本来就是同一件东西，
 * 只是被写成了两个名字。新组件应当只有一个入口：`variant` 选语义，几何由这里唯一决定。
 *
 * 颜色一律由主题色 `color-mix(…, transparent)` 调出来，不写死 rgba ——
 * 换主题时底色跟着走（旧 `.st-*` 的硬编码 rgba 曾经在暗色主题下全靠运气）。
 *
 * 不写 `<style scoped>`：样式全走 Tailwind 工具类 + 主题变量。
 */
import { computed, useAttrs } from "vue";
import { cn } from "../../lib/cn";

defineOptions({ inheritAttrs: false });

const props = defineProps({
  // neutral | accent | ok | warn | bad —— 与 .st-* 的语义一一对应
  variant: { type: String, default: "neutral" },
  // 门 / 等签字这种要加粗强调的场合同 true（对应 .st-gate 的 font-weight:600）
  strong: { type: Boolean, default: false },
  // 删除线：已放弃 / 已作废
  struck: { type: Boolean, default: false },
});

const attrs = useAttrs();

// 圆角与字号必须与 style.css 的 .pill / .badge 一致（7px / 12px），
// 由 tests/unit/test_ui_base.py【U5】比对 —— 三个名字一套几何，不再各自长歪。
const VARIANTS = {
  neutral: "bg-[color-mix(in_srgb,var(--muted)_14%,transparent)] text-[color:var(--muted)]",
  accent: "bg-[color-mix(in_srgb,var(--accent)_18%,transparent)] text-[color:var(--ink-accent)]",
  ok: "bg-[color-mix(in_srgb,var(--ok)_18%,transparent)] text-[color:var(--ink-ok)]",
  warn: "bg-[color-mix(in_srgb,var(--warn)_18%,transparent)] text-[color:var(--ink-warn)]",
  bad: "bg-[color-mix(in_srgb,var(--bad)_20%,transparent)] text-[color:var(--ink-bad)]",
};

const classes = computed(() => cn(
  "inline-flex items-center whitespace-nowrap rounded-[7px] px-[9px] py-[3px]",
  "text-[12px] leading-[1.5]",
  props.strong ? "font-semibold" : "font-normal",
  props.struck ? "line-through opacity-60" : "",
  VARIANTS[props.variant] || VARIANTS.neutral,
  attrs.class,
));
</script>

<template>
  <span v-bind="attrs" :class="classes"><slot /></span>
</template>
