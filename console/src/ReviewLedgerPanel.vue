<script setup>
/**
 * 七维评审账本面板（2026-10-20，台账二·“七维评审 + 原文举证”）。
 *
 * 数据源：GET /review/ledger（只读；?chapter=N 取单章明细）。
 * 账本由 stage5 每批落盘（data/review/NN.json），七维分数**必须**有原文举证
 * 才算分 —— 这条判据由 `utils/review_ledger` 在落盘时执行，界面只负责显示
 * “哪一维没算分、为什么”。
 *
 * 为什么不给"重算"按钮：重算是模型行为（要烧钱），而且要重跑 stage5 才有新原稿。
 * 这里只读，避免把一个看板做成第二个写入口。
 */
import { ref, computed, onMounted } from "vue";

import { API } from "./apiBase.js";

const emit = defineEmits(["say"]);

const data = ref(null);
const busy = ref(false);
const err = ref("");
const pick = ref(null);      // 选中的章号
const detail = ref(null);
const detailErr = ref("");
// 结构指标（2026-11-05）：七维只验“引句能否对上原文”，不判断好坏。这块把
// “对白死板 / 段落形态”这类**可确定性算**的东西摆出来，只提示人工抽样。
const struct = ref(null);

async function load() {
  busy.value = true;
  err.value = "";
  try {
    const r = await fetch(API + "/review/ledger");
    const d = await r.json().catch(() => ({}));
    if (r.status === 200 && d.ok) {
      data.value = d;
      aesQuestions.value = d.aesthetic_questions || [];
    } else {
      data.value = null;
      err.value = d.error || ("读取失败：HTTP " + r.status);
    }
  } catch (e) {
    err.value = String(e);
  }
  try {
    const r2 = await fetch(API + "/review/structure");
    const d2 = await r2.json().catch(() => ({}));
    struct.value = r2.status === 200 && d2.ok ? d2 : null;
  } catch (e) {
    struct.value = null;
  }
  busy.value = false;
}

async function openChapter(n) {
  pick.value = n;
  detail.value = null;
  detailErr.value = "";
  try {
    const r = await fetch(API + "/review/ledger?chapter=" + n);
    const d = await r.json().catch(() => ({}));
    if (r.status === 200 && d.ok) detail.value = d;
    else detailErr.value = d.error || ("读取失败：HTTP " + r.status);
  } catch (e) {
    detailErr.value = String(e);
  }
}

const dims = computed(() => data.value?.dimensions || []);
const rows = computed(() => data.value?.chapters || []);
const sum = computed(() => data.value?.summary || {});
const completeCount = computed(() => rows.value.filter((r) => r.complete).length);

// 结构指标（文学抽样提示）
const structDims = computed(() => struct.value?.dimensions || []);
const structSum = computed(() => struct.value?.summary || {});
const structNeed = computed(() => structSum.value.needs_sampling || []);
const structRows = computed(() => (struct.value?.chapters || []).filter((c) => c.needs_sampling));

function fmtMetric(d, v) {
  if (v == null) return "—";
  return d.unit === "比例" ? (v * 100).toFixed(1) + "%" : Number(v).toFixed(1);
}

function triggerText(t) {
  const d = structDims.value.find((x) => x.code === t.code);
  const line = d && d.unit === "比例" ? (t.limit * 100).toFixed(1) + "%" : t.limit;
  const val = d && d.unit === "比例" ? (t.value * 100).toFixed(1) + "%" : t.value;
  return `${t.label} ${val}（${t.direction === "min" ? "低于" : "高于"}线 ${line}）`;
}

function scoreCls(v) {
  if (v == null) return "rl-unverified";
  if (v >= 8) return "rl-ok";
  if (v >= 6) return "rl-warn";
  return "rl-bad";
}

function dimRows() {
  const r = detail.value;
  if (!r) return [];
  return (dims.value.length ? dims.value : Object.keys(r.dims || {}).map((k) => ({ code: k, label: k })))
    .map((d) => ({ ...d, rec: (r.dims || {})[d.code] || {} }));
}

// 审美五问：整块来自 detail.aesthetic（老账本没有 → 返回空数组，界面不显示这块）
const aesQuestions = ref([]);
function aesRows() {
  const r = detail.value;
  const items = r?.aesthetic?.items || {};
  const src = aesQuestions.value.length
    ? aesQuestions.value
    : Object.keys(items).map((k) => ({ code: k, label: items[k]?.label || k }));
  return src.map((q) => ({ ...q, rec: items[q.code] || {} }));
}

onMounted(load);
defineExpose({ load });
</script>

<template>
  <div class="card">
    <div class="card-head">
      <h3>审稿账本（逻辑七维 + 结构指标）</h3>
      <span class="spacer"></span>
      <!-- 举证数与均分只在下面的“逻辑七维（流程）”块里显示一次 ——
           卡片头再说一遍会和块内重复，同屏两个“均分”反而让人以为是两个分。 -->
      <button class="mini" :disabled="busy" @click="load">{{ busy ? "读取中…" : "刷新" }}</button>
    </div>
    <div class="meta">
      这一页有两件事，别混着看：<b>逻辑七维</b>=流程（引句能不能对上原文、维缺不缺）；
      <b>结构指标</b>=文学抽样提示（对白与段落形态）。两者都<b>不给文学分</b> —— 写得好不好只能你自己看。
    </div>

    <div v-if="err" class="empty" style="padding:14px 0;">{{ err }}</div>
    <div v-else-if="!data" class="meta" style="padding:10px 0;">读取中…</div>
    <div v-else-if="!rows.length" class="meta" style="padding:10px 0;">
      还没有账本 —— 跑过 stage5（逻辑检查）之后每章自动生成。
    </div>
    <template v-else>
      <div class="rl-block-head">
        <b>逻辑七维（流程）</b>
        <span class="meta">完整举证 {{ completeCount }} / {{ rows.length }} 章
          <template v-if="sum.mean != null"> · 流程均分 {{ sum.mean }}（不代表写得好）</template>
        </span>
      </div>
      <table class="rl-table">
        <thead>
          <tr>
            <th>章</th>
            <th v-for="d in dims" :key="d.code">{{ d.label }}</th>
            <th title="七维流程分：只反映举证与维度是否齐全，不代表文学质量">流程均分</th>
            <th>审美</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.chapter" :class="{ on: pick === r.chapter }"
              @click="openChapter(r.chapter)">
            <td class="rl-ch">{{ r.chapter }}</td>
            <td v-for="d in dims" :key="d.code" :class="scoreCls(r.scores?.[d.code])">
              {{ r.scores?.[d.code] != null && r.complete === false && (r.unverified || []).includes(d.code)
                 ? "未举证" : (r.scores?.[d.code] ?? "—") }}
            </td>
            <td :class="scoreCls(r.mean)">{{ r.mean ?? "—" }}</td>
            <td :class="scoreCls(r.aesthetic_mean)">
              {{ r.aesthetic_mean != null ? r.aesthetic_mean : (r.aesthetic_complete ? "—" : "未评") }}
            </td>
          </tr>
        </tbody>
      </table>
      <div class="meta" style="margin-top:6px;">
        “未举证”= 该维的引用在本章原文里找不到（或引得太短/分数越界），按判据不计分。
      </div>
    </template>

    <!-- 结构指标（2026-11-05）：与七维并列的**另一件事** —— 七维验“引句对不对”，
         这块算“对白/段落形态是否可疑”。只提示人工抽样，不给文学分、不合成总分。 -->
    <div class="rl-split">
      <div class="rl-block-head">
        <b>结构指标（文学抽样提示）</b>
        <span class="meta" v-if="struct && struct.exists">
          需抽样 {{ structNeed.length }} 章 · 只提示抽样，不判合格、不合成总分
        </span>
      </div>
      <div v-if="!struct || struct.exists === false" class="meta" style="padding:6px 0;">
        {{ (struct && struct.hint) || "还没有结构指标 —— 跑过阶段 5（逻辑检查）之后自动生成。" }}
      </div>
      <template v-else>
        <div v-if="struct.missing_criteria && struct.missing_criteria.length"
             class="meta" style="color: var(--warn); padding: 4px 0;">
          口径表缺 {{ struct.missing_criteria.join("、") }} —— 这几项本轮不判（先升级口径表）。
        </div>
        <div class="rl-metrics">
          <div v-for="d in structDims" :key="d.code" class="rl-metric">
            <div class="rl-metric-label">{{ d.label }}</div>
            <div class="rl-metric-value">{{ fmtMetric(d, (structSum.metrics_mean || {})[d.code]) }}</div>
            <div class="meta">全书均值 · 触发 {{ (structSum.triggered_count || {})[d.code] || 0 }} 章</div>
          </div>
        </div>
        <div v-if="structNeed.length" class="rl-need">
          <div class="meta" style="margin-bottom: 4px;">需人工抽样（点章号看七维明细）：</div>
          <div v-for="c in structRows" :key="c.chapter" class="rl-need-row">
            <button class="mini" @click="openChapter(c.chapter)">第 {{ c.chapter }} 章</button>
            <span class="meta">{{ c.triggered.map(triggerText).join("；") }}</span>
          </div>
        </div>
        <div v-else class="meta" style="padding: 4px 0;">
          本轮没有越线项 —— 指标不等于评价，仍建议抽读一章。
        </div>
      </template>
    </div>

    <div v-if="detail" class="rl-detail">
      <div class="card-head">
        <h4>第 {{ detail.chapter }} 章</h4>
        <span class="spacer"></span>
        <span class="meta" v-if="detail.summary">{{ detail.summary }}</span>
      </div>
      <div v-for="d in dimRows()" :key="d.code" class="rl-dim" :class="scoreCls(d.rec.score)">
        <div class="rl-dim-head">
          <b>{{ d.rec.label || d.label }}</b>
          <span class="rl-score">{{ d.rec.verified ? d.rec.score + " / 10" : "未计分" }}</span>
          <span class="meta" v-if="!d.rec.verified">{{ d.rec.reason }}</span>
        </div>
        <blockquote v-if="d.rec.quote" class="rl-quote">{{ d.rec.quote }}</blockquote>
        <div class="meta" v-if="d.rec.note">{{ d.rec.note }}</div>
      </div>

      <!-- 审美五问（2026-10-20）：七维管"没写错"，五问管"写得好不好"。
           另开一块、独立判据（一问一句，同一句只算第一处）；老账本没有这块 → 整块不显示。 -->
      <template v-if="aesRows().length">
        <div class="rl-aes-head">
          <b>审美五问</b>
          <span class="meta">
            {{ detail.aesthetic_complete ? "五问齐全" : "有未举证的问题" }}
            <template v-if="detail.aesthetic?.mean != null"> · 均分 {{ detail.aesthetic.mean }}</template>
          </span>
        </div>
        <div v-for="q in aesRows()" :key="q.code" class="rl-dim" :class="scoreCls(q.rec.score)">
          <div class="rl-dim-head">
            <b>{{ q.rec.label || q.label }}</b>
            <span class="rl-score">{{ q.rec.verified ? q.rec.score + " / 10" : "未计分" }}</span>
            <span class="meta" v-if="!q.rec.verified">{{ q.rec.reason }}</span>
          </div>
          <blockquote v-if="q.rec.quote" class="rl-quote">{{ q.rec.quote }}</blockquote>
          <div class="meta" v-if="q.rec.note">{{ q.rec.note }}</div>
        </div>
      </template>
    </div>
    <div v-else-if="detailErr" class="meta" style="padding:8px 0;">{{ detailErr }}</div>
  </div>
</template>

<style scoped>
.rl-table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 12.5px; }
.rl-table th {
  text-align: left; font-weight: 600; color: var(--muted);
  padding: 6px 8px; border-bottom: 1px solid var(--border); white-space: nowrap;
}
.rl-table td { padding: 6px 8px; border-bottom: 1px dashed var(--border); }
.rl-table tbody tr { cursor: pointer; }
.rl-table tbody tr:hover { background: var(--field-hover); }
.rl-table tbody tr.on { background: var(--field-on); }
.rl-ch { font-weight: 600; }
.rl-ok { color: var(--ok); }
.rl-warn { color: var(--warn); }
.rl-bad { color: var(--bad); font-weight: 600; }
.rl-unverified { color: var(--muted); opacity: 0.7; font-size: 11.5px; }

.rl-block-head {
  display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap;
  margin: 14px 0 2px;
}
.rl-split { margin-top: 16px; padding-top: 10px; border-top: 1px dashed var(--border); }
.rl-metrics {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 8px; margin: 8px 0;
}
.rl-metric {
  border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px;
  background: var(--field-dim);
}
.rl-metric-label { font-size: 12px; color: var(--muted); }
.rl-metric-value { font-size: 18px; font-weight: 600; line-height: 1.4; }
.rl-need-row { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; margin: 4px 0; }

.rl-detail { margin-top: 14px; border-top: 1px solid var(--border); padding-top: 10px; }
.rl-aes-head {
  display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap;
  margin: 14px 0 2px; padding-top: 10px; border-top: 1px dashed var(--border);
}
.rl-dim {
  border-left: 3px solid var(--border);
  padding: 7px 12px; margin: 7px 0;
  background: var(--field-dim); border-radius: 6px;
}
.rl-dim.rl-ok { border-left-color: var(--ok); }
.rl-dim.rl-warn { border-left-color: var(--warn); }
.rl-dim.rl-bad { border-left-color: var(--bad); }
.rl-dim-head { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
.rl-score { font-weight: 600; }
.rl-quote {
  margin: 6px 0 0; padding: 6px 10px; font-size: 12.5px; line-height: 1.7;
  border-left: 2px solid var(--border); background: var(--field-inset);
  white-space: pre-wrap; word-break: break-word;
}
</style>
