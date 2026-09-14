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
    // 未配置范文时不留在空面板：自动改用「项目章节」跑一次（依次试前 3 章）
    if (autoFallback && !r.data.ok && r.data.configured === false
        && source.value === "reference") {
      source.value = "chapter";
      for (const n of [1, 2, 3]) {
        chapN.value = n;
        note.value = "未配置 book.style_reference —— 已自动改用「第 " + n + " 章」做分析；"
          + "想分析范文请在 config/project.yaml 配置，或切到「指定文件」。";
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
    if (r.status === 404) say("尚无拆书结果 —— 可用「导入参考书」分析外部文本");
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
  analyze();
  loadPacing("current");
});
</script>

<template>
  <div class="style-panel">
    <div class="tabs-sub" style="margin-bottom: 12px;">
      <button :class="{ on: sub === 'style' }" @click="sub = 'style'">文风特征</button>
      <button :class="{ on: sub === 'pacing' }" @click="sub = 'pacing'">章节节奏</button>
    </div>

    <!-- ================= 文风特征 ================= -->
    <template v-if="sub === 'style'">
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
          取 config/project.yaml 的 <b>book.style_reference</b>；未配置可在「设置」页写入风格笔记，
          或改用「指定文件」。<b>未配置时自动回退为分析项目章节</b>。
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
    <template v-else>
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
        暂无节奏数据 —— 先跑 chapter 阶段，或点「导入参考书…」拆一本外部文本
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
  </div>
</template>

<style scoped>
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
