<script setup>
/**
 * stage5 改稿裁定（2026-11-04，N1=B）。
 *
 * 数据源（零 token，只读盘）：
 *   GET  /review/stage5/list            待裁定清单
 *   GET  /review/stage5/chapter?n=      单章逐条 old/new
 *   POST /review/stage5/decide          勾“采用 / 不采用”（GUI-only）
 *   POST /review/stage5/apply           合成 checked/NN.md（GUI-only）
 *
 * 为什么要有这块：stage5 原先由模型改完直接写 checked/，改完的稿就是成品。
 * 现在模型产物先落 checked_pending，只有作者逐条点头才合成。
 */
import { ref, computed, onMounted } from "vue";
import { API } from "./apiBase.js";
import { postJSON } from "./guiApi.js";

const emit = defineEmits(["say"]);

const rows = ref([]);
const current = ref(null);      // 当前章详情
const busy = ref(false);
const err = ref("");
const hint = ref("");

const pending = computed(() => rows.value.filter((r) => r.pending && r.pending.length));

function say(msg) {
  hint.value = msg;
  emit("say", msg);
}

async function get(path) {
  const r = await fetch(API + path);
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200 || !d.ok) throw new Error(d.error || "HTTP " + r.status);
  return d;
}

async function post(path, body) {
  return postJSON(path, body);
}

async function load() {
  busy.value = true;
  err.value = "";
  try {
    const d = await get("/review/stage5/list");
    rows.value = d.chapters || [];
    if (current.value && !rows.value.some((r) => r.chapter === current.value.chapter)) {
      current.value = null;
    }
    if (!current.value && rows.value.length) await open(rows.value[0].chapter);
  } catch (e) {
    err.value = String(e.message || e);
  } finally {
    busy.value = false;
  }
}

async function open(n) {
  busy.value = true;
  err.value = "";
  try {
    current.value = await get("/review/stage5/chapter?n=" + n);
  } catch (e) {
    err.value = String(e.message || e);
    current.value = null;
  } finally {
    busy.value = false;
  }
}

async function decide(item, accepted) {
  if (!current.value) return;
  busy.value = true;
  err.value = "";
  try {
    await post("/review/stage5/decide", {
      chapter: current.value.chapter,
      decisions: { [item.id]: accepted },
    });
    say(accepted ? `第 ${item.id} 项：采用` : `第 ${item.id} 项：不采用`);
    await open(current.value.chapter);
    await refreshRows();
  } catch (e) {
    err.value = String(e.message || e);
  } finally {
    busy.value = false;
  }
}

async function refreshRows() {
  const d = await get("/review/stage5/list");
  rows.value = d.chapters || [];
}

async function applyChapter() {
  if (!current.value) return;
  busy.value = true;
  err.value = "";
  try {
    const d = await post("/review/stage5/apply", { chapter: current.value.chapter });
    say(d.message || "已合成");
    await load();
  } catch (e) {
    err.value = String(e.message || e);
  } finally {
    busy.value = false;
  }
}

async function applyAll() {
  busy.value = true;
  err.value = "";
  try {
    const d = await post("/review/stage5/apply", { all: true });
    say(`已合成 ${d.applied} 章`);
    await load();
  } catch (e) {
    err.value = String(e.message || e);
  } finally {
    busy.value = false;
  }
}

function kindLabel(k) {
  return { replace: "改写", insert: "新增", delete: "删除" }[k] || k;
}

onMounted(load);
</script>

<template>
  <div class="card">
    <div class="card-head">
      <h3>stage5 改稿裁定</h3>
      <span v-if="pending.length" class="pill warn">{{ pending.length }} 章待定</span>
      <span class="spacer"></span>
      <button class="mini" :disabled="busy" @click="load">刷新</button>
      <button class="mini" :disabled="busy || !pending.length" @click="applyAll">合成全部已定稿</button>
    </div>

    <p class="meta">
      模型改稿先落 <code>data/chapters/checked_pending/</code>，逐条勾“采用/不采用”后才合成
      <code>data/chapters/checked/</code>。没勾完不会进阶段 6。
    </p>

    <div v-if="err" class="err">{{ err }}</div>
    <div v-if="hint" class="meta">{{ hint }}</div>

    <div v-if="!rows.length" class="meta">没有待裁定的改稿（跑了阶段 5 且未关闭裁定闸时才出现）。</div>

    <div v-else class="split">
      <div class="list">
        <button
          v-for="r in rows"
          :key="r.chapter"
          class="row"
          :class="{ on: current && current.chapter === r.chapter }"
          @click="open(r.chapter)"
        >
          <span>第 {{ r.chapter }} 章</span>
          <span class="meta">{{ r.decided }}/{{ r.items }}</span>
          <span v-if="r.pending.length" class="pill warn">待定</span>
          <span v-else class="pill ok">已定</span>
        </button>
      </div>

      <div v-if="current" class="detail">
        <div class="detail-head">
          <strong>第 {{ current.chapter }} 章</strong>
          <span class="meta">· {{ current.status }} · raw {{ current.raw_sha }}</span>
          <span class="spacer"></span>
          <button
            class="mini primary"
            :disabled="busy || !current.can_apply"
            :title="current.can_apply ? '' : '还有未裁定的项'"
            @click="applyChapter"
          >
            合成本章
          </button>
        </div>

        <div v-if="!current.items.length" class="meta">模型没有改动这一章，合成结果与原文一致。</div>

        <div v-for="it in current.items" :key="it.id" class="item">
          <div class="item-head">
            <span class="tag">{{ kindLabel(it.kind) }}</span>
            <span class="meta">第 {{ it.id }} 项</span>
            <span class="spacer"></span>
            <span v-if="it.accepted === true" class="pill ok">采用</span>
            <span v-else-if="it.accepted === false" class="pill bad">不采用</span>
            <span v-else class="pill warn">未定</span>
          </div>
          <div v-if="it.old" class="old"><pre>{{ it.old }}</pre></div>
          <div v-if="it.new" class="new"><pre>{{ it.new }}</pre></div>
          <div class="actions">
            <button class="mini ok-btn" :disabled="busy" @click="decide(it, true)">采用模型改法</button>
            <button class="mini bad-btn" :disabled="busy" @click="decide(it, false)">保留原文</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.card { margin-bottom: 12px; }
.split { display: grid; grid-template-columns: 190px 1fr; gap: 12px; }
.list { display: flex; flex-direction: column; gap: 4px; }
.row { display: flex; align-items: center; gap: 6px; justify-content: flex-start; text-align: left; }
.row.on { border-color: var(--accent); }
.detail { min-width: 0; }
.detail-head { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
.item { border: 1px solid var(--border); border-radius: 6px; padding: 8px; margin-bottom: 10px; }
.item-head { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
.tag { font-size: 12px; padding: 1px 6px; border-radius: 4px; background: var(--card); border: 1px solid var(--border); }
.old { border-left: 3px solid var(--bad); padding: 4px 8px; margin-bottom: 4px; }
.new { border-left: 3px solid var(--ok); padding: 4px 8px; }
pre { white-space: pre-wrap; margin: 4px 0; font-family: inherit; }
.actions { display: flex; gap: 6px; margin-top: 6px; }
.spacer { flex: 1; }
.err { color: var(--bad); margin: 6px 0; }
.ok-btn { color: var(--ok); }
.bad-btn { color: var(--bad); }
@media (max-width: 760px) { .split { grid-template-columns: 1fr; } }
</style>
