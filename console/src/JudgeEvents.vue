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
 * ## 三条硬约束（改之前先读）
 *
 * 1. **系统不代勾、不替你数**：勾选框只由用户点击改变，组件不预置、不推断。
 * 2. **四类必填**：勾了就得说属于哪类（人物处境 / 信息占有 / 关系 / 目标）；
 *    勾了没分类 → 该行标“已勾，还没说属于哪类”，且**保存前拦下**。
 * 3. **两个数分开显示**：勾了几条 ≠ 计入下限几条。
 *    R24 AC-24-2：没填证据句的落“无举证”、不计入下限。界面必须把差别摊开，
 *    不能只给一个数（AC-24-7 同一精神）。
 */
import { ref, computed, watch } from "vue";
import { API } from "./apiBase.js";

const props = defineProps({
  chapter: { type: Number, required: true },
  events: { type: Array, default: () => [] },   // [{n, text, checked, class, evidence}]
  hint: { type: String, default: "" },          // “这几条像同一场戏”提示（可为空）
  eventMin: { type: Number, default: 2 },
  densityNote: { type: String, default: "" },   // 章纲密度等只提示不改档的话
});
const emit = defineEmits(["say", "saved"]);

const CLASSES = ["人物处境", "信息占有", "关系", "目标"];

const checked = ref({});   // {index(0-based): true}
const cls = ref({});       // {index: 类别}
const evd = ref({});       // {index: 证据句}
const busy = ref(false);
const err = ref("");

function reset() {
  const ck = {}, cl = {}, ev = {};
  (props.events || []).forEach((e) => {
    const i = e.n - 1;
    if (e.checked) ck[i] = true;
    if (e.class) cl[i] = e.class;
    if (e.evidence) ev[i] = e.evidence;
  });
  checked.value = ck;
  cls.value = cl;
  evd.value = ev;
  err.value = "";
}
watch(() => [props.chapter, props.events], reset, { immediate: true });

const checkedCount = computed(
  () => Object.keys(checked.value).filter((k) => checked.value[k]).length);
// 计入下限 = 勾了 + 分了类 + 有证据句
const countedCount = computed(
  () => Object.keys(checked.value).filter(
    (k) => checked.value[k] && cls.value[k] && String(evd.value[k] || "").trim()).length);
const unproven = computed(
  () => Object.keys(checked.value).filter(
    (k) => checked.value[k] && !String(evd.value[k] || "").trim()).length);
const unclassified = computed(
  () => Object.keys(checked.value).filter((k) => checked.value[k] && !cls.value[k]));

const canSave = computed(() => !busy.value && props.events.length > 0);

async function save() {
  if (unclassified.value.length) {
    // R24 AC-24-1：类别空 → 不可提交，并说清缺哪几条
    err.value = "第 " + unclassified.value.map((k) => Number(k) + 1).join("、")
      + " 条勾了但没说属于哪一类（四选一是硬门）";
    return;
  }
  busy.value = true;
  err.value = "";
  try {
    const idx = Object.keys(checked.value)
      .filter((k) => checked.value[k]).map(Number);
    const classes = {}, evidences = {};
    idx.forEach((i) => {
      if (cls.value[i]) classes[i] = cls.value[i];
      const t = String(evd.value[i] || "").trim();
      if (t) evidences[i] = t;
    });
    const r = await fetch(API + "/judge/events", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chapter: props.chapter, checked: idx,
                             classes, evidences }),
    });
    const d = await r.json().catch(() => ({}));
    if (r.status >= 400) throw new Error(d.error || ("HTTP " + r.status));
    emit("say", "第 " + props.chapter + " 章：勾了 " + idx.length + " 件"
                + (unproven.value ? "（其中 " + unproven.value + " 条没填证据句，不计入下限）" : ""));
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
        章纲 {{ events.length }} 条 ·
        勾了 <b>{{ checkedCount }}</b> 件 ·
        计入下限 <b>{{ countedCount }}</b> / {{ eventMin }}
      </span>
      <span class="spacer"></span>
      <button class="mini primary" :disabled="!canSave" @click="save">
        {{ busy ? "保存中…" : "保存勾选" }}
      </button>
    </div>

    <div v-if="!events.length" class="je-empty">
      这一章章纲没写事件 —— 不可勾选，也不出结论。
      <span class="meta">（先在章纲的“核心事件”里写清这一章发生的事，再回来勾）</span>
    </div>

    <template v-else>
      <div v-for="ev in events" :key="ev.n" class="je-row"
           :class="{ on: checked[ev.n - 1] }">
        <label class="je-line">
          <input type="checkbox" v-model="checked[ev.n - 1]" />
          <span class="je-no">{{ ev.n }}</span>
          <span class="je-text">{{ ev.text }}</span>
        </label>
        <div v-if="checked[ev.n - 1]" class="je-detail">
          <div class="je-classes">
            <button v-for="c in CLASSES" :key="c" class="je-class"
                    :class="{ on: cls[ev.n - 1] === c }"
                    @click="cls[ev.n - 1] = c">{{ c }}</button>
          </div>
          <input v-model="evd[ev.n - 1]" class="je-evidence"
                 placeholder="哪句话能看出来（可留空 —— 留空落“无举证”，不计入下限）" />
          <span v-if="!cls[ev.n - 1]" class="je-warn">已勾，还没说属于哪类</span>
        </div>
      </div>

      <div v-if="densityNote" class="je-hint">系统提示（只提示、不改档）：{{ densityNote }}</div>
      <div v-if="hint" class="je-hint">{{ hint }}</div>
      <div v-if="unproven" class="je-hint">
        有 {{ unproven }} 条勾了但没填证据句 —— 会计入“勾了几件”，但不计入下限。
      </div>
      <div v-if="err" class="je-err">{{ err }}</div>
    </template>
  </div>
</template>
