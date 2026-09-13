<!--
  OutlineTree.vue — 手写树形组件（Vue 3 script setup，不引第三方树库）

  能力：
  - 折叠/展开（有子节点才显示箭头）
  - 点击选中（emit select）
  - 同层级拖拽排序（HTML5 draggable，只允许同 group 内拖拽）
  - 双击节点 → 进入内联编辑（textarea，Ctrl+Enter/失焦提交）

  设计取舍：节点条目数量在几十量级，用嵌套 v-for 渲染即可，不做虚拟滚动。
-->
<template>
  <ul class="tree" :class="{ 'tree-root': depth === 0 }">
    <li v-for="(node, idx) in nodes" :key="node.id || (node.title + '_' + idx)" class="tree-item">
      <div
        class="tree-row"
        :class="[
          'lv' + depth,
          { sel: selectedId && node.id === selectedId },
          { dragging: dragId === node.id },
          { 'drop-before': dropId === node.id && dropPos === 'before' },
          { 'drop-after': dropId === node.id && dropPos === 'after' },
        ]"
        :draggable="draggable && !!node.id"
        :title="rowTitle(node)"
        @click="$emit('select', node)"
        @dblclick.stop="editable && node.id && startEdit(node)"
        @dragstart="onDragStart($event, node)"
        @dragover.prevent="onDragOver($event, node, idx)"
        @dragleave="onDragLeave(node)"
        @drop.prevent="onDrop($event, node, idx)"
        @dragend="onDragEnd"
      >
        <span
          class="tree-caret"
          :class="{ open: isOpen(node) }"
          @click.stop="toggle(node)"
        >{{ hasChildren(node) ? (isOpen(node) ? '▾' : '▸') : '' }}</span>

        <span v-if="node.level" class="tree-dot" :class="'dot-' + node.level" :title="'评分 ' + node.score"></span>

        <!-- 内联编辑 -->
        <textarea
          v-if="editId === node.id"
          ref="editBox"
          class="tree-edit"
          v-model="editText"
          @click.stop
          @dblclick.stop
          @keydown.ctrl.enter.prevent="commitEdit(node)"
          @keydown.esc.prevent="cancelEdit"
          @blur="commitEdit(node)"
        ></textarea>

        <!-- 普通显示 -->
        <span v-else class="tree-label">
          <span class="tree-title">{{ node.title }}</span>
          <span v-if="node.tail" class="tree-tail">{{ node.tail }}</span>
          <span v-if="node.badge" class="tree-badge">{{ node.badge }}</span>
        </span>
      </div>

      <OutlineTree
        v-if="hasChildren(node) && isOpen(node)"
        :nodes="node.children || []"
        :depth="depth + 1"
        :selected-id="selectedId"
        :expanded="expanded"
        :editable="editable"
        :draggable="draggable"
        :group="group"
        @select="$emit('select', $event)"
        @toggle="$emit('toggle', $event)"
        @reorder="$emit('reorder', $event)"
        @edit="$emit('edit', $event)"
      />
    </li>
  </ul>
</template>

<script setup>
import { ref, nextTick } from "vue";

const props = defineProps({
  nodes: { type: Array, default: () => [] },
  depth: { type: Number, default: 0 },
  selectedId: { type: String, default: "" },
  expanded: { type: Object, default: () => ({}) },   // { id: true }
  editable: { type: Boolean, default: false },
  draggable: { type: Boolean, default: true },
  group: { type: String, default: "" },              // 拖拽分组（同组才可互换）
});
const emit = defineEmits(["select", "toggle", "reorder", "edit"]);

const editId = ref("");
const editText = ref("");
const editBox = ref(null);
const dragId = ref("");
const dropId = ref("");
const dropPos = ref("");

function hasChildren(node) {
  return Array.isArray(node.children) && node.children.length > 0;
}
function isOpen(node) {
  // 根/一级默认展开
  if (props.depth <= 0) return true;
  if (props.expanded[node.id] === undefined) return props.depth <= 1;
  return !!props.expanded[node.id];
}
function toggle(node) {
  if (!node.id) return;
  emit("toggle", { id: node.id, open: !isOpen(node) });
}

/* ---------- 内联编辑 ---------- */
/** 悬浮提示：树内 tail 是截断过的摘要，完整原文放 title 属性，鼠标停一下就能看全 */
function rowTitle(node) {
  const full = node.fullTail || "";
  return full ? `${node.title}：${full}` : "";
}
function startEdit(node) {
  editId.value = node.id;
  // 预填必须用**完整**原文（node.text / fullTail）——用截断的 tail 会导致
  // "双击编辑后直接保存"把用户原文截掉
  const full = node.fullTail || node.tail || "";
  editText.value = node.text || `${node.title}${full ? "：" + full : ""}`;
  nextTick(() => {
    const box = Array.isArray(editBox.value) ? editBox.value[0] : editBox.value;
    if (box && box.focus) box.focus();
  });
}
function commitEdit(node) {
  if (editId.value !== node.id) return;
  const val = editText.value.trim();
  editId.value = "";
  if (!val || val === (node.text || "")) return;
  emit("edit", { id: node.id, text: val, node });
}
function cancelEdit() {
  editId.value = "";
}

/* ---------- 拖拽排序（同 group 内） ---------- */
function onDragStart(e, node) {
  if (!props.draggable || !node.id) return;
  dragId.value = node.id;
  e.dataTransfer.effectAllowed = "move";
  try { e.dataTransfer.setData("text/plain", props.group + "::" + node.id); } catch (_) { /* noop */ }
}
function onDragOver(e, node, idx) {
  if (!dragId.value || !node.id || node.id === dragId.value) return;
  const rect = e.currentTarget.getBoundingClientRect();
  dropId.value = node.id;
  dropPos.value = e.clientY < rect.top + rect.height / 2 ? "before" : "after";
}
function onDragLeave(node) {
  if (dropId.value === node.id) { dropId.value = ""; dropPos.value = ""; }
}
function onDrop(e, node, idx) {
  if (!dragId.value || !node.id) return;
  let group = props.group;
  try {
    const raw = e.dataTransfer.getData("text/plain") || "";
    if (raw.includes("::")) group = raw.split("::")[0];
  } catch (_) { /* noop */ }
  // 红线：不允许跨类型拖拽
  if (group !== props.group) { onDragEnd(); return; }
  if (node.id === dragId.value) { onDragEnd(); return; }

  const fromIdx = (props.nodes || []).findIndex((x) => x.id === dragId.value);
  const toIdx = idx + (dropPos.value === "after" ? 1 : 0);
  if (fromIdx < 0) { onDragEnd(); return; }
  emit("reorder", { group: props.group, from: fromIdx, to: toIdx, fromId: dragId.value, toId: node.id, pos: dropPos.value });
  onDragEnd();
}
function onDragEnd() {
  dragId.value = "";
  dropId.value = "";
  dropPos.value = "";
}
</script>
