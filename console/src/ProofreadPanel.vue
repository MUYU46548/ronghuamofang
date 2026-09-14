<script setup>
/**
 * 校对面板（stage 5.5：润色后、交付 Word 前）。
 *
 * 数据源：GET /proofread/report（schema 与 review_report 对齐）
 * 触发：  POST /proofread/run {scope, llm, dry_run}
 * 闭环：  勾选"建议" → POST /refine/chapter 定向精修本章（复用既有精修链路）
 *
 * 确定性检查（标点/错字/格式/节奏）零 token；LLM 语义校对默认关闭，需显式勾选。
 */
import { ref, computed, onMounted } from "vue";

const API = "http://127.0.0.1:8765";
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

const report = ref(null);
const running = ref(false);
const scope = ref("");          // "" = 自动（refined > checked > raw）
const useLlm = ref(false);
const expanded = ref(new Set());
const picked = ref({});         // { "章号_findingId": true } 默认全选
const refining = ref(null);     // 正在精修的章号

const chapters = computed(() => (report.value?.chapters || []).filter((c) => c.n !== null));
const rhythmChapter = computed(() => (report.value?.chapters || []).find((c) => c.n === null));
const summary = computed(() => report.value?.summary || {});
const rhythm = computed(() => report.value?.rhythm || {});

const pickedCount = computed(() =>
  Object.values(picked.value).filter(Boolean).length);
const totalFindings = computed(() =>
  chapters.value.reduce((s, c) => s + (c.findings?.length || 0), 0));

function wordBars() {
  const rs = (rhythm.value.chapters || {});
  const ns = Object.keys(rs).map(Number).sort((a, b) => a - b);
  if (!ns.length) return [];
  const max = Math.max(...ns.map((n) => rs[n].word_count || 1));
  const outlierNs = new Set((rhythm.value.outliers || []).map((o) => o.n));
  return ns.map((n) => ({
    n,
    h: Math.max(2, Math.round(((rs[n].word_count || 0) / max) * 100)),
    words: rs[n].word_count,
    outlier: outlierNs.has(n),
    dialogue: rs[n].dialogue_ratio,
  }));
}

const sevIcon = (s) => ({ error: "🔴", warn: "🟡", info: "🔵" }[s] || "⚪");
const typeLabel = (t) => ({
  punct: "标点", typo: "错字", format: "格式",
  rhythm: "节奏", grammar: "语句", fact: "设定", name: "称谓", other: "其他",
}[t] || t);
const sevClass = (s) => ({ error: "sev-error", warn: "sev-warn", info: "sev-info" }[s] || "sev-info");

function toggleChapter(n) {
  const s = expanded.value;
  s.has(n) ? s.delete(n) : s.add(n);
}

function keyOf(cn, f) { return `${cn}_${f.id}`; }

function initPicks(rep) {
  const p = {};
  for (const c of rep.chapters || []) {
    if (c.n === null) continue;
    for (const f of c.findings || []) p[keyOf(c.n, f)] = true;
  }
  picked.value = p;
}

function toggleAll(flag) {
  const p = { ...picked.value };
  for (const c of chapters.value) {
    for (const f of c.findings || []) p[keyOf(c.n, f)] = flag;
  }
  picked.value = p;
}

function pickedOf(c) {
  return (c.findings || []).filter((f) => picked.value[keyOf(c.n, f)]);
}

async function loadReport(silent = false) {
  const r = await api("/proofread/report");
  if (r.status === 200 && Array.isArray(r.data.chapters)) {
    report.value = r.data;
    initPicks(r.data);
    if (!silent) say("校对报告已加载");
  } else if (r.status === 404) {
    report.value = null;
    if (!silent) say("暂无校对报告 —— 点击「运行校对」开始检查");
  } else {
    say("报告读取失败: " + (r.data.error || r.status));
  }
}

async function runProofread() {
  if (running.value) return;
  const opts = [];
  opts.push("范围：" + (scope.value || "自动（润色稿 > 检查稿 > 原稿）"));
  opts.push("确定性检查：标点 / 错字 / 格式 / 节奏（零 token）");
  if (useLlm.value) opts.push("LLM 语义校对：开启（会消耗 token）");
  if (!window.confirm("确定运行校对？\n\n" + opts.join("\n"))) return;
  running.value = true;
  try {
    const r = await api("/proofread/run", "POST", {
      scope: scope.value || null, llm: useLlm.value, dry_run: false,
    });
    if (r.status !== 202) { say("提交失败: " + (r.data.error || r.status)); return; }
    say("校对任务已提交: " + r.data.job_id);
    for (let i = 0; i < 300; i++) {
      await new Promise((res) => setTimeout(res, 2000));
      const j = await api("/jobs/" + r.data.job_id);
      if (j.status !== 200) break;
      if (j.data.state !== "running") {
        if (j.data.state === "ok") { say(j.data.result || "校对完成"); await loadReport(true); }
        else say("校对失败: " + (j.data.result || j.data.error || ""));
        return;
      }
    }
    say("校对仍在运行，可稍后点「刷新报告」查看");
  } catch (e) {
    say("错误: " + (e.message || e));
  } finally {
    running.value = false;
  }
}

/** 把选中 finding 的建议拼成一条精修意见。 */
function buildFeedback(list) {
  return list.map((f) => {
    const sug = f.suggestion || f.suggested_action || "";
    const loc = f.location ? "（位置：" + f.location + "）" : "";
    return `【${typeLabel(f.type)}】${f.detail}${loc}` + (sug ? " → " + sug : "");
  }).join("\n");
}

async function refineChapter(c) {
  const list = pickedOf(c);
  if (!list.length) return say("请先勾选本章至少一条建议");
  if (!window.confirm(
    `确定按 ${list.length} 条建议精修第 ${c.n} 章？\n\n`
    + "· 复用章节精修链路（同 refine_chapter）\n"
    + "· 旧稿自动备份到 data/chapters/history/\n"
    + "· ±20% 字数铁律仍然生效")) return;
  refining.value = c.n;
  try {
    const r = await api("/refine/chapter", "POST", {
      chapter: c.n, feedback: buildFeedback(list), dry_run: false,
    });
    if (r.status === 202) say(`第 ${c.n} 章精修已提交（job ${r.data.job_id}）`);
    else say("提交失败: " + (r.data.error || r.status));
  } catch (e) {
    say("错误: " + (e.message || e));
  } finally {
    refining.value = null;
  }
}

onMounted(() => loadReport(true));
</script>

<template>
  <div class="proofread-panel">
    <div class="card">
      <div class="card-head">
        <h3>校对（stage 5.5 · 润色后、交付前）</h3>
        <span class="spacer"></span>
        <select v-model="scope" class="model-select" title="校对哪一版稿件">
          <option value="">自动（润色稿优先）</option>
          <option value="refined">润色稿 refined</option>
          <option value="checked">检查稿 checked</option>
          <option value="raw">原稿 raw</option>
        </select>
        <label class="llm-toggle" title="LLM 语义校对会消耗 token，默认关闭">
          <input type="checkbox" v-model="useLlm" />
          LLM 语义校对
        </label>
        <button class="mini primary" :disabled="running" @click="runProofread">
          {{ running ? "校对中…" : "运行校对" }}
        </button>
        <button class="mini" @click="loadReport(false)">刷新报告</button>
      </div>
      <div class="meta">
        确定性检查（标点配对 / 省略号破折号统一 / 全半角 / 高频错字 / 的地得 / 别名混用 / 字数节奏）
        零 token 开销；LLM 语义校对负责设定矛盾、称谓混乱、病句，需显式开启。
      </div>
    </div>

    <div v-if="!report" class="card empty">
      暂无校对报告 —— 点击「运行校对」开始检查（不会改动任何稿件）
    </div>

    <template v-else>
      <div class="card">
        <h4>问题统计</h4>
        <div class="stat-row">
          <div class="cost-stat"><div class="stat-label">校对章节</div>
            <div class="stat-val">{{ summary.chapters }}</div>
            <div class="stat-sub">范围 {{ report.scope }}</div></div>
          <div class="cost-stat"><div class="stat-label">问题总数</div>
            <div class="stat-val">{{ summary.issues }}</div>
            <div class="stat-sub">{{ report.use_llm ? "含 LLM 语义校对" : "纯确定性" }}</div></div>
          <div class="cost-stat"><div class="stat-label">🔴 错误</div>
            <div class="stat-val bad-val">{{ summary.error }}</div>
            <div class="stat-sub">必须改</div></div>
          <div class="cost-stat"><div class="stat-label">🟡 警告</div>
            <div class="stat-val warn-val">{{ summary.warn }}</div>
            <div class="stat-sub">建议改</div></div>
          <div class="cost-stat"><div class="stat-label">🔵 提示</div>
            <div class="stat-val">{{ summary.info }}</div>
            <div class="stat-sub">自行判断</div></div>
        </div>
      </div>

      <!-- 章节节奏 -->
      <div class="card">
        <div class="card-head">
          <h4>章节节奏</h4>
          <span class="spacer"></span>
          <span class="meta" v-if="rhythm.word_count">
            均值 {{ rhythm.word_count.mean }} 字（σ={{ rhythm.word_count.std }}，CV={{ rhythm.word_count.cv }}）
          </span>
        </div>
        <div v-if="!wordBars().length" class="empty">无节奏数据</div>
        <template v-else>
          <div class="bars">
            <div v-for="b in wordBars()" :key="b.n" class="bar-col"
                 :title="`第${b.n}章 · ${b.words} 字 · 对话占比 ${(b.dialogue * 100).toFixed(1)}%`">
              <div class="bar" :class="{ outlier: b.outlier }" :style="{ height: b.h + '%' }"></div>
              <div class="bar-x">{{ b.n }}</div>
            </div>
          </div>
          <div class="meta">
            <span v-if="rhythm.outliers?.length" class="bad-val">
              字数离群：{{ rhythm.outliers.map((o) => `第${o.n}章 ${o.delta_pct > 0 ? "+" : ""}${o.delta_pct}%`).join("、") }}
            </span>
            <span v-else>各章字数分布均匀 ✓</span>
            <span v-if="rhythm.dialogue_ratio">
              · 对话占比 {{ (rhythm.dialogue_ratio.min * 100).toFixed(1) }}% ~
              {{ (rhythm.dialogue_ratio.max * 100).toFixed(1) }}%
              （均 {{ (rhythm.dialogue_ratio.mean * 100).toFixed(1) }}%）
            </span>
          </div>
          <div v-for="f in (rhythmChapter?.findings || [])" :key="f.id" class="rhythm-note">
            {{ sevIcon(f.severity) }} {{ f.detail }}
            <span v-if="f.suggestion" class="meta">— {{ f.suggestion }}</span>
          </div>
        </template>
      </div>

      <!-- 批量操作 -->
      <div class="card">
        <div class="card-head">
          <span class="meta">已勾选 <b>{{ pickedCount }}</b> / {{ totalFindings }} 条建议</span>
          <span class="spacer"></span>
          <button class="mini" @click="toggleAll(true)">全选</button>
          <button class="mini" @click="toggleAll(false)">全不选</button>
        </div>
      </div>

      <!-- 逐章问题 -->
      <div v-for="c in chapters" :key="c.n" class="card chapter-card">
        <div class="chapter-head" @click="toggleChapter(c.n)">
          <span class="stage-no" :class="{ lit: c.findings?.length }">{{ c.n }}</span>
          <span class="chapter-title">{{ c.title }}</span>
          <span class="finding-count">{{ c.word_count }} 字 · {{ c.findings?.length || 0 }} 条</span>
          <span class="spacer"></span>
          <button class="mini primary" :disabled="refining === c.n || !pickedOf(c).length"
                  @click.stop="refineChapter(c)">
            {{ refining === c.n ? "提交中…" : `按建议精修（${pickedOf(c).length}）` }}
          </button>
          <span class="expand-icon">{{ expanded.has(c.n) ? "▼" : "▶" }}</span>
        </div>

        <div v-if="expanded.has(c.n)" class="chapter-body">
          <div v-if="!c.findings?.length" class="no-issues">无问题 ✓</div>
          <div v-for="f in c.findings" :key="f.id" class="finding-row" :class="sevClass(f.severity)">
            <div class="finding-header">
              <input type="checkbox" v-model="picked[keyOf(c.n, f)]" />
              <span class="sev-icon">{{ sevIcon(f.severity) }}</span>
              <span class="finding-type">{{ typeLabel(f.type) }}</span>
              <span v-if="f.source === 'llm'" class="pill st-running">LLM</span>
              <span class="finding-id">{{ f.id }}</span>
            </div>
            <div class="finding-detail">{{ f.detail }}</div>
            <div v-if="f.location" class="finding-loc">位置：{{ f.location }}</div>
            <div v-if="f.suggestion" class="finding-suggestion">建议：{{ f.suggestion }}</div>
          </div>
        </div>
      </div>

      <div class="card meta">
        报告文件：data/outline/proofread_report.json / .md ·
        确定性部分完全离线，不消耗 token ·
        「按建议精修」走既有章节精修链路（自动备份 + ±20% 字数铁律）
      </div>
    </template>
  </div>
</template>

<style scoped>
.spacer { flex: 1; }
.llm-toggle { display: flex; align-items: center; gap: 5px; font-size: 12.5px; color: var(--muted); }
.stat-row { display: flex; gap: 14px; flex-wrap: wrap; }
.cost-stat { flex: 1; min-width: 120px; background: var(--bg); border-radius: 10px; padding: 10px 12px; }
.stat-sub { font-size: 11.5px; color: var(--muted); }
.bad-val { color: var(--bad); }
.warn-val { color: var(--warn); }

.bars { display: flex; align-items: flex-end; gap: 4px; height: 130px; padding: 8px 0 0; }
.bar-col { flex: 1; display: flex; flex-direction: column; align-items: center; height: 100%; justify-content: flex-end; }
.bar { width: 100%; max-width: 40px; background: var(--accent-soft); border-radius: 4px 4px 0 0; transition: height .3s; }
.bar.outlier { background: var(--warn); }
.bar-x { font-size: 10px; color: var(--muted); margin-top: 3px; }
.rhythm-note { font-size: 12.5px; margin-top: 6px; color: var(--ink); }

.chapter-card { padding: 0; overflow: hidden; }
.chapter-head { display: flex; align-items: center; gap: 10px; padding: 12px 16px; cursor: pointer; user-select: none; }
.chapter-head:hover { background: rgba(155, 143, 196, 0.06); }
.chapter-title { font-weight: 500; }
.finding-count { font-size: 12px; color: var(--muted); }
.expand-icon { color: var(--muted); font-size: 10px; }
.chapter-body { padding: 12px 16px; border-top: 1px solid var(--border); }
.no-issues { color: var(--ok); font-size: 13px; padding: 8px 0; }

.finding-row { padding: 10px 12px; border-radius: 8px; margin-bottom: 8px; border-left: 3px solid transparent; }
.finding-row.sev-error { border-left-color: var(--bad); background: rgba(196, 127, 127, 0.06); }
.finding-row.sev-warn { border-left-color: var(--warn); background: rgba(201, 162, 106, 0.06); }
.finding-row.sev-info { border-left-color: var(--accent); background: rgba(155, 143, 196, 0.06); }
.finding-header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.sev-icon { font-size: 12px; }
.finding-type { font-size: 12px; font-weight: 500; }
.finding-id { font-size: 11px; color: var(--muted); margin-left: auto; }
.finding-detail { font-size: 13px; line-height: 1.6; margin-bottom: 3px; }
.finding-loc { font-size: 12px; color: var(--muted); }
.finding-suggestion { font-size: 12px; color: var(--muted); margin-top: 2px; }
</style>
