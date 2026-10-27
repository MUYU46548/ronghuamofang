<script setup>
/**
 * 写作预设面板（2026-10-21，roadmap 10.3）—— 多套写作要求存档与切换。
 *
 * 数据：GET/POST /config/writing_presets/{save,apply}。
 * 预设是"可切换的层"，project.yaml 是"当前生效值"：切换 = 留 history + 定向写回。
 *
 * 关键取舍：只收写作要求（style/style_notes/target_words/form/language/genre），
 * 书名/章数/作者是作品身份，不进预设 —— 混进来切换会把 A 书改成 B 书。
 */
import { ref, computed, onMounted } from "vue";

import { API } from "./apiBase.js";

const emit = defineEmits(["say"]);

const data = ref(null);
const busy = ref(false);
const err = ref("");
// 保存表单
const newId = ref("");
const captureNote = ref("");

const presets = computed(() => (data.value && data.value.presets) || []);
const current = computed(() => (data.value && data.value.current) || {});
const history = computed(() => (data.value && data.value.history) || []);

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
  emit("say", "已把当前生效值贴到下方 JSON 框 —— 改个 id 保存即成新预设");
}

async function saveFromJson() {
  let obj;
  try {
    obj = JSON.parse(captureNote.value || "{}");
  } catch (e) {
    emit("say", "JSON 解析失败：" + e);
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
    if (r.status === 200 && d.ok) captureNote.value = "";
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
  <div class="wpp">
    <div class="wpp-head">
      <b>写作预设</b>
      <span class="meta">多套写作要求存档，一键切换（切换会写回上方配置，留 history）</span>
      <span class="spacer"></span>
      <button class="mini" :disabled="busy" @click="captureCurrent">存当前为新预设</button>
      <button class="mini ghost" :disabled="busy" @click="load">刷新</button>
    </div>

    <div v-if="err" class="meta err-line">{{ err }}</div>

    <div v-if="current && Object.keys(current).length" class="meta wpp-current">
      当前生效：
      <span v-for="(v, k) in current" :key="k" class="wpp-chip">{{ k }}: {{ fmtVal(v) }}</span>
    </div>

    <table v-if="presets.length" class="wpp-table">
      <thead>
        <tr><th>id</th><th>内容</th><th>操作</th></tr>
      </thead>
      <tbody>
        <tr v-for="p in presets" :key="p.id">
          <td><code>{{ p.id }}</code></td>
          <td class="meta">
            <span v-if="p.error" class="err-line">{{ p.error }}</span>
            <span v-else>{{ p.keys.join("、") }}<span v-if="p.form"> · 文体 {{ p.form }}</span></span>
          </td>
          <td class="wpp-ops">
            <button class="mini" :disabled="busy" @click="applyPreset(p)">切换到这套</button>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-else-if="!busy" class="meta wpp-empty">
      还没有存过预设。点“存当前为新预设”把此刻的写作要求存下来，
      以后换风格一键切回。
    </div>

    <div v-if="captureNote !== ''" class="wpp-capture">
      <label class="meta">新预设 id（小写字母/数字/中划线）+ 内容（只收写作要求；
        书名/章数/作者不进预设）</label>
      <div class="wpp-row">
        <input v-model="newId" placeholder="如 noir" class="wpp-id" />
        <button class="mini" :disabled="busy" @click="saveFromJson">保存</button>
        <button class="mini ghost" @click="captureNote = ''">取消</button>
      </div>
      <textarea v-model="captureNote" rows="7" class="wpp-json"></textarea>
    </div>

    <div v-if="history.length" class="wpp-hist">
      <div class="meta"><b>最近切换</b>（切回旧值有据）</div>
      <div v-for="(h, i) in history.slice().reverse().slice(0, 5)" :key="i" class="meta">
        · {{ (h.at || "").replace("T", " ").slice(0, 19) }} → “{{ h.preset }}”
        <span v-if="h.prev && h.prev.style">（原 style：{{ fmtVal(h.prev.style) }}）</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.wpp { margin-top: 14px; padding-top: 10px; border-top: 1px dashed var(--border); }
.wpp-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
.err-line { color: var(--danger, #c0392b); }
.wpp-current { margin-bottom: 8px; }
.wpp-chip {
  display: inline-block; margin-right: 6px; padding: 1px 6px;
  border: 1px solid var(--border); border-radius: 4px;
}
.wpp-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.wpp-table th, .wpp-table td {
  text-align: left; padding: 5px 6px; border-bottom: 1px solid var(--border);
}
.wpp-table th { font-weight: 600; opacity: .75; }
.wpp-ops { white-space: nowrap; }
.wpp-empty { padding: 4px 0 2px; }
.wpp-capture { margin-top: 10px; display: flex; flex-direction: column; gap: 6px; }
.wpp-row { display: flex; gap: 8px; align-items: center; }
.wpp-id {
  width: 160px; font-size: 12px; padding: 4px 8px;
  border: 1px solid var(--border); border-radius: 6px;
  background: var(--field-inset, transparent); color: var(--foreground);
}
.wpp-json {
  font-size: 12px; line-height: 1.6; padding: 6px 8px; resize: vertical;
  border: 1px solid var(--border); border-radius: 6px;
  background: var(--field-inset, transparent); color: var(--foreground);
}
.wpp-hist {
  margin-top: 10px; padding: 6px 8px;
  border-left: 2px solid var(--accent, #4a9eff);
  background: var(--field-inset, transparent);
}
</style>
