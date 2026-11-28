<script setup>
/**
 * UiInput / UiTextarea —— 带 variant 的输入控件封装（2026-11-28）。
 *
 * 为什么要有：现在所有面板都把 `:where(input, select, textarea)` 基础层当基座用。
 * 那个基础层特异性为 0（这是刻意的，面板自己的类才能覆盖宽度），所以它只能给
 * “一行 30px 的标准输入框”这一种形态。C 档组件要的其余三种形态它给不了：
 *   - `inset`：弱化底色（对话框 textarea / 流式输出区，用 --field-inset）
 *   - `code`：等宽字 + 可换行（JSON / 词表 / 规则文本）
 *   - `plain`：完全无边框（表格内联编辑、审阅台正文区）
 * 没有封装时每个组件自己补 border/background，正是 `test_ui_tokens【8】` 抓的那类漂移。
 *
 * **不重新定义几何**：height / padding / border-radius 仍然来自 style.css 的
 * `:where(...)` 基础层（30px / 10px / --r-sm），这里只加“形态差异”。
 * 这样改基础层的那一天，所有封装过的控件一起跟着变，不会分叉出第二套。
 *
 * 用法：
 *   <UiInput v-model="x" type="number" class="w-[90px]" />
 *   <UiInput v-model="t" multiline variant="code" :rows="7" />
 *
 * ## v-model 必须自己实现（2026-11-28 首版踩过，两条都是静默的）
 *
 * 1. 首版没声明 `modelValue`，指望它随 `v-bind="attrs"` 落到底层控件 —— 实际属性名
 *    变成 `modelvalue=""`，**输入的值永远不回传**，保存时拿到空串，界面不报错。
 * 2. `<textarea><slot /></textarea>` 里的 slot 会被 Vue 编译器按 HTML 规范当成
 *    **纯文本**渲染 —— 界面上直接出现字面的 `<slot />`（截图目检抓到，断言全绿）。
 * 所以：显式 props + 显式 emit，且 textarea 内部不许放任何子节点。
 *
 * 不写 `<style scoped>`：样式全走 Tailwind 工具类 + 主题变量。
 */
import { computed, useAttrs } from "vue";
import { cn } from "../../lib/cn";

defineOptions({ inheritAttrs: false });

const props = defineProps({
  modelValue: { type: [String, Number], default: "" },
  // default | inset | code | plain
  variant: { type: String, default: "default" },
  // textarea 专用；input 忽略
  rows: { type: [String, Number], default: 4 },
  // 渲染成 textarea 而不是 input（同一个组件两种形态，省一个 import）
  multiline: { type: Boolean, default: false },
  // v-model.number 的修饰符：Vue 会给组件传 `modelModifiers = {number: true}`。
  // 不接住它，章号会以字符串 "7" 传给后端（契约是整数），出错也只在后端那侧显形。
  modelModifiers: { type: Object, default: () => ({}) },
});

const emit = defineEmits(["update:modelValue"]);

const attrs = useAttrs();

// 形态差异只写这几项：底色 / 边框 / 字体 / 可调整性。
// height/padding/border-radius 一律留给基础层（见文件头）。
const VARIANTS = {
  default: "",
  inset: "bg-[var(--field-inset)]",
  code: "bg-[var(--field-inset)] font-[family-name:var(--mono)] leading-[1.6]",
  plain: "border-transparent bg-transparent px-0",
};

const classes = computed(() => cn(
  "box-border w-full text-[length:var(--fs-ui)] text-[color:var(--ink)]",
  VARIANTS[props.variant] || VARIANTS.default,
  attrs.class,
));

function onInput(e) {
  const raw = e.target.value;
  // `.number` 修饰符：空串保持空串（清空输入框时不该变成 0），
  // 能解析成数字就转数字，否则原样传（让后端按它的校验报错）。
  if (props.modelModifiers && props.modelModifiers.number && raw !== "") {
    const n = Number(raw);
    emit("update:modelValue", Number.isNaN(n) ? raw : n);
    return;
  }
  emit("update:modelValue", raw);
}
</script>

<template>
  <!-- 两个控件都写成自闭合：textarea 里的任何子节点都会被当作文本内容渲染，
       首版就是因此把字面的 <slot /> 画到了界面上（截图目检才抓到）。 -->
  <textarea v-if="multiline" v-bind="attrs" :rows="rows" :value="modelValue"
            :class="classes" @input="onInput" />
  <input v-else v-bind="attrs" :value="modelValue" :class="classes" @input="onInput" />
</template>
