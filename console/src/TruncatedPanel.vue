<script setup>
/**
 * 残稿面板（2026-10-20，预警 P1“残稿可见可采纳”）。
 *
 * 数据源：GET /truncated（只读盘，零 token）。
 *
 * 为什么必须做：`finish_reason=length` 时我们不落盘（纪律正确），但残缺文本一直
 * 隔离在 `data/state/truncated/` 里，**人看不见**。失败之后用户只看到“失败”，
 * 不知道模型其实已经写了 3000 字。
 *
 * 三条界面纪律：
 *  1. **采纳 != 完成**：采纳后仍标“残稿”，按钮变成“已补齐”，系统不会当它完成。
 *  2. **不自动采纳**：截断点之后的情节可能整段缺失，必须人看一眼。
 *  3. 太短的残稿不给采纳按钮，并说明原因（多半刚开始就断了，重写更省）。
 *
 * 2026-11-28：样式迁到 Tailwind + 主题变量（scoped 样式块已摘除），
 * 表格 / 徽标 / 按钮 / 输入框换上新基座（UiTable / UiBadge / UiButton / UiInput）。
 * 三处有意保留的像素值：
 *  - 章号输入框 `w-[62px]`：表格一格内要塞下三位数，再宽就把“采纳”挤下去；
 *  - 预览列 `max-w-[420px]` + 单行省略：不截时一行正文会把操作列挤出视口；
 *  - 徽标用 `variant="warn" strong`：等价于旧 `.pill.st-gate`（琥珀 + 加粗，
 *    表示“等你处理”而不是普通提示）。
 */
import { ref, onMounted } from "vue";

import UiBadge from "./components/ui/UiBadge.vue";
import UiButton from "./components/ui/UiButton.vue";
import UiInput from "./components/ui/UiInput.vue";
import UiTable from "./components/ui/UiTable.vue";
import { API } from "./apiBase.js";

const emit = defineEmits(["say"]);

const items = ref([]);
const busy = ref(false);
const err = ref("");
const loaded = ref(false);
const chapterOf = ref({});
const minChars = ref(300);

// 列定义走基座：五个列头与旧的 tsp-table 一一对应，几何由 UiTable 唯一决定。
const COLUMNS = [
  { key: "at", label: "时间", cls: "whitespace-nowrap text-[color:var(--muted)]" },
  { key: "chars", label: "字数", cls: "w-[70px]" },
  { key: "preview", label: "开头", cls: "max-w-[420px]" },
  { key: "adopt", label: "采纳为第", cls: "w-[130px]" },
  { key: "status", label: "状态" },
];

async function load() {
  busy.value = true;
  err.value = "";
  try {
    const r = await fetch(API + "/truncated");
    const d = await r.json().catch(() => ({}));
    if (r.status === 200 && d.ok) {
      items.value = d.items || [];
      minChars.value = d.min_chars || 300;
      const map = {};
      for (const it of items.value) {
        if (it.adopted_as) continue;
        map[it.name] = it.chapter_hint || "";
      }
      chapterOf.value = map;
    } else {
      items.value = [];
      err.value = d.error || ("读取失败：HTTP " + r.status);
    }
  } catch (e) {
    err.value = String(e);
  } finally {
    busy.value = false;
    loaded.value = true;
  }
}

async function post(path, body) {
  const r = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Mofang-Source": "gui" },
    body: JSON.stringify(body),
  });
  const d = await r.json().catch(() => ({}));
  return { status: r.status, data: d };
}

async function adopt(it) {
  const no = chapterOf.value[it.name];
  if (!no) {
    emit("say", "先填“采纳为第几章”");
    return;
  }
  busy.value = true;
  try {
    let r = await post("/truncated/adopt", { name: it.name, chapter: no });
    // 目标章已有内容 → 后端拒绝并说明；确认后才带 replace 重试
    if (r.status !== 200 && String(r.data.error || "").includes("覆盖")) {
      if (window.confirm(`第 ${no} 章已有内容。\n\n确定用这份残稿覆盖它？\n（旧内容会被替换，建议先确认那一章不是你要留的）`)) {
        r = await post("/truncated/adopt", { name: it.name, chapter: no, replace: true });
      } else {
        return;
      }
    }
    if (r.status !== 200) {
      emit("say", "采纳失败：" + (r.data.error || r.status));
      return;
    }
    emit("say", r.data.message || "已采纳");
    await load();
  } finally {
    busy.value = false;
  }
}

async function clearMark(no) {
  if (!window.confirm(`确认第 ${no} 章已经补齐/改好？\n\n清除标记后，系统会重新按正常标准判定这一章是否完成。`)) return;
  busy.value = true;
  try {
    const r = await post("/truncated/clear", { chapter: no });
    emit("say", r.status === 200 ? (r.data.message || "已清除")
                                : ("失败：" + (r.data.error || r.status)));
    await load();
  } finally {
    busy.value = false;
  }
}

onMounted(load);
defineExpose({ load });
</script>

<template>
  <div class="sep-dashed">
    <div class="row-baseline mb-2">
      <b class="text-[color:var(--ink)]">残稿（写到一半被截断的正文）</b>
      <span class="meta" v-if="loaded">共 {{ items.length }} 份</span>
      <span class="spacer"></span>
      <UiButton :disabled="busy" @click="load">刷新</UiButton>
    </div>

    <div v-if="err" class="meta text-[color:var(--bad)]">{{ err }}</div>
    <div v-else-if="loaded && !items.length" class="meta pb-0.5 pt-1">
      没有残稿。被截断的输出会隔离存放，以后可以在这里看见并采纳为草稿。
    </div>

    <UiTable v-else-if="items.length" :columns="COLUMNS" :rows="items" row-key="name">
      <template #cell-chars="{ row }">
        <span :class="row.adoptable ? '' : 'text-[color:var(--warn)]'">{{ row.chars }}</span>
      </template>
      <template #cell-preview="{ row }">
        <span class="block overflow-hidden text-ellipsis whitespace-nowrap text-[color:var(--muted)]">{{ row.preview }}</span>
      </template>
      <template #cell-adopt="{ row }">
        <div v-if="!row.adopted_as" class="row-tight">
          <UiInput v-model.number="chapterOf[row.name]" type="number" min="1" max="999"
                   class="w-[62px]" placeholder="章号" />
          <UiButton :disabled="busy || !row.adoptable" @click="adopt(row)"
                    :title="row.adoptable ? '把这份残稿写成该章草稿' : ('太短（少于 ' + minChars + ' 字），建议直接重写')">
            采纳
          </UiButton>
        </div>
        <span v-else class="meta">—</span>
      </template>
      <template #cell-status="{ row }">
        <span v-if="row.adopted_as" class="row-tight">
          <UiBadge variant="warn" strong>第 {{ row.adopted_as }} 章 · 残稿</UiBadge>
          <UiButton :disabled="busy" @click="clearMark(row.adopted_as)">已补齐</UiButton>
        </span>
        <span v-else-if="!row.adoptable" class="meta">太短，不建议采纳</span>
        <span v-else class="meta">未采纳</span>
      </template>
    </UiTable>
  </div>
</template>
