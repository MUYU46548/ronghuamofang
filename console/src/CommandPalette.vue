<script setup>
// 命令面板（Ctrl+K）：模糊搜索 + 键盘上下选择 + Enter 执行。
// 只负责展示与选择，命令的真正执行由父组件 App.vue 的 runCommand(id) 完成。
import { ref, computed, watch, nextTick } from "vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  // [{id, label, desc?, group?, hint?, keywords?}]
  commands: { type: Array, default: () => [] },
  placeholder: { type: String, default: "搜索命令…（运行阶段 / 页签 / 审稿 / 导出 / 主题）" },
});
const emit = defineEmits(["close", "pick"]);

const q = ref("");
const sel = ref(0);
const inputEl = ref(null);
const listEl = ref(null);

// 命中过滤：空格分词，每个词都要命中（标签/描述/分组/关键词）
const filtered = computed(() => {
  const term = q.value.trim().toLowerCase();
  const all = props.commands || [];
  if (!term) return all;
  const tokens = term.split(/\s+/);
  return all.filter((c) => {
    const hay = [c.label, c.desc, c.group, c.hint, c.keywords]
      .filter(Boolean).join(" ").toLowerCase();
    return tokens.every((t) => hay.includes(t));
  });
});

// 保序分组 + 全局序号（键盘导航用序号）
const groups = computed(() => {
  const out = [];
  filtered.value.forEach((c, i) => {
    const name = c.group || "其他";
    let b = out.find((x) => x.name === name);
    if (!b) { b = { name, items: [] }; out.push(b); }
    b.items.push({ ...c, i });
  });
  return out;
});

watch(() => props.open, (v) => {
  if (v) {
    q.value = "";
    sel.value = 0;
    nextTick(() => inputEl.value?.focus());
  }
});

// 过滤结果变化时把选中项收进合法范围
watch(filtered, () => {
  if (sel.value >= filtered.value.length) sel.value = Math.max(0, filtered.value.length - 1);
});
watch(sel, () => {
  nextTick(() => listEl.value?.querySelector(".cmd-item.on")?.scrollIntoView({ block: "nearest" }));
});

function move(d) {
  const n = filtered.value.length;
  if (!n) return;
  sel.value = (sel.value + d + n) % n;
}

function onKey(e) {
  if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
  else if (e.key === "Home") { e.preventDefault(); sel.value = 0; }
  else if (e.key === "End") { e.preventDefault(); sel.value = Math.max(0, filtered.value.length - 1); }
  else if (e.key === "Enter") {
    e.preventDefault();
    const c = filtered.value[sel.value];
    if (c) emit("pick", c.id);
  } else if (e.key === "Escape") {
    e.preventDefault();
    emit("close");
  }
}

function pick(c) {
  emit("pick", c.id);
}
</script>

<template>
  <div v-if="open" class="drawer-mask cmd-mask" @click.self="$emit('close')">
    <div class="cmd-palette" role="dialog" aria-label="命令面板">
      <input
        ref="inputEl"
        v-model="q"
        class="cmd-input"
        type="text"
        :placeholder="placeholder"
        spellcheck="false"
        @keydown="onKey"
      />
      <div ref="listEl" class="cmd-list">
        <div v-if="!filtered.length" class="empty">没有匹配的命令</div>
        <template v-for="g in groups" :key="g.name">
          <div class="cmd-group">{{ g.name }}</div>
          <button
            v-for="c in g.items"
            :key="c.id"
            class="cmd-item"
            :class="{ on: c.i === sel }"
            @mouseenter="sel = c.i"
            @click="pick(c)"
          >
            <span class="cmd-label">{{ c.label }}</span>
            <span v-if="c.hint" class="cmd-key">{{ c.hint }}</span>
            <span v-if="c.desc" class="cmd-desc">{{ c.desc }}</span>
          </button>
        </template>
      </div>
      <div class="cmd-foot">
        <span>↑↓ 选择</span><span>Enter 执行</span><span>Esc 关闭</span>
        <span class="spacer"></span>
        <span>{{ filtered.length }} / {{ (commands || []).length }} 条命令</span>
      </div>
    </div>
  </div>
</template>
