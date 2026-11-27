<script setup>
import { computed, useAttrs } from "vue";
import { cn } from "../../lib/cn";

defineOptions({ inheritAttrs: false });

const props = defineProps({
  variant: { type: String, default: "default" }, // default | primary | ghost | danger
  size: { type: String, default: "md" },         // sm | md
});
const attrs = useAttrs();

// 四档语义与 style.css 的 `.btn-*` 层同源（2026-11-25 L1-c 定案）：
//   primary = 现在该点的那个（--accent-strong 底 + --btn-primary-ink 字）。
//   十套主题实测：strong 配白字 4.60–4.71:1，而 --accent 配白字只有 2.23–3.47:1
//   —— 用 accent 做底的主按钮看着像“灰按钮”，作者 2026-11-25 明确否掉过。
//   ghost 字色用 --muted（与 .btn-ghost 一致）：次级动作不该和主按钮抢视线。
const VARIANTS = {
  default: "border-[var(--border)] bg-[var(--field)] text-[color:var(--ink)] enabled:hover:bg-[var(--field-hover)]",
  primary: "border-transparent bg-[var(--accent-strong)] text-[color:var(--btn-primary-ink)] enabled:hover:brightness-[1.06]",
  ghost: "border-transparent bg-transparent text-[color:var(--muted)] enabled:hover:bg-[var(--field-dim)] enabled:hover:text-[color:var(--ink)]",
  danger: "border-transparent bg-[var(--bad)] text-white enabled:hover:brightness-[1.06]",
};
// md 的高度必须等于 `.mini` / `.btn` 的 30px —— 同一行里输入框(30px)与按钮不该一高一矮。
// 由 tests/unit/test_ui_base.py【U3】比对；改这里要同步改 style.css 的 .mini/.btn。
const SIZES = {
  sm: "h-7 px-2 gap-1",
  md: "h-[30px] px-3 gap-1.5",
};

const classes = computed(() => cn(
  "inline-flex items-center justify-center whitespace-nowrap rounded-[var(--r-sm)] border",
  "text-[length:var(--fs-ui)] font-medium transition-colors",
  "disabled:cursor-not-allowed disabled:opacity-50",
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]",
  VARIANTS[props.variant] || VARIANTS.default,
  SIZES[props.size] || SIZES.md,
  attrs.class,
));
</script>

<template>
  <button v-bind="attrs" :class="classes">
    <slot />
  </button>
</template>
