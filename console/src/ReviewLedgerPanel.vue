<script setup>
/**
 * 七维评审账本面板（2026-10-20，台账二·「七维评审 + 原文举证」）。
 *
 * 数据源：GET /review/ledger（只读；?chapter=N 取单章明细）。
 * 账本由 stage5 每批落盘（data/review/NN.json），七维分数**必须**有原文举证
 * 才算分 —— 这条判据由 `utils/review_ledger` 在落盘时执行，界面只负责显示
 * 「哪一维没算分、为什么」。
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

async function load() {
  busy.value = true;
  err.value = "";
  try {
    const r = await fetch(API + "/review/ledger");
    const d = await r.json().catch(() => ({}));
    if (r.status === 200 && d.ok) {
      data.value = d;
    } else {
      data.value = null;
      err.value = d.error || ("读取失败：HTTP " + r.status);
    }
  } catch (e) {
    err.value = String(e);
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

onMounted(load);
defineExpose({ load });
</script>

<template>
  <div class="card">
    <div class="card-head">
      <h3>七维评审账本</h3>
      <span class="spacer"></span>
      <span class="meta" v-if="rows.length">
        完整举证 {{ completeCount }} / {{ rows.length }} 章
        <template v-if="sum.mean != null"> · 均分 {{ sum.mean }}</template>
      </span>
      <button class="mini" :disabled="busy" @click="load">{{ busy ? "读取中…" : "刷新" }}</button>
    </div>
    <div class="meta">
      stage5 的逻辑检查结论：七维逐维打分，<b>每一维都必须引用本章原文里真实存在的句子</b>，
      对不上原文的那一维<b>不计分</b>（不是扣分）。点章节看举证与未举证原因。
    </div>

    <div v-if="err" class="empty" style="padding:14px 0;">{{ err }}</div>
    <div v-else-if="!data" class="meta" style="padding:10px 0;">读取中…</div>
    <div v-else-if="!rows.length" class="meta" style="padding:10px 0;">
      还没有账本 —— 跑过 stage5（逻辑检查）之后每章自动生成。
    </div>
    <template v-else>
      <table class="rl-table">
        <thead>
          <tr>
            <th>章</th>
            <th v-for="d in dims" :key="d.code">{{ d.label }}</th>
            <th>均分</th>
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
          </tr>
        </tbody>
      </table>
      <div class="meta" style="margin-top:6px;">
        「未举证」= 该维的引用在本章原文里找不到（或引得太短/分数越界），按判据不计分。
      </div>
    </template>

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

.rl-detail { margin-top: 14px; border-top: 1px solid var(--border); padding-top: 10px; }
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
