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
 */
import { ref, computed, onMounted } from "vue";

import { API } from "./apiBase.js";

const emit = defineEmits(["say"]);

const data = ref(null);
const busy = ref(false);
const err = ref("");

const items = computed(() => (data.value && data.value.items) || []);
const summary = computed(() => (data.value && data.value.summary) || {});
const notes = computed(() => (data.value && data.value.notes) || []);

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

function statusLabel(s) {
  return { open: "未推进", advanced: "已推进", closed: "已回收", abandoned: "已放弃" }[s] || s;
}

onMounted(load);
defineExpose({ load });
</script>

<template>
  <div class="fsp">
    <div class="fsp-head">
      <b>伏笔台账</b>
      <span class="meta" v-if="data">
        共 {{ summary.total || 0 }} 条 · 未推进 {{ summary.open || 0 }}
        · 已推进 {{ summary.advanced || 0 }} · 已回收 {{ summary.closed || 0 }}
        · 超期 <span :class="{ 'warn-text': (summary.stale || 0) > 0 }">{{ summary.stale || 0 }}</span>
      </span>
      <span class="spacer"></span>
      <button class="mini" :disabled="busy" @click="load">刷新</button>
    </div>

    <div v-if="err" class="meta err-line">{{ err }}</div>

    <div v-else-if="data && !items.length" class="meta fsp-empty">
      还没有可抽取的伏笔。台账来自**章大纲的“悬念钩子”**（阶段 3 每章一条），
      跑过阶段 3 之后这里会列出每章埋下的线。
    </div>

    <table v-else-if="items.length" class="fsp-table">
      <thead>
        <tr>
          <th>线索</th>
          <th>埋设</th>
          <th>状态</th>
          <th>闲置</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="it in items" :key="it.id" :class="{ stale: it.stale }">
          <td>
            <div class="fsp-text">{{ it.text }}</div>
            <div class="meta fsp-id">
              {{ it.id }}
              <span v-if="it.advanced && it.advanced.length">· 推进于第 {{ it.advanced.join('、') }} 章</span>
              <span v-else>· 埋下后一直没再提</span>
              <span v-if="it.source === 'human'"> · 人工：{{ it.reason }}</span>
            </div>
          </td>
          <td>第 {{ it.planted }} 章</td>
          <td>
            <span class="pill" :class="'fsp-' + it.status">{{ statusLabel(it.status) }}</span>
          </td>
          <td :class="{ 'warn-text': it.stale }">{{ it.idle }} 章</td>
          <td class="fsp-ops">
            <button class="mini" :disabled="busy || it.status === 'closed'"
                    @click="mark(it, 'closed')">已回收</button>
            <button class="mini" :disabled="busy || it.status === 'abandoned'"
                    @click="mark(it, 'abandoned')">已放弃</button>
            <button class="mini" :disabled="busy || (it.status !== 'closed' && it.status !== 'abandoned')"
                    @click="mark(it, 'open')">重开</button>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-for="(n, i) in notes" :key="i" class="meta fsp-note">{{ n }}</div>
  </div>
</template>

<style scoped>
.fsp { margin-top: 14px; padding-top: 10px; border-top: 1px dashed var(--border); }
.fsp-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
.fsp-empty { padding: 4px 0 2px; }
.err-line { color: var(--danger, #c0392b); }
.fsp-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.fsp-table th, .fsp-table td {
  text-align: left; padding: 5px 6px; border-bottom: 1px solid var(--border); vertical-align: top;
}
.fsp-table th { font-weight: 600; opacity: .75; }
.fsp-table tr.stale td { background: color-mix(in srgb, var(--warn, #b8860b) 8%, transparent); }
.fsp-text { line-height: 1.5; }
.fsp-id { margin-top: 2px; }
.fsp-ops { white-space: nowrap; }
.fsp-ops .mini { margin-right: 4px; }
.fsp-note {
  margin-top: 6px; padding: 6px 8px; border-left: 2px solid var(--warn, #b8860b);
  background: var(--field-inset, transparent);
}
.warn-text { color: var(--warn, #b8860b); }
.fsp-open { opacity: .8; }
.fsp-advanced { opacity: .95; }
.fsp-closed { opacity: .6; }
.fsp-abandoned { opacity: .45; text-decoration: line-through; }
</style>
