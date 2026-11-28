<script setup>
/**
 * UiField —— 表单行（label + 说明 + 校验信息）的唯一封装（2026-11-28）。
 *
 * 为什么要有：CompassPanel / WritingRulesPanel / VaultDirsPanel 等面板都在手写
 * `<label class="meta">…</label>` + 输入控件 + 一行错误/提示。第三个面板写的时候
 * 就已经分叉了（有的 label 用 `<span>`、有的用 `<label>`；有的提示在输入框上方、
 * 有的在下方）。第四个开始必然各写一套，等于把刚清理掉的“每页自创一套”重新种回去。
 *
 * 结构固定三段：label（可选）→ 控件（默认插槽）→ hint / error（同一行位置，
 * 互相排斥：有 error 时 hint 让位，否则用户会同时看到“可留空”和“必填”）。
 *
 * 不写 `<style scoped>`：样式全走 Tailwind 工具类 + 主题变量。
 */
import { computed } from "vue";

const props = defineProps({
  // 字段名。空字符串 = 不渲染 label 那一行（做分组头 / 纯控件行时用）
  label: { type: String, default: "" },
  // 常驻说明（浅色小字），错误出现时让位
  hint: { type: String, default: "" },
  // 校验错误。非空即渲染成 --bad 色，并压掉 hint
  error: { type: String, default: "" },
  // 是否必填：只影响 label 后面的红点，不做校验（校验归业务逻辑）
  required: { type: Boolean, default: false },
  // 行内布局：label 与控件同一行（数字输入 + 短 label 的场合），
  // 否则默认上下堆叠（文本域 / 下拉都更适合堆叠）
  inline: { type: Boolean, default: false },
});

const labelCls = computed(() =>
  "text-[length:var(--fs-ui)] leading-[var(--lh-body)] text-[color:var(--muted)]" +
  (props.inline ? " shrink-0" : ""));
</script>

<template>
  <div :class="inline ? 'flex items-center gap-2' : 'flex flex-col gap-1.5'">
    <label v-if="label" :class="labelCls">
      {{ label }}<span v-if="required" class="ml-0.5 text-[color:var(--bad)]">*</span>
    </label>
    <div :class="inline ? 'shrink-0' : ''">
      <slot />
    </div>
    <div v-if="error"
         class="text-[length:var(--fs-ui)] leading-[var(--lh-body)] text-[color:var(--bad)]">
      {{ error }}
    </div>
    <div v-else-if="hint"
         class="text-[length:var(--fs-ui)] leading-[var(--lh-body)] text-[color:var(--muted)]">
      {{ hint }}
    </div>
  </div>
</template>
