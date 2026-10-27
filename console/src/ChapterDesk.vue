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
import { ref, computed, onMounted, watch } from "vue";
import ChapterEditor from "./ChapterEditor.vue";

const props = defineProps({
  api: { type: Function, required: true },
  chapters: { type: Array, default: () => [] },
  quality: { type: Object, default: () => ({}) },
});
const emit = defineEmits(["say"]);

const n = ref(1);
const outline = ref({ loading: false, content: "", error: "" });
const ledger = ref({ loading: false, data: null, error: "" });

const chapterOptions = computed(() => props.chapters || []);

function qualityOf(no) {
  const q = props.quality ? props.quality[no] : null;
  return q == null ? null : q;
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
      : { loading: false, data: null, error: "这一章还没有七维评审（阶段 5 跑过才有）" };
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
  if (target) n.value = target.n;
}
const canPrev = computed(() => {
  const i = chapterOptions.value.findIndex((c) => c.n === n.value);
  return i > 0;
});
const canNext = computed(() => {
  const i = chapterOptions.value.findIndex((c) => c.n === n.value);
  return i >= 0 && i < chapterOptions.value.length - 1;
});

watch(n, () => { loadOutline(); loadLedger(); });

onMounted(() => {
  if (chapterOptions.value.length) n.value = chapterOptions.value[0].n;
  loadOutline();
  loadLedger();
});
</script>

<template>
  <div class="desk">
    <!-- 左：章列表 -->
    <aside class="desk-left">
      <div class="desk-left-head">
        <b>章节</b>
        <span class="meta">{{ chapterOptions.length }} 章</span>
      </div>
      <div class="desk-chap-list">
        <button v-for="c in chapterOptions" :key="c.n" class="desk-chap"
                :class="{ on: c.n === n }" @click="n = c.n">
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
          <button class="mini" :disabled="!canPrev" @click="go(-1)">← 上一章</button>
          <b>第 {{ n }} 章</b>
          <button class="mini" :disabled="!canNext" @click="go(1)">下一章 →</button>
          <span class="spacer"></span>
          <span class="meta">改完记得保存（Ctrl+S）；不保存就切章会丢</span>
        </div>
        <div class="desk-mid-body">
          <ChapterEditor :api="api" :n="n" embedded @say="(m) => emit('say', m)" />
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

    <!-- 右：写作依据（大纲 + 七维评审） -->
    <aside class="desk-right">
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
.desk { display: flex; gap: 12px; height: calc(100vh - 210px); min-height: 420px; }
.desk-left { flex: none; width: 176px; display: flex; flex-direction: column; min-height: 0; }
.desk-left-head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 6px; }
.desk-chap-list { flex: 1; overflow: auto; border: 1px solid var(--border); border-radius: 8px; }
.desk-chap {
  display: flex; align-items: center; gap: 8px; width: 100%;
  padding: 6px 10px; text-align: left; cursor: pointer;
  background: transparent; border: 0; border-bottom: 1px solid var(--border);
  color: var(--foreground); font-size: 13px;
}
.desk-chap:hover { background: rgba(107, 138, 253, 0.08); }
.desk-chap.on { background: rgba(107, 138, 253, 0.16); font-weight: 600; }
.desk-chap-no { flex: none; width: 22px; text-align: right; color: var(--muted, #888); font-size: 11px; }
.desk-chap-name { flex: 1; }
.desk-empty { padding: 10px; line-height: 1.7; }

.desk-mid { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.desk-mid-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.desk-mid-body { flex: 1; min-height: 0; display: flex; }
.desk-empty-mid {
  margin: auto; max-width: 420px; padding: 18px 20px; line-height: 1.9;
  border: 1px dashed var(--border); border-radius: 10px;
}

.desk-right { flex: none; width: 288px; overflow: auto; display: flex; flex-direction: column; gap: 12px; }
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
.st-done { color: var(--ok, #2e8b57); }
.st-warn { color: var(--warn, #b8860b); }
.st-failed { color: var(--danger, #c0392b); }
.st-pending { color: var(--muted, #888); }
</style>
