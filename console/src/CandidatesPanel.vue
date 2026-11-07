<template>
  <div class="cand-panel">
    <div class="cand-head">
      <b>多写几版再挑（Best-of-N）</b>
      <select v-model.number="n" class="model-select" style="max-width: 140px;">
        <option v-for="c in chapters" :key="c.n" :value="c.n">第 {{ c.n }} 章</option>
      </select>
      <button class="mini" :disabled="busy" @click="load">{{ busy ? "读取中…" : "查看候选" }}</button>
      <span class="spacer"></span>
      <span class="meta">重写时多写</span>
      <select v-model.number="mode" class="model-select" style="max-width: 120px;" @change="saveMode">
        <option :value="0">关闭（1 版）</option>
        <option :value="2">2 版</option>
        <option :value="3">3 版</option>
        <option :value="5">5 版</option>
      </select>
      <span class="meta">{{ msg }}</span>
    </div>
    <div class="meta cand-note">
      同一章重写时多写几版，按字数 / 病灶词 / 段落 / 复读 / 预算五项算出分数，
      <b>每条扣分都写清楚</b>；自动留下分最高那版，你也可以在这里改用另一版。
      分数只用来比较几版之间谁更稳，不是“这章写得好不好”的结论。
      <b>候选是只读的证据</b>：要改正文就去下面的章节编辑器 —— 它改的永远是“已经存下来的那一版”，
      想换版本先点“用这版”，换了之后编辑器里改的就是新的那版。
      <span v-if="mode > 0" class="cand-cost">当前开着：一次重写会调用 {{ mode }} 次模型（花费也是 {{ mode }} 倍）。</span>
    </div>

    <div v-if="!exists" class="meta cand-empty">{{ emptyText }}</div>
    <div v-else class="cand-grid">
      <div v-for="c in candidates" :key="c.id" class="cand-col"
           :class="{ chosen: c.id === appliedId, winner: c.id === data.chosen }">
        <div class="cand-col-head">
          <b>{{ labelOf(c.id) }}</b>
          <span v-if="c.id === data.chosen" class="pill st-done" title="自动择优选中的就是这版">自动选中</span>
          <span v-if="c.id === appliedId" class="pill st-gate" title="你手动采用了这版">你在用</span>
          <span class="spacer"></span>
          <b class="cand-score" :class="scoreClass(c.score)">{{ c.score == null ? "—" : c.score }}</b>
        </div>
        <div class="meta">字数 {{ c.words ?? "?" }}<template v-if="c.status !== 'ok'"> · 生成失败</template></div>
        <ul class="cand-reasons">
          <li v-for="(r, i) in (c.reasons || [])" :key="i"
              :class="{ bad: String(r).indexOf('偏') >= 0 || String(r).indexOf('低于') >= 0 || String(r).indexOf('超出') >= 0 }">
            {{ r }}
          </li>
        </ul>
        <pre class="cand-text">{{ preview(c.text) }}</pre>
        <button class="mini cand-apply" :disabled="busy || c.status !== 'ok' || c.id === appliedId"
                :class="{ danger: confirmId === c.id }"
                @click="apply(c)">{{ c.id === appliedId ? "已采用"
                  : (confirmId === c.id ? "确认替换正文？" : "用这版") }}</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";

const props = defineProps({
  api: { type: Function, required: true },
  chapters: { type: Array, default: () => [] },
});
const emit = defineEmits(["say"]);

const n = ref(1);
const busy = ref(false);
const msg = ref("");
const exists = ref(false);
const data = ref({ chosen: "", candidates: [] });
const appliedId = ref("");
const confirmId = ref("");
const mode = ref(0);          // gates.best_of_n：0=关，2/3/5=版数

const candidates = computed(() => {
  // 按分数降序排（拿不到分数的排最后）：三栏并排时，最该看的那版在最左边。
  return (data.value.candidates || []).slice().sort((a, b) => {
    const sa = a.score == null ? -1 : a.score;
    const sb = b.score == null ? -1 : b.score;
    return sb - sa;
  });
});
const emptyText = computed(() => msg.value
  || "这一章还没有候选。它只在“低分章重写”时生成 —— 右上角把“重写时多写”设成 2~5 版，"
     + "下次重写这一章时才会多写几版。");

async function saveMode() {
  busy.value = true;
  try {
    const d = payload(await props.api("/config/best_of_n", "POST", { value: mode.value }));
    mode.value = d.current;
    msg.value = d.message || "已保存";
    emit("say", msg.value);
  } catch (e) {
    msg.value = "设置失败：" + e.message;
  } finally {
    busy.value = false;
  }
}

function payload(r) {
  if (!r || r.status !== 200 || (r.data && r.data.ok === false)) {
    const e = (r && r.data && r.data.error) || (r && r.status) || "请求失败";
    throw new Error(String(e));
  }
  return r.data || {};
}

function labelOf(id) {
  const i = String(id || "").replace(/^cand/, "");
  return "第 " + (i || "?") + " 版";
}

function scoreClass(s) {
  if (s == null) return "";
  if (s >= 80) return "st-done";
  if (s >= 60) return "st-warn";
  return "st-failed";
}

/** 预览：只显示开头，点开整章要看就去审阅台（这里三栏并排，塞全文会互相顶。 */
function preview(text) {
  const s = String(text || "").replace(/\r/g, "");
  return s.length > 260 ? s.slice(0, 260) + "…（后续内容在章节编辑器里看）" : s;
}

async function load() {
  if (!candidates.value.length) exists.value = false;
  busy.value = true;
  msg.value = "";
  try {
    const [cfg, d] = await Promise.all([
      props.api("/config/best_of_n").catch(() => null),
      props.api("/chapters/candidates?n=" + n.value),
    ]);
    if (cfg && cfg.status === 200 && cfg.data && cfg.data.ok) mode.value = cfg.data.current || 0;
    const data_ = payload(d);
    exists.value = !!data_.exists;
    data.value = { chosen: data_.chosen || "", candidates: data_.candidates || [] };
    appliedId.value = data_.applied_by_author || "";
    msg.value = data_.exists
      ? ("共 " + (data_.candidates || []).length + " 版，自动选中 " + (data_.chosen || "未定"))
      : (data_.note || "");
  } catch (e) {
    exists.value = false;
    msg.value = "读取候选失败：" + e.message;
  } finally {
    busy.value = false;
  }
}

async function apply(c) {
  // 两步确认（应用内按钮状态，不用 window.confirm：Electron 原生对话框在渲染进程里不可靠）
  if (confirmId.value !== c.id) {
    confirmId.value = c.id;
    msg.value = "再点一次“确认替换正文？”就会用这版覆盖本章（旧稿自动进章节历史）";
    return;
  }
  confirmId.value = "";
  busy.value = true;
  try {
    const d = payload(await props.api("/chapters/candidates/apply", "POST",
                                     { n: n.value, id: c.id }));
    appliedId.value = c.id;
    msg.value = d.message || "已采用";
    emit("say", msg.value);
  } catch (e) {
    msg.value = "采用失败：" + e.message;
  } finally {
    busy.value = false;
  }
}

watch(n, () => { exists.value = false; data.value = { chosen: "", candidates: [] }; msg.value = ""; });
onMounted(() => {
  if (props.chapters.length) n.value = props.chapters[0].n;
});
</script>

<style scoped>
.cand-panel { margin: 10px 0 4px; }
.cand-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.cand-note { line-height: 1.7; margin: 6px 0 10px; }
.cand-cost { display: block; margin-top: 4px; color: var(--warn, #b8860b); }
.cand-empty { padding: 10px 12px; border: 1px dashed var(--border); border-radius: 8px; }
.cand-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
@media (max-width: 1200px) { .cand-grid { grid-template-columns: 1fr; } }
.cand-col {
  min-width: 0; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px;
  display: flex; flex-direction: column; gap: 6px;
  background: var(--card, transparent);
}
.cand-col.winner { border-color: var(--ok, #2e8b57); }
.cand-col.chosen { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset; }
.cand-col-head { display: flex; align-items: center; gap: 6px; }
.cand-score { font-size: 18px; }
.cand-reasons { margin: 0; padding-left: 18px; font-size: 12.5px; line-height: 1.65; }
.cand-reasons .bad { color: var(--warn, #b8860b); }
.cand-text {
  margin: 0; max-height: 190px; overflow: auto; padding: 8px 10px;
  font-family: "Source Han Serif SC", "Noto Serif SC", serif; font-size: 12.5px;
  line-height: 1.75; white-space: pre-wrap; border-radius: 6px;
  background: var(--field-inset, #fff); color: var(--ink); border: 1px solid var(--border);
}
.cand-apply { margin-top: auto; }
.st-done { color: var(--ok, #2e8b57); }
.st-warn { color: var(--warn, #b8860b); }
.st-failed { color: var(--bad); }
.st-gate { color: var(--accent); }
</style>
