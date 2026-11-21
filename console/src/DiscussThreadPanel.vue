<template>
  <details class="discuss-box" :open="threads.length > 0">
    <summary>
      讨论（{{ threads.length }} 条 · {{ pending }} 条待定）
      <span v-if="!threads.length" class="muted">—— 讨论的结论会进这一章的写作提示词</span>
    </summary>

    <div class="discuss-row">
      <label>第
        <input v-model.number="chapter" type="number" min="1" class="discuss-no" @change="switchChapter" />
        章</label>
      <button class="mini" @click="load" title="读这一章的讨论">刷新</button>
      <button class="mini" v-if="picked" @click="compress" :disabled="sugBusy"
              title="把很早的轮次压成摘要（只动给模型看的那份会话，讨论稿原文一个字不改）">
        压缩老轮次
      </button>
      <button class="mini" @click="openNew" :disabled="!chapter || !newTopic.trim()"
              title="开一个新讨论（只写 data/discuss/，不动正文）">开题</button>
      <input v-model="newTopic" class="discuss-topic" placeholder="讨论主题（例：结局要不要开信）" />
    </div>

    <div class="discuss-body">
      <ul class="discuss-list">
        <li v-for="t in threads" :key="t.topic" :class="{ cur: t.topic === picked }"
            @click="pick(t)">
          <span class="pill" :class="t.status === 'decided' ? 'st-ok' : 'st-gate'">
            {{ t.status === "decided" ? "已定稿" : "待定" }}
          </span>
          {{ t.topic }}
          <span class="muted">{{ t.rounds || 0 }} 轮</span>
        </li>
        <li v-if="!threads.length" class="muted">这一章还没有讨论。</li>
      </ul>

      <div class="discuss-main">
        <pre v-if="text" class="discuss-text">{{ text }}</pre>
        <div v-else class="muted">选左边一条讨论，或先开题。</div>

        <div v-if="picked" class="discuss-say">
          <textarea v-model="sayText" rows="3"
                    @input="keepSayDraft"
                    placeholder="这一轮说什么（先存草稿，点下面的按钮才进讨论）；让 AI 说时，这里就是给它这一轮的提示"></textarea>
          <div class="discuss-actions">
            <button class="mini primary" @click="aiTurn" :disabled="aiBusy && !aiJob"
                    :class="{ 'stop-ai': aiBusy }"
                    :title="aiBusy ? '立刻掐断这一轮（已生成的不会记进讨论）' : 'AI 按上面的对话与正文说一轮（会花钱）'">
              {{ aiBusy ? "让 AI 闭嘴" : "让 AI 说这一轮" }}
            </button>
            <button class="mini" @click="say('author')" :disabled="!sayText.trim() || aiBusy">以作者身份说</button>
            <button class="mini" @click="say('ai')" :disabled="!sayText.trim() || aiBusy"
                    title="手写一轮 AI 发言（AI 没接上引擎时用；正常路径是上面的按钮）">以 AI 身份手写</button>
            <span v-if="sayDraftPending" class="pill draft">草稿，还没说出去</span>
            <!-- 2026-11-21（问题表 P2-8）：这是状态说明，不是动作 —— 原来和四个按钮
                 挤在同一行，看着像第五个按钮。独占一行。 -->
            <span class="muted note-full">轮次只追加，不改写历史。</span>
          </div>
        </div>

        <!-- AI 正在写：这段**还不是讨论稿**（被停/被截断都不落盘）。
             为什么要边写边显示：作者要能判断"它跑偏了，现在就掐"。 -->
        <div v-if="aiPreview || aiNote" class="discuss-ai-live">
          <div class="ai-live-head">
            <span class="pill ai-live">AI 正在说</span>
            <span class="muted">还没记进讨论 —— 停下来或写坏了都不会留下</span>
          </div>
          <pre v-if="aiPreview" class="discuss-ai-preview">{{ aiPreview }}</pre>
          <div v-if="aiNote" class="muted">{{ aiNote }}</div>
        </div>

        <!-- AI 说"该改哪里"（C5/C14，2026-11-16）：讨论原来只停在"聊聊"，AI 在
             .ai.json 里给出 action 也没有任何东西读它。这里把建议摊出来，
             但**不自动动手** —— 点"按这条生成候选"才进候选区，正文要再点采用。
             锚点漂移（正文改过、段号对不上）在按钮之前就说清，不让作者点了才报错。 -->
        <div v-if="picked && suggestions.length" class="discuss-sugs">
          <div class="sug-head">
            <b>AI 说该改的地方（{{ suggestions.length }}）</b>
            <span class="muted">只是建议：点"按这条生成候选"才会算一遍，正文要你再点采用才换。</span>
          </div>
          <div v-for="s in suggestions" :key="s.id" class="sug-item"
               :class="{ 'sug-bad-item': s.anchor && s.anchor.ok === false }">
            <span class="pill" :class="s.kind === 'rewrite_chapter' ? 'st-gate' : ''">
              {{ sugKind(s.kind) }}
            </span>
            <span class="sug-para">
              {{ s.anchor && s.anchor.ok !== false && typeof s.anchor.index === 'number'
                 ? "第 " + (s.anchor.index + 1) + " 段" : "（定位不到）" }}
            </span>
            <span v-if="s.target && s.target.quote" class="sug-quote">
              “{{ truncate(s.target.quote, 36) }}”
            </span>
            <span class="muted">{{ truncate(s.why, 80) }}</span>
            <span v-if="s.anchor && s.anchor.ok === false" class="sug-bad">
              ⚠ {{ s.anchor.why }}
            </span>
            <span v-else-if="s.anchor && s.anchor.drift" class="sug-drift">
              {{ s.anchor.why }}
            </span>
            <span class="spacer"></span>
            <button class="mini primary" :disabled="sugBusy || (s.anchor && s.anchor.ok === false)"
                    :title="s.anchor && s.anchor.ok === false
                      ? '这一段已经不在原位 —— 重新开一条讨论，别拿旧段号改' 
                      : '按这条建议生成候选（正文不动）'"
                    @click="applySug(s)">
              {{ pendingRewrite && pendingRewrite.id === s.id ? "确认重写整章" : "按这条生成候选" }}
            </button>
            <!-- 讨论 → 批注（2026-11-17）：不调模型、不改正文，先把“这里该改”记到段落上，
                 之后在批注列表里点“改这一处”走同一条读改链。 -->
            <button class="mini" :disabled="sugBusy || (s.anchor && s.anchor.ok === false)
                                          || s.status === 'annotated'"
                    :title="s.status === 'annotated'
                      ? '已落成批注（到“章节 → 段落批注”里看）'
                      : '落成批注：不调模型、不改正文；之后在批注列表点“改这一处”'"
                    @click="annotateSug(s)">
              {{ s.status === 'annotated' ? "已落成批注" : "落成批注" }}
            </button>
            <button class="mini" :disabled="sugBusy" @click="dismissSug(s)">不用</button>
          </div>
        </div>

        <details v-if="picked" class="discuss-decide">
          <summary>定稿（作者拍板 → 变成写作约束）</summary>
          <p class="muted">至少写一条；一条都没有不算结论。定稿晚于正文时，这一章会被判待重写。</p>
          <textarea v-model="dec.must" rows="2" placeholder="必须发生（每行一条）"></textarea>
          <textarea v-model="dec.must_not" rows="2" placeholder="绝不发生（每行一条）"></textarea>
          <textarea v-model="dec.voice" rows="2" placeholder="人物口吻（每行一条，如：叶清和：短句，不解释情绪）"></textarea>
          <textarea v-model="dec.plant" rows="1" placeholder="本章要埋的伏笔（每行一条）"></textarea>
          <textarea v-model="dec.payoff" rows="1" placeholder="本章要揭的伏笔（每行一条）"></textarea>
          <textarea v-model="dec.summary" rows="2" placeholder="一句话结论（可空）"></textarea>
          <button class="mini primary" @click="decide" :disabled="deciding">定稿</button>
        </details>
      </div>
    </div>
    <div v-if="msg" class="discuss-msg">{{ msg }}</div>
  </details>
</template>

<script setup>
import { computed, ref, watch, onUnmounted } from "vue";
import { loadDraft, saveDraft, clearDraft, hasDraft, draftKey } from "./draftStore.js";
import { API } from "./apiBase.js";

const props = defineProps({
  api: { type: Function, required: true },
  /* 章号列表（App 传 chapters，形如 [{n:1,...}] 或 [1,2,3]） */
  chapters: { type: Array, default: () => [] },
  defaultChapter: { type: Number, default: 1 },
  /* C16（2026-11-16）：判定的缺陷卡点"就这条开讨论"时带过来的焦点：
     {chapter, topic, question, hint, at}。at 是时间戳 —— 同一条再点一次也要重新定位。 */
  focus: { type: Object, default: null },
});

const chapter = ref(props.defaultChapter || 1);
const threads = ref([]);
const picked = ref("");
const text = ref("");
const newTopic = ref("");
const sayText = ref("");
const msg = ref("");
const deciding = ref(false);
const dec = ref({ must: "", must_not: "", voice: "", plant: "", payoff: "", summary: "" });

/* ---------- AI 说一轮（C1/C18，2026-11-16） ----------
   为什么要这一整块：原来那个"以 AI 身份说"是**作者替 AI 打字**（`say('ai')`
   只是 POST 一个字符串）。现在真正的路径是 POST /discuss/ai_turn → job →
   SSE 看它边写边说，/stop 立刻掐断。
   三条状态规则：
   1. aiPreview 里的字**还不是讨论稿**（被停/被截断都不落盘），所以界面上
      必须明说"还没记进讨论"，不能让作者以为已经存了；
   2. 正在说的时候不许切主题/切章 —— 否则预览会张冠李戴；
   3. 停止按钮与"开始"是同一个按钮（同一个位置），不让人去找第二个开关。 */
const aiBusy = ref(false);
const aiJob = ref("");
const aiRaw = ref("");            // 模型原始输出（可能是 JSON）
const aiNote = ref("");
const aiStoppable = ref(true);    // 当前引擎能不能"中途掐断"（hermes 整块返回 → false）

/* ---------- AI 的建议 → 候选区（C5/C14，2026-11-16） ----------
   三个状态必须分清，别混：
   1. 建议（suggestions）：AI 说"这一段该改" —— 零成本，不会自己动手；
   2. 候选（para_pending）：作者点了"按这条生成候选"之后，模型算出来的改法 ——
      正文仍没动，要作者点"采用"（在段落精修页签里）；
   3. 正文：只有"采用"才变。
   整章重写是唯一代价大的一条，所以点第一次只换按钮文字，点第二次才真发。 */
const suggestions = ref([]);
const sugBusy = ref(false);
const pendingRewrite = ref(null);   // 已点过一次、等第二次确认的整章重写建议

function truncate(s, n) {
  const t = String(s || "").replace(/\s+/g, " ").trim();
  return t.length > n ? t.slice(0, n) + "…" : t;
}

function sugKind(kind) {
  return ({ refine_paragraph: "改这一段", suggest_edit: "建议改", rewrite_chapter: "整章重写" })[kind]
    || String(kind || "建议");
}

async function loadSuggestions() {
  if (!picked.value) { suggestions.value = []; return; }
  try {
    const d = payload(await props.api(
      `/discuss/actions?chapter=${chapter.value}&topic=${encodeURIComponent(picked.value)}`));
    suggestions.value = d.items || [];
  } catch (e) {
    suggestions.value = [];       // 读不到建议不该打断"看讨论"
  }
}

async function applySug(s) {
  if (!picked.value) return;
  // 整章重写：第一次点击只换文案，第二次才带 confirm —— 破坏性操作不许一步到位
  if (s.kind === "rewrite_chapter") {
    if (!pendingRewrite.value || pendingRewrite.value.id !== s.id) {
      pendingRewrite.value = s;
      msg.value = "整章重写会把旧稿换掉（先备份到 data/chapters/rewrite_backup/）。"
        + "确认就再点一次那个按钮。";
      return;
    }
  }
  sugBusy.value = true;
  try {
    const bodyObj = { chapter: chapter.value, topic: picked.value, id: s.id };
    if (s.kind === "rewrite_chapter") bodyObj.confirm = true;
    const r = await props.api("/discuss/actions/apply", "POST", bodyObj);
    const d = (r && r.data) || {};
    if (r && (r.status === 202 || r.status === 200)) {
      msg.value = d.message
        || (s.kind === "rewrite_chapter"
          ? "已开始重写整章（旧稿已备份）。"
          : "正在生成候选（只算不落，正文一个字不动）—— 算完到“精修”页签点“采用”。");
      pendingRewrite.value = null;
      await loadSuggestions();
    } else {
      msg.value = "没动手：" + (d.error || (r && r.status) || "未知原因");
    }
  } catch (e) {
    msg.value = "没动手：" + e.message;
  } finally {
    sugBusy.value = false;
  }
}

async function annotateSug(s) {
  if (!picked.value) return;
  sugBusy.value = true;
  try {
    const r = await props.api("/discuss/actions/annotate", "POST",
      { chapter: chapter.value, topic: picked.value, id: s.id });
    const d = (r && r.data) || {};
    if (r && r.status === 200 && d.ok) {
      msg.value = d.message || "已落成批注（正文没动）";
      await loadSuggestions();
    } else {
      msg.value = "没落成批注：" + (d.error || (r && r.status) || "未知原因");
    }
  } catch (e) {
    msg.value = "没落成批注：" + e.message;
  } finally {
    sugBusy.value = false;
  }
}

async function dismissSug(s) {
  if (!picked.value) return;
  sugBusy.value = true;
  try {
    payload(await props.api("/discuss/actions/dismiss", "POST",
      { chapter: chapter.value, topic: picked.value, id: s.id }));
    await loadSuggestions();
    msg.value = "已放弃这条建议（讨论稿与正文都没动）";
  } catch (e) {
    msg.value = "没放弃掉：" + e.message;
  } finally {
    sugBusy.value = false;
  }
}

/* C11：把很早的轮次压成摘要。只动给模型看的那份会话，讨论稿原文不改。 */
async function compress() {
  if (!picked.value) return;
  sugBusy.value = true;
  try {
    const r = await props.api("/discuss/compress", "POST",
      { chapter: chapter.value, topic: picked.value });
    const d = (r && r.data) || {};
    msg.value = d.message || (r && r.status === 200 ? "处理完了" : "没压缩：" + (d.error || r.status));
  } catch (e) {
    msg.value = "没压缩：" + e.message;
  } finally {
    sugBusy.value = false;
  }
}

/** 把 JSON 流里 `text` 字段的**已生成部分**抽出来给人看。
 *  为什么必须有这一步：模型输出的是 JSON，直接把 token 摊在屏幕上，
 *  作者看到的是 `{"text": "...", "stance":` —— 等于让他读协议。
 *  抽不出来就退回原文（至少有东西在动，比空白强）；抽出来的半截转义
 *  用宽松替换兜底，不为一个未闭合的 \u 把整段预览吞掉。 */
function previewHuman(raw) {
  const s = String(raw || "");
  const m = s.match(/"text"\s*:\s*"((?:[^"\\]|\\.)*)/);
  if (!m) return s;
  const frag = m[1];
  try {
    return JSON.parse('"' + frag + '"');
  } catch (e) {
    return frag.replace(/\\n/g, "\n").replace(/\\"/g, '"').replace(/\\\\/g, "\\");
  }
}

const aiPreview = computed(() => previewHuman(aiRaw.value));
let aiSource = null;

function closeStream() {
  if (aiSource) {
    try { aiSource.close(); } catch (e) { /* 已关闭 */ }
    aiSource = null;
  }
}

function connectStream(jobId) {
  closeStream();
  const es = new EventSource(API + "/stream/" + jobId);
  aiSource = es;
  es.onmessage = (ev) => {
    let m = {};
    try { m = JSON.parse(ev.data); } catch (e) { return; }
    if (m.type === "token") {
      aiRaw.value += m.text || "";
      return;
    }
    if (m.type !== "done") return;
    closeStream();
    aiBusy.value = false;
    aiJob.value = "";
    const st = m.status || "ok";
    const detail = m.result || "";
    aiRaw.value = "";
    aiNote.value = "";
    if (st === "ok") {
      msg.value = "AI 已说一轮。" + (detail && !detail.startsWith("AI 说了") ? detail : "");
      pick({ topic: picked.value });
      load(false);
    } else if (st === "stopped") {
      msg.value = "已停下 —— 这一轮没有记进讨论（残片留在 data/state/truncated/）。";
      load(false);
    } else {
      msg.value = "AI 这一轮失败了：" + (detail || "没有返回原因");
    }
  };
  es.onerror = () => {
    closeStream();
    aiBusy.value = false;
    aiJob.value = "";
    aiNote.value = "";
    msg.value = "与后台的连接断了（这一轮可能还在跑：刷新讨论看结果，别急着重来一次）。";
  };
}

async function stopAI() {
  if (!aiJob.value) return;
  if (!aiStoppable.value) {
    msg.value = "当前引擎一次只回整段 —— 这一轮停不下来，等它说完（说完才会记进讨论）。";
    return;
  }
  aiNote.value = "正在停…";
  try {
    const r = await props.api("/stop", "POST", { job_id: aiJob.value });
    if (!r || r.status !== 200) {
      msg.value = "停止请求没发出去：" + ((r && r.data && r.data.error) || (r && r.status) || "未知");
      aiNote.value = "";
    }
  } catch (e) {
    msg.value = "停止请求没发出去：" + e.message;
    aiNote.value = "";
  }
}

async function aiTurn() {
  if (aiBusy.value) { await stopAI(); return; }
  if (!picked.value) return;
  keepSayDraft();               // 作者草稿先存回去：这一轮只是给 AI 的提示，别丢
  aiBusy.value = true;
  aiJob.value = "";
  aiRaw.value = "";
  aiNote.value = "正在装配上下文并启动…";
  try {
    const r = await props.api("/discuss/ai_turn", "POST", {
      chapter: chapter.value,
      topic: picked.value,
      hint: sayText.value.trim(),
    });
    const d = (r && r.data) || {};
    // 自任务模式（engine: self）：两段式 —— 先出任务书，产物到位后再点一次收编。
    // 这不是失败，也不该按"AI 没开口"报错。
    if (r && r.status === 200 && d.mode === "self" && d.settled) {
      aiBusy.value = false;
      aiJob.value = "";
      aiRaw.value = "";
      aiNote.value = "";
      msg.value = d.message || "已经把它写的那一轮收编进讨论了。";
      pick({ topic: picked.value });
      load(false);
      return;
    }
    if (r && r.status === 202 && d.mode === "self") {
      aiBusy.value = false;
      aiJob.value = "";
      aiRaw.value = "";
      aiNote.value = "";
      msg.value = "自任务模式：任务书已写到 " + (d.task || "（见 data/state/tasks/self/discuss/）")
        + "（没有调用模型）。执行完把结果写到任务书里指定的路径，再点一次就会收编成一轮。";
      return;
    }
    if (!r || r.status !== 202) {
      throw new Error(d.error || d.error_kind || (r && r.status) || "提交失败");
    }
    aiJob.value = (r.data && r.data.job_id) || "";
    aiStoppable.value = (r.data && r.data.stoppable) !== false;
    aiNote.value = aiStoppable.value ? ""
      : "当前引擎一次只回整段 —— 这一轮要等它说完，中途停不下来。";
    connectStream(aiJob.value);
  } catch (e) {
    aiBusy.value = false;
    aiJob.value = "";
    aiNote.value = "";
    msg.value = "AI 没开口：" + e.message;
  }
}

onUnmounted(closeStream);


const pending = computed(() => threads.value.filter((t) => t.status !== "decided").length);

/* ---------- 发言草稿（P1-3，2026-11-15） ----------
   为什么按“章 + 主题”分开存：原来是一个共用的 sayText —— 在主题 A 写了一半，
   切到主题 B 它还在，一不小心就以 B 的名义说出去了。现在各自一份草稿，
   切回来能接着写，切换时先把上一份存回去。 */
function sayKey() {
  return draftKey("discuss", chapter.value, picked.value);
}

function restoreSay() {
  sayText.value = picked.value ? loadDraft(sayKey()).text : "";
}

function keepSayDraft() {
  if (picked.value) saveDraft(sayKey(), sayText.value);
}

const sayDraftPending = computed(() => !!picked.value && hasDraft(sayKey()));

/** 换章：先把这一章这份草稿存回去，再读新章。 */
function switchChapter() {
  if (aiBusy.value) {
    msg.value = "AI 正在说这一轮 —— 先让它说完，或点“让 AI 闭嘴”。";
    return;
  }
  keepSayDraft();
  sayText.value = "";
  load();
}

function nums(list) {
  return (list || []).map((c) => (typeof c === "number" ? c : c && (c.n || c.chapter))).filter(Boolean);
}

/* 注意：App 的 api() 返回 {status, data}，不是裸 payload（2026-11-03 踩过，
   面板一开始把 r.threads 当结果 → 永远空列表）。 */
function payload(r) {
  if (!r || r.status !== 200) {
    const why = (r && r.data && (r.data.error || r.data.hint)) || (r && r.status) || "请求失败";
    throw new Error(String(why));
  }
  return r.data || {};
}

async function load(resetMsg = true) {
  // resetMsg=false 的用意（2026-11-16）：AI 轮结束时要点亮"已说一轮/已停下"这类
  // 提示，紧接着的 refresh 若无条件清 msg，作者就永远看不到它 —— 实测在真机验收里
  // 表现为"点了没反应"（其实后端做对了，只是提示被自己抹掉）。
  if (resetMsg) msg.value = "";
  try {
    const d = payload(await props.api(`/discuss/list?chapter=${chapter.value}`));
    threads.value = d.threads || [];
    if (picked.value && !threads.value.some((t) => t.topic === picked.value)) {
      picked.value = "";
      text.value = "";
      sayText.value = "";
      suggestions.value = [];
    }
  } catch (e) {
    msg.value = "读讨论失败：" + e.message;
  }
  if (picked.value) await loadSuggestions();
}

async function pick(t) {
  if (aiBusy.value) {
    msg.value = "AI 正在说这一轮 —— 先让它说完，或点“让 AI 闭嘴”。";
    return;
  }
  keepSayDraft();                    // 切走之前，先把当前主题的草稿存回去
  picked.value = t.topic;
  restoreSay();                      // 再把这一份草稿接回来（各自的互不串）
  try {
    const d = payload(await props.api(
      `/discuss/read?chapter=${chapter.value}&topic=${encodeURIComponent(t.topic)}`));
    text.value = d.text || "";
  } catch (e) {
    text.value = "读不到：" + e.message;
  }
  pendingRewrite.value = null;
  await loadSuggestions();
}

async function openNew() {
  if (!newTopic.value.trim()) return;
  try {
    payload(await props.api("/discuss/open", "POST",
      { chapter: chapter.value, topic: newTopic.value.trim() }));
    const t = newTopic.value.trim();
    newTopic.value = "";
    await load();
    picked.value = t;
    await pick({ topic: t });
    msg.value = "已开题：" + t;
  } catch (e) {
    msg.value = "开题失败：" + e.message;
  }
}

async function say(role) {
  if (!sayText.value.trim() || !picked.value) return;
  const key = sayKey();
  try {
    payload(await props.api("/discuss/say", "POST",
      { chapter: chapter.value, topic: picked.value, role, text: sayText.value.trim() }));
    clearDraft(key);                 // 只有真发出去了才清草稿
    sayText.value = "";
    await pick({ topic: picked.value });
    await load();
    msg.value = role === "ai" ? "已追加一轮（AI）" : "已追加一轮（作者）";
  } catch (e) {
    msg.value = "没发出去（草稿还留着）：" + e.message;
  }
}

function lines(s) {
  return String(s || "").split("\n").map((x) => x.trim()).filter(Boolean);
}

async function decide() {
  if (!picked.value) return;
  deciding.value = true;
  try {
    payload(await props.api("/discuss/decide", "POST", {
      chapter: chapter.value,
      topic: picked.value,
      must: lines(dec.value.must),
      must_not: lines(dec.value.must_not),
      voice: lines(dec.value.voice),
      foreshadow_plant: lines(dec.value.plant),
      foreshadow_payoff: lines(dec.value.payoff),
      summary: dec.value.summary,
    }));
    dec.value = { must: "", must_not: "", voice: "", plant: "", payoff: "", summary: "" };
    await load();
    await pick({ topic: picked.value });
    msg.value = "已定稿：结论会进这一章的写作提示词；正文比结论旧时该章会判待重写。";
  } catch (e) {
    msg.value = "定稿失败：" + e.message;
  } finally {
    deciding.value = false;
  }
}

watch(() => props.defaultChapter, (v) => {
  if (v) { chapter.value = v; load(); }
});

/* C16：判定缺陷卡点"就这条开讨论" → 切到那一章、开好主题（已存在就接着用）、
   把原句预填进输入框当"给 AI 的提示"。为什么预填的是 hint 而不是直接发出去：
   这一轮要花一次模型调用，发不发由作者决定（拍板 #2：踢一脚走一下）。 */
watch(() => props.focus, async (f) => {
  if (!f || !f.chapter || !f.topic) return;
  if (aiBusy.value) {
    msg.value = "AI 正在说这一轮 —— 先让它说完，或点“让 AI 闭嘴”，再切过来。";
    return;
  }
  keepSayDraft();
  chapter.value = Number(f.chapter) || chapter.value;
  await load(false);
  if (!threads.value.some((t) => t.topic === f.topic)) {
    try {
      payload(await props.api("/discuss/open", "POST",
        { chapter: chapter.value, topic: f.topic, question: f.question || "" }));
    } catch (e) {
      msg.value = "开题失败：" + e.message;
      return;
    }
    await load(false);
  }
  await pick({ topic: f.topic });
  if (f.hint) sayText.value = f.hint;
  msg.value = "已就这一条开好讨论（第 " + chapter.value + " 章）—— 先写你的看法，"
    + "或直接点“让 AI 说这一轮”。";
}, { deep: true });
load();
</script>

<style scoped>
.discuss-box { margin: 10px 0; border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; }
.discuss-box > summary { cursor: pointer; font-weight: 600; }
.discuss-row { display: flex; gap: 8px; align-items: center; margin: 8px 0; flex-wrap: wrap; }
.discuss-no { width: 64px; padding: 2px 6px; }
.discuss-topic { flex: 1 1 260px; padding: 4px 8px; }
/* min-width:0 + box-sizing：grid 子项默认 min-width:auto，textarea 的固有宽度会把
   整个页面撑出横向滚动条（2026-11-03 目检发现页面底部出现水平滚动条）。 */
.discuss-body { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 12px; }
.discuss-body > * { min-width: 0; }
.discuss-main { min-width: 0; }
.discuss-list, .discuss-text { max-width: 100%; }
.discuss-list { list-style: none; margin: 0; padding: 0; max-height: 260px; overflow: auto; }
.discuss-list li { padding: 4px 6px; cursor: pointer; border-radius: 6px; }
.discuss-list li.cur { background: var(--card); }
.discuss-text { max-height: 260px; overflow: auto; white-space: pre-wrap; font-size: 12px;
                background: var(--card); padding: 8px; border-radius: 6px; }
.discuss-say textarea, .discuss-decide textarea {
  width: 100%; box-sizing: border-box; margin-top: 6px; font-size: 12px; padding: 6px;
  resize: vertical; }
.discuss-actions { display: flex; gap: 8px; align-items: center; margin-top: 6px; flex-wrap: wrap; }
/* 停止态：同一个按钮换成“让 AI 闭嘴”，用 warn 色（不用红 —— 红留给失败）。
   位置不动：作者正在看它写的时候，手指已经在那个位置上了。 */
.discuss-actions button.stop-ai {
  color: var(--ink-warn);
  background: color-mix(in srgb, var(--warn) 22%, var(--field));
  border-color: var(--warn);
  font-weight: 600;
}
/* AI 正在写：这一段**还不是讨论稿**，所以用虚线框 + “还没记进讨论”的说明，
   与下面的正式讨论稿（.discuss-text）在视觉上分开。 */
.discuss-ai-live { margin-top: 8px; }
.ai-live-head { display: flex; gap: 8px; align-items: center; margin-bottom: 4px; flex-wrap: wrap; }
.pill.ai-live {
  color: var(--accent);
  background: var(--accent-soft);
  border: 1px solid var(--accent);
}
.discuss-ai-preview {
  margin: 0; max-height: 200px; overflow: auto; white-space: pre-wrap;
  font-size: 12px; line-height: 1.6; padding: 8px; border-radius: 6px;
  background: var(--card); border: 1px dashed var(--border);
}
/* 草稿标记：浅灰底那一档在浅色主题下太弱（目检 2026-11-15 提的），
   这是“没发出去”的状态，必须比辅助说明显眼，但不用红色（红色留给失败）。 */
.pill.draft {
  color: var(--ink-warn);
  background: color-mix(in srgb, var(--warn) 24%, var(--field));
  border: 1px solid var(--warn);
  font-weight: 600;
}
.discuss-msg { margin-top: 6px; font-size: 12px; color: var(--muted); }
.muted { color: var(--muted); font-size: 12px; }
/* AI 的建议（C5/C14）：与讨论稿视觉分开 —— 它不是"说过的话"，是"要做的事"。
   定位不到的条目标红并禁用按钮：宁可不让点，也不拿旧段号改错段落。 */
.discuss-sugs { margin-top: 10px; border-top: 1px dashed var(--border); padding-top: 8px; }
.sug-head { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; margin-bottom: 6px; }
.sug-item {
  display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap;
  font-size: 12px; padding: 5px 6px; border-radius: 6px; background: var(--card);
  margin-bottom: 4px;
}
.sug-bad-item { border: 1px solid var(--bad); }
.sug-para { font-weight: 600; }
.sug-quote { color: var(--accent); }
.sug-bad { color: var(--bad); }
.sug-drift { color: var(--ink-warn); }
.sug-item .spacer { flex: 1 1 auto; }
@media (max-width: 900px) { .discuss-body { grid-template-columns: 1fr; } }
</style>
