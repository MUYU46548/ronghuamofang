<script setup>
/**
 * “这一章发生了几件事（你来定）”—— 作者事件勾选（F03b / I3）。
 *
 * ## 为什么抽成组件
 *
 * 同一块交互有两个落点，产品定义（04-交互定义）要求：
 *   · **分章页**：每章章纲卡片下方（I3 的原位置，用户先看大纲再勾）；
 *   · **章节页 → 单章判定详情**：看缺陷单时顺手勾（原实现）。
 * 两处各写一份必然漂移（本仓在“判据写两遍”上栽过多次）。所以这里只有一份实现，
 * 两处传不同的 `chapter` 即可。
 *
 * ## 文案纪律（2026-10-31 重写，起因：用户说“看不懂这个功能”）
 *
 * 第一版有三处黑话，全部改掉：
 *   · “计入下限 1/2” → 没人知道“下限”是什么 → 改成“不算进 2 件”，并在常显说明里
 *     讲清“为什么是 2 件”（一章只有一件事 = 篇幅再长也是水章）；
 *   · 四类名字太抽象 → 每类补一行例子（按钮 title + 选中后行内显示）；
 *   · “不打勾 = 我认为这不算”没写出来 → 现在有显式“不算一件事”按钮，
 *     且“没勾”与“标为不算”的区别写清了（前者=还没看，后者=已判断）。
 * **原则：界面上出现的每个词，作者不用先学一套术语就能懂。**术语放手册里。
 *
 * ## 三条硬约束（改之前先读）
 *
 * 1. **系统不代勾、不替你数**：勾选框只由用户点击改变，组件不预置、不推断。
 * 2. **四类必填**：勾了就得说它改变了什么；勾了没分类 → 保存前拦下并点名第几条。
 * 3. **两个数分开显示**：认定几件 ≠ 有几件填了证据句。
 *    R24 AC-24-2：没填证据句的落“无举证”、不算进下限。差别必须摊开写，
 *    不能合并成一个数字（AC-24-7 同一精神）。
 */
import { ref, computed, watch } from "vue";
import { postJSON } from "./guiApi.js";

const props = defineProps({
  chapter: { type: Number, required: true },
  // [{n, text, checked, rejected, class, evidence}]
  events: { type: Array, default: () => [] },
  hint: { type: String, default: "" },          // “像同一场戏”的字面提示
  eventMin: { type: Number, default: 2 },
  densityNote: { type: String, default: "" },   // 章纲密度等只提示不改档的话
  // 分类表来自口径表 J22（作者可改）。空 = 老接口/老口径 → 用出厂四类兜底，
  // 并在下面显式提示“去改标准里定”，不假装那是作者定的。
  classes: { type: Array, default: () => [] },
  classSource: { type: String, default: "caliber" },
});
const emit = defineEmits(["say", "saved"]);

// 出厂四类：只在接口没给分类表时兜底（正常态由后端按 J22 送上来）
const FALLBACK_CLASSES = [
  { key: "人物处境", eg: "被抓 / 受伤 / 被辞退 / 身份暴露" },
  { key: "信息占有", eg: "得知真相 / 被瞒住 / 误会解开" },
  { key: "关系", eg: "结盟 / 翻脸 / 背叛" },
  { key: "目标", eg: "决定复仇 / 放弃 / 改主意" },
];
const CLASSES = computed(() =>
  (props.classes && props.classes.length ? props.classes : FALLBACK_CLASSES));

const checked = ref({});   // {index(0-based): true}
const rejected = ref({});  // {index: true} —— 明确判定“这不算一件事”
const cls = ref({});       // {index: 类别}
const evd = ref({});       // {index: 证据句}
const busy = ref(false);
const err = ref("");

function reset() {
  const ck = {}, rj = {}, cl = {}, ev = {};
  (props.events || []).forEach((e) => {
    const i = e.n - 1;
    if (e.checked) ck[i] = true;
    if (e.rejected) rj[i] = true;
    if (e.class) cl[i] = e.class;
    if (e.evidence) ev[i] = e.evidence;
  });
  checked.value = ck;
  rejected.value = rj;
  cls.value = cl;
  evd.value = ev;
  err.value = "";
}
watch(() => [props.chapter, props.events], reset, { immediate: true });

function toggleCheck(i) {
  // 勾选与“不算一件事”互斥：勾了就撤销拒判标记
  checked.value[i] = !checked.value[i];
  if (checked.value[i]) delete rejected.value[i];
}
function toggleReject(i) {
  rejected.value[i] = !rejected.value[i];
  if (rejected.value[i]) {
    delete checked.value[i];
    delete cls.value[i];
    delete evd.value[i];
  }
}

const checkedCount = computed(
  () => Object.keys(checked.value).filter((k) => checked.value[k]).length);
const rejectedCount = computed(
  () => Object.keys(rejected.value).filter((k) => rejected.value[k]).length);
const judgedCount = computed(() => checkedCount.value + rejectedCount.value);
// “有几件填了证据句”—— 只有这些才计入下限
const countedCount = computed(
  () => Object.keys(checked.value).filter(
    (k) => checked.value[k] && cls.value[k] && String(evd.value[k] || "").trim()).length);
const unproven = computed(
  () => Object.keys(checked.value).filter(
    (k) => checked.value[k] && !String(evd.value[k] || "").trim()).length);
const unclassified = computed(
  () => Object.keys(checked.value).filter((k) => checked.value[k] && !cls.value[k]));

const allJudged = computed(
  () => props.events.length > 0 && judgedCount.value >= props.events.length);
const canSave = computed(() => !busy.value && props.events.length > 0);

async function save() {
  if (unclassified.value.length) {
    // R24 AC-24-1：类别空 → 不可提交，并说清缺哪几条
    err.value = "第 " + unclassified.value.map((k) => Number(k) + 1).join("、")
      + " 条勾了但没说它改变了什么（下面选一类）";
    return;
  }
  busy.value = true;
  err.value = "";
  try {
    const idx = Object.keys(checked.value)
      .filter((k) => checked.value[k]).map(Number);
    const rj = Object.keys(rejected.value)
      .filter((k) => rejected.value[k]).map(Number);
    const classes = {}, evidences = {};
    idx.forEach((i) => {
      if (cls.value[i]) classes[i] = cls.value[i];
      const t = String(evd.value[i] || "").trim();
      if (t) evidences[i] = t;
    });
    // 写请求走 guiApi.postJSON：带双因子头（agent_mode=true 时 /judge/events 是
    // 作者专属写端点，自己拼 fetch 会被 403，界面表现为“点了没反应”）。
    const d = await postJSON("/judge/events", {
      chapter: props.chapter, checked: idx, classes, evidences, rejected: rj,
      total: props.events.length,
    });
    emit("say", "第 " + props.chapter + " 章：认定 " + idx.length + " 件事"
                + (unproven.value ? "（其中 " + unproven.value + " 条没填证据，不算进 "
                                    + props.eventMin + " 件）" : "")
                + (rj.length ? "，另有 " + rj.length + " 条你标为不算" : ""));
    emit("saved", d.events || null);
  } catch (e) {
    err.value = String(e.message || e);
  }
  busy.value = false;
}
</script>

<template>
  <div class="je-events">
    <div class="je-head">
      <b>这一章发生了几件事（你来定）</b>
      <span v-if="events.length" class="meta">
        章纲 {{ events.length }} 条 · 已过目
        <b>{{ judgedCount }}/{{ events.length }}</b> ·
        认定 <b>{{ checkedCount }}</b> 件
        <span v-if="unproven" class="je-sub">
          （{{ unproven }} 条没填证据，不算进 {{ eventMin }} 件）
        </span>
      </span>
      <span class="spacer"></span>
      <button class="mini primary" :disabled="!canSave" @click="save">
        {{ busy ? "保存中…" : "保存勾选" }}
      </button>
    </div>

    <!-- 判据说明：常显。用户第一版看不懂，主要就缺这一段 -->
    <div v-if="events.length" class="je-guide">
      只数<b>改变了状态</b>的事：处境 / 知道的事 / 关系 / 目标变了。
      单纯走动、查看、说话<b>不算</b>。
      一章至少 <b>{{ eventMin }} 件</b>才算不是凑数的章 —— 这是“有没有推进”的人工关卡，
      系统自己测不了。
    </div>
    <div v-if="events.length && classSource === 'legacy'" class="je-hint">
      分类表还是出厂的四个名字（还没登记进你的判定标准）。
      想加一类“时间”“地点”之类，去 章节 → 判定 → 改标准 → 事件分类表 —— 那里归你定。
    </div>

    <div v-if="!events.length" class="je-empty">
      这一章章纲还没写“核心事件”—— 先回上面把它写清，这里才有得勾。
      <div class="meta">
        （章纲里一条事件都没有时，判定会直接给“不可判定：无章纲”，不是“合格”。）
      </div>
    </div>

    <template v-else>
      <div v-for="ev in events" :key="ev.n" class="je-row"
           :class="{ on: checked[ev.n - 1], rejected: rejected[ev.n - 1] }">
        <div class="je-line">
          <input type="checkbox" :checked="!!checked[ev.n - 1]"
                 :disabled="!!rejected[ev.n - 1]"
                 @change="toggleCheck(ev.n - 1)" />
          <span class="je-no">{{ ev.n }}</span>
          <span class="je-text">{{ ev.text }}</span>
          <button class="je-reject" :class="{ on: rejected[ev.n - 1] }"
                  :title="rejected[ev.n - 1] ? '撤销：重新考虑这条' : '我判断这条只是动作，不算一件事'"
                  @click="toggleReject(ev.n - 1)">
            {{ rejected[ev.n - 1] ? "已标：不算" : "不算一件事" }}
          </button>
        </div>

        <div v-if="checked[ev.n - 1]" class="je-detail">
          <div class="je-ask">它改变了什么？</div>
          <div class="je-classes">
            <button v-for="c in CLASSES" :key="c.key" class="je-class"
                    :class="{ on: cls[ev.n - 1] === c.key }"
                    :title="c.eg"
                    @click="cls[ev.n - 1] = c.key">{{ c.key }}</button>
          </div>
          <div class="je-eg">
            {{ (CLASSES.find((c) => c.key === cls[ev.n - 1]) || {}).eg
               || "选一类（鼠标放上去有例子）" }}
          </div>
          <input v-model="evd[ev.n - 1]" class="je-evidence"
                 placeholder="哪句原文能看出它变了（可留空 —— 留空不算进下限）" />
          <span v-if="!cls[ev.n - 1]" class="je-warn">已勾，还没说它改变了什么</span>
        </div>
      </div>

      <div v-if="densityNote" class="je-hint">
        章纲密度提示（只提示、不改档）：{{ densityNote }}
      </div>
      <div v-if="hint" class="je-hint">{{ hint }}</div>
      <div v-if="unproven" class="je-hint">
        有 {{ unproven }} 条填了“是事件”却没写证据 —— 仍算你认定的事，
        但<b>不算进“至少 {{ eventMin }} 件”</b>这条线。
      </div>
      <div v-if="allJudged" class="je-done">
        这一章 {{ events.length }} 条都过目了（认定 {{ checkedCount }} 件，
        标为不算 {{ rejectedCount }} 条）。保存后判定会按这个数重算。
      </div>
      <div v-if="err" class="je-err">{{ err }}</div>
    </template>
  </div>
</template>
