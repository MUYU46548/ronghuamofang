<script setup>
/* BranchSamplePanel —— 分支样例选向工作台（第 4 件 · 选项可视化，2026-10-18）
 *
 * 为什么存在：用户反馈“竞品有选项可视化，分支样例方向直观可选，显然根本没做进
 * 绒花墨坊里”。此前 stage4 只吃 5 个骨架字段，“这一章该用什么基调写”**没有
 * 任何入口** —— 方向是隐含的、不可比的、也不能被用户拍板。
 *
 * 与定向改写（FieldRefinePanel）的分工：那个改**章大纲的某个字段**（结构层），
 * 这个定**整章的写作方向**（基调/视角/节奏）。两者的产物都走同一条落地协议：
 * 落提案 → 进“审核”页签待审 → 用户批准才写回。**不直接写稿**。
 *
 * 挂载位置：分章页签“选写作方向”按钮展开的抽屉里 —— 用户正看着章大纲，
 * 这是决定“这章怎么写”最自然的时机。
 *
 * 关键交互：三个方向**并排对比**（不是下拉），因为“直观可选”的前提是能一眼
 * 看出差别；选定后可就地改，提交的是编辑框里的文本。 */
import { ref, watch, onMounted } from "vue";
import { API } from "./apiBase.js";

const props = defineProps({
  chapter: { type: Number, default: 0 },
  open: { type: Boolean, default: false },
});
const emit = defineEmits(["close", "say"]);

const KINDS = [
  { key: "tone", label: "基调" },
  { key: "pov", label: "视角" },
  { key: "pace", label: "节奏" },
];
const KIND_HINT = {
  tone: "克制隐忍 / 外放热烈 / 冷峻疏离",
  pov: "主角内聚焦 / 全知俯瞰 / 旁观者侧写",
  pace: "密集聚焦单场景 / 舒缓铺陈多线 / 快慢交替",
};

const kind = ref("tone");
const note = ref("");
const samples = ref([]);
const chosenIdx = ref(-1);
const editedText = ref("");
const generating = ref(false);
const submitting = ref(false);
const meta = ref(null);
const existing = ref([]);        // 这一章已选定的方向（读回显示）
const loadingExisting = ref(false);
const toast = ref("");
let toastTimer = null;

function say(msg) {
  toast.value = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toast.value = ""), 5000);
}

async function apiCall(path, method = "GET", body = null) {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  if (body) opt.body = JSON.stringify(body);
  const r = await fetch(API + path, opt);
  let data = {};
  try { data = await r.json(); } catch (e) { /* 空响应 */ }
  return { status: r.status, data };
}

/* 读这一章已经选过哪些方向（面板打开时刷新一次） */
async function loadExisting() {
  if (!props.chapter) { existing.value = []; return; }
  loadingExisting.value = true;
  try {
    const r = await apiCall("/outline/sample/current", "POST", { chapter: props.chapter });
    if (r.status === 200 && r.data.ok) existing.value = r.data.samples || [];
    else existing.value = [];
  } catch (e) {
    existing.value = [];
  } finally {
    loadingExisting.value = false;
  }
}

async function pollJob(jobId, tries = 150) {
  for (let i = 0; i < tries; i++) {
    await new Promise(r => setTimeout(r, 2000));
    const r = await apiCall("/jobs/" + jobId);
    if (r.status !== 200) { say("查询任务状态失败"); return null; }
    if (r.data.state !== "running") return r.data;
  }
  say("任务仍在运行，稍后刷新看看");
  return null;
}

async function generate() {
  if (!props.chapter) { say("先在左侧选一章"); return; }
  if (generating.value) return;
  generating.value = true;
  samples.value = [];
  chosenIdx.value = -1;
  editedText.value = "";
  meta.value = null;
  try {
    const r = await apiCall("/outline/sample/candidates", "POST", {
      chapter: props.chapter, kind: kind.value, note: note.value,
    });
    if (r.status !== 202) { say(r.data.error || "提交失败"); return; }
    const done = await pollJob(r.data.job_id);
    if (done && done.state === "ok") {
      const p = done.result || {};
      samples.value = p.samples || [];
      meta.value = {
        cost_yuan: p.cost_yuan, model: p.model,
        tokens_in: p.tokens_in, tokens_out: p.tokens_out,
      };
      say(`拿到 ${samples.value.length} 个方向 —— 并排对比，挑一个`);
    } else if (done && done.state === "error") {
      say(done.error || done.result?.error || "生成失败");
    }
  } catch (e) {
    say("生成失败: " + (e.message || e));
  } finally {
    generating.value = false;
  }
}

function pick(i) {
  chosenIdx.value = i;
  editedText.value = samples.value[i].text || "";
}

async function submit() {
  const text = editedText.value.trim();
  if (!text) { say("先挑一个方向"); return; }
  if (submitting.value) return;
  submitting.value = true;
  try {
    const s = chosenIdx.value >= 0 ? samples.value[chosenIdx.value] : null;
    const r = await apiCall("/outline/sample/submit", "POST", {
      chapter: props.chapter, kind: kind.value,
      label: s ? (s.label || "") : "",
      text, why: s ? (s.why || "") : "", note: note.value,
    });
    if (r.status === 200 && r.data.ok) {
      say("已落提案待审 —— 去“审核”页签批准后才会进这章的写作参照");
      samples.value = [];
      chosenIdx.value = -1;
      editedText.value = "";
      await loadExisting();
      emit("say", "sample_submitted");
    } else {
      say(r.data.error || "提交失败");
    }
  } catch (e) {
    say("提交失败: " + (e.message || e));
  } finally {
    submitting.value = false;
  }
}

watch(() => props.open, (v) => { if (v) loadExisting(); });
onMounted(() => { if (props.open) loadExisting(); });
</script>

<template>
  <div v-if="open" class="bs-wrap">
    <div class="bs-head">
      <h3>选写作方向 · 第 {{ chapter }} 章</h3>
      <button class="mini" @click="emit('close')">收起</button>
    </div>
    <div class="bs-hint">
      同一章出几个<strong>方向明显不同</strong>的开头，先比“同一件事，三种写法”再动笔。
      <strong>不直接写稿</strong>：提交后进“审核”页签待审，你批准才成为这章的写作参照。
    </div>

    <div class="bs-controls">
      <label>方向维度</label>
      <select v-model="kind" class="bs-select">
        <option v-for="k in KINDS" :key="k.key" :value="k.key">{{ k.label }}</option>
      </select>
      <span class="bs-kind-hint">{{ KIND_HINT[kind] }}</span>
    </div>

    <div class="bs-note">
      <label>你的意见（可选）</label>
      <textarea v-model="note" rows="2"
                placeholder="例如：这章气氛要压下来，别写得太热闹（留空 = 按大纲最自然的三种可能分叉）"></textarea>
    </div>

    <div class="bs-actions">
      <button class="mini primary" :disabled="generating || !chapter" @click="generate">
        {{ generating ? "生成中…（约 30-90 秒）" : "生成方向样例" }}
      </button>
      <span v-if="meta" class="bs-meta">
        {{ meta.model }} · 约 {{ (meta.cost_yuan || 0).toFixed(4) }} 元
      </span>
    </div>

    <!-- 并排对比：三张卡片横向排，差别一眼可见 -->
    <div v-if="samples.length" class="bs-cands">
      <div class="bs-label">方向 {{ samples.length }} 个（并排对比，点卡片选定）</div>
      <div class="bs-card-row">
        <div v-for="(s, i) in samples" :key="i"
             class="bs-card" :class="{ on: chosenIdx === i }" @click="pick(i)">
          <div class="bs-card-head">
            <span class="bs-card-no">{{ s.label || "方向 " + (i + 1) }}</span>
            <span v-if="s.why" class="bs-card-why">{{ s.why }}</span>
          </div>
          <div class="bs-card-text">{{ s.text }}</div>
        </div>
      </div>

      <div class="bs-edit">
        <div class="bs-label">选中的样例（可就地改，提交的是这里的文本）</div>
        <textarea v-model="editedText" rows="5"
                  placeholder="点上面某张卡片，或直接在这里写"></textarea>
      </div>

      <div class="bs-actions">
        <button class="mini primary" :disabled="submitting || !editedText.trim()" @click="submit">
          {{ submitting ? "提交中…" : "提交为写作参照（进待审队列）" }}
        </button>
        <button class="mini" @click="samples = []; chosenIdx = -1; editedText = ''">
          清空
        </button>
      </div>
    </div>

    <!-- 已选定的方向（读回） -->
    <div v-if="existing.length" class="bs-existing">
      <div class="bs-label">这一章已选定的方向（写作时会作为参照注入）</div>
      <div v-for="(e, i) in existing" :key="i" class="bs-ex-item">
        <div class="bs-ex-head">
          <span class="bs-ex-title">{{ e.title }}</span>
          <span v-if="e.kind" class="bs-ex-kind">{{ e.kind }}</span>
        </div>
        <div class="bs-ex-body">{{ e.body }}</div>
      </div>
    </div>

    <div v-if="toast" class="bs-toast">{{ toast }}</div>
  </div>
</template>

<style scoped>
.bs-wrap { margin: 10px 0 14px; padding: 12px 14px; border: 1px solid var(--border);
           border-radius: 8px; background: var(--bg); }
.bs-head { display: flex; align-items: center; justify-content: space-between;
           gap: 10px; margin-bottom: 4px; }
.bs-head h3 { margin: 0; font-size: 15px; }
.bs-hint { font-size: 12px; color: var(--muted); line-height: 1.6; margin-bottom: 12px; }
.bs-controls { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
               margin-bottom: 10px; }
.bs-controls label { font-size: 13px; color: var(--muted); }
.bs-select { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px;
             font-size: 13px; background: var(--bg); }
.bs-kind-hint { font-size: 12px; color: var(--muted); }
.bs-note { margin-bottom: 10px; }
.bs-note label { display: block; font-size: 13px; color: var(--muted); margin-bottom: 4px; }
.bs-note textarea, .bs-edit textarea {
  width: 100%; padding: 8px 10px; border: 1px solid var(--border); border-radius: 6px;
  font-size: 13px; font-family: inherit; background: var(--bg); resize: vertical;
  line-height: 1.6;
}
.bs-actions { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.bs-meta { font-size: 12px; color: var(--muted); }
.bs-label { font-size: 12px; color: var(--muted); margin-bottom: 6px; }
.bs-cands { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border); }
/* 三列并排 —— 横向滚动兜底，窄窗口不会挤成一条 */
.bs-card-row { display: flex; gap: 10px; margin-bottom: 12px; overflow-x: auto;
               padding-bottom: 4px; }
.bs-card { flex: 1 1 0; min-width: 240px; padding: 10px 12px;
           border: 1px solid var(--border); border-radius: 8px; cursor: pointer;
           transition: border-color .15s, background .15s; }
.bs-card:hover { border-color: var(--accent); }
.bs-card.on { border-color: var(--accent); background: var(--accent-soft); }
.bs-card-head { display: flex; flex-direction: column; gap: 2px; margin-bottom: 8px; }
.bs-card-no { font-size: 13px; font-weight: 600; color: var(--accent); }
.bs-card-why { font-size: 12px; color: var(--muted); }
.bs-card-text { font-size: 13px; line-height: 1.7; white-space: pre-wrap;
                max-height: 260px; overflow-y: auto; }
.bs-edit { margin-bottom: 12px; }
.bs-existing { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--border); }
.bs-ex-item { padding: 8px 10px; border: 1px solid var(--border); border-radius: 6px;
              margin-bottom: 8px; }
.bs-ex-head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 4px; }
.bs-ex-title { font-size: 13px; font-weight: 600; }
.bs-ex-kind { font-size: 11px; color: var(--muted); }
.bs-ex-body { font-size: 12px; line-height: 1.6; white-space: pre-wrap; color: var(--muted);
              max-height: 120px; overflow-y: auto; }
/* toast 放常规流末尾，不用 sticky —— sticky 在面板高于视口时会浮在卡片上方 */
.bs-toast { margin-top: 12px; padding: 10px 14px; background: var(--accent-soft);
            border: 1px solid var(--accent); border-radius: 8px; font-size: 13px; }
</style>
