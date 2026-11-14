<script setup>
/**
 * stage5 改稿裁定（2026-11-04，N1=B；2026-11-14 改逐处步进 P1-1）。
 *
 * 数据源（零 token，只读盘）：
 *   GET  /review/stage5/list            待裁定清单
 *   GET  /review/stage5/chapter?n=      单章逐条 old/new
 *   POST /review/stage5/decide          勾“采用 / 不采用”（GUI-only）
 *   POST /review/stage5/apply           合成 checked/NN.md（GUI-only）
 *
 * 为什么要有这块：stage5 原先由模型改完直接写 checked/，改完的稿就是成品。
 * 现在模型产物先落 checked_pending，只有作者逐条点头才合成。
 *
 * 为什么改成逐处步进（P1-1）：原来 40 项全展开 × 两个按钮，人的实际行为就是一路点。
 * 现在一次只看一处，定完自动跳到下一处没定的；按钮位置不动，连续点就是流水线。
 * 后端完全不变 —— 裁定数据本来就按项存，一次只提交当前这一处的决定。
 */
import { ref, computed, onMounted } from "vue";
import { API } from "./apiBase.js";
import { postJSON } from "./guiApi.js";

const emit = defineEmits(["say"]);

const rows = ref([]);
const current = ref(null);      // 当前章详情
const idx = ref(0);             // 当前看第几处（0 基）
const busy = ref(false);
const err = ref("");
const hint = ref("");

const pending = computed(() => rows.value.filter((r) => r.pending && r.pending.length));
const items = computed(() => (current.value && current.value.items) || []);
const total = computed(() => items.value.length);
const item = computed(() => items.value[idx.value] || null);
const decided = computed(() => items.value.filter((it) => it.accepted != null).length);
const remaining = computed(() => total.value - decided.value);

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

function clamp() {
  const n = items.value.length;
  idx.value = !n ? 0 : Math.min(Math.max(idx.value, 0), n - 1);
}

/** 从 from 开始找下一处还没定的；没有就返回 -1。 */
function nextUndecided(from) {
  const list = items.value;
  for (let i = Math.max(from, 0); i < list.length; i++) {
    if (list[i].accepted == null) return i;
  }
  return -1;
}

async function load() {
  busy.value = true;
  err.value = "";
  try {
    const d = await get("/review/stage5/list");
    rows.value = needRows(d.chapters);
    if (current.value && !rows.value.some((r) => r.chapter === current.value.chapter)) {
      current.value = null;
      idx.value = 0;
    }
    if (!current.value && rows.value.length) await open(rows.value[0].chapter);
  } catch (e) {
    err.value = String(e.message || e);
  } finally {
    busy.value = false;
  }
}

/** 读一章。keepPos=true 时停在原来那一处（裁定后刷新不跳位置）。 */
async function open(n, keepPos = false) {
  busy.value = true;
  err.value = "";
  try {
    current.value = await get("/review/stage5/chapter?n=" + n);
    if (!keepPos) {
      const first = nextUndecided(0);
      idx.value = first < 0 ? 0 : first;
    }
    clamp();
  } catch (e) {
    err.value = String(e.message || e);
    current.value = null;
    idx.value = 0;
  } finally {
    busy.value = false;
  }
}

async function refreshRows() {
  const d = await get("/review/stage5/list");
  rows.value = needRows(d.chapters);
}

/**
 * 只留“还需要你动手”的章。
 * 为什么过滤（2026-11-14）：`list_all` 连已合成（approved）的章一起返回，
 * 合成完它还挂在“待裁定”列表里、按钮还亮着 —— 看着像没合成成功。
 */
function needRows(list) {
  return (list || []).filter((r) => r.status !== "approved");
}

async function decide(it, accepted) {
  if (!current.value || !it) return;
  busy.value = true;
  err.value = "";
  const at = idx.value;
  try {
    await post("/review/stage5/decide", {
      chapter: current.value.chapter,
      decisions: { [it.id]: accepted },
    });
    say(`第 ${it.id} 处：${accepted ? "采用改法" : "保留原文"}`);
    await open(current.value.chapter, true);
    // 只往后找下一处没定的（往前翻回去改过的处不抢焦点）
    const nxt = nextUndecided(at + 1);
    if (nxt >= 0) idx.value = nxt;
    await refreshRows();
  } catch (e) {
    err.value = String(e.message || e);
  } finally {
    busy.value = false;
  }
}

function step(delta) {
  if (!total.value) return;
  idx.value = Math.min(Math.max(idx.value + delta, 0), total.value - 1);
}

function jumpToUndecided() {
  const nxt = nextUndecided(0);
  if (nxt >= 0) idx.value = nxt;
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

function chipClass(it, i) {
  return {
    on: i === idx.value,
    ok: it.accepted === true,
    bad: it.accepted === false,
  };
}

onMounted(load);
</script>

<template>
  <div class="card">
    <div class="card-head">
      <h3>阶段 5 改稿裁定</h3>
      <span v-if="pending.length" class="pill warn">{{ pending.length }} 章待定</span>
      <span class="spacer"></span>
      <button class="mini" :disabled="busy || !pending.length" @click="applyAll">合成全部已定稿</button>
    </div>

    <p class="meta">
      模型改完的稿子不会直接盖掉你的正文：先列在这里，你一处一处看，点“采用”才合成。
      没定完，流水线不会往下走。
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
            :title="current.can_apply ? '' : '还有没定的处'"
            @click="applyChapter"
          >
            合成本章
          </button>
        </div>

        <div v-if="!total" class="meta">模型没有改动这一章，合成结果与原文一致。</div>

        <template v-else>
          <!-- 步进条：一次只看一处，上一处 / 下一处 + 计数 -->
          <div class="step-bar">
            <button class="mini" :disabled="busy || idx <= 0" @click="step(-1)">‹ 上一处</button>
            <span class="counter">第 {{ idx + 1 }} / {{ total }} 处</span>
            <button class="mini" :disabled="busy || idx >= total - 1" @click="step(1)">下一处 ›</button>
            <span class="spacer"></span>
            <span class="meta">已定 {{ decided }} / {{ total }}</span>
            <button v-if="remaining" class="mini" :disabled="busy" @click="jumpToUndecided">跳到没定的</button>
            <span v-else class="pill ok done-pill">全部定完，可以合成</span>
          </div>

          <!-- 一处一格：颜色 + 勾/叉，点哪格看哪处（长章不必靠盲翻；不靠颜色也分得清） -->
          <div class="chips">
            <button
              v-for="(it, i) in items"
              :key="it.id"
              class="chip"
              :class="chipClass(it, i)"
              :title="`第 ${it.id} 处 · ${kindLabel(it.kind)}`"
              @click="idx = i"
            >
              <span v-if="it.accepted === true" class="chip-mark">✓</span>
              <span v-else-if="it.accepted === false" class="chip-mark">✕</span>{{ it.id }}
            </button>
          </div>

          <div v-if="item" class="item">
            <div class="item-head">
              <span class="tag">{{ kindLabel(item.kind) }}</span>
              <span class="meta">第 {{ item.id }} 处</span>
              <span class="spacer"></span>
              <span v-if="item.accepted === true" class="pill ok">采用改法</span>
              <span v-else-if="item.accepted === false" class="pill bad">保留原文</span>
              <span v-else class="pill warn">还没定</span>
            </div>
            <div v-if="item.old" class="old">
              <div class="blk-label">原文</div>
              <pre>{{ item.old }}</pre>
            </div>
            <div v-if="item.new" class="new">
              <div class="blk-label">改后</div>
              <pre>{{ item.new }}</pre>
            </div>
            <div class="actions">
              <button class="mini ok-btn" :disabled="busy" @click="decide(item, true)">采用模型改法</button>
              <button class="mini bad-btn" :disabled="busy" @click="decide(item, false)">保留原文</button>
              <span class="meta">定完这一处，自动跳到下一处没定的</span>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.split { display: grid; grid-template-columns: 190px 1fr; gap: 12px; }
.list { display: flex; flex-direction: column; gap: 4px; }
.row { display: flex; align-items: center; gap: 6px; justify-content: flex-start; text-align: left; }
.row.on { border-color: var(--accent); }
.detail { min-width: 0; }
.detail-head { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
.step-bar { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; flex-wrap: wrap; }
.counter {
  font-variant-numeric: tabular-nums; font-size: 13px; color: var(--ink);
  min-width: 86px; text-align: center;
}
.chips { display: flex; flex-wrap: wrap; gap: 3px; margin-bottom: 10px; }
.chip {
  min-width: 27px; padding: 2px 6px; font-size: 12px; border-radius: 4px; cursor: pointer;
  background: transparent; color: var(--muted); border: 1px solid var(--border);
  font-variant-numeric: tabular-nums;
}
.chip:hover { border-color: var(--accent); color: var(--ink-accent); }
.chip.ok {
  background: color-mix(in srgb, var(--ok) 20%, var(--field));
  color: var(--ink-ok); border-color: var(--ok);
}
.chip.bad {
  background: color-mix(in srgb, var(--bad) 20%, var(--field));
  color: var(--ink-bad); border-color: var(--bad);
}
.chip-mark { font-size: 10.5px; margin-right: 1px; }
/* 选中环画在格子外圈、与填色之间留一道底色，选中态不盖掉“采用/保留”的绿红 */
.chip.on { box-shadow: 0 0 0 1px var(--card), 0 0 0 3px var(--accent); }
.done-pill {
  font-weight: 600; color: var(--ink-ok);
  background: color-mix(in srgb, var(--ok) 28%, var(--field));
  border: 1px solid var(--ok);
}
.item { border: 1px solid var(--border); border-radius: 6px; padding: 8px; margin-bottom: 10px; }
.item-head { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
.tag { font-size: 12px; padding: 1px 6px; border-radius: 4px; background: var(--card); border: 1px solid var(--border); }
.blk-label { font-size: 11.5px; color: var(--muted); margin-bottom: 2px; }
.old { border-left: 3px solid var(--bad); padding: 4px 8px; margin-bottom: 4px; }
.new { border-left: 3px solid var(--ok); padding: 4px 8px; }
pre { white-space: pre-wrap; margin: 4px 0; font-family: inherit; }
.actions { display: flex; align-items: center; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
.err { color: var(--bad); margin: 6px 0; }
/* 采用/保留这两个按钮是这一屏的主要动作，用深一档的墨色，不用浅绿浅红（对比度不够） */
.ok-btn { color: var(--ink-ok); font-weight: 600; }
.bad-btn { color: var(--ink-bad); font-weight: 600; }
@media (max-width: 760px) { .split { grid-template-columns: 1fr; } }
</style>
