<script setup>
/**
 * 一致性账本面板（P3）。
 *
 * 数据源：GET /story_state（只读）。
 * 账本由 stage4 每章落盘后增量更新，记的是"跨章事实"：谁在哪章死了、
 * 哪件物品被扔了、哪一年出现过。冲突是**代码**判的，零 token。
 *
 * 这里只显示，不提供"一键修复"：矛盾该改哪一句只有作者知道，
 * 给个按钮自动改等于把判断权交给规则。
 */
import { ref, computed, onMounted } from "vue";

import { API } from "./apiBase.js";

const emit = defineEmits(["say"]);

const state = ref(null);
const busy = ref(false);
const err = ref("");

async function load() {
  busy.value = true;
  err.value = "";
  try {
    const r = await fetch(API + "/story_state");
    const d = await r.json().catch(() => ({}));
    if (r.status === 200 && d.ok) {
      state.value = d;
    } else {
      state.value = null;
      err.value = d.error || ("读取失败：HTTP " + r.status);
    }
  } catch (e) {
    err.value = String(e);
  }
  busy.value = false;
}

const conflictCount = computed(
  () => (state.value?.conflicts || []).filter((c) => c.level === "conflict").length);
const warningCount = computed(
  () => (state.value?.conflicts || []).filter((c) => c.level !== "conflict").length);

function shortTime(s) {
  if (!s) return "";
  return String(s).replace("T", " ").slice(0, 16);
}

onMounted(load);
defineExpose({ load });
</script>

<template>
  <div class="card">
    <div class="card-head">
      <h3>一致性账本</h3>
      <span class="spacer"></span>
      <span class="meta" v-if="state && state.updated_at">更新于 {{ shortTime(state.updated_at) }}</span>
      <button class="mini" :disabled="busy" @click="load">{{ busy ? "读取中…" : "刷新" }}</button>
    </div>
    <div class="meta">
      跨章事实账本：死亡 / 物品去向 / 时间锚。stage4 每章落盘后增量更新，<b>零 token、只报不拦</b>。
      冲突会给出章号与原文证据 —— 改哪一句由你定。
    </div>

    <div v-if="err" class="empty" style="padding:14px 0;">{{ err }}</div>
    <div v-else-if="!state" class="meta" style="padding:10px 0;">读取中…</div>
    <div v-else-if="!state.events" class="meta" style="padding:10px 0;">
      还没有账本 —— 跑过 stage4 之后每章自动生成。
    </div>
    <template v-else>
      <div class="cs-summary">
        <span class="cs-stat"><b>{{ state.events }}</b> 条事件</span>
        <span class="cs-stat" :class="{ 'bad-val': conflictCount }">
          <b>{{ conflictCount }}</b> 条冲突</span>
        <span class="cs-stat"><b>{{ warningCount }}</b> 条提示</span>
      </div>

      <div v-if="!state.conflicts.length" class="meta cs-ok">未发现矛盾。</div>

      <div v-for="(c, i) in state.conflicts"
           :key="c.code + '-' + (c.chapter || 0) + '-' + (c.who || c.item || '') + '-' + i"
           class="cs-item" :class="{ warn: c.level === 'warning' }">
        <div class="cs-head">
          <span class="cs-tag" :class="c.level === 'conflict' ? 'bad' : 'warn'">
            {{ c.level === "conflict" ? "冲突" : "提示" }}</span>
          <span class="cs-ch" v-if="c.chapter">第 {{ c.chapter }} 章</span>
          <span class="cs-detail">{{ c.detail }}</span>
        </div>
        <div class="cs-ev" v-if="c.evidence && c.evidence.length">
          <div v-for="(e, j) in c.evidence" :key="j">· {{ e }}</div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.cs-summary {
  display: flex;
  gap: 16px;
  align-items: baseline;
  margin: 10px 0 8px;
  flex-wrap: wrap;
}
.cs-stat b { font-size: 16px; }

.cs-item {
  border-left: 3px solid var(--bad);
  padding: 8px 12px;
  margin: 8px 0;
  background: var(--field-dim);
  border-radius: 6px;
}
.cs-item.warn { border-left-color: var(--warn); }

.cs-head {
  display: flex;
  gap: 8px;
  align-items: baseline;
  flex-wrap: wrap;
}
.cs-tag {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--field);
  border: 1px solid var(--border);
  flex: none;
}
.cs-tag.bad { color: var(--bad); border-color: var(--bad); }
.cs-tag.warn { color: var(--warn); border-color: var(--warn); }
.cs-ch { font-weight: 600; flex: none; }
.cs-detail { flex: 1 1 240px; }

.cs-ev {
  margin-top: 6px;
  font-size: 12.5px;
  line-height: 1.75;
  opacity: 0.78;
}
.cs-ok { color: var(--ok); }
</style>
