<script setup>
/**
 * 伏笔台账面板（2026-10-20，预警 P1 / roadmap v0.9.3）。
 *
 * 数据源：GET /foreshadow（只读盘，零额外 token）。
 * 逐条来自**章大纲的“悬念钩子”字段**（v0.7.2 起强制），
 * “推进”由后续章文本重合判定（bigram 交集 + 覆盖率双达标）。
 *
 * 关键取舍：**回收不自动判定**。文本相似不等于"解决了"，自动判会让台账变成谎话。
 * 所以“已回收 / 已放弃”只能人点，而且**必须写理由** —— 这是唯一能防止台账被
 * 随手清空的东西。人工标记压过机器判定（与素材裁决同口径）。
 *
 * 2026-11-28：样式迁到 Tailwind + 主题变量（scoped 样式块已摘除），
 * 表格 / 徽标 / 按钮换上新基座；状态四档由 `UiBadge` 的 variant 表达，
 * 不再靠 `.fsp-open/.fsp-closed` 这种一档一个类名（那正是漂移的源头）。
 * 有意保留：超期行整行淡琥珀底（`color-mix` 8%，与旧值同源）、
 * `fsp-ops` 列 nowrap（三颗按钮换行会撑高行高）。
 */
import { ref, computed, onMounted } from "vue";

import UiBadge from "./components/ui/UiBadge.vue";
import UiButton from "./components/ui/UiButton.vue";
import UiTable from "./components/ui/UiTable.vue";
import { API } from "./apiBase.js";

const emit = defineEmits(["say"]);

const data = ref(null);
const busy = ref(false);
const err = ref("");

const items = computed(() => (data.value && data.value.items) || []);
const summary = computed(() => (data.value && data.value.summary) || {});
const notes = computed(() => (data.value && data.value.notes) || []);

const COLUMNS = [
  { key: "text", label: "线索" },
  { key: "planted", label: "埋设", cls: "whitespace-nowrap w-[88px]" },
  { key: "status", label: "状态", cls: "w-[92px]" },
  { key: "idle", label: "闲置", cls: "whitespace-nowrap w-[72px]" },
  { key: "ops", label: "操作", cls: "whitespace-nowrap" },
];

// 状态 → 徽标语义（四档）。open/advanced 用中性，回收/放弃给出可读的视觉差。
const BADGE = {
  open: { variant: "neutral", text: "未推进" },
  advanced: { variant: "accent", text: "已推进" },
  closed: { variant: "ok", text: "已回收" },
  abandoned: { variant: "neutral", text: "已放弃", struck: true },
};

function badgeOf(s) {
  return BADGE[s] || { variant: "neutral", text: s };
}

// 超期行整行淡琥珀底：颜色由主题色按 8% 调出（与旧 `tr.stale td` 同源），
// 判定条件留在组件里，UiTable 只负责把它加上某个 <tr>。
function rowCls(row) {
  return row.stale ? "bg-[color-mix(in_srgb,var(--warn)_8%,transparent)]" : "";
}

async function load() {
  busy.value = true;
  err.value = "";
  try {
    const r = await fetch(API + "/foreshadow");
    const d = await r.json().catch(() => ({}));
    if (r.status === 200 && d.ok) {
      data.value = d;
    } else {
      data.value = null;
      err.value = d.error || ("读取失败：HTTP " + r.status);
    }
  } catch (e) {
    err.value = String(e);
  } finally {
    busy.value = false;
  }
}

async function post(path, body) {
  const r = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Mofang-Source": "gui" },
    body: JSON.stringify(body),
  });
  return { status: r.status, data: await r.json().catch(() => ({})) };
}

async function mark(it, status) {
  const label = status === "closed" ? "已回收" : status === "abandoned" ? "已放弃" : "重新打开";
  let reason = "";
  if (status !== "open") {
    const v = window.prompt(
      `标记“${it.id}”为${label}。\n\n请写一句理由（必填）：\n`
      + `· 已回收：在哪一章、怎么解决的\n`
      + `· 已放弃：为什么这条不要了`,
      "");
    if (v === null) return;
    reason = String(v).trim();
    if (!reason) {
      emit("say", "“" + label + "”必须写理由 —— 否则以后没人知道为什么");
      return;
    }
  }
  busy.value = true;
  try {
    const r = await post("/foreshadow/status", { id: it.id, status, reason });
    if (r.status !== 200) {
      emit("say", "标记失败：" + (r.data.error || r.status));
      return;
    }
    emit("say", r.data.message || ("已标记为" + label));
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
      <b class="text-[color:var(--ink)]">伏笔清单</b>
      <span class="meta" v-if="data">
        共 {{ summary.total || 0 }} 条 · 未推进 {{ summary.open || 0 }}
        · 已推进 {{ summary.advanced || 0 }} · 已回收 {{ summary.closed || 0 }}
        · 超期 <span :class="{ 'text-[color:var(--warn)]': (summary.stale || 0) > 0 }">{{ summary.stale || 0 }}</span>
      </span>
      <span class="spacer"></span>
      <UiButton :disabled="busy" @click="load">刷新</UiButton>
    </div>

    <div v-if="err" class="meta text-[color:var(--bad)]">{{ err }}</div>

    <div v-else-if="data && !items.length" class="meta pb-0.5 pt-1">
      还没有可抽取的伏笔。清单来自章大纲的“悬念钩子”（阶段 3 每章一条），
      跑过阶段 3 之后这里会列出每章埋下的线。
    </div>

    <UiTable v-else-if="items.length" :columns="COLUMNS" :rows="items" row-key="id"
             :row-class="rowCls">
      <template #cell-text="{ row }">
        <div class="leading-[1.5]">{{ row.text }}</div>
        <div class="meta mt-0.5">
          {{ row.id }}
          <span v-if="row.advanced && row.advanced.length">· 推进于第 {{ row.advanced.join('、') }} 章</span>
          <span v-else>· 埋下后一直没再提</span>
          <span v-if="row.source === 'human'"> · 人工：{{ row.reason }}</span>
        </div>
      </template>
      <template #cell-planted="{ row }">第 {{ row.planted }} 章</template>
      <template #cell-status="{ row }">
        <UiBadge :variant="badgeOf(row.status).variant" :struck="!!badgeOf(row.status).struck">
          {{ badgeOf(row.status).text }}
        </UiBadge>
      </template>
      <template #cell-idle="{ row }">
        <span :class="{ 'text-[color:var(--warn)]': row.stale }">{{ row.idle }} 章</span>
      </template>
      <template #cell-ops="{ row }">
        <div class="flex items-center gap-1 whitespace-nowrap">
          <UiButton :disabled="busy || row.status === 'closed'"
                    @click="mark(row, 'closed')">已回收</UiButton>
          <UiButton :disabled="busy || row.status === 'abandoned'"
                    @click="mark(row, 'abandoned')">已放弃</UiButton>
          <UiButton :disabled="busy || (row.status !== 'closed' && row.status !== 'abandoned')"
                    @click="mark(row, 'open')">重开</UiButton>
        </div>
      </template>
    </UiTable>

    <div v-for="(n, i) in notes" :key="i"
         class="meta mt-1.5 border-l-2 border-[var(--warn)] bg-[var(--field-inset)] px-2 py-1.5">
      {{ n }}
    </div>
  </div>
</template>
