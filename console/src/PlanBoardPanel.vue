<script setup>
/**
 * 规划四态（2026-11-04，M9 决议“接全量”的第一步：点选采用）。
 *
 * 后端早有这套状态机（utils/plan_state.py + /plan/*），但前端零调用 ——
 * 作者看不到"AI 提了哪些构想、哪些已采用"，只能通读全文。
 *
 * 语义：AI 只能 propose，条目停在 idea；**采用 / 丢弃 / 写进正文都是作者动作**
 * （Agent 调用这四个端点会被 403）。
 */
import { ref, computed, onMounted } from "vue";
import { API } from "./apiBase.js";
import { postJSON } from "./guiApi.js";

const emit = defineEmits(["say"]);

const TABS = [
  ["idea", "构想", "AI 提的、你还没定的"],
  ["selected", "已采用", "你点头了，还没写进正文"],
  ["written", "已写进正文", ""],
  ["dropped", "已丢弃", "可复活"],
];

const items = ref([]);
const stats = ref({});
const tab = ref("idea");
const busy = ref(false);
const err = ref("");
const hint = ref("");
const reason = ref("");
const refPath = ref("");
const openWrite = ref("");       // 哪条在填"写进正文"的引用路径

const shown = computed(() => items.value.filter((x) => x.status === tab.value));

function say(m) {
  hint.value = m;
  emit("say", m);
}

async function get(path) {
  const r = await fetch(API + path);
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || "HTTP " + r.status);
  return d;
}

async function post(path, body) {
  return postJSON(path, body);
}

async function load() {
  busy.value = true;
  err.value = "";
  try {
    const d = await get("/plan/list");
    items.value = d.items || [];
    stats.value = d.stats || {};
  } catch (e) {
    err.value = String(e.message || e);
  } finally {
    busy.value = false;
  }
}

async function act(kind, it) {
  busy.value = true;
  err.value = "";
  try {
    if (kind === "select") {
      await post("/plan/select", { id: it.id, reason: reason.value });
      say(`已采用：${it.title}`);
    } else if (kind === "drop") {
      await post("/plan/drop", { id: it.id, reason: reason.value || "作者丢弃" });
      say(`已丢弃：${it.title}`);
    } else if (kind === "revive") {
      await post("/plan/revive", { id: it.id, reason: reason.value });
      say(`已复活回构想：${it.title}`);
    } else if (kind === "write") {
      if (!refPath.value.trim()) return say("先填写进了哪一章（如 data/chapters/raw/03.md）");
      await post("/plan/write", { id: it.id, ref_path: refPath.value.trim(), reason: reason.value });
      say(`已标为写进正文：${it.title}`);
      openWrite.value = "";
      refPath.value = "";
    }
    reason.value = "";
    await load();
    tab.value = kind === "select" ? "selected" : kind === "drop" ? "dropped"
      : kind === "revive" ? "idea" : tab.value;
  } catch (e) {
    err.value = String(e.message || e);
  } finally {
    busy.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="card">
    <div class="card-head">
      <h3>规划四态（构想 → 采用 → 写进正文）</h3>
      <span class="spacer"></span>
      <input v-model="reason" class="reason" placeholder="理由（可选，会记进审计）" />
      <button class="mini" :disabled="busy" @click="load">刷新</button>
    </div>

    <p class="meta">
      AI 提的方案只会停在<b>构想</b>；<b>采用 / 丢弃 / 写进正文</b>都是你的动作。
      外接 Agent 可以提案，但不能替你点这四个按钮。
    </p>

    <div class="tabs-sub">
      <button v-for="t in TABS" :key="t[0]" :class="{ on: tab === t[0] }" @click="tab = t[0]"
              :title="t[2]">
        {{ t[1] }}<span class="n">{{ stats[t[0]] || 0 }}</span>
      </button>
    </div>

    <div v-if="err" class="err">{{ err }}</div>
    <div v-if="hint" class="meta">{{ hint }}</div>

    <div v-if="!shown.length" class="meta">
      {{ tab === "idea" ? "没有待定的构想（AI 提方案时会出现在这里）。" : "这一栏是空的。" }}
    </div>

    <div v-for="it in shown" :key="it.id" class="item">
      <div class="item-head">
        <strong>{{ it.title }}</strong>
        <span class="meta">· {{ it.source || "unknown" }} · {{ it.updated_at || "" }}</span>
        <span class="spacer"></span>
        <span class="pill" :class="it.status">{{ it.status }}</span>
      </div>
      <div v-if="it.summary" class="summary">{{ it.summary }}</div>
      <div v-if="it.ref_path" class="meta">已写进：{{ it.ref_path }}</div>

      <div class="actions">
        <template v-if="it.status === 'idea'">
          <button class="mini primary" :disabled="busy" @click="act('select', it)">采用</button>
          <button class="mini bad-btn" :disabled="busy" @click="act('drop', it)">丢弃</button>
        </template>
        <template v-else-if="it.status === 'selected'">
          <button class="mini primary" :disabled="busy"
                  @click="openWrite = openWrite === it.id ? '' : it.id">写进正文</button>
          <button class="mini bad-btn" :disabled="busy" @click="act('drop', it)">丢弃</button>
          <input v-if="openWrite === it.id" v-model="refPath" class="refpath"
                 placeholder="data/chapters/raw/03.md" />
          <button v-if="openWrite === it.id" class="mini primary" :disabled="busy"
                  @click="act('write', it)">确认</button>
        </template>
        <template v-else-if="it.status === 'dropped'">
          <button class="mini" :disabled="busy" @click="act('revive', it)">复活回构想</button>
        </template>
        <span v-else class="meta">已落地，无需操作</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.card { margin-bottom: 12px; }
.reason { width: 200px; }
.tabs-sub .n { margin-left: 4px; opacity: 0.7; }
.item { border: 1px solid var(--border); border-radius: 6px; padding: 8px; margin-bottom: 8px; }
.item-head { display: flex; align-items: center; gap: 6px; }
.summary { margin-top: 4px; white-space: pre-wrap; }
.actions { display: flex; gap: 6px; align-items: center; margin-top: 6px; flex-wrap: wrap; }
.refpath { width: 260px; }
.spacer { flex: 1; }
.err { color: var(--bad); margin: 6px 0; }
.bad-btn { color: var(--bad); }
.pill.idea { color: var(--warn); }
.pill.selected { color: var(--accent); }
.pill.written { color: var(--ok); }
.pill.dropped { color: var(--muted-foreground); }
</style>
