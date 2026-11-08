<script setup>
/**
 * 审阅台（2026-10-26）。
 *
 * 用户第 2 条：“第二阶段产物依旧令人崩溃，完全不是一个适合审阅、适合精修、
 * 适合讨论的形态。是整个前端的设计有问题。”
 * 第 10 条：“用户依旧不知道跑到哪一步，全过程也几乎没办法介入（我期待的方案
 * 可视化卡片没出现，全过程任何一步需要精修需要处理都找不到入口）。”
 *
 * ## 为什么是“三栏”
 *
 * 审阅一章需要三样东西同时在场，缺一个就要来回切页签：
 *
 *   左  章列表        —— 40 章时"下一章"不该靠回列表页再点
 *   中  正文（编辑器）—— 发现问题就地改；段落批注也在这里
 *   右  写作依据      —— 这一章的大纲（它本来该写成什么）+ 七维评审（它实际写成什么）
 *
 * 之前这三样分散在“章节 / 分章 / 审稿”三个页签，等于每次审阅都要重走一遍导航。
 *
 * ## 它不做什么
 *
 * 不做“自动修好”。右栏只摆事实（大纲要求、七维得分、未举证项），改不改、
 * 怎么改由用户决定 —— 中栏的编辑器与批注就是给他的动作。
 */
import { ref, computed, onMounted, onUnmounted, watch } from "vue";
import ChapterEditor from "./ChapterEditor.vue";

const props = defineProps({
  api: { type: Function, required: true },
  chapters: { type: Array, default: () => [] },
  quality: { type: Object, default: () => ({}) },
  // 从列表里某一章的“审阅”按钮进来时，中栏直接开这一章（2026-11-08）。
  initialChapter: { type: Number, default: 1 },
  // 透传给内联编辑器：self 引擎下段落重写不可用（按钮禁用 + 说明）
  selfEngine: { type: Boolean, default: false },
});
const emit = defineEmits(["say"]);

const n = ref(props.initialChapter > 0 ? props.initialChapter : 1);
const outline = ref({ loading: false, content: "", error: "" });
const ledger = ref({ loading: false, data: null, error: "" });
// 设定卡（2026-11-05，照 SP-1026 的工作台补）：右栏要能同时看到
// “这一章用到哪些设定卡”，否则改正文时还得切到设定页签去查人。
const setting = ref(null);
const appear = ref({});
// 切章前的未保存拦截（2026-11-05）：编辑器里改到一半点“下一章”，此前会直接
// 换章、改动无声消失（顶部只写了一行灰字提醒，等于没拦）。现在先问，再切。
const editorRef = ref(null);
const pendingSwitch = ref(null);

/* ---------- 栏宽（B7，2026-11-06）：让正文成为主视觉 ----------
 * 用户原话“正文不是主视觉”。先量后改（Temp/probe_b7.py 真渲染读数）：
 *   1680 视口 → 审阅台只有 1230 宽（外层 `.content` 卡在 1320），左 220 + 右 280 一夹，
 *   正文列 710；阅读/批注态再减编辑器侧栏 240 → 只剩 408px ≈ **27 字/行**。
 *   而 1920 视口下审阅台**还是** 1230 —— 视口再宽也没用，是外层上限，不是栏宽。
 *
 * 所以三处一起改（任何一处单独改都不够）：
 *   ① 审阅台打开时 `.content` 放宽到 1560（只影响这一视图，不动其它页面的栅格）；
 *   ② 左章列表 / 右依据栏都能收起，状态记 localStorage（下次还是你上次的布局）；
 *   ③ 窗口不够宽（审阅台 < 1220px，正文算下来不足 45 字/行）时**自动收右栏**，
 *      但不覆盖用户手动选择（手动展开过就尊重），且给出可点的解释。
 * 正文列自己有 46 汉字测量上限（ChapterEditor），免得宽屏拉成 70+ 字/行 —— 那是另一头的不适。
 */
const LEFT_KEY = "mofang_desk_left";
const RIGHT_KEY = "mofang_desk_right";
const leftOpen = ref(localStorage.getItem(LEFT_KEY) !== "0");
const rightOpen = ref(localStorage.getItem(RIGHT_KEY) !== "0");
const rightTouched = ref(localStorage.getItem(RIGHT_KEY) != null);
const deskEl = ref(null);
const deskW = ref(0);
const autoNarrow = computed(() => deskW.value > 0 && deskW.value < 1220);
// 用户手动动过 → 完全按他的选择；没动过 → 窄了就自动收右栏（宽度省下来给正文）。
// ⚠️ 这里别写成 `|| rightTouched`：那等于“动过就永远显示”，点收起没反应（2026-11-06 实测抓到）。
const rightShown = computed(() => (rightTouched.value ? rightOpen.value : !autoNarrow.value));
function toggleLeft() {
  leftOpen.value = !leftOpen.value;
  localStorage.setItem(LEFT_KEY, leftOpen.value ? "1" : "0");
}
function toggleRight() {
  // 先按“当前显示状态”取反，再改 touched —— 反了的话状态已经被 touched 改写，
  // 读到的 rightShown 是改后的值，点击会变成空操作（2026-11-06 实测踩到）。
  const next = !rightShown.value;
  rightTouched.value = true;
  rightOpen.value = next;
  localStorage.setItem(RIGHT_KEY, next ? "1" : "0");
}
let deskRO = null;
onUnmounted(() => { if (deskRO) deskRO.disconnect(); });

function requestChapter(x) {
  if (!x || x === n.value) return;
  if (editorRef.value && editorRef.value.dirty) {
    pendingSwitch.value = x;
    return;
  }
  n.value = x;
}
async function saveThenSwitch() {
  try { await editorRef.value?.save?.(); } catch (e) { /* 保存失败就不切，错误在编辑器里 */ return; }
  if (editorRef.value && editorRef.value.dirty) return;
  const t = pendingSwitch.value;
  pendingSwitch.value = null;
  if (t) n.value = t;
}
function discardThenSwitch() {
  const t = pendingSwitch.value;
  pendingSwitch.value = null;
  if (t) n.value = t;
}

async function loadSettingCards() {
  const r = await props.api("/setting/current");
  setting.value = (r.status === 200 && r.data && r.data.ok) ? (r.data.setting || null) : null;
  const a = await props.api("/setting/appearances");
  // ⚠️ 形状是 {ok, alias, generated_at, total_chapters, characters:{名字:{per_chapter}}, new_candidates}。
  // 第一版把**顶层**每个键当角色遍历（characters 是个 dict，没有 per_chapter）→ 恒为 0 个角色。
  // 角色表在 `.characters` 下面（2026-11-05 用户实测“本章出场 0 个角色”抓出）。
  if (a.status === 200 && a.data && a.data.ok) {
    const src = a.data.characters && typeof a.data.characters === "object"
      ? a.data.characters : {};
    const out = {};
    for (const [k, v] of Object.entries(src)) {
      if (!v || typeof v !== "object") continue;
      out[k] = v;
    }
    appear.value = out;
  } else {
    appear.value = {};
  }
}

// 本章出场的设定卡：appearances 的 per_chapter 里有这一章，就算出场。
const chapterCards = computed(() => {
  const per = {};
  for (const [name, st] of Object.entries(appear.value || {})) {
    const hits = (st && st.per_chapter && st.per_chapter[String(n.value)]) || 0;
    if (!hits) continue;
    const card = ((setting.value || {}).characters || [])
      .find((c) => c && (c.name === name || (Array.isArray(c.alias) && c.alias.includes(name))));
    per[name] = { name, hits, level: (st && st.level) || "background",
                  kind: (card && (card.type || card.kind)) || "人物",
                  alias: (card && Array.isArray(card.alias) && card.alias) || [] };
  }
  return Object.values(per).sort((a, b) => (b.hits || 0) - (a.hits || 0));
});

const chapterOptions = computed(() => props.chapters || []);

function qualityOf(no) {
  const q = props.quality ? props.quality[no] : null;
  return q == null ? null : q;
}
// 章状态点（照 SP-1026 的三色点）：绿=润色稿在、橙=只有检查稿、灰=只有原稿
function dotClass(c) {
  const f = c.files || {};
  if (f.refined) return "dot-done";
  if (f.checked) return "dot-warn";
  return "dot-todo";
}
function dotTitle(c) {
  const f = c.files || {};
  if (f.refined) return "润色稿已就绪";
  if (f.checked) return "只有检查稿，未润色";
  return "只有原稿";
}

function qualityClass(q) {
  if (q == null) return "st-pending";
  if (q >= 7) return "st-done";
  if (q >= 4) return "st-warn";
  return "st-failed";
}

async function loadOutline() {
  outline.value = { loading: true, content: "", error: "" };
  const r = await props.api("/outline/chapters/get?n=" + n.value);
  if (r.status === 200 && r.data && r.data.ok) {
    outline.value = { loading: false, content: r.data.content || "", error: "" };
  } else {
    outline.value = {
      loading: false, content: "",
      error: (r.data && r.data.error) || "这一章还没有大纲（它可能不在滚动规划窗口内，写到跟前才会补）",
    };
  }
}

/* 上下文包（2026-11-05，SP-1026）：这一章写作时到底注入了什么。
   数据来自 GET /chapters/context，与 stage4 用的是同一份 memory_policy 统计 ——
   面板不再自己算一遍“大概多少”。 */
const ctx = ref({ loading: false, data: null, error: "" });
const ctxOpen = ref(false);
async function loadContext() {
  ctx.value = { loading: true, data: null, error: "" };
  const r = await props.api("/chapters/context?n=" + n.value);
  if (r.status === 200 && r.data && r.data.ok) {
    ctx.value = { loading: false, data: r.data, error: "" };
  } else {
    ctx.value = { loading: false, data: null,
                  error: (r.data && r.data.error) || "读取这一章的资料清单失败" };
  }
}
const ctxSummary = computed(() => (ctx.value.data && ctx.value.data.summary) || null);
function dimText(by) {
  const L = { foreshadow: "伏笔", character: "角色", state: "状态", relation: "关系" };
  const parts = Object.entries(by || {}).map(([k, v]) => (L[k] || k) + (v || 0));
  return parts.length ? parts.join(" / ") : "无";
}

async function loadLedger() {
  ledger.value = { loading: true, data: null, error: "" };
  const r = await props.api("/review/ledger?chapter=" + n.value);
  if (r.status === 200 && r.data && r.data.ok) {
    // 单章查询返回 {ok, chapter, dims, aesthetic, complete, ...}；汇总查询是另一个形状。
    // 这里只认单章形状，拿不到就明说"未评"，不拿汇总数据冒充单章。
    const d = r.data;
    const single = d.chapter != null && (d.dims || d.aesthetic);
    ledger.value = single
      ? { loading: false, data: d, error: "" }
      : { loading: false, data: null, error: "这一章还没有七维评审（从七个角度做的检查，阶段 5 跑过才有）" };
  } else {
    ledger.value = {
      loading: false, data: null,
      error: (r.data && r.data.error) || "读取评审账本失败",
    };
  }
}

const scoredDims = computed(() => {
  const d = (ledger.value.data && ledger.value.data.dims) || {};
  return Object.entries(d).map(([k, v]) => ({
    code: k,
    label: (v && v.label) || k,
    score: v && typeof v.score === "number" ? v.score : null,
    note: (v && (v.reason || v.note)) || "",
    quote: (v && v.quote) || "",
  }));
});
const aestheticItems = computed(() => {
  const a = (ledger.value.data && ledger.value.data.aesthetic) || {};
  return Object.entries(a).map(([k, v]) => ({
    code: k,
    label: (v && v.label) || k,
    score: v && typeof v.score === "number" ? v.score : null,
    note: (v && (v.reason || v.note)) || "",
  }));
});

function go(delta) {
  const i = chapterOptions.value.findIndex((c) => c.n === n.value);
  const target = chapterOptions.value[i + delta];
  if (target) requestChapter(target.n);
}
const canPrev = computed(() => {
  const i = chapterOptions.value.findIndex((c) => c.n === n.value);
  return i > 0;
});
const canNext = computed(() => {
  const i = chapterOptions.value.findIndex((c) => c.n === n.value);
  return i >= 0 && i < chapterOptions.value.length - 1;
});

watch(n, () => { loadOutline(); loadLedger(); loadContext(); });

onMounted(() => {
  if (chapterOptions.value.length) n.value = chapterOptions.value[0].n;
  loadOutline();
  loadLedger();
  loadContext();
  loadSettingCards();
  // 量的是审阅台自身的宽度（不是视口）：内容区上限一变，这套判据自动跟着变，
  // 不需要再维护一份“多少 px 视口该收哪栏”的魔数表。
  if (typeof ResizeObserver !== "undefined" && deskEl.value) {
    deskRO = new ResizeObserver((ents) => { deskW.value = ents[0].contentRect.width; });
    deskRO.observe(deskEl.value);
  }
});
</script>

<template>
  <div class="desk" ref="deskEl">
    <!-- 左：章列表（可收起，见下方 `.desk-rail`） -->
    <aside v-if="leftOpen" class="desk-left">
      <div class="desk-left-head">
        <b>章节</b>
        <span class="meta">{{ chapterOptions.length }} 章</span>
      </div>
      <div class="desk-chap-list">
        <button v-for="c in chapterOptions" :key="c.n" class="desk-chap"
                :class="{ on: c.n === n }" @click="requestChapter(c.n)">
          <span class="desk-chap-dot" :class="dotClass(c)" :title="dotTitle(c)"></span>
          <span class="desk-chap-no">{{ c.n }}</span>
          <span class="desk-chap-name">第 {{ c.n }} 章</span>
          <span v-if="qualityOf(c.n) != null" class="pill" :class="qualityClass(qualityOf(c.n))">
            {{ qualityOf(c.n) }}
          </span>
        </button>
        <div v-if="!chapterOptions.length" class="meta desk-empty">
          还没有章节。先在“流水线”跑阶段 4，或者去“章节”点一下“重新探测”。
        </div>
      </div>
    </aside>

    <!-- 中：正文 + 批注（复用章节编辑器，内联形态） -->
    <section class="desk-mid">
      <template v-if="chapterOptions.length">
        <div class="desk-mid-head">
          <button class="mini desk-toggle" :title="leftOpen ? '收起章列表，正文更宽' : '展开章列表'"
                  @click="toggleLeft">{{ leftOpen ? "⇤ 章列表" : "⇥ 章列表" }}</button>
          <button class="mini" :disabled="!canPrev" @click="go(-1)">← 上一章</button>
          <b>第 {{ n }} 章</b>
          <button class="mini" :disabled="!canNext" @click="go(1)">下一章 →</button>
          <span class="spacer"></span>
          <span v-if="autoNarrow && !rightShown" class="meta">窗口较窄，依据栏已自动收起</span>
          <span class="meta">改完记得保存（Ctrl+S）；不保存就切章会丢</span>
          <button class="mini desk-toggle" :title="rightShown ? '收起写作依据栏，正文更宽' : '展开写作依据栏（大纲 / 出场 / 七维）'"
                  @click="toggleRight">{{ rightShown ? "依据栏 ⇥" : "⇤ 依据栏" }}</button>
        </div>
        <div class="desk-mid-body">
          <!-- 未保存拦截条：切章/翻章前出现，三个按钮就是全部出口 -->
          <div v-if="pendingSwitch != null" class="desk-unsaved">
            <b>这一章有未保存的改动</b>
            <span class="meta">切到第 {{ pendingSwitch }} 章前先决定怎么处理</span>
            <span class="spacer"></span>
            <button class="mini" @click="pendingSwitch = null">留在这章</button>
            <button class="mini danger" @click="discardThenSwitch">丢弃改动并切换</button>
            <button class="mini primary" @click="saveThenSwitch">保存并切换</button>
          </div>
          <ChapterEditor ref="editorRef" :api="api" :n="n" :self-engine="selfEngine" embedded @say="(m) => emit('say', m)" />
        </div>
      </template>
      <!-- 没有章节时不渲染编辑器：否则它会对不存在的章反复报 404，
           让人以为编辑器坏了（实际是这本还没写到那一步）。 -->
      <div v-else class="desk-empty-mid">
        <b>还没有可以审阅的章节</b>
        <div class="meta">
          先在“流水线”跑阶段 4（逐章写作），或者到“章节 → 列表”用“导入 Word 稿”
          把已有稿件当作已写好的章节导进来。
        </div>
        <div class="meta">
          导好后回到这里，左栏会出现章列表，中栏可以直接改正文、按段落提批注。
        </div>
      </div>
    </section>

    <!-- 右：写作依据（大纲 + 七维评审）。可收起：窗口不宽时它会把正文挤到 27 字/行。 -->
    <aside v-if="rightShown" class="desk-right">
      <div class="desk-block">
        <div class="desk-block-title">
          本章出场
          <span class="meta">{{ chapterCards.length }} 个角色</span>
        </div>
        <div v-if="!chapterCards.length" class="meta desk-dims-note">
          这一章还没有出场统计（<code>appearances.json</code> 由阶段 4 每章完成后更新）。
        </div>
        <div v-else class="desk-cards">
          <div v-for="c in chapterCards" :key="c.name" class="desk-card">
            <span class="desk-card-dot" :class="'lv-' + c.level"></span>
            <span class="desk-card-name">{{ c.name }}</span>
            <span class="desk-card-meta">{{ c.kind }} · 出场 {{ c.hits }} 次</span>
          </div>
        </div>
      </div>

      <div class="desk-block">
        <div class="desk-block-title">
          这一章带了哪些资料
          <span class="meta" v-if="ctxSummary">用到 {{ ctxSummary.recalled }} 条 · 未回收伏笔 {{ ctxSummary.open_foreshadows }}</span>
        </div>
        <div v-if="ctx.loading" class="meta">读取中…</div>
        <div v-else-if="!ctx.data" class="meta">{{ ctx.error }}</div>
        <template v-else-if="ctxSummary">
          <div class="desk-ctx-grid">
            <div class="desk-ctx-item">
              <b>{{ ctxSummary.recalled }}</b><span>用到的设定卡</span>
              <em>{{ dimText(ctxSummary.by_dim) }}</em>
            </div>
            <div class="desk-ctx-item">
              <b>{{ ctxSummary.open_foreshadows }}</b><span>未回收伏笔</span>
              <em v-if="ctxSummary.stale_foreshadows">{{ ctxSummary.stale_foreshadows }} 条过期没回收</em>
              <em v-else>没有过期的</em>
            </div>
            <div class="desk-ctx-item">
              <b>{{ ctxSummary.chars }}</b><span>上下文字数</span>
              <em v-if="ctxSummary.budget">
                前情摘要 {{ ctxSummary.allocated ? ctxSummary.allocated.rolling : 0 }}/{{ ctxSummary.budget.rolling_chars }} 字
                · 检索 {{ ctxSummary.allocated ? ctxSummary.allocated.recall : 0 }}/{{ ctxSummary.budget.recall_chars }} 字
              </em>
            </div>
          </div>
          <div v-if="ctxSummary.candidates" class="meta desk-dims-note">
            找到 {{ ctxSummary.candidates }} 条，超出字数上限没放进来 {{ ctxSummary.dropped }} 条；
            前情摘要用了 {{ ctxSummary.rolling_chapters }} 章<template v-if="ctxSummary.rolling_omitted">（另有 {{ ctxSummary.rolling_omitted }} 章被裁）</template>。
          </div>
          <button class="mini" @click="ctxOpen = !ctxOpen">
            {{ ctxOpen ? "收起明细" : "看注入了哪几条" }}
          </button>
          <div v-if="ctxOpen" class="desk-ctx-list">
            <div v-for="(c, i) in ctx.data.recalled" :key="i" class="desk-ctx-row">
              <span class="pill">{{ { foreshadow: '伏笔', character: '角色', state: '状态', relation: '关系' }[c.dim] || c.dim }}</span>
              <span class="desk-ctx-text">{{ c.text }}</span>
              <span class="meta">{{ c.why }}</span>
            </div>
            <div v-if="!ctx.data.recalled || !ctx.data.recalled.length" class="meta">
              这一章没有检索到相关历史 —— 不是漏了：找到的内容都和本章大纲关系不够近。
            </div>
          </div>
          <div v-if="ctx.data.hint" class="meta desk-dims-note">{{ ctx.data.hint }}</div>
        </template>
      </div>

      <div class="desk-block">
        <div class="desk-block-title">这一章本来要写成什么</div>
        <div v-if="outline.loading" class="meta">读取中…</div>
        <div v-else-if="outline.content" class="desk-outline">{{ outline.content }}</div>
        <div v-else class="meta">{{ outline.error }}</div>
      </div>

      <div class="desk-block">
        <div class="desk-block-title">
          七维评审
          <span v-if="ledger.data && ledger.data.complete === false" class="pill st-warn">不完整</span>
        </div>
        <div v-if="ledger.loading" class="meta">读取中…</div>
        <template v-else-if="ledger.data">
          <div class="desk-dims">
            <div v-for="d in scoredDims" :key="d.code" class="desk-dim">
              <span class="desk-dim-label">{{ d.label }}</span>
              <span v-if="d.score != null" class="desk-dim-score" :class="qualityClass(d.score)">{{ d.score }}</span>
              <span v-else class="desk-dim-score na">未举证</span>
            </div>
          </div>
          <div v-if="aestheticItems.length" class="desk-dims desk-dims-aes">
            <div v-for="d in aestheticItems" :key="d.code" class="desk-dim">
              <span class="desk-dim-label">{{ d.label }}</span>
              <span v-if="d.score != null" class="desk-dim-score" :class="qualityClass(d.score)">{{ d.score }}</span>
              <span v-else class="desk-dim-score na">未举证</span>
            </div>
          </div>
          <div class="meta desk-dims-note">
            “未举证”= 模型给的引句在本章原文里找不到，这一维**不计分**（不是扣分）。
          </div>
        </template>
        <div v-else class="meta">{{ ledger.error }}</div>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.desk { display: flex; gap: 10px; height: calc(100vh - 210px); min-height: 420px; font-size: 13px; }
.desk-left { flex: none; width: 220px; display: flex; flex-direction: column; min-height: 0; }
.desk-left-head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px; }
.desk-chap-list { flex: 1; overflow: auto; border: 1px solid var(--border); border-radius: 8px; }
.desk-chap {
  display: flex; align-items: center; gap: 8px; width: 100%;
  padding: 6px 10px; text-align: left; cursor: pointer;
  background: transparent; border: 0; border-bottom: 1px solid var(--border);
  color: var(--ink); font-size: 13px;
}
.desk-chap:hover { background: rgba(107, 138, 253, 0.08); }
.desk-chap.on { background: rgba(107, 138, 253, 0.16); font-weight: 600; }
.desk-chap-no { flex: none; width: 22px; text-align: right; color: var(--muted, #888); font-size: 11px; }
/* 章状态点（照 SP-1026 的三色点）：一眼看出这章写到哪一层 */
.desk-chap-dot { flex: none; width: 7px; height: 7px; border-radius: 50%; background: var(--border); }
.desk-chap-dot.dot-done { background: var(--ok, #2e8b57); }
.desk-chap-dot.dot-warn { background: var(--warn, #b8860b); }
.desk-chap-dot.dot-todo { background: var(--border); }
/* 本章出场卡：名 + 类型 + 本章次数 */
.desk-cards { display: flex; flex-direction: column; gap: 4px; }
.desk-card { display: flex; align-items: baseline; gap: 8px; font-size: 12.5px; }
.desk-card-dot { flex: none; width: 6px; height: 6px; border-radius: 50%; background: var(--muted, #888); }
.desk-card-dot.lv-major { background: var(--accent); }
.desk-card-dot.lv-minor { background: var(--warn, #b8860b); }
.desk-card-name { flex: 1; }
.desk-card-meta { flex: none; color: var(--muted, #888); font-size: 11.5px; }
.desk-chap-name { flex: 1; }
.desk-empty { padding: 10px; line-height: 1.7; }

.desk-mid { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.desk-unsaved {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  margin-bottom: 6px; padding: 6px 10px;
  border: 1px solid var(--warn, #b8860b); border-radius: 8px;
  background: rgba(184, 134, 11, 0.08); font-size: 12.5px;
}
.desk-mid-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }
.desk-toggle { flex: none; }
.desk-mid-body { flex: 1; min-height: 0; display: flex; }
.desk-empty-mid {
  margin: auto; max-width: 420px; padding: 18px 20px; line-height: 1.9;
  border: 1px dashed var(--border); border-radius: 10px;
}

.desk-right { flex: none; width: 280px; overflow: auto; display: flex; flex-direction: column; gap: 10px; }
.desk-block { padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; }
.desk-block-title { font-weight: 600; margin-bottom: 6px; display: flex; align-items: center; gap: 6px; }
.desk-outline {
  max-height: 300px; overflow: auto; font-size: 12px; line-height: 1.75;
  white-space: pre-wrap; word-break: break-word;
}
.desk-dims { display: flex; flex-direction: column; gap: 4px; }
.desk-dims-aes { margin-top: 8px; padding-top: 8px; border-top: 1px dashed var(--border); }
.desk-dim { display: flex; align-items: baseline; gap: 8px; font-size: 12px; }
.desk-dim-label { flex: 1; }
.desk-dim-score { flex: none; width: 34px; text-align: right; font-variant-numeric: tabular-nums; }
.desk-dim-score.na { width: 56px; color: var(--muted, #888); }
.desk-dims-note { margin-top: 8px; line-height: 1.6; }
/* 上下文包（2026-11-05）：三个读数一行摆开，明细默认收起 —— 右栏本来就窄，
   一上来堆十几条召回会把它撑成第二页正文。 */
.desk-ctx-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
.desk-ctx-item {
  min-width: 0; padding: 6px 8px; border-radius: 7px;
  background: rgba(155, 143, 196, 0.08); border: 1px solid var(--border);
  display: flex; flex-direction: column; gap: 1px;
}
.desk-ctx-item b { font-size: 16px; line-height: 1.2; }
.desk-ctx-item span { font-size: 11px; color: var(--muted); }
.desk-ctx-item em { font-style: normal; font-size: 10.5px; color: var(--muted); overflow-wrap: anywhere; }
.desk-ctx-list { margin-top: 6px; display: flex; flex-direction: column; gap: 5px; }
.desk-ctx-row { font-size: 12px; line-height: 1.6; }
.desk-ctx-row .pill { margin-right: 5px; }
.desk-ctx-text { overflow-wrap: anywhere; }
.st-done { color: var(--ok, #2e8b57); }
.st-warn { color: var(--warn, #b8860b); }
.st-failed { color: var(--bad); }
.st-pending { color: var(--muted, #888); }
</style>
