<script setup>
/**
 * 写作预设面板（2026-10-21，roadmap 10.3）—— 多套写作要求存档与切换。
 *
 * 数据：GET/POST /config/writing_presets/{save,apply}。
 * 预设是"可切换的层"，project.yaml 是"当前生效值"：切换 = 留 history + 定向写回。
 *
 * 关键取舍：只收写作要求（style/style_notes/target_words/form/language/genre），
 * 书名/章数/作者是作品身份，不进预设 —— 混进来切换会把 A 书改成 B 书。
 *
 * 2026-11-28：样式迁到 Tailwind + 主题变量（scoped 样式块已摘除）。
 * 表格换 `UiTable`、按钮换 `UiButton`、输入框换 `UiInput`（code 档给 JSON 原文）、
 * “当前生效”的六个 chip 换 `UiBadge`（它们本来就是徽标，不是自造的小方块）。
 * 有意保留：`w-[160px]` 预设 id 输入宽度（再宽就把“保存”挤到下一行）。
 */
import { ref, computed, onMounted } from "vue";

import UiBadge from "./components/ui/UiBadge.vue";
import UiField from "./components/ui/UiField.vue";
import UiButton from "./components/ui/UiButton.vue";
import UiInput from "./components/ui/UiInput.vue";
import UiTable from "./components/ui/UiTable.vue";
import { API } from "./apiBase.js";

const emit = defineEmits(["say"]);

const data = ref(null);
const busy = ref(false);
const err = ref("");
// 保存表单
const newId = ref("");
const captureNote = ref("");
// 表单是否展开：**不能**拿 `captureNote !== ""` 当条件 —— 那样用户清空内容框
// （Ctrl+A 想重写时几乎必然发生）表单会整个消失，正在改的东西一起没了。
// 2026-11-28 实测：e2e 里 `fill("")` 之后输入框直接不可见，报的是 type 超时。
const formOpen = ref(false);

const presets = computed(() => (data.value && data.value.presets) || []);
const current = computed(() => (data.value && data.value.current) || {});
const history = computed(() => (data.value && data.value.history) || []);

const COLUMNS = [
  { key: "id", label: "id", cls: "w-[140px]" },
  { key: "content", label: "内容" },
  { key: "ops", label: "操作", cls: "whitespace-nowrap w-[130px]" },
];

async function load() {
  busy.value = true;
  err.value = "";
  try {
    const r = await fetch(API + "/config/writing_presets");
    const d = await r.json().catch(() => ({}));
    if (r.status === 200 && d.ok) data.value = d;
    else err.value = d.error || ("读取失败：HTTP " + r.status);
  } catch (e) {
    err.value = String(e);
  } finally {
    busy.value = false;
  }
}

function captureCurrent() {
  // 把"当前生效值"填进保存表单（预设最常见的来源就是此刻的配置）
  newId.value = "";
  captureNote.value = JSON.stringify(current.value, null, 1);
  formOpen.value = true;
  emit("say", "已把当前生效值贴到下方输入框 —— 改个 id 保存即成新预设");
}

async function saveFromJson() {
  let obj;
  try {
    obj = JSON.parse(captureNote.value || "{}");
  } catch (e) {
    emit("say", "数据格式解析失败：" + e);
    return;
  }
  if (!newId.value.trim()) {
    emit("say", "先给预设起个 id（小写字母/数字/中划线）");
    return;
  }
  busy.value = true;
  try {
    const r = await fetch(API + "/config/writing_presets/save", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Mofang-Source": "gui" },
      body: JSON.stringify({ id: newId.value.trim(), data: obj }),
    });
    const d = await r.json().catch(() => ({}));
    emit("say", r.status === 200 && d.ok ? (d.message || "已保存")
      : ("保存被拒：" + (d.error || r.status)));
    if (r.status === 200 && d.ok) {
      captureNote.value = "";
      newId.value = "";
      formOpen.value = false;
    }
    await load();
  } finally {
    busy.value = false;
  }
}

async function applyPreset(p) {
  busy.value = true;
  try {
    const r = await fetch(API + "/config/writing_presets/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Mofang-Source": "gui" },
      body: JSON.stringify({ id: p.id }),
    });
    const d = await r.json().catch(() => ({}));
    emit("say", r.status === 200 && d.ok ? (d.message || "已切换")
      : ("切换被拒：" + (d.error || r.status)));
    await load();
  } finally {
    busy.value = false;
  }
}

function fmtVal(v) {
  if (Array.isArray(v)) return v.join(" ~ ");
  const s = String(v ?? "");
  return s.length > 30 ? s.slice(0, 30) + "…" : s;
}

onMounted(load);
defineExpose({ load });
</script>

<template>
  <div class="sep-dashed">
    <div class="row-baseline mb-2">
      <b class="text-[color:var(--ink)]">写作预设</b>
      <span class="meta">多套写作要求存档，一键切换（切换会写回上方配置，留 history）</span>
      <span class="spacer"></span>
      <UiButton :disabled="busy" @click="captureCurrent">存当前为新预设</UiButton>
      <UiButton variant="ghost" :disabled="busy" @click="load">刷新</UiButton>
    </div>

    <div v-if="err" class="meta text-[color:var(--bad)]">{{ err }}</div>

    <div v-if="current && Object.keys(current).length" class="meta mb-2">
      当前生效：
      <UiBadge v-for="(v, k) in current" :key="k" class="mr-1.5">{{ k }}: {{ fmtVal(v) }}</UiBadge>
    </div>

    <UiTable v-if="presets.length" :columns="COLUMNS" :rows="presets" row-key="id">
      <template #cell-id="{ row }">
        <code>{{ row.id }}</code>
      </template>
      <template #cell-content="{ row }">
        <span v-if="row.error" class="text-[color:var(--bad)]">{{ row.error }}</span>
        <span v-else class="meta">{{ row.keys.join("、") }}<span v-if="row.form"> · 文体 {{ row.form }}</span></span>
      </template>
      <template #cell-ops="{ row }">
        <UiButton :disabled="busy" @click="applyPreset(row)">切换到这套</UiButton>
      </template>
    </UiTable>
    <div v-else-if="!busy" class="meta pb-0.5 pt-1">
      还没有存过预设。点“存当前为新预设”把此刻的写作要求存下来，
      以后换风格一键切回。
    </div>

    <div v-if="formOpen" class="mt-2.5 flex flex-col gap-1.5">
      <!-- 表单行用 UiField：label / 说明 / 控件 / 校验的排布只在这一处决定。
           旧实现是手写 <label class="meta">，第三个面板就会各写一套（已经发生过）。 -->
      <UiField label="新预设名（小写字母/数字/中划线）"
               hint="只收写作要求；书名/章数/作者是作品身份，不进预设">
        <div class="row">
          <UiInput v-model="newId" placeholder="如 noir" class="w-[160px]" />
          <UiButton :disabled="busy" @click="saveFromJson">保存</UiButton>
          <UiButton variant="ghost" @click="formOpen = false; captureNote = ''; newId = ''">取消</UiButton>
        </div>
      </UiField>
      <UiField label="内容（JSON，一次只收一套写作要求）">
        <UiInput v-model="captureNote" multiline variant="code" :rows="7" />
      </UiField>
    </div>

    <div v-if="history.length"
         class="mt-2.5 border-l-2 border-[var(--accent)] bg-[var(--field-inset)] px-2 py-1.5">
      <div class="meta"><b>最近切换</b>（切回旧值有据）</div>
      <div v-for="(h, i) in history.slice().reverse().slice(0, 5)" :key="i" class="meta">
        · {{ (h.at || "").replace("T", " ").slice(0, 19) }} → “{{ h.preset }}”
        <span v-if="h.prev && h.prev.style">（原 style：{{ fmtVal(h.prev.style) }}）</span>
      </div>
    </div>
  </div>
</template>
