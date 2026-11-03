<template>
  <details class="discuss-box" :open="threads.length > 0">
    <summary>
      讨论（{{ threads.length }} 条 · {{ pending }} 条待定）
      <span v-if="!threads.length" class="muted">—— 讨论的结论会进这一章的写作提示词</span>
    </summary>

    <div class="discuss-row">
      <label>第
        <input v-model.number="chapter" type="number" min="1" class="discuss-no" @change="load" />
        章</label>
      <button class="mini" @click="load" title="读这一章的讨论">刷新</button>
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
                    placeholder="这一轮说什么（AI 的建议 / 作者的取舍）"></textarea>
          <div class="discuss-actions">
            <button class="mini primary" @click="say('author')" :disabled="!sayText.trim()">以作者身份说</button>
            <button class="mini" @click="say('ai')" :disabled="!sayText.trim()">以 AI 身份说</button>
            <span class="muted">轮次只追加，不改写历史。</span>
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
import { computed, ref, watch } from "vue";

const props = defineProps({
  api: { type: Function, required: true },
  /* 章号列表（App 传 chapters，形如 [{n:1,...}] 或 [1,2,3]） */
  chapters: { type: Array, default: () => [] },
  defaultChapter: { type: Number, default: 1 },
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

const pending = computed(() => threads.value.filter((t) => t.status !== "decided").length);

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

async function load() {
  msg.value = "";
  try {
    const d = payload(await props.api(`/discuss/list?chapter=${chapter.value}`));
    threads.value = d.threads || [];
    if (picked.value && !threads.value.some((t) => t.topic === picked.value)) {
      picked.value = "";
      text.value = "";
    }
  } catch (e) {
    msg.value = "读讨论失败：" + e.message;
  }
}

async function pick(t) {
  picked.value = t.topic;
  try {
    const d = payload(await props.api(
      `/discuss/read?chapter=${chapter.value}&topic=${encodeURIComponent(t.topic)}`));
    text.value = d.text || "";
  } catch (e) {
    text.value = "读不到：" + e.message;
  }
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
  try {
    payload(await props.api("/discuss/say", "POST",
      { chapter: chapter.value, topic: picked.value, role, text: sayText.value.trim() }));
    sayText.value = "";
    await pick({ topic: picked.value });
    await load();
    msg.value = role === "ai" ? "已追加一轮（AI）" : "已追加一轮（作者）";
  } catch (e) {
    msg.value = "追加失败：" + e.message;
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
.discuss-actions { display: flex; gap: 8px; align-items: center; margin-top: 6px; }
.discuss-msg { margin-top: 6px; font-size: 12px; color: var(--muted); }
.muted { color: var(--muted); font-size: 12px; }
@media (max-width: 900px) { .discuss-body { grid-template-columns: 1fr; } }
</style>
