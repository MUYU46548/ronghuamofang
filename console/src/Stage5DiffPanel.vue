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
import { ref, computed, onMounted, nextTick } from "vue";
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
/**
 * 待裁定的项 = 去掉“只改了标点/空白”的项（P1-2，2026-11-15）。
 * 那些项不单独占一格：它们挂在最近的一处实质改动上，那一处怎么定它们就怎么走。
 * 后端把挂靠关系写在 `folded_into` / `folds` 里，这里只负责不显示它们。
 */
const live = computed(() => items.value.filter((it) => !it.folded_into));
const total = computed(() => live.value.length);
const item = computed(() => live.value[idx.value] || null);
const decided = computed(() => live.value.filter((it) => it.accepted != null).length);
const remaining = computed(() => total.value - decided.value);
/** 当前这一处顺带改了哪几处标点/空白（展开给人看，不藏）。 */
const foldedHere = computed(() =>
  item.value ? items.value.filter((it) => it.folded_into === item.value.id) : []
);
const foldedAll = computed(() => items.value.length - live.value.length);

/* ---------- 阅读态（P2-1，2026-11-15） ----------
   为什么要它：原来只有“这一段的原文 / 改后”两个黑框，作者读不到改动在整页里的位置，
   也就谈不上“不读 diff、在成书的版面上改”。现在两稿各排一遍、改过的段标出来，
   点段落能定位到那一处，点“在版面里看”能把当前这一处滚到眼前。
   版面的数据来自后端（同一份 blocks 排的），这里只负责显示与定位 —— 不自己再算 diff。 */
const view = ref("step");                 // step = 逐处看 · page = 整页看（版面）
const layout = computed(() => (current.value && current.value.layout) || null);
const byId = computed(() => {
  const m = {};
  for (const it of items.value) m[it.id] = it;
  return m;
});

/** 并入的标点项，它的“这一处”其实是挂靠的那一处。 */
function hostId(id) {
  const seen = new Set();
  let cur = byId.value[id];
  while (cur && cur.folded_into && !seen.has(cur.id)) {
    seen.add(cur.id);
    cur = byId.value[cur.folded_into];
  }
  return cur ? cur.id : id;
}

/** 某一段落属于“第几处”（并入项折到挂靠项上；不是改动的段返回 -1）。 */
function liveIndexFor(layoutItem) {
  if (layoutItem == null) return -1;
  const hid = hostId(layoutItem);
  return live.value.findIndex((it) => it.id === hid);
}

/** 点版面上的一段：把逐处视图也切到那一处（两个视图同一份状态）。 */
async function pickFromPage(layoutItem) {
  const i = liveIndexFor(layoutItem);
  if (i < 0) return;
  idx.value = i;
  await nextTick();
  scrollCurrent();
}

function scrollCurrent() {
  const el = document.querySelector('.detail .s5p.is-cur');
  if (el && el.scrollIntoView) el.scrollIntoView({ block: "center", behavior: "smooth" });
}

/** 逐处 → 版面：切过去并把当前这一处滚到眼前。 */
async function showInPage() {
  view.value = "page";
  await nextTick();
  scrollCurrent();
}

/** 某一段落挂靠的那一项定成什么了（true=采用 / false=保留 / null=还没定）。 */
function hostAccepted(layoutItem) {
  if (layoutItem == null) return null;
  const host = byId.value[hostId(layoutItem)];
  return host ? host.accepted : null;
}

function pageClass(entry) {
  const i = liveIndexFor(entry.item);
  const host = entry.item == null ? null : byId.value[hostId(entry.item)];
  return {
    chg: entry.item != null,
    folded: Boolean(host && host.folded_into),
    "is-cur": i >= 0 && i === idx.value,
    ok: Boolean(host && host.accepted === true),
    bad: Boolean(host && host.accepted === false),
  };
}

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
  const n = live.value.length;
  idx.value = !n ? 0 : Math.min(Math.max(idx.value, 0), n - 1);
}

/** 从 from 开始找下一处还没定的；没有就返回 -1。 */
function nextUndecided(from) {
  const list = live.value;
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
    // 只往后找下一处没定的（往前翻回去改过的处不抢焦点）。
    // 版面模式下**不跳**：定了这一段就停在它身上，跳走会让“当前这一处”的高亮
    // 和刚点的那一段对不上（逐处模式才需要连续点）。
    const nxt = view.value === "step" ? nextUndecided(at + 1) : -1;
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
    // next 是后端给的“下一步该干嘛”（全部合成后阶段 5 才会被标完成）——
    // 原来只说“已合成”，用户不知道还差什么、书为什么不动。
    say(d.next || d.message || "已合成");
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
    say(d.next || (`已合成 ${d.applied} 章`));
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
      <div class="list stack">
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
          <span class="view-toggle">
            <button class="mini" :class="{ on: view === 'step' }" @click="view = 'step'">逐处看</button>
            <button class="mini" :class="{ on: view === 'page' }" @click="showInPage">整页看（版面）</button>
          </span>
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
          <p v-if="foldedAll" class="meta folded-note">
            另有 {{ foldedAll }} 处只是标点/空白的调整，已并入最近一处的决定（不单独占一格）。
          </p>
          <template v-if="view === 'step'">
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
              v-for="(it, i) in live"
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
              <button class="mini" @click="showInPage">在版面里看这一处</button>
              <span class="meta">定完这一处，自动跳到下一处没定的</span>
            </div>
            <details v-if="foldedHere.length" class="folds">
              <summary>
                这一处顺带改了 {{ foldedHere.length }} 处标点/空白（跟着这一处走，不单独占一格）
              </summary>
              <div v-for="f in foldedHere" :key="f.id" class="fold">
                <div v-if="f.old" class="blk-label">附：这一处的原文</div>
                <pre v-if="f.old">{{ f.old }}</pre>
                <div v-if="f.new" class="blk-label">附：这一处的改后</div>
                <pre v-if="f.new">{{ f.new }}</pre>
              </div>
            </details>
          </div>
          </template>

          <!-- 整页看（版面）：两稿各排一遍，改动的段落标出来，点段落定位到那一处 -->
          <div v-else class="page-view">
            <p v-if="!layout" class="meta">读不到两稿（缺 raw 或暂存稿），版面排不出来。</p>
            <template v-else>
              <p class="meta page-note">
                左是原稿、右是模型的改后稿，按成书的版面排。带底色的是改动处，两栏同一处标同一个号；
                点改动的那一段就定位到那一处，鼠标移上去可以直接点“采用 / 保留”。
                两栏各自滚动、各按自己的段落顺序排 —— 新增/删除只在一边有，所以同一个号不一定在同一水平线上。
              </p>
              <div class="page-cols">
                <div class="page-col">
                  <div class="page-col-head">原稿</div>
                  <div
                    v-for="(e, i) in layout.raw"
                    :key="'r' + i"
                    class="s5p"
                    :class="pageClass(e)"
                    :data-item="e.item"
                    @click="pickFromPage(e.item)"
                  >
                    <span v-if="e.item != null" class="s5p-tag">第 {{ hostId(e.item) }} 处</span>
                    <span v-if="hostAccepted(e.item) === true" class="s5p-state ok">✓ 已采用</span>
                    <span v-else-if="hostAccepted(e.item) === false" class="s5p-state bad">✕ 保留原文</span>
                    <span class="s5p-text">{{ e.text }}</span>
                    <span v-if="e.item != null" class="s5p-act">
                      <button class="mini" :disabled="busy" @click.stop="decide(byId[hostId(e.item)], true)">采用</button>
                      <button class="mini" :disabled="busy" @click.stop="decide(byId[hostId(e.item)], false)">保留</button>
                    </span>
                  </div>
                </div>
                <div class="page-col">
                  <div class="page-col-head">改后</div>
                  <div
                    v-for="(e, i) in layout.proposed"
                    :key="'p' + i"
                    class="s5p"
                    :class="pageClass(e)"
                    :data-item="e.item"
                    @click="pickFromPage(e.item)"
                  >
                    <span v-if="e.item != null" class="s5p-tag">第 {{ hostId(e.item) }} 处</span>
                    <span v-if="hostAccepted(e.item) === true" class="s5p-state ok">✓ 已采用</span>
                    <span v-else-if="hostAccepted(e.item) === false" class="s5p-state bad">✕ 保留原文</span>
                    <span class="s5p-text">{{ e.text }}</span>
                    <span v-if="e.item != null" class="s5p-act">
                      <button class="mini" :disabled="busy" @click.stop="decide(byId[hostId(e.item)], true)">采用</button>
                      <button class="mini" :disabled="busy" @click.stop="decide(byId[hostId(e.item)], false)">保留</button>
                    </span>
                  </div>
                </div>
              </div>
            </template>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.split { display: grid; grid-template-columns: 190px 1fr; gap: 12px; }
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
  min-width: 27px; padding: 2px 6px; font-size: 13px; border-radius: 4px; cursor: pointer;
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
.chip-mark { font-size: 11px; margin-right: 1px; }
/* 选中环画在格子外圈、与填色之间留一道底色，选中态不盖掉“采用/保留”的绿红 */
.chip.on { box-shadow: 0 0 0 1px var(--card), 0 0 0 3px var(--accent); }
.done-pill {
  font-weight: 600; color: var(--ink-ok);
  background: color-mix(in srgb, var(--ok) 28%, var(--field));
  border: 1px solid var(--ok);
}
.item { border: 1px solid var(--border); border-radius: 6px; padding: 8px; margin-bottom: 10px; }
.item-head { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
.tag { font-size: 13px; padding: 1px 6px; border-radius: 4px; background: var(--card); border: 1px solid var(--border); }
.blk-label { font-size: 12px; color: var(--muted); margin-bottom: 2px; }
.old { border-left: 3px solid var(--bad); padding: 4px 8px; margin-bottom: 4px; }
.new { border-left: 3px solid var(--ok); padding: 4px 8px; }
pre { white-space: pre-wrap; margin: 4px 0; font-family: inherit; }
.actions { display: flex; align-items: center; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
.err { color: var(--bad); margin: 6px 0; }
.folded-note { margin: 0 0 6px; }
.folds { margin-top: 8px; border-top: 1px dashed var(--border); padding-top: 6px; }
.folds summary { cursor: pointer; font-size: 13px; color: var(--muted); }
.folds summary:hover { color: var(--ink-accent); }
.fold { margin: 6px 0 0; padding: 4px 8px; border-left: 2px solid var(--border);
        border-radius: 4px; background: color-mix(in srgb, var(--border) 16%, transparent); }
.fold pre { font-size: 13px; color: var(--muted); }
/* 采用/保留这两个按钮是这一屏的主要动作，用深一档的墨色，不用浅绿浅红（对比度不够） */
.ok-btn { color: var(--ink-ok); font-weight: 600; }
.bad-btn { color: var(--ink-bad); font-weight: 600; }
/* 视图开关：逐处看（默认）/ 整页看（版面） */
.view-toggle { display: flex; gap: 4px; }
.view-toggle .on { border-color: var(--accent); color: var(--ink-accent); }
/* 整页看（P2-1）：按成书版面排两稿，改动段带底色 + 编号，段落上直接能定 */
.page-note { margin: 0 0 8px; }
.page-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.page-col {
  min-width: 0; max-height: 480px; overflow: auto;
  border: 1px solid var(--border); border-radius: 6px;
  padding: 8px 10px; background: var(--field-inset, var(--card));
}
.page-col-head {
  position: sticky; top: 0; z-index: 1; font-size: 13px; color: var(--muted);
  padding: 0 0 6px; background: inherit;
}
/* 正文用衬线（与阅读态同一套），缩进两字 —— 看上去是“稿子”，不是 diff */
.s5p {
  font-family: "Source Han Serif SC", "Noto Serif SC", "Songti SC", serif;
  font-size: 14.5px; line-height: 1.9; text-indent: 2em;
  margin: 0 0 0.6em; padding: 2px 4px; border-radius: 4px;
}
.s5p.chg {
  cursor: pointer; text-indent: 0; padding-left: 6px;
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  border-left: 3px solid var(--accent);
}
.s5p.chg:hover { background: color-mix(in srgb, var(--accent) 20%, transparent); }
.s5p.folded {
  background: color-mix(in srgb, var(--warn) 12%, transparent);
  border-left-color: var(--warn);
}
.s5p.ok { background: color-mix(in srgb, var(--ok) 15%, transparent); border-left-color: var(--ok); }
.s5p.bad { background: color-mix(in srgb, var(--bad) 13%, transparent); border-left-color: var(--bad); }
.s5p.is-cur { box-shadow: 0 0 0 1px var(--card), 0 0 0 3px var(--accent); }
.s5p-tag { font-size: 12px; color: var(--ink-accent); margin-right: 6px; }
/* 已定状态不能只靠底色区分（目检 2026-11-15 提的）：给一个字的结论 */
.s5p-state { font-size: 12px; padding: 0 5px; border-radius: 4px; margin-right: 6px; font-weight: 600; }
.s5p-state.ok { color: var(--ink-ok); background: color-mix(in srgb, var(--ok) 24%, var(--field)); }
.s5p-state.bad { color: var(--ink-bad); background: color-mix(in srgb, var(--bad) 22%, var(--field)); }
/* 段落上的两个按钮默认不露脸：整页模式下满屏按钮会把“读稿”变成“点按钮”。
   悬停或当前这一处时才出现（键盘仍可 Tab 到）。 */
.s5p-act { display: inline-flex; gap: 4px; margin-left: 6px; opacity: 0; transition: opacity .12s; }
.s5p.chg:hover .s5p-act,
.s5p.is-cur .s5p-act,
.s5p-act:focus-within { opacity: 1; }
@media (max-width: 900px) { .page-cols { grid-template-columns: 1fr; } }
@media (max-width: 760px) { .split { grid-template-columns: 1fr; } }
</style>
