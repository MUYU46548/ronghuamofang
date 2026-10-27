<script setup>
/* FieldRefinePanel —— 定向改写工作台（第 3 件 · C 兜底，2026-10-18）
 *
 * 为什么存在：Hermes 不在场时讨论入口不能断。这里给一条**单轮**轻链路：
 * 选一个章大纲字段 → 写一句意见 → 一次调用产出 2-3 个候选 → 挑一个（可就地改）
 * → 落提案进“审核”页签待审队列 → 你批准才写回章大纲。
 *
 * 与段落级精修（ParagraphRefinePanel）的分工：那个改的是**章节正文**（data/chapters/），
 * 这个改的是**章大纲**（data/outline/chapters/）的一个字段。粒度都是“一处”，
 * 都不做整章重写 —— 2026-10-18 用户裁定“整章重写意义不大”。
 *
 * 与 Hermes 讨论（分章页签“讨论此章”）的分工：那是多轮、有工具、有记忆的重通道；
 * 这里是单轮、无工具的轻兜底。两者产物走同一条落地协议，你在同一个队列里审批。
 *
 * 红线：候选**不直接写稿**。submit 只落提案，写回发生在你点“批准”之后。 */
import { ref, onMounted } from "vue";
import { API } from "./apiBase.js";

const FIELD_DEFS = [
  { key: "title",     label: "章节标题" },
  { key: "events",    label: "核心事件" },
  { key: "roles",     label: "涉及角色" },
  { key: "function",  label: "章节功能" },
  { key: "carryover", label: "承接" },
  { key: "hook",      label: "钩子" },
];

const chapterN = ref(null);
const field = ref("events");
const note = ref("");
const currentValue = ref("");
const candidates = ref([]);
const chosenIdx = ref(-1);
const editedText = ref("");
const loadingValue = ref(false);
const generating = ref(false);
const submitting = ref(false);
const meta = ref(null);          // {cost_yuan, model, tokens_in, tokens_out}
const toast = ref("");
let toastTimer = null;

function say(msg) {
  toast.value = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toast.value = ""), 4500);
}

async function api(path, method = "GET", body = null) {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  if (body) opt.body = JSON.stringify(body);
  const r = await fetch(API + path, opt);
  let data = {};
  try { data = await r.json(); } catch (e) { /* 空响应 */ }
  return { status: r.status, data };
}

function fieldLabel(key) {
  const f = FIELD_DEFS.find(x => x.key === key);
  return f ? f.label : key;
}

/* 读当前字段值（切字段 / 输章号时预填，让用户知道在改什么） */
async function loadValue() {
  if (!chapterN.value || !field.value) return;
  loadingValue.value = true;
  candidates.value = [];
  chosenIdx.value = -1;
  meta.value = null;
  try {
    const r = await api("/outline/field/current", "POST", {
      chapter: chapterN.value, field: field.value,
    });
    if (r.status === 200 && r.data.ok) {
      currentValue.value = r.data.value || "";
    } else {
      currentValue.value = "";
      say(r.data.error || "读取失败");
    }
  } catch (e) {
    say("读取失败: " + (e.message || e));
  } finally {
    loadingValue.value = false;
  }
}

/* 生成候选（单轮 LLM，走后台 job —— 与段落精修同一套 start_job 约定） */
async function generate() {
  if (!chapterN.value) { say("先填章节号"); return; }
  if (generating.value) return;
  generating.value = true;
  candidates.value = [];
  chosenIdx.value = -1;
  meta.value = null;
  try {
    const r = await api("/outline/field/candidates", "POST", {
      chapter: chapterN.value, field: field.value, note: note.value,
    });
    if (r.status !== 202) { say(r.data.error || "提交失败"); return; }
    const done = await pollJob(r.data.job_id);
    if (done && done.state === "ok") {
      const p = done.result || {};
      candidates.value = p.candidates || [];
      meta.value = {
        cost_yuan: p.cost_yuan, model: p.model,
        tokens_in: p.tokens_in, tokens_out: p.tokens_out,
      };
      say(`拿到 ${candidates.value.length} 个候选 —— 挑一个，或就地改`);
    }
  } catch (e) {
    say("生成失败: " + (e.message || e));
  } finally {
    generating.value = false;
  }
}

async function pollJob(jobId, tries = 150) {
  for (let i = 0; i < tries; i++) {
    await new Promise(r => setTimeout(r, 2000));
    const r = await api("/jobs/" + jobId);
    if (r.status !== 200) { say("查询任务状态失败"); return null; }
    if (r.data.state !== "running") return r.data;
  }
  say("任务仍在运行，稍后刷新看看");
  return null;
}

function pick(i) {
  chosenIdx.value = i;
  editedText.value = candidates.value[i].text;   // 预填到编辑框，可就地改
}

/* 提交所选 → 落提案（不写稿；写回发生在“审核”页签批准之后） */
async function submit() {
  const text = editedText.value.trim();
  if (!text) { say("先挑一个候选或自己写内容"); return; }
  if (submitting.value) return;
  submitting.value = true;
  try {
    const why = chosenIdx.value >= 0 ? (candidates.value[chosenIdx.value].why || "") : "";
    const r = await api("/outline/field/submit", "POST", {
      chapter: chapterN.value, field: field.value, text,
      why, note: note.value,
    });
    if (r.status === 200 && r.data.ok) {
      say("已落提案待审 —— 去“审核”页签批准后才会写回章大纲");
      candidates.value = [];
      chosenIdx.value = -1;
      editedText.value = "";
      await loadValue();
    } else {
      say(r.data.error || "提交失败");
    }
  } catch (e) {
    say("提交失败: " + (e.message || e));
  } finally {
    submitting.value = false;
  }
}

onMounted(() => { /* 不自动加载：用户填了章号才有意义 */ });
</script>

<template>
  <div class="fr-wrap">
    <div class="fr-head">
      <h3>定向改写（候选对比）</h3>
      <div class="fr-hint">
        选一个章大纲字段 → 写句意见 → 挑一个候选。<strong>不直接写稿</strong>：
        提交后进“审核”页签待审，你批准才写回。Hermes 不在场时用这条轻链路。
      </div>
    </div>

    <div class="fr-controls">
      <label>章节号</label>
      <input type="number" min="1" max="999" v-model.number="chapterN"
             class="fr-input" placeholder="如 3"
             @keydown.enter="loadValue" />
      <label>字段</label>
      <select v-model="field" class="fr-select" @change="loadValue">
        <option v-for="f in FIELD_DEFS" :key="f.key" :value="f.key">
          {{ f.label }}
        </option>
      </select>
      <button class="mini" :disabled="loadingValue || !chapterN" @click="loadValue">
        {{ loadingValue ? "读取中…" : "读当前值" }}
      </button>
    </div>

    <div class="fr-note">
      <label>你的意见</label>
      <textarea v-model="note" rows="2"
                placeholder="例如：说服那段太生硬，该用更具体的旧创案例（留空 = 让它自己改进）"></textarea>
    </div>

    <div class="fr-actions">
      <button class="mini primary" :disabled="generating || !chapterN" @click="generate">
        {{ generating ? "生成中…（约 20-60 秒）" : "生成候选" }}
      </button>
      <span v-if="meta" class="fr-meta">
        {{ meta.model }} · 约 {{ (meta.cost_yuan || 0).toFixed(4) }} 元
      </span>
    </div>

    <!-- 当前值 -->
    <div v-if="currentValue" class="fr-current">
      <div class="fr-label">当前“{{ fieldLabel(field) }}”</div>
      <pre class="fr-pre">{{ currentValue }}</pre>
    </div>

    <!-- 候选卡片 -->
    <div v-if="candidates.length" class="fr-cands">
      <div class="fr-label">候选 {{ candidates.length }} 个（点卡片选中，选中后可改）</div>
      <div class="fr-cand-list">
        <div v-for="(c, i) in candidates" :key="i"
             class="fr-cand" :class="{ on: chosenIdx === i }"
             @click="pick(i)">
          <div class="fr-cand-head">
            <span class="fr-cand-no">候选 {{ i + 1 }}</span>
            <span v-if="c.why" class="fr-cand-why">{{ c.why }}</span>
          </div>
          <div class="fr-cand-text">{{ c.text }}</div>
        </div>
      </div>

      <div class="fr-edit">
        <div class="fr-label">
          选中的内容（可就地改，提交的是这里的文本）
        </div>
        <textarea v-model="editedText" rows="4"
                  placeholder="点上面某个候选，或直接在这里写"></textarea>
      </div>

      <div class="fr-actions">
        <button class="mini primary" :disabled="submitting || !editedText.trim()"
                @click="submit">
          {{ submitting ? "提交中…" : "提交为提案（进待审队列）" }}
        </button>
        <button class="mini" @click="candidates = []; chosenIdx = -1; editedText = ''">
          清空候选
        </button>
      </div>
    </div>

    <div v-if="toast" class="fr-toast">{{ toast }}</div>
  </div>
</template>

<style scoped>
.fr-wrap { position: relative; padding-bottom: 8px; }
.fr-head h3 { margin: 0 0 4px; font-size: 16px; }
.fr-hint { font-size: 12px; color: var(--muted); margin-bottom: 14px; line-height: 1.6; }
.fr-controls { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
.fr-controls label { font-size: 13px; color: var(--muted); }
.fr-input { width: 90px; padding: 6px 10px; border: 1px solid var(--border);
            border-radius: 6px; font-size: 13px; }
.fr-select { padding: 6px 10px; border: 1px solid var(--border);
             border-radius: 6px; font-size: 13px; background: var(--bg); }
.fr-note { margin-bottom: 10px; }
.fr-note label { display: block; font-size: 13px; color: var(--muted); margin-bottom: 4px; }
.fr-note textarea, .fr-edit textarea {
  width: 100%; padding: 8px 10px; border: 1px solid var(--border);
  border-radius: 6px; font-size: 13px; font-family: inherit;
  background: var(--bg); resize: vertical; line-height: 1.6;
}
.fr-actions { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.fr-meta { font-size: 12px; color: var(--muted); }
.fr-label { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
.fr-current { margin-bottom: 14px; }
.fr-pre { white-space: pre-wrap; word-break: break-word; font-size: 13px;
          line-height: 1.7; padding: 10px 12px; border: 1px solid var(--border);
          border-radius: 6px; background: var(--bg); margin: 0; }
.fr-cands { margin-top: 14px; padding-top: 14px; border-top: 1px solid var(--border); }
.fr-cand-list { display: flex; flex-direction: column; gap: 8px; margin-bottom: 12px; }
.fr-cand { padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px;
           cursor: pointer; transition: border-color .15s, background .15s; }
.fr-cand:hover { border-color: var(--accent); }
.fr-cand.on { border-color: var(--accent); background: var(--accent-soft); }
.fr-cand-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 6px; }
.fr-cand-no { font-size: 12px; font-weight: 600; color: var(--accent); }
.fr-cand-why { font-size: 12px; color: var(--muted); }
.fr-cand-text { font-size: 13px; line-height: 1.7; white-space: pre-wrap; }
.fr-edit { margin-bottom: 12px; }
/* toast 放在常规流末尾，不用 sticky —— sticky 在面板高于视口时会浮在候选
   卡片上方遮住正文（2026-10-18 视觉验收目检发现：候选 2 被 toast 盖住）。 */
.fr-toast { margin-top: 12px; padding: 10px 14px;
            background: var(--accent-soft); border: 1px solid var(--accent);
            border-radius: 8px; font-size: 13px; }
</style>
