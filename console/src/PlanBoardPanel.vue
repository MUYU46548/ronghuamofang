<script setup>
/**
 * 规划四态（2026-11-04，M9 决议“接全量”的第一步：点选采用）。
 *
 * 后端早有这套状态机（utils/plan_state.py + /plan/*），但前端零调用 ——
 * 作者看不到"AI 提了哪些构想、哪些已采用"，只能通读全文。
 *
 * 语义：AI 只能 propose，条目停在 idea；**采用 / 丢弃 / 写进正文都是作者动作**
 * （Agent 调用这四个端点会被 403）。
 *
 * 两个视图（2026-11-04，task-20261104-004）：清单 + 画布（四列看板）。
 * **共用同一份 /plan/list 与同一组 /plan/* 写端点** —— 画布不是另一套状态，
 * 在画布上点“采用”与在清单上点“采用”写的是同一条记录（data/state/plan.json）。
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
const VIEWS = [["list", "清单"], ["canvas", "画布"]];
// 每种状态能做什么（清单与画布共用同一张表，别写两份）
const ACTIONS = {
  idea: [["select", "采用", "primary"], ["drop", "丢弃", "bad-btn"]],
  selected: [["write", "写进正文", "primary"], ["drop", "丢弃", "bad-btn"]],
  dropped: [["revive", "复活回构想", ""]],
};

const items = ref([]);
const stats = ref({});
const tab = ref("idea");
const view = ref("list");
const busy = ref(false);
const err = ref("");
const hint = ref("");
const reason = ref("");
const refPath = ref("");
const openWrite = ref("");       // 哪条在填"写进正文"的引用路径

const shown = computed(() => items.value.filter((x) => x.status === tab.value));
const byStatus = computed(() => {
  const out = {};
  for (const t of TABS) out[t[0]] = items.value.filter((x) => x.status === t[0]);
  return out;
});

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
    // 清单视图按动作跳栏（画布四列都在，不需要跳）
    if (view.value === "list") {
      tab.value = kind === "select" ? "selected" : kind === "drop" ? "dropped"
        : kind === "revive" ? "idea" : tab.value;
    }
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
      <span class="views">
        <button v-for="v in VIEWS" :key="v[0]" class="mini"
                :class="{ primary: view === v[0] }" @click="view = v[0]">{{ v[1] }}</button>
      </span>
      <input v-model="reason" class="reason" placeholder="理由（可选，会记进操作记录）" />
      <button class="mini" :disabled="busy" @click="load">刷新</button>
    </div>

    <p class="meta">
      AI 提的方案只会停在<b>构想</b>；<b>采用 / 丢弃 / 写进正文</b>都是你的动作。
      外接 Agent 可以提案，但不能替你点这些按钮。清单与画布是同一份数据
      （<code>data/state/plan.json</code>），在哪边点都一样。
    </p>

    <div v-if="err" class="err">{{ err }}</div>
    <div v-if="hint" class="meta">{{ hint }}</div>

    <!-- ---------- 清单视图 ---------- -->
    <template v-if="view === 'list'">
    <div class="tabs-sub">
      <button v-for="t in TABS" :key="t[0]" :class="{ on: tab === t[0] }" @click="tab = t[0]"
              :title="t[2]">
        {{ t[1] }}<span class="n">{{ stats[t[0]] || 0 }}</span>
      </button>
    </div>

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
        <span v-else class="meta">已写进正文，无需操作</span>
      </div>
    </div>
    </template>

    <!-- ---------- 画布视图（2026-11-04，task-20261104-004）---------- -->
    <div v-else class="canvas" data-testid="plan-canvas">
      <div v-for="t in TABS" :key="t[0]" class="col" :data-col="t[0]">
        <div class="col-head">
          <b>{{ t[1] }}</b>
          <span class="n">{{ byStatus[t[0]].length }}</span>
        </div>
        <div v-if="!byStatus[t[0]].length" class="empty">空</div>
        <div v-for="it in byStatus[t[0]]" :key="it.id" class="tile" :data-id="it.id">
          <div class="tile-head"><strong>{{ it.title }}</strong></div>
          <div v-if="it.summary" class="tile-sum">{{ it.summary }}</div>
          <div class="meta">{{ it.source || "unknown" }} · {{ it.updated_at || "" }}</div>
          <div v-if="it.ref_path" class="meta">已写进：{{ it.ref_path }}</div>
          <div class="actions">
            <button v-for="a in (ACTIONS[it.status] || [])" :key="a[0]" class="mini"
                    :class="a[2]" :disabled="busy" :data-act="a[0]"
                    @click="a[0] === 'write' ? (openWrite = openWrite === it.id ? '' : it.id) : act(a[0], it)">
              {{ a[1] }}
            </button>
            <input v-if="openWrite === it.id && it.status === 'selected'" v-model="refPath"
                   class="refpath" placeholder="data/chapters/raw/03.md" />
            <button v-if="openWrite === it.id && it.status === 'selected'" class="mini primary"
                    :disabled="busy" data-act="write-confirm" @click="act('write', it)">确认</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.card { margin-bottom: 12px; }
.reason { width: 200px; }
.views { display: inline-flex; gap: 4px; margin-right: 8px; }
.tabs-sub .n { margin-left: 4px; opacity: 0.7; }
.item { border: 1px solid var(--border); border-radius: 6px; padding: 8px; margin-bottom: 8px; }
.item-head { display: flex; align-items: center; gap: 6px; }
.summary { margin-top: 4px; white-space: pre-wrap; }
.actions { display: flex; gap: 6px; align-items: center; margin-top: 6px; flex-wrap: wrap; }
.refpath { width: 260px; }
.err { color: var(--bad); margin: 6px 0; }
.bad-btn { color: var(--bad); }
.pill.idea { color: var(--warn); }
.pill.selected { color: var(--accent); }
.pill.written { color: var(--ok); }
.pill.dropped { color: var(--muted-foreground); }

/* 画布：四列等宽；窄窗口横向滚动，不把列压扁（压扁后标题全断行，等于没法看） */
.canvas { display: grid; grid-template-columns: repeat(4, minmax(180px, 1fr)); gap: 8px;
          margin-top: 10px; overflow-x: auto; }
.col { border: 1px solid var(--border); border-radius: 6px; padding: 8px;
       background: color-mix(in srgb, var(--card) 86%, transparent); min-height: 90px; }
.col-head { display: flex; align-items: center; justify-content: space-between;
            margin-bottom: 6px; font-size: 13px; }
.col-head .n { opacity: 0.7; }
.empty { color: var(--muted-foreground); font-size: 12px; padding: 4px 0; }
.tile { border: 1px solid var(--border); border-radius: 6px; padding: 6px; margin-bottom: 6px;
        background: var(--card); }
.tile-head { font-size: 13px; }
.tile-sum { font-size: 12px; margin-top: 3px; opacity: 0.9;
            display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
            overflow: hidden; }
.tile .actions { margin-top: 4px; }
.tile .refpath { width: 100%; }
</style>
