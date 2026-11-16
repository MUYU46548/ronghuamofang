<template>
  <!-- 不受控的 details：绑 `:open` 会在每次数据刷新时把面板按回关闭状态
       （Vue 重渲染把 open 属性写回 ref 的值），作者正在看的表会突然合上 ——
       2026-11-16 真机验收踩到，别再加回来。 -->
  <details class="qm-box">
    <summary>
      这一章的体检（节 / 文风 / 这一轮）
      <span class="muted" v-if="loaded">
        · 第 {{ n }} 章 {{ chapterHanzi }} 汉字
        <template v-if="sec.declared"> · {{ sec.section_count }} 节</template>
      </span>
    </summary>

    <div class="qm-row">
      <label>第
        <input v-model.number="n" type="number" min="1" class="qm-no" @change="reload" />
        章</label>
      <label class="muted">这一轮
        <select v-model="roundPick" @change="switchRound" class="qm-sel">
          <option v-for="r in rounds" :key="r.name" :value="r.name">{{ r.name }}（{{ r.goal }}）</option>
        </select>
      </label>
      <button class="mini" @click="reload">重算</button>
      <button class="mini primary" :disabled="busy || !loaded" @click="annotate"
              @pointerup="annotatePointer"
              title="把结构问题变成段落批注：批注带段号与原句，改哪一段由你点">
        把命中项变成批注
      </button>
      <span class="qm-msg muted" v-if="msg">{{ msg }}</span>
    </div>

    <div v-if="!loaded && !busy" class="muted">还没有这一章的体检数据（先有正文）。</div>

    <template v-if="loaded">
      <!-- 节级：一章写几节、每节多少字、哪一节空了 -->
      <div class="qm-sec">
        <div class="qm-head">
          <b>节</b>
          <span class="muted" v-if="sec.declared">{{ sec.why }}</span>
          <span class="muted" v-else>这一章的大纲没有分节 —— 按整章算（不是问题）</span>
        </div>
        <table v-if="sec.declared" class="qm-table">
          <thead><tr><th>节</th><th>名字</th><th>汉字</th><th>目标</th><th>做到</th><th>状态</th></tr></thead>
          <tbody>
            <tr v-for="s in sec.sections" :key="s.n">
              <td>{{ s.n }}</td>
              <td>{{ s.name || "—" }}</td>
              <td>{{ s.hanzi }}</td>
              <td>{{ s.target || "—" }}</td>
              <td>{{ s.ratio ? Math.round(s.ratio * 100) + "%" : "—" }}</td>
              <td>
                <span v-if="s.ok" class="qm-ok">够了</span>
                <span v-else-if="!s.undeclared" class="qm-bad">太短</span>
                <span v-else class="muted">多写的</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 四个分布型指标 -->
      <div class="qm-style">
        <div class="qm-head">
          <b>文风</b>
          <span class="muted">只看数字，不改稿：改成什么样由你定</span>
        </div>
        <div class="qm-grid">
          <div v-for="m in metricRows" :key="m.key" class="qm-cell" :class="{ bad: m.bad }">
            <span class="qm-val">{{ m.value }}</span>
            <span class="qm-lbl">{{ m.label }}</span>
            <span class="muted">{{ m.hint }}</span>
          </div>
        </div>
      </div>

      <!-- 这一轮达标没有 -->
      <div class="qm-round">
        <div class="qm-head">
          <b>这一轮“{{ rnd.round || "—" }}”</b>
          <span :class="rnd.ok ? 'qm-ok' : 'qm-bad'">{{ rnd.ok ? "达标" : "还差" }}</span>
          <span class="muted" v-if="rnd.goal">目标：{{ rnd.goal }}</span>
        </div>
        <ul class="qm-checks">
          <li v-for="c in rnd.checks || []" :key="c.key">
            <span :class="c.ok ? 'qm-ok' : 'qm-bad'">{{ c.ok ? "✓" : "✗" }}</span>
            {{ checkLabel(c.key) }}：{{ c.actual }}（要 {{ c.expr }}）
          </li>
        </ul>
        <div class="muted" v-if="!rnd.ok">差的项：{{ (rnd.gaps || []).map(checkLabel).join("、") }}</div>
      </div>
    </template>
  </details>
</template>

<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  api: { type: Function, required: true },
  chapter: { type: Number, default: 1 },
});

const n = ref(props.chapter || 1);
const loaded = ref(false);
const busy = ref(false);
const msg = ref("");
const sec = ref({ declared: false, sections: [] });
const met = ref({ style: {} });
const rnd = ref({ checks: [] });
const rounds = ref([]);
const roundPick = ref("");

const LABELS = {
  dialogue_runs_per_chapter: "连排对白（处）",
  short_dialogue_ratio: "≤12 字的对白占比",
  end_short_ratio: "段末短句占比",
  say_ask_per_10k: "“说/问”每万字",
  sections_covered: "节覆盖",
  section_min: "最短一节字数",
  chapter_min: "本章汉字",
  short_para_ratio: "碎段占比",
};
const HINTS = {
  dialogue_runs_per_chapter: "连续三段以引号开头 = 一处问答阶梯",
  short_dialogue_ratio: "对白太短、连着来，读起来像谁也不好好说话",
  end_short_ratio: "每段都想用一句漂亮话收尾，是模板腔",
  say_ask_per_10k: "说话人交代得太密，动作与场景就被挤掉了",
};

function checkLabel(k) { return LABELS[k] || k; }
function payload(r) { return (r && r.data) || {}; }

const chapterHanzi = computed(() => met.value.hanzi || met.value.style?.hanzi || 0);
const metricRows = computed(() => {
  const s = met.value.style || {};
  const f = Object.fromEntries((((met.value.gate || {}).fails) || []).map((x) => [x.code, x]));
  return [
    { key: "dialogue_runs", label: LABELS.dialogue_runs_per_chapter, value: s.dialogue_runs ?? "—",
      hint: HINTS.dialogue_runs_per_chapter, bad: !!f.dialogue_runs },
    { key: "short_dialogue_ratio", label: LABELS.short_dialogue_ratio,
      value: s.short_dialogue_ratio != null ? Math.round(s.short_dialogue_ratio * 100) + "%" : "—",
      hint: HINTS.short_dialogue_ratio, bad: !!f.short_dialogue_ratio },
    { key: "end_short_ratio", label: LABELS.end_short_ratio,
      value: s.end_short_ratio != null ? Math.round(s.end_short_ratio * 100) + "%" : "—",
      hint: HINTS.end_short_ratio, bad: !!f.end_short_ratio },
    { key: "say_ask_per_10k", label: LABELS.say_ask_per_10k, value: s.say_ask_per_10k ?? "—",
      hint: HINTS.say_ask_per_10k, bad: !!f.say_ask_per_10k },
  ];
});

async function loadRounds() {
  try {
    const d = payload(await props.api("/rounds", "GET"));
    rounds.value = d.rounds || [];
    roundPick.value = (d.current && d.current.name) || (rounds.value[0]?.name || "");
  } catch (e) { /* 轮次表读不到不影响体检 */ }
}

async function switchRound() {
  if (!roundPick.value) return;
  try {
    await props.api("/rounds", "POST", { current: roundPick.value });
    await loadEval();
  } catch (e) {
    msg.value = "切轮次失败：" + e.message;
  }
}

async function loadEval() {
  const d = payload(await props.api("/rounds/evaluate", "POST",
    { n: n.value, round: roundPick.value || undefined }));
  if (d && d.checks) rnd.value = d;
}

async function reload() {
  busy.value = true;
  msg.value = "";
  try {
    const ms = payload(await props.api(`/chapters/metrics?n=${n.value}`, "GET"));
    if (!ms.style) {
      loaded.value = false;
      msg.value = ms.error || "这一章还没有正文";
      return;
    }
    met.value = ms;
    const ss = payload(await props.api(`/chapters/sections?n=${n.value}`, "GET"));
    sec.value = ss.declared !== undefined ? ss : { declared: false, sections: [] };
    await loadEval();
    loaded.value = true;
  } catch (e) {
    loaded.value = false;
    msg.value = "体检读不到：" + e.message;
  } finally {
    busy.value = false;
  }
}

/* 为什么还挂 pointerup（2026-11-16 真机验收实测）：
   在这个面板里，真实鼠标点击会出现 pointerdown / mousedown / pointerup 都到达按钮、
   却**不产生 click 事件**（同排的"重算"按钮正常）。JS 派发 click 能触发，说明
   handler 没问题，是节点在按下与抬起之间被替换掉了 → 浏览器不再合成 click。
   加一个 pointerup 兜底：同一动作 300ms 内只执行一次，鼠标与键盘都只跑一遍。 */
let lastAnnotateTs = 0;
function annotatePointer() {
  if (Date.now() - lastAnnotateTs < 300) return;
  annotate();
}

async function annotate() {
  lastAnnotateTs = Date.now();
  busy.value = true;
  try {
    const d = payload(await props.api("/chapters/metrics/annotate", "POST", { n: n.value }));
    msg.value = d.message || ("已生成 " + (d.created || 0) + " 条批注");
  } catch (e) {
    msg.value = "生成批注失败：" + e.message;
  } finally {
    busy.value = false;
  }
}

watch(() => props.chapter, (v) => { if (v) { n.value = v; if (loaded.value) reload(); } });
loadRounds();
</script>

<style scoped>
.qm-box { margin: 10px 0; border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; }
.qm-box > summary { cursor: pointer; font-weight: 600; }
.qm-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 8px 0; }
.qm-no { width: 64px; padding: 2px 6px; }
.qm-sel { padding: 2px 6px; }
.qm-head { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; margin: 8px 0 4px; }
.qm-table { border-collapse: collapse; font-size: 12px; width: 100%; }
.qm-table th, .qm-table td { border-bottom: 1px solid var(--border); padding: 3px 6px; text-align: left; }
.qm-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 8px; }
.qm-cell { border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; display: flex;
           flex-direction: column; gap: 2px; }
.qm-cell.bad { border-color: var(--bad); }
.qm-val { font-size: 18px; font-weight: 600; }
.qm-lbl { font-size: 12px; }
.qm-checks { margin: 4px 0; padding-left: 18px; font-size: 12px; }
.qm-ok { color: var(--ok); }
.qm-bad { color: var(--bad); }
.muted { color: var(--muted); font-size: 12px; }
</style>
