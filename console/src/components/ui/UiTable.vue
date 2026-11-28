<script setup>
/**
 * UiTable —— 宽表格基座（2026-11-28）。
 *
 * 为什么要有：至少 5 个组件是“表头 + 若干列 + 行尾操作按钮”的同一形态
 * （残稿 / 伏笔 / 写作预设 / 审阅账本 / 一致性面板…）。它们现在的 `.xxx-table`
 * 各自写一套 `border-collapse` + 单元格 padding + 表头 opacity，值已经分成
 * 5px 和 6px 两档 —— 同一屏里两张表列高不一样，说不清哪里乱但看着就是乱。
 *
 * 用法（列定义 + 每列一个具名插槽，行尾操作走 `ops` 列）：
 *
 *   <UiTable :columns="cols" :rows="items" row-key="name" :row-class="rowCls">
 *     <template #cell-at="{ row }"><span class="copy-muted">{{ row.at }}</span></template>
 *     <template #cell-preview="{ row }">{{ row.preview }}</template>
 *     <template #cell-ops="{ row }">
 *       <UiButton @click="adopt(row)">采纳</UiButton>
 *     </template>
 *   </UiTable>
 *
 * 空态交给调用方（`<template #empty>`）：每张表的“没有数据”要说的下一步不一样，
 * 基座给一句通用话只会变成噪音。
 *
 * 不写 `<style scoped>`：样式全走 Tailwind 工具类 + 主题变量。
 */
import { computed, useAttrs } from "vue";
import { cn } from "../../lib/cn";

defineOptions({ inheritAttrs: false });

const props = defineProps({
  // [{ key, label, cls?, width? }]
  columns: { type: Array, required: true },
  rows: { type: Array, default: () => [] },
  // 行对象的唯一键字段名（不用 index 做 key：列表会重排）
  rowKey: { type: String, default: "id" },
  // 行级类名：`(row) => 'bg-[…]'`，用来标超期行这类整行状态
  rowClass: { type: Function, default: null },
  // 表格紧凑档：内容型表格（一行多行文字）用默认值，纯数字/短标签表格用 compact
  compact: { type: Boolean, default: false },
});

const attrs = useAttrs();

// 单元格几何是这一层的唯一真源：改这里等于改所有用它的表。
// th 的字重/弱化也与旧 `.xxx-table th` 一致（600 / opacity .75）。
const cellPad = computed(() => (props.compact ? "px-1.5 py-1" : "px-1.5 py-[5px]"));

const tableCls = computed(() => cn(
  "w-full border-collapse text-[length:var(--fs-ui)] text-[color:var(--ink)]",
  attrs.class,
));

function thCls(col) {
  return cn(
    "border-b border-[var(--border)] text-left align-bottom font-semibold opacity-75",
    cellPad.value,
    col.cls,
  );
}

function tdCls(col) {
  return cn(
    "border-b border-[var(--border)] text-left align-top",
    cellPad.value,
    col.cls,
  );
}
</script>

<template>
  <table v-bind="attrs" :class="tableCls">
    <thead>
      <tr>
        <th v-for="col in columns" :key="col.key" :class="thCls(col)"
            :style="col.width ? { width: col.width } : null">{{ col.label }}</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="(row, i) in rows" :key="row[rowKey] ?? i"
          :class="rowClass ? rowClass(row) : ''">
        <td v-for="col in columns" :key="col.key" :class="tdCls(col)">
          <slot :name="'cell-' + col.key" :row="row" :index="i">{{ row[col.key] }}</slot>
        </td>
      </tr>
      <tr v-if="!rows.length">
        <td :colspan="columns.length" class="border-b-0 px-1.5 py-1">
          <slot name="empty" />
        </td>
      </tr>
    </tbody>
  </table>
</template>
