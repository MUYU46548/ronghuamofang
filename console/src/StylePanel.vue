<script setup>
/**
 * 文风分析 + 章节节奏面板。
 *
 * 数据源：
 *   POST /style/analyze {source, path?, n?, scope?, compare?} → 特征 + 可选风格偏差
 *   GET  /book/pacing?source=current&scope=  → 本书章节节奏
 *   POST /book/split {path}                  → 拆书（导入外部文本算节奏）
 *
 * 章节节奏区：纯 CSS 柱状图（每章字数）+ SVG 移动平均趋势线（默认 5 章窗口），
 * 二者共用 0-100 百分比坐标系，不引任何图表库。离群章（偏离均值 >40%）标红。
 *
 * 量化指标全部来自 utils/style_analyzer（确定性、零 LLM），
 * 与 stage4/6 注入写作的真实特征同源，故面板所见即管线所用。
 */
import { ref, computed, onMounted, watch, nextTick } from "vue";

import { API } from "./apiBase.js";
const emit = defineEmits(["say"]);

async function api(path, method = "GET", body = null) {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  if (body) opt.body = JSON.stringify(body);
  const r = await fetch(API + path, opt);
  let data = {};
  try { data = await r.json(); } catch (e) { /* empty */ }
  return { status: r.status, data };
}

function say(msg) { emit("say", msg); }

const sub = ref("style");           // style | pacing
const source = ref("reference");    // reference | path | chapter
const filePath = ref("");
const chapN = ref(1);
const chapScope = ref("");
const compareOn = ref(true);
const loading = ref(false);
const feat = ref(null);             // {ok, label, chars, features}
const drift = ref(null);
const err = ref("");

const pacing = ref(null);
const pacingLoading = ref(false);
const pacingSource = ref("current");

const f = computed(() => feat.value?.features || {});

/* ---------- 小工具 ---------- */
const pct = (v) => (Number(v || 0) * 100).toFixed(1) + "%";
const num = (v, d = 1) => (v === undefined || v === null ? "-" : Number(v).toFixed(d));

function barWidth(v, max) {
  const m = Math.max(max || 0.0001, 0.0001);
  return Math.max(2, Math.round((Math.abs(v || 0) / m) * 100));
}

const connectiveRows = computed(() => {
  const c = f.value.connectives || {};
  const counts = c.counts || {};
  const ratio = c.ratio || {};
  const max = Math.max(0.0001, ...Object.values(counts).map(Number));
  return Object.keys(counts).map((k) => ({
    name: k, count: counts[k], ratio: ratio[k],
    w: barWidth(counts[k], max), dominant: c.dominant === k,
  }));
});

const personRows = computed(() => {
  const p = f.value.person || {};
  const counts = p.counts || {};
  const max = Math.max(0.0001, ...Object.values(counts).map(Number));
  return Object.keys(counts).map((k) => ({
    name: { first: "第一人称", second: "第二人称", third: "第三人称" }[k] || k,
    count: counts[k], w: barWidth(counts[k], max), dominant: p.dominant === k,
  }));
});

const sentLen = computed(() => {
  const x = f.value;
  return {
    avg: x.avg_sentence_len, std: x.sentence_len_std, cv: x.sentence_len_cv,
    min: x.sentence_len_min, max: x.sentence_len_max,
    short: x.short_sentence_ratio, long: x.long_sentence_ratio,
    alternation: x.rhythm_alternation, burstiness: x.rhythm_burstiness,
    signature: x.rhythm_signature,
  };
});

const punctuationRows = computed(() => {
  const p = f.value.punctuation || {};
  return [
    ["每句逗号数", num(p.comma_per_sentence, 2)],
    ["分号密度（/千字）", num(p.semicolon_density, 2)],
    ["破折号密度（/千字）", num(p.dash_density, 2)],
    ["省略号密度（/千字）", num(p.ellipsis_density, 2)],
    ["顿号密度（/千字）", num(p.pause_mark_density, 2)],
    ["引号密度（/千字）", num(p.quote_density, 2)],
  ];
});

/* ---------- 范文正文（GUI 粘贴窗口，2026-10-19） ----------
 * 存的是工作区里的文件（默认 materials/style_sample.md，随书档归档），
 * 不是 config 里的字符串 —— 范文可达数万字，塞 yaml 会让读配置变慢且易被手改坏。
 * 字数口径由后端算（prose_char_count，与 stage4 判“是否只有占位符”同源）。
 */
const refState = ref(null);      // GET /config/style_reference
const refText = ref("");
const refDirty = ref(false);
const refSaving = ref(false);

async function loadReference() {
  const r = await api("/config/style_reference");
  if (r.data && r.data.ok) {
    refState.value = r.data;
    refText.value = r.data.text || "";
    refDirty.value = false;
  } else {
    refState.value = { ok: false, error: r.data?.error || ("HTTP " + r.status) };
  }
}

async function saveReference(clear = false) {
  if (clear && !window.confirm(
    "清空范文？\n\n· 写作阶段将不再注入范文\n· 旧内容已自动备份到 data/state/style_reference_history/")) return;
  refSaving.value = true;
  const r = await api("/config/style_reference", "POST", { text: clear ? "" : refText.value });
  refSaving.value = false;
  if (r.data?.ok) {
    refState.value = r.data;
    if (clear) { refText.value = ""; }
    refDirty.value = false;
    say(r.data.message || "已保存");
  } else {
    say(r.data?.error || ("保存失败 HTTP " + r.status));
  }
}

/* ---------- 文风分析 ---------- */
const note = ref("");

async function analyze(autoFallback = true) {
  err.value = "";
  const body = { source: source.value };
  if (source.value === "path") {
    if (!filePath.value.trim()) { say("请先选择或填写文件路径"); return; }
    body.path = filePath.value.trim();
  } else if (source.value === "chapter") {
    body.n = Number(chapN.value);
    if (chapScope.value) body.scope = chapScope.value;
    if (compareOn.value) body.compare = { n: Number(chapN.value), scope: chapScope.value || undefined };
  }
  loading.value = true;
  try {
    const r = await api("/style/analyze", "POST", body);
    // 未配置范文时不留在空面板：自动改用“项目章节”跑一次（依次试前 3 章）
    if (autoFallback && !r.data.ok && r.data.configured === false
        && source.value === "reference") {
      source.value = "chapter";
      for (const n of [1, 2, 3]) {
        chapN.value = n;
        note.value = "未配置 book.style_reference —— 已自动改用“第 " + n + " 章”做分析；"
          + "想分析范文请在 config/project.yaml 配置，或切到“指定文件”。";
        const r2 = await api("/style/analyze", "POST",
                             { source: "chapter", n, compare: compareOn.value ? { n } : undefined });
        if (r2.data.ok) {
          feat.value = r2.data;
          drift.value = r2.data.drift || null;
          say("未配置范文，已改为分析第 " + n + " 章");
          return;
        }
      }
      feat.value = null;
      err.value = r.data.hint || "未配置范文，且项目尚无章节产物可分析";
      say(err.value);
      return;
    }
    if (!r.data.ok) {
      feat.value = null;
      err.value = r.data.hint || r.data.error || ("HTTP " + r.status);
      say(err.value);
      return;
    }
    note.value = "";
    feat.value = r.data;
    drift.value = r.data.drift || null;
    if (!r.data.sufficient) say("文本过短，特征不可靠（需 ≥100 字）");
    else say("文风分析完成：" + (r.data.label || ""));
  } catch (e) {
    say("错误: " + (e.message || e));
  } finally {
    loading.value = false;
  }
}

async function pickFile() {
  if (!window.mofangAPI?.openFileDialog) { say("文件选择需通过 Electron 启动"); return; }
  const r = await window.mofangAPI.openFileDialog({
    title: "选择范文 / 参考文本", properties: ["openFile"],
  });
  if (r.ok && r.paths?.length) { filePath.value = r.paths[0]; source.value = "path"; }
  else if (!r.ok) say(r.error || "选择文件失败");
}

/* ---------- 章节节奏 ---------- */
async function loadPacing(sourceKind = pacingSource.value) {
  pacingLoading.value = true;
  const qs = sourceKind === "current" ? "?source=current" : "";
  const r = await api("/book/pacing" + qs);
  if (r.status === 200 && (r.data.chapters || []).length) {
    pacing.value = r.data;
    pacing.value.origin = sourceKind;
  } else {
    pacing.value = null;
    if (r.status === 404) say("尚无拆书结果 —— 可用“导入参考书”分析外部文本");
    else say("节奏数据读取失败: " + (r.data?.error || r.status));
  }
  pacingLoading.value = false;
}

async function importBook() {
  if (!window.mofangAPI?.openFileDialog) { say("文件选择需通过 Electron 启动"); return; }
  const r = await window.mofangAPI.openFileDialog({
    title: "选择要拆分的文本文件（.txt / .md）", properties: ["openFile"],
  });
  if (!r.ok || !r.paths?.length) { if (!r.ok) say(r.error || "选择文件失败"); return; }
  const path = r.paths[0];
  if (!window.confirm("确定拆书？\n\n文件：" + path
      + "\n· 仅读取该文件，不会修改它\n· 结果写入 data/state/book_pacing.json")) return;
  pacingLoading.value = true;
  const res = await api("/book/split", "POST", { path, emit: false });
  pacingLoading.value = false;
  if (res.status === 200 && res.data.ok) {
    say(res.data.message);
    pacingSource.value = "book";
    await loadPacing("book");
  } else {
    say("拆书失败: " + (res.data?.error || res.status));
  }
}

/* ---------- 同质化检测（P1：确定性、零 token、只报不拦） ----------
   数据源 GET /homogeneity（汇总）与 ?chapter=N（单章）。
   报告由 stage4/stage6 落盘时写出，这里**只读不重算** —— 重算会把
   "落盘那一刻"的结论换成"现在这版正文"的结论，诊断就失真了。 */
const homo = ref(null);
const homoPick = ref("");
const homoDetail = ref(null);
const homoBusy = ref(false);
const homoErr = ref("");

const HOMO_LABEL = {
  shingle_repeat: "复读",
  phrase_repeat: "短语反复",
  dialogue_same_voice: "对话同腔",
  sentence_monotony: "句式单调",
  metaphor_density: "比喻堆砌",
  sensory_lock: "感官锁死",
};

async function loadHomo() {
  homoBusy.value = true;
  homoErr.value = "";
  try {
    const r = await api("/homogeneity");
    if (r.status === 200 && r.data.ok) {
      homo.value = r.data;
      const first = (r.data.chapters || [])[0];
      const pick = homoPick.value && (r.data.chapters || []).some(
        (c) => String(c.chapter) === String(homoPick.value)) ? homoPick.value : null;
      if (pick) await pickHomo(pick);
      else if (first) await pickHomo(first.chapter);
      else homoDetail.value = null;
    } else {
      homo.value = null;
      homoErr.value = r.data?.error || ("读取失败：HTTP " + r.status);
    }
  } catch (e) {
    homoErr.value = String(e);
  }
  homoBusy.value = false;
}

async function pickHomo(chapter) {
  homoPick.value = chapter;
  homoDetail.value = null;
  const r = await api("/homogeneity?chapter=" + encodeURIComponent(chapter));
  if (r.status === 200 && r.data.ok) homoDetail.value = r.data.report;
  else homoErr.value = r.data?.error || ("读取失败：HTTP " + r.status);
}

function homoFlag(c) {
  if (c.error) return { text: "解析失败", cls: "bad" };
  if (!c.enabled) return { text: "样本不足", cls: "muted" };
  const n = (c.findings || []).length;
  return n ? { text: n + " 项待看", cls: "warn" } : { text: "无异常", cls: "ok" };
}

/* ---------- 参考作品拆解（P2，可选功能） ----------
   三条纪律落在界面上：
   1) 没同意声明不能拆（按钮直接禁用，后端也会拒）；
   2) 画像不含原文，片段候选默认一条都不勾；
   3) 只有勾选的片段才落盘，且会随写作任务发给模型服务商（弹层里已写明）。 */
const decConsent = ref(false);
const decDeclOpen = ref(false);
const decDeclEl = ref(null);
// 打开即把焦点放到“我不同意”——默认焦点必须是最保守的那个选项。
// 注意：模板里的 autofocus 对 v-if 动态插入的节点**不生效**，必须手动 focus，
// 否则焦点停在上一个“阅读并同意声明”按钮上，连按回车就直接同意了。
watch(decDeclOpen, async (v) => {
  if (!v) return;
  await nextTick();
  decDeclEl.value?.querySelector(".dec-decl-foot .mini")?.focus();
});
const decPath = ref("");
const decCands = ref([]);
const decPicked = ref({});
const decSaved = ref(null);
const decBusy = ref(false);
const decGuide = ref([]);

async function loadDecompose() {
  const [c, s] = await Promise.all([api("/decompose/consent"), api("/decompose/snippets")]);
  if (c.status === 200) decConsent.value = !!c.data.agreed;
  if (s.status === 200) decSaved.value = s.data.saved || null;
}
async function agreeDecompose() {
  const r = await api("/decompose/consent", "POST", { agreed: true });
  if (r.status === 200 && r.data.ok) {
    decConsent.value = true;
    decDeclOpen.value = false;
    say("已同意，可以开始拆解");
  } else {
    say("记录同意失败: " + (r.data.error || r.status));
  }
}
async function pickDecomposeFile() {
  if (!window.mofangAPI?.openFileDialog) { say("文件选择需通过 Electron 启动"); return; }
  const r = await window.mofangAPI.openFileDialog({
    title: "选择要拆解的作品（.txt / .md）", properties: ["openFile"],
  });
  if (r.ok && r.paths?.length) decPath.value = r.paths[0];
  else if (!r.ok) say(r.error || "选择文件失败");
}
async function runDecomposeProfile() {
  if (!decPath.value.trim()) { say("先选一个文件"); return; }
  decBusy.value = true;
  const r = await api("/decompose/profile", "POST", { path: decPath.value.trim() });
  decBusy.value = false;
  if (r.status !== 200 || !r.data.ok) { say("拆解失败: " + (r.data.error || r.status)); return; }
  decGuide.value = r.data.guidance || [];
  say(r.data.message || "画像已生成");
}
async function runCandidates() {
  if (!decPath.value.trim()) { say("先选一个文件"); return; }
  decBusy.value = true;
  const r = await api("/decompose/snippets/candidates", "POST", { path: decPath.value.trim() });
  decBusy.value = false;
  if (r.status !== 200 || !r.data.ok) { say("提取失败: " + (r.data.error || r.status)); return; }
  decCands.value = r.data.candidates || [];
  decPicked.value = {};                      // 默认一条都不选
  say("抽到 " + decCands.value.length + " 条候选（默认未勾选）");
}
function togglePick(id) {
  decPicked.value = { ...decPicked.value, [id]: !decPicked.value[id] };
}
const pickedCount = computed(() => Object.values(decPicked.value).filter(Boolean).length);
async function savePicked() {
  const picks = decCands.value
    .filter((c) => decPicked.value[c.id])
    .map((c) => ({ kind: c.kind, text: c.text }));
  if (!picks.length) { say("一条都没勾选"); return; }
  const name = decPath.value.split(/[\\/]/).pop() || "";
  const r = await api("/decompose/snippets/save", "POST", { picks, source: { name } });
  if (r.status !== 200 || !r.data.ok) { say("保存失败: " + (r.data.error || r.status)); return; }
  say(r.data.message || "已保存");
  decCands.value = [];
  decPicked.value = {};
  await loadDecompose();
}
async function clearSnippets() {
  if (!window.confirm("删除已保存的参考片段？写作将不再注入这些片段。")) return;
  const r = await api("/decompose/snippets/clear", "POST", {});
  if (r.status === 200) { say(r.data.message || "已清空"); await loadDecompose(); }
}

const DECL_LINES = [
  "本功能处理的是你本机上的文件：绒花墨坊不会上传你导入的作品，也不会保留其原文；",
  "拆解结果只保存统计数据（章长、对话占比、句长分布、钩子比例），不保存任何原句；",
  "如果你勾选“参考片段”，这些片段会随写作任务发给**你自己配置的大模型服务商**，",
  "　该次调用适用其隐私保留与训练声明；绒花墨坊不介入、不担保；",
  "你必须对导入作品拥有合法使用权，并自行承担由此产生的全部责任；",
  "不同意以上任一条款，请勿使用本功能。",
];

const pacingBars = computed(() => {
  const p = pacing.value;
  if (!p?.chapters?.length) return [];
  const max = Math.max(...p.chapters.map((c) => c.word_count || 1));
  const outNs = new Set((p.summary?.outliers || []).map((o) => o.index));
  return p.chapters.map((c) => ({
    n: c.index, title: c.title, words: c.word_count,
    h: Math.max(2, Math.round((c.word_count / max) * 100)),
    outlier: outNs.has(c.index), dlg: c.dialogue_ratio,
  }));
});

/**
 * 移动平均趋势线（默认 5 章窗口；章数不足则窗口 = 章数）。
 *
 * 纯 SVG polyline，viewBox 用 0-100 百分比坐标 + preserveAspectRatio="none"，
 * 因此与柱状图共用同一套坐标：
 *   x = 第 i 根柱子的中心（按 flex 布局 w=1、gap=0.14 反推）
 *   y = 100 - 柱高百分比（柱子高度与 MA 值用同一个 max 归一化）
 */
const MA_WINDOW = 5;
const pacingTrend = computed(() => {
  const list = pacingBars.value;
  if (list.length < 2) return { has: false, win: 0, points: "", last: null };
  const max = Math.max(...list.map((b) => b.words || 1), 1);
  const n = list.length;
  const win = Math.min(MA_WINDOW, n);
  const g = 0.14;                            // gap 2px / 典型柱宽 14px
  const W = n + (n - 1) * g;
  const cx = (i) => ((i * (1 + g) + 0.5) / W) * 100;
  const yOf = (v) => 100 - Math.max(2, Math.round(((v || 0) / max) * 100));
  const ma = list.map((_, i) => {
    const seg = list.slice(Math.max(0, i - win + 1), i + 1);
    return seg.reduce((a, b) => a + (b.words || 0), 0) / seg.length;
  });
  return {
    has: true, win, ma,
    last: Math.round(ma[ma.length - 1]),
    points: ma.map((v, i) => `${cx(i).toFixed(2)},${yOf(v).toFixed(2)}`).join(" "),
  };
});

onMounted(() => {
  loadReference();
  analyze();
  loadPacing("current");
  loadDecompose();
});
</script>

<template>
  <div class="style-panel">
    <div class="tabs-sub" style="margin-bottom: 12px;">
      <button :class="{ on: sub === 'style' }" @click="sub = 'style'">文风特征</button>
      <button :class="{ on: sub === 'pacing' }" @click="sub = 'pacing'">章节节奏</button>
      <button :class="{ on: sub === 'homo' }" @click="sub = 'homo'; loadHomo()">同质化检测</button>
    </div>

    <!-- ================= 文风特征 ================= -->
    <template v-if="sub === 'style'">
      <!-- 范文正文：一键粘贴。属于用户数据（工作区文件，随书档归档） -->
      <div class="card">
        <div class="card-head">
          <h3>范文正文</h3>
          <span class="spacer"></span>
          <button class="mini" :disabled="refSaving" @click="saveReference(false)">
            {{ refSaving ? "保存中…" : "保存" }}</button>
          <button class="mini" :disabled="refSaving || !refText"
                  @click="saveReference(true)">清空</button>
        </div>
        <div class="meta">
          写作时模仿它的节奏、用词与句式。<b>给 2~3 个场景切片</b>（对白 / 动作 / 内省各一）
          比给一整章更有效 —— 系统只抽 3 段做 few-shot。粘贴后点“保存”即可，无需改配置。
        </div>
        <textarea v-model="refText" class="style-ref-input" rows="12"
                  placeholder="在这里粘贴正文（Ctrl+V）…"
                  @input="refDirty = true"></textarea>
        <div class="meta ref-status" v-if="refState && refState.ok">
          <span class="ref-path">{{ refState.path }}</span>
          <span>· 有效正文 <b>{{ refState.prose_chars }}</b> 字（注入上限 {{ refState.limit }}）</span>
          <span v-if="!refState.effective" class="ref-warn">· 不足 {{ refState.min_prose }} 字，暂不生效</span>
          <span v-else class="ref-ok">· 已生效</span>
          <span v-if="refState.truncated" class="ref-warn">
            · 超出上限 {{ refState.truncated }} 字，stage4 只注入前 {{ refState.limit }} 字</span>
          <span v-if="refDirty" class="ref-warn">· 有未保存改动</span>
        </div>
        <div class="meta ref-warn" v-else-if="refState && !refState.ok">
          读取失败：{{ refState.error }}
        </div>
      </div>

      <!-- 参考作品拆解（可选功能）：画像不存原文，片段默认不勾 -->
      <div class="card">
        <div class="card-head">
          <h3>参考作品拆解</h3>
          <span class="spacer"></span>
          <span v-if="decConsent" class="meta">已同意声明</span>
          <span v-else class="ref-warn">未同意声明，功能锁定</span>
        </div>
        <div class="meta">
          可选功能：分析一部<b>你有权使用</b>的作品，提取结构与节奏，喂给写作阶段当数值约束。
          <b>不保存原文</b>；片段只有勾选才落盘。
        </div>
        <div v-if="!decConsent" class="art-row" style="margin-top:8px;">
          <button class="mini primary" @click="decDeclOpen = true">阅读并同意声明</button>
        </div>
        <template v-else>
          <div class="art-row" style="margin-top:8px;">
            <input v-model="decPath" class="text-input" placeholder="作品文件完整路径（.txt / .md）" />
            <button class="mini" @click="pickDecomposeFile">浏览…</button>
            <button class="mini" :disabled="decBusy" @click="runDecomposeProfile">拆解为画像</button>
            <button class="mini" :disabled="decBusy" @click="runCandidates">提取候选片段</button>
          </div>
          <div class="meta" v-if="decGuide.length" style="margin-top:8px;">
            <div v-for="(g, i) in decGuide" :key="i">· {{ g }}</div>
          </div>
          <div v-if="decCands.length" class="dec-cands">
            <div class="meta" style="margin:8px 0 6px;">
              候选 {{ decCands.length }} 条 · 已勾 <b>{{ pickedCount }}</b> 条
              —— <b>默认一条都不选</b>，勾了才会保存并随写作任务发给模型服务商
            </div>
            <label v-for="c in decCands" :key="c.id" class="dec-cand">
              <input type="checkbox" :checked="!!decPicked[c.id]" @change="togglePick(c.id)" />
              <span class="dec-kind" :class="'k-' + c.kind">{{ c.kind }}</span>
              <span class="dec-text">{{ c.text.slice(0, 90) }}{{ c.text.length > 90 ? "…" : "" }}</span>
              <span class="meta">{{ c.chars }} 字</span>
            </label>
            <div class="art-row" style="margin-top:8px;">
              <button class="mini primary" :disabled="!pickedCount" @click="savePicked">
                保存勾选的 {{ pickedCount }} 条</button>
              <button class="mini" @click="decCands = []; decPicked = {}">放弃候选</button>
            </div>
          </div>
          <div class="meta" v-if="decSaved && decSaved.snippets" style="margin-top:8px;">
            已保存 {{ decSaved.snippets.length }} 条片段（stage4 会注入）
            <button class="mini" style="margin-left:8px;" @click="clearSnippets">清空</button>
          </div>
        </template>
      </div>

      <div class="card">
        <div class="card-head">
          <h3>文风分析</h3>
          <span class="spacer"></span>
          <select v-model="source" class="model-select">
            <option value="reference">配置的范文（book.style_reference）</option>
            <option value="path">指定文件</option>
            <option value="chapter">项目章节</option>
          </select>
          <button class="mini primary" :disabled="loading" @click="analyze">
            {{ loading ? "分析中…" : "分析" }}
          </button>
        </div>

        <div v-if="source === 'path'" class="art-row">
          <input v-model="filePath" class="text-input" placeholder="范文 / 参考文本的绝对路径" />
          <button class="mini" @click="pickFile">选择文件…</button>
        </div>
        <div v-else-if="source === 'chapter'" class="art-row">
          <span class="art-label">第</span>
          <input v-model.number="chapN" type="number" min="1" class="text-input" style="max-width:90px;" />
          <span class="art-label">章</span>
          <select v-model="chapScope" class="model-select">
            <option value="">自动</option>
            <option value="raw">raw</option>
            <option value="checked">checked</option>
            <option value="refined">refined</option>
          </select>
          <label class="llm-toggle">
            <input type="checkbox" v-model="compareOn" /> 与范文对比风格偏差
          </label>
        </div>
        <div v-else class="meta">
          取 config/project.yaml 的 <b>book.style_reference</b>；未配置可在“设置”页写入风格笔记，
          或改用“指定文件”。<b>未配置时自动回退为分析项目章节</b>。
        </div>

        <div v-if="note" class="note">{{ note }}</div>
        <div v-if="err" class="empty" style="padding:14px 0;">{{ err }}</div>
      </div>

      <template v-if="f && feat?.sufficient">
        <div class="card">
          <h4>概览 <span class="meta">— {{ feat.label }}（{{ feat.chars }} 字符）</span></h4>
          <div class="stat-row">
            <div class="cost-stat"><div class="stat-label">平均句长</div>
              <div class="stat-val">{{ num(sentLen.avg) }}</div>
              <div class="stat-sub">σ={{ num(sentLen.std) }} · CV={{ num(sentLen.cv, 2) }}</div></div>
            <div class="cost-stat"><div class="stat-label">节奏签名</div>
              <div class="stat-val sm">{{ sentLen.signature || "-" }}</div>
              <div class="stat-sub">长短交替 {{ num(sentLen.alternation, 2) }}</div></div>
            <div class="cost-stat"><div class="stat-label">对话占比</div>
              <div class="stat-val">{{ pct(f.dialogue_ratio) }}</div>
              <div class="stat-sub">含无引号对白 {{ pct(f.speech?.spoken_ratio) }}</div></div>
            <div class="cost-stat"><div class="stat-label">词汇丰富度</div>
              <div class="stat-val">{{ num(f.vocabulary?.mattr, 3) }}</div>
              <div class="stat-sub">MATTR · TTR={{ num(f.vocabulary?.ttr, 3) }}</div></div>
            <div class="cost-stat"><div class="stat-label">情感基调</div>
              <div class="stat-val sm">{{ f.sentiment?.tone || "-" }}</div>
              <div class="stat-sub">极性 {{ num(f.sentiment?.polarity, 2) }}</div></div>
          </div>
        </div>

        <div class="grid-2col">
          <div class="card">
            <h4>句长分布</h4>
            <div class="kv"><span>最短 / 最长</span><b>{{ sentLen.min }} / {{ sentLen.max }} 字</b></div>
            <div class="kv"><span>短句（&lt;15 字）占比</span><b>{{ pct(sentLen.short) }}</b></div>
            <div class="meter"><div class="meter-in" :style="{ width: pct(sentLen.short) }"></div></div>
            <div class="kv"><span>长句（&gt;40 字）占比</span><b>{{ pct(sentLen.long) }}</b></div>
            <div class="meter"><div class="meter-in alt" :style="{ width: pct(sentLen.long) }"></div></div>
            <div class="kv"><span>爆发度（burstiness）</span><b>{{ num(sentLen.burstiness, 2) }}</b></div>
            <div class="kv"><span>短句连发段数</span><b>{{ f.short_burst_count ?? "-" }}</b></div>
            <div class="meta">句数 {{ f.sentence_count }} · 段落 {{ f.paragraph_count }}</div>
          </div>

          <div class="card">
            <h4>连接词偏好</h4>
            <div v-if="!connectiveRows.length" class="empty">无数据</div>
            <div v-for="r in connectiveRows" :key="r.name" class="bar-row">
              <span class="bar-label" :class="{ dom: r.dominant }">{{ r.name }}</span>
              <div class="meter"><div class="meter-in" :style="{ width: r.w + '%' }"></div></div>
              <span class="bar-val">{{ r.count }} 次 · {{ pct(r.ratio) }}</span>
            </div>
            <h4 style="margin-top:14px;">人称视角</h4>
            <div v-for="r in personRows" :key="r.name" class="bar-row">
              <span class="bar-label" :class="{ dom: r.dominant }">{{ r.name }}</span>
              <div class="meter"><div class="meter-in alt" :style="{ width: r.w + '%' }"></div></div>
              <span class="bar-val">{{ r.count }} 次</span>
            </div>
          </div>
        </div>

        <div class="grid-2col">
          <div class="card">
            <h4>标点习惯</h4>
            <table class="cost-table">
              <tbody>
                <tr v-for="row in punctuationRows" :key="row[0]">
                  <td>{{ row[0] }}</td><td style="text-align:right;">{{ row[1] }}</td>
                </tr>
              </tbody>
            </table>
            <h4 style="margin-top:14px;">时间标记词</h4>
            <div class="chips">
              <span v-for="w in (f.temporal_markers?.top || [])" :key="w" class="chip">{{ w }}</span>
              <span v-if="!f.temporal_markers?.top?.length" class="meta">—</span>
            </div>
            <div class="meta">{{ f.temporal_markers?.count || 0 }} 次 · 密度
              {{ num(f.temporal_markers?.density_per_1k, 2) }}/千字</div>
            <h4 style="margin-top:14px;">语气词</h4>
            <div class="chips">
              <span v-for="w in (f.modality?.top || [])" :key="w" class="chip">{{ w }}</span>
              <span v-if="!f.modality?.top?.length" class="meta">—</span>
            </div>
          </div>

          <div class="card">
            <h4>高频表达与句式</h4>
            <div class="chips">
              <span v-for="w in (f.common_phrases || [])" :key="w" class="chip">{{ w }}</span>
              <span v-if="!f.common_phrases?.length" class="meta">—</span>
            </div>
            <div class="kv" style="margin-top:10px;"><span>修辞密度（比喻 / 句）</span>
              <b>{{ num(f.metaphor_density, 3) }}</b></div>
            <div class="kv"><span>拟人密度（/ 句）</span><b>{{ num(f.personification_density, 3) }}</b></div>
            <div class="kv"><span>关联词对密度（/ 句）</span><b>{{ num(f.correlative_density, 3) }}</b></div>
            <h4 style="margin-top:14px;">开头 / 结尾模式</h4>
            <div class="kv"><span>以连接词开头</span><b>{{ pct(f.opening_patterns?.connective_start_ratio) }}</b></div>
            <div class="kv"><span>以时间词开头</span><b>{{ pct(f.opening_patterns?.temporal_start_ratio) }}</b></div>
            <div class="kv"><span>以对白开头</span><b>{{ pct(f.opening_patterns?.quote_start_ratio) }}</b></div>
            <div class="kv"><span>句末语气词收尾</span><b>{{ pct(f.ending_patterns?.particle_end_ratio) }}</b></div>
            <div class="meta">逗号 {{ num(f.punctuation?.comma_per_sentence, 2) }}/句 ·
              问句 {{ pct(f.ending_patterns?.question_ratio) }} ·
              感叹 {{ pct(f.ending_patterns?.exclaim_ratio) }}</div>
          </div>
        </div>
      </template>

      <!-- 风格偏差 -->
      <div v-if="drift" class="card">
        <div class="card-head">
          <h4>风格偏差（对比 {{ drift.drift_chapter || "" }}）</h4>
          <span class="spacer"></span>
          <span class="meta">阈值 {{ pct(drift.threshold) }}</span>
        </div>
        <div v-if="drift.skipped" class="empty">{{ drift.summary }}</div>
        <template v-else>
          <div class="drift-summary" :class="{ bad: drift.drift_count > 0 }">{{ drift.summary }}</div>
          <table v-if="drift.drifts?.length" class="cost-table" style="margin-top:8px;">
            <thead><tr><th>维度</th><th>范文</th><th>本章</th><th>偏差</th></tr></thead>
            <tbody>
              <tr v-for="d in drift.drifts" :key="d.dimension">
                <td>{{ d.dimension }}</td><td>{{ d.ref_text }}</td><td>{{ d.output_text }}</td>
                <td :class="d.drift_pct > 0 ? 'bad-val' : 'ok-val'">
                  {{ d.drift_pct > 0 ? "+" : "" }}{{ d.drift_pct }}%
                </td>
              </tr>
            </tbody>
          </table>
          <div v-if="drift.matches?.length" class="meta" style="margin-top:8px;">
            匹配良好：{{ drift.matches.map((m) => m.dimension).join("、") }}
          </div>
        </template>
      </div>
    </template>

    <!-- ================= 章节节奏 ================= -->
    <!-- 必须是 v-else-if 而不是 v-else：加第三个子页签后，v-else 会把节奏区
         也渲染到“同质化检测”下面（视觉验收实测到两块叠在一起）。 -->
    <template v-else-if="sub === 'pacing'">
      <div class="card">
        <div class="card-head">
          <h3>章节节奏</h3>
          <span class="spacer"></span>
          <button class="mini" :class="{ primary: pacingSource === 'current' }"
                  @click="pacingSource = 'current'; loadPacing('current')">本书章节</button>
          <button class="mini" :class="{ primary: pacingSource === 'book' }"
                  @click="pacingSource = 'book'; loadPacing('book')">导入的参考书</button>
          <button class="mini" @click="importBook">导入参考书…</button>
        </div>
        <div class="meta" v-if="pacing">
          来源：{{ pacing.source }} ｜ 章数 {{ pacing.summary.chapters }} ｜
          总字数 {{ pacing.summary.total_words }} ｜
          均值 {{ pacing.summary.word_count.mean }}（σ={{ pacing.summary.word_count.std }}，
          CV={{ pacing.summary.word_count.cv }}）｜ 趋势 <b>{{ pacing.summary.trend }}</b>
        </div>
      </div>

      <div v-if="pacingLoading" class="card empty">读取中…</div>
      <div v-else-if="!pacing" class="card empty">
        暂无节奏数据 —— 先跑 chapter 阶段，或点“导入参考书…”拆一本外部文本
      </div>

      <template v-else>
        <div class="card">
          <h4>字数分布</h4>
          <div class="bars-plot">
            <div class="bars">
              <div v-for="b in pacingBars" :key="b.n" class="bar-col"
                   :title="`${b.title} · ${b.words} 字 · 对话 ${(b.dlg * 100).toFixed(1)}%`">
                <div class="bar" :class="{ outlier: b.outlier }" :style="{ height: b.h + '%' }"></div>
              </div>
            </div>
            <!-- 移动平均趋势线：与柱状图共用 0-100 百分比坐标系 -->
            <svg v-if="pacingTrend.has" class="trend-svg" viewBox="0 0 100 100"
                 preserveAspectRatio="none" aria-hidden="true">
              <polyline :points="pacingTrend.points" fill="none" stroke="var(--accent)"
                        stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"
                        vector-effect="non-scaling-stroke" opacity="0.9" />
            </svg>
            <div class="bars-x">
              <div v-for="b in pacingBars" :key="'x' + b.n" class="bar-x">{{ b.n }}</div>
            </div>
          </div>
          <div class="legend">
            <span class="lg"><i class="swatch bar-sw"></i>每章字数</span>
            <span class="lg"><i class="swatch outlier-sw"></i>离群章（偏离均值 &gt;40%）</span>
            <span class="lg" v-if="pacingTrend.has">
              <i class="swatch trend-sw"></i>{{ pacingTrend.win }} 章移动平均趋势线
              <span v-if="pacingTrend.last" class="meta">（最新 {{ pacingTrend.last }} 字）</span>
            </span>
          </div>
          <div class="meta" style="margin-top:10px;">
            <span v-if="pacing.summary.outliers?.length" class="bad-val">
              离群章：{{ pacing.summary.outliers.map((o) => `#${o.index} ${o.delta_pct > 0 ? "+" : ""}${o.delta_pct}%`).join("、") }}
            </span>
            <span v-else>各章字数分布均匀 ✓</span>
            · 对话占比 {{ pct(pacing.summary.dialogue_ratio.min) }} ~
            {{ pct(pacing.summary.dialogue_ratio.max) }}
          </div>
        </div>

        <div class="card">
          <h4>逐章明细</h4>
          <table class="cost-table">
            <thead>
              <tr><th>序</th><th>标题</th><th>字数</th><th>段数</th><th>均段</th>
                  <th>均句</th><th>句长σ</th><th>对话%</th></tr>
            </thead>
            <tbody>
              <tr v-for="c in pacing.chapters" :key="c.index">
                <td>{{ c.index }}</td>
                <td class="title-cell">{{ c.title }}</td>
                <td>{{ c.word_count }}</td>
                <td>{{ c.paragraphs }}</td>
                <td>{{ c.avg_para_len }}</td>
                <td>{{ c.avg_sentence_len }}</td>
                <td>{{ c.sentence_len_std }}</td>
                <td>{{ (c.dialogue_ratio * 100).toFixed(1) }}%</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </template>

    <template v-if="sub === 'homo'">
      <div class="card">
        <div class="card-head">
          <h3>同质化检测</h3>
          <span class="spacer"></span>
          <button class="mini" :disabled="homoBusy" @click="loadHomo">
            {{ homoBusy ? "读取中…" : "刷新" }}</button>
        </div>
        <div class="meta">
          确定性指标，<b>零 token、只报不拦</b>：复读、短语反复、对话同腔、句式单调、比喻堆砌、感官锁死。
          stage4（初稿）与 stage6（润色）落盘后自动生成报告。
          <b>告警不等于判废</b> —— 它只说"这里像机器在打转"，改不改由人定。
        </div>
        <div v-if="homoErr" class="empty" style="padding:14px 0;">{{ homoErr }}</div>
        <div v-else-if="!homo || !(homo.chapters || []).length" class="meta" style="padding:10px 0;">
          还没有报告 —— 跑过 stage4 之后每章自动生成；也可在命令行单独测一章：
          <code>python scripts/utils/homogeneity.py 章节.md --names 角色1,角色2</code>
        </div>
        <table v-else class="cost-table homo-table">
          <thead>
            <tr><th>章</th><th>字数</th><th>状态</th><th>命中项</th></tr>
          </thead>
          <tbody>
            <tr v-for="c in homo.chapters" :key="c.chapter"
                :class="{ 'homo-on': String(c.chapter) === String(homoPick) }"
                @click="pickHomo(c.chapter)">
              <td>{{ c.chapter }}</td>
              <td>{{ c.chars }}</td>
              <td :class="'homo-' + homoFlag(c).cls">{{ homoFlag(c).text }}</td>
              <td class="meta">
                {{ (c.findings || []).map((f) => HOMO_LABEL[f.code] || f.code).join("、") || "-" }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="card" v-if="homoDetail">
        <h4>
          第 {{ homoDetail.chapter ?? homoPick }} 章
          <span class="meta">— {{ homoDetail.chars }} 字</span>
        </h4>
        <div v-if="!(homoDetail.findings || []).length" class="meta">
          <span :class="homoDetail.enabled ? 'homo-ok' : 'meta'">
            {{ homoDetail.enabled ? "未发现同质化迹象" : "样本不足 600 字，比率型指标未计算" }}</span>
        </div>
        <div v-for="f in homoDetail.findings" :key="f.code" class="homo-finding">
          <div class="homo-f-head">
            <b>{{ HOMO_LABEL[f.code] || f.code }}</b>
            <span class="bad-val">{{ f.detail }}</span>
          </div>
          <div class="meta">实测 {{ f.value }} · 阈值 {{ f.threshold }}</div>
        </div>
        <div class="meta" v-if="(homoDetail.skipped || []).length">
          跳过：{{ homoDetail.skipped.join("；") }}
        </div>
        <div class="meta homo-metrics" v-if="homoDetail.metrics">
          指标原始值：
          <code v-for="(v, k) in homoDetail.metrics" :key="k">{{ k }}={{ v }}</code>
        </div>
      </div>
    </template>

    <!-- 拆解功能声明弹层（2026-10-20 改）：**与 DisclaimerDialog 同构**的居中确认框，
         不再用右侧抽屉（.drawer 是“浏览/编辑长内容”的容器，拿它装一纸声明既不像
         确认框、底部按钮也贴在视口边缘）。形态照抄本应用既有范式：
         drawer-mask（居中）→ dialog → card-head 标题 → 可滚动正文 → dialog-actions。
         交互纪律不变：**不同意是默认焦点**，关掉弹层 = 不启用（不会静默同意）。 -->
    <div v-if="decDeclOpen" class="drawer-mask dec-mask" @click.self="decDeclOpen = false"
         @keydown.esc.stop.prevent="decDeclOpen = false">
      <div class="dialog dec-decl" role="dialog" aria-modal="true"
           aria-label="拆解功能版权与免责声明" ref="decDeclEl">
        <div class="card-head">
          <h3>拆解功能 · 版权与免责声明</h3>
          <span class="meta">首次使用需确认</span>
        </div>
        <div class="dec-decl-body">
          <p v-for="(l, i) in DECL_LINES" :key="i">{{ l }}</p>
          <p class="meta">完整声明见项目文档 docs/拆解功能-版权与免责声明.md；本弹层是其摘要。</p>
        </div>
        <div class="dialog-actions dec-decl-foot">
          <span class="meta dec-decl-hint">关闭本框 = 不启用该功能</span>
          <span class="spacer"></span>
          <button class="mini" autofocus @click="decDeclOpen = false">我不同意</button>
          <button class="mini primary" @click="agreeDecompose">我同意，继续</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 参考作品拆解：候选列表 + 声明弹层 */
.dec-cands { border-top: 1px dashed var(--border); margin-top: 10px; }
.dec-cand { display: flex; gap: 8px; align-items: flex-start; padding: 6px 4px; border-radius: 6px; }
.dec-cand:hover { background: var(--field-hover); }
.dec-kind {
  flex: none;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--field);
  border: 1px solid var(--border);
}
.k-dialogue { color: var(--warn); border-color: var(--warn); }
.k-action { color: var(--ok); border-color: var(--ok); }
.k-scene { color: var(--bad); border-color: var(--bad); }
.dec-text { flex: 1; font-size: 12.5px; line-height: 1.6; }
/* 声明弹层（2026-10-20）：居中确认框，形态与免责声明弹窗一致。
   要点：正文自带滚动区（长声明不把按钮顶出视口）、按钮永远可见。 */
.dec-mask { z-index: 40; }
.dec-decl {
  width: min(620px, 92vw);
  max-height: 84vh;
  display: flex;
  flex-direction: column;
}
.dec-decl .card-head { margin-bottom: 8px; }
.dec-decl-body {
  flex: 1;
  min-height: 160px;
  max-height: 52vh;
  overflow-y: auto;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--field-inset, rgba(127, 127, 127, 0.06));
  font-size: 13px;
  line-height: 1.85;
}
.dec-decl-body p { margin: 0 0 9px; }
.dec-decl-body p:last-child { margin-bottom: 0; }
.dec-decl-foot { align-items: center; margin-top: 12px; }
.dec-decl-hint { font-size: 12px; }

/* 同质化检测：行可点，选中走 --field-on；状态色一律走主题变量（暗色下也要读得清） */
.homo-table tbody tr { cursor: pointer; }
.homo-table tbody tr:hover { background: var(--field-hover); }
.homo-table tbody tr.homo-on { background: var(--field-on); }
.homo-ok { color: var(--ok); }
.homo-warn { color: var(--warn); }
.homo-bad { color: var(--bad); }
.homo-muted { opacity: 0.65; }
.homo-finding { padding: 8px 0; border-bottom: 1px dashed var(--border); }
.homo-finding:last-of-type { border-bottom: none; }
.homo-f-head { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
.homo-metrics { margin-top: 8px; }
.homo-metrics code {
  margin-right: 10px;
  background: var(--field-code);
  padding: 1px 5px;
  border-radius: 4px;
}

/* 范文粘贴框：暗色主题下必须走 var(--field)，禁止硬编码白底 */
.style-ref-input {
  width: 100%;
  margin: 8px 0 6px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--field);
  color: var(--ink);
  font-size: 13px;
  line-height: 1.75;
  font-family: inherit;
  resize: vertical;
}
.style-ref-input:focus { outline: none; border-color: var(--accent); }
.ref-status { display: flex; flex-wrap: wrap; gap: 4px 6px; align-items: baseline; }
.ref-path { font-family: ui-monospace, Consolas, monospace; font-size: 11.5px; color: var(--muted); }
.ref-ok { color: var(--ok); }
.ref-warn { color: var(--warn, #b45309); }
.spacer { flex: 1; }
.llm-toggle { display: flex; align-items: center; gap: 5px; font-size: 12.5px; color: var(--muted); }
.stat-row { display: flex; gap: 12px; flex-wrap: wrap; }
.cost-stat { flex: 1; min-width: 118px; background: var(--bg); border-radius: 10px; padding: 10px 12px; }
.stat-val.sm { font-size: 15px; }
.stat-sub { font-size: 11.5px; color: var(--muted); }
.grid-2col { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px; }
.kv { display: flex; justify-content: space-between; font-size: 12.5px; padding: 3px 0; color: var(--muted); }
.kv b { color: var(--ink); font-weight: 600; }
.meter { height: 7px; background: var(--border); border-radius: 4px; overflow: hidden; margin: 4px 0 8px; }
.meter-in { height: 100%; background: var(--accent); border-radius: 4px; }
.meter-in.alt { background: var(--accent-soft); }
.bar-row { display: flex; align-items: center; gap: 8px; }
.bar-row .meter { flex: 1; margin: 4px 0; }
.bar-label { width: 68px; font-size: 12px; color: var(--muted); }
.bar-label.dom { color: var(--accent); font-weight: 700; }
.bar-val { width: 96px; text-align: right; font-size: 11.5px; color: var(--muted); }
.chips { display: flex; flex-wrap: wrap; gap: 5px; margin: 4px 0 6px; }
.chip { background: var(--accent-soft); color: var(--ink); border-radius: 999px; padding: 2px 9px; font-size: 12px; }
.drift-summary { font-size: 13px; padding: 8px 10px; border-radius: 8px; background: rgba(127, 174, 143, 0.12); color: #4e7d5e; }
.drift-summary.bad { background: rgba(201, 162, 106, 0.14); color: #8f6b33; }
.bad-val { color: var(--bad); }
.ok-val { color: var(--ok); }
/* 柱状图 + 趋势线共用坐标系：.bars 与 .trend-svg 同高同起点（0-100% 一一对应） */
.bars-plot { position: relative; padding-bottom: 16px; }
.bars { display: flex; align-items: flex-end; gap: 2px; height: 132px; }
.bar-col { flex: 1; min-width: 6px; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; }
.bar { width: 100%; max-width: 34px; background: var(--accent-soft); border-radius: 4px 4px 0 0; transition: height .3s; }
.bar.outlier { background: var(--bad); }
.trend-svg { position: absolute; left: 0; right: 0; top: 0; height: 132px; width: 100%; pointer-events: none; }
.bars-x { position: absolute; left: 0; right: 0; bottom: 0; display: flex; gap: 2px; }
.bars-x .bar-x { flex: 1; min-width: 6px; text-align: center; font-size: 10px; color: var(--muted); }
.legend { display: flex; flex-wrap: wrap; gap: 14px; margin-top: 6px; font-size: 12px; color: var(--muted); }
.legend .lg { display: inline-flex; align-items: center; gap: 5px; }
.legend .swatch { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
.swatch.bar-sw { background: var(--accent-soft); }
.swatch.outlier-sw { background: var(--bad); }
.swatch.trend-sw { background: var(--accent); height: 3px; border-radius: 2px; }
.title-cell { max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.note {
  margin-top: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  background: rgba(155, 143, 196, 0.12);
  color: var(--ink);
  font-size: 12.5px;
  line-height: 1.6;
}
.title-cell { max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
