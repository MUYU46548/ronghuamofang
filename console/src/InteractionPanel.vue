<!--
  两人同场互动约束（C 组“关系网驱动互动”，2026-11-03）

  要治的病（台账原话）：关系图只展示“有一条边”，没有“这两人同场会怎么说话”
  的约束 —— 于是同一对师兄弟，这章互称全名、下章喊师兄，人称与分寸全靠即兴。

  口径：证据只取设定集里已有的关系原文；基调由关系词表判定；称呼没写就明说没写。
  输出是**按基调的写作建议**，不是正典 —— 界面上也这么写。
-->
<template>
  <div class="card ix-card">
    <div class="card-head">
      <h3>两人同场</h3>
      <span class="meta">按设定里的关系，给出这两人同场时的称呼 / 禁忌 / 写法约束（零 token，不是定稿设定）</span>
      <span class="spacer"></span>
      <button v-if="res && res.ok && res.found" class="mini" @click="copyIt">📋 复制为写作约束</button>
    </div>

    <div class="ix-row">
      <input class="prov-input" list="ix-names" v-model="a" placeholder="角色 A" @keyup.enter="run" />
      <span class="meta">×</span>
      <input class="prov-input" list="ix-names" v-model="b" placeholder="角色 B" @keyup.enter="run" />
      <datalist id="ix-names">
        <option v-for="n in names" :key="n" :value="n"></option>
      </datalist>
      <button class="mini primary" :disabled="busy" @click="run">{{ busy ? "生成中…" : "生成约束" }}</button>
    </div>

    <div v-if="err" class="ix-err">{{ err }}</div>

    <div v-if="res && res.found" class="ix-result">
      <div class="ix-head">
        <span class="pill" :class="toneClass">{{ res.tone }}</span>
        <b>{{ res.a }} × {{ res.b }}</b>
      </div>
      <div v-if="res.note" class="ix-note">{{ res.note }}</div>

      <div class="ix-sec">
        <div class="ix-k">依据（设定原文，可复核）</div>
        <div v-if="!res.basis.length" class="meta">设定集里没有这两人的关系原文。</div>
        <div v-for="(x, i) in res.basis" :key="i" class="ix-basis">
          <span class="meta">{{ x.who }}</span>
          <span>{{ x.text }}</span>
        </div>
      </div>

      <div class="ix-grid">
        <div class="ix-sec">
          <div class="ix-k">称呼</div>
          <div class="ix-text">{{ res.guide.address }}</div>
        </div>
        <div class="ix-sec">
          <div class="ix-k">别这样写</div>
          <div class="ix-text">{{ res.guide.avoid }}</div>
        </div>
      </div>

      <div class="ix-sec">
        <div class="ix-k">写法</div>
        <ul class="ix-writing">
          <li v-for="(w, i) in res.guide.writing" :key="i">{{ w }}</li>
        </ul>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref, computed } from "vue";

const props = defineProps({ api: { type: Function, required: true } });
const emit = defineEmits(["say"]);

const names = ref([]);
const a = ref("");
const b = ref("");
const res = ref(null);
const err = ref("");
const busy = ref(false);

const toneClass = computed(() => {
  const t = res.value && res.value.tone;
  if (t === "仇敌" || t === "对立") return "st-failed";
  if (t === "主从" || t === "疏离") return "st-warn";
  if (t === "亲近") return "st-done";
  return "";
});

function say(msg) { emit("say", msg); }

async function loadNames() {
  const r = await props.api("/setting/names");
  if (r.status === 200 && r.data && r.data.ok) {
    names.value = [...(r.data.characters || []), ...(r.data.world || [])];
  }
}

async function run() {
  err.value = "";
  if (!a.value.trim() || !b.value.trim()) { err.value = "两个角色名都要填"; return; }
  busy.value = true;
  try {
    const r = await props.api("/setting/interaction?a=" + encodeURIComponent(a.value.trim())
                              + "&b=" + encodeURIComponent(b.value.trim()));
    if (r.status === 200 && r.data && r.data.ok) {
      res.value = r.data;
      if (!r.data.found) err.value = r.data.note || "设定集里没有这两个角色";
    } else {
      res.value = null;
      err.value = (r.data && (r.data.error || r.data.note)) || ("HTTP " + r.status);
    }
  } finally {
    busy.value = false;
  }
}

function copyIt() {
  const r = res.value;
  if (!r) return;
  const lines = [
    `【同场约束】${r.a} × ${r.b}（基调：${r.tone}）`,
    "依据：" + (r.basis.map((x) => `${x.who}：${x.text}`).join("；") || "（无原文）"),
    "称呼：" + r.guide.address,
    "别这样写：" + r.guide.avoid,
    "写法：" + (r.guide.writing || []).join(" "),
  ];
  if (r.note) lines.push("注意：" + r.note);
  const text = lines.join("\n");
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(
      () => say("同场约束已复制"),
      () => say("复制失败（剪贴板不可用）"));
  } else {
    say("复制失败（剪贴板不可用）");
  }
}

onMounted(loadNames);
</script>
