<script setup>
/**
 * 指南针面板（2026-10-21，roadmap 7.3）—— 全书终局锚点。
 *
 * 数据：GET/POST /outline/compass。终局方向一句话 + 活跃长线（≤5 条）+ 规模估计。
 * 写偏时以它为锚；跨卷多了没更新，注入块里会提醒。
 *
 * 关键取舍：长线 >5 条拒绝写入（长线太多等于没有主线）；终局方向变更自动留
 * history（compass_v*.json，切回有据）。
 */
import { ref, computed, onMounted } from "vue";

import { API } from "./apiBase.js";

const emit = defineEmits(["say"]);

const data = ref(null);
const busy = ref(false);
const err = ref("");
// 编辑态
const direction = ref("");
const threadsText = ref("");
const scale = ref(null);
const volumeAtUpdate = ref(null);

const exists = computed(() => data.value && data.value.exists);
const historyNote = computed(() => (data.value && data.value.history_note) || "");

async function load() {
  busy.value = true;
  err.value = "";
  try {
    const r = await fetch(API + "/outline/compass");
    const d = await r.json().catch(() => ({}));
    if (r.status === 200 && d.ok) {
      data.value = d;
      const c = d.compass || {};
      direction.value = c.ending_direction || "";
      threadsText.value = (c.active_threads || [])
        .map((t) => (typeof t === "string" ? t : t.text || "") +
          (t.planted ? `（埋于第${t.planted}章）` : ""))
        .join("\n");
      scale.value = c.scale_estimate ?? null;
      volumeAtUpdate.value = c.volume_at_update ?? null;
    } else {
      err.value = d.error || ("读取失败：HTTP " + r.status);
    }
  } catch (e) {
    err.value = String(e);
  } finally {
    busy.value = false;
  }
}

async function save() {
  const dir = direction.value.trim();
  if (dir.length < 10) {
    emit("say", "终局方向太短 —— 一句话说不清要去哪，就还不是指南针");
    return;
  }
  const threads = threadsText.value.split("\n")
    .map((s) => s.trim()).filter(Boolean)
    .map((s) => {
      const m = s.match(/^(.*?)（埋于第(\d+)章）$/);
      return m ? { text: m[1].trim(), planted: parseInt(m[2], 10) } : { text: s };
    });
  const body = { ending_direction: dir };
  if (threads.length) body.active_threads = threads;
  if (scale.value != null && String(scale.value).trim() !== "") {
    body.scale_estimate = parseInt(scale.value, 10);
  }
  if (volumeAtUpdate.value != null && String(volumeAtUpdate.value).trim() !== "") {
    body.volume_at_update = parseInt(volumeAtUpdate.value, 10);
  }
  busy.value = true;
  try {
    const r = await fetch(API + "/outline/compass", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Mofang-Source": "gui" },
      body: JSON.stringify(body),
    });
    const d = await r.json().catch(() => ({}));
    if (r.status === 200 && d.ok) {
      emit("say", (d.warnings && d.warnings.length)
        ? "已保存（有提示：" + d.warnings.join("；") + "）" : "指南针已保存");
      await load();
    } else {
      emit("say", "保存被拒：" + (d.error || r.status));
    }
  } catch (e) {
    err.value = String(e);
  } finally {
    busy.value = false;
  }
}

onMounted(load);
defineExpose({ load });
</script>

<template>
  <div class="cnp">
    <div class="cnp-head">
      <b>指南针</b>
      <span class="meta">全书往哪去：终局方向 + 活跃长线（最多 5 条）。写偏时以它为锚</span>
      <span class="spacer"></span>
      <button class="mini" :disabled="busy" @click="save">保存</button>
    </div>

    <div v-if="err" class="meta err-line">{{ err }}</div>

    <div class="cnp-form">
      <label class="meta">终局方向（一句话：这本书最终要落在哪）</label>
      <textarea v-model="direction" rows="2" placeholder="例：主角放弃封印，让核心的力量流散到世间各处去"></textarea>

      <label class="meta">活跃长线（每行一条；可写“线索（埋于第N章）”）</label>
      <textarea v-model="threadsText" rows="4" placeholder="例：&#10;沈砚与程砧的信任裂痕（埋于第2章）&#10;暗卫的真实目的"></textarea>

      <div class="cnp-row">
        <label class="meta">预计总章数</label>
        <input v-model="scale" type="number" min="1" placeholder="可留空" class="cnp-num" />
        <label class="meta">更新时在第几卷</label>
        <input v-model="volumeAtUpdate" type="number" min="1" placeholder="可留空" class="cnp-num" />
      </div>
    </div>

    <div v-if="exists && data.warnings && data.warnings.length" class="cnp-warn">
      <div v-for="(w, i) in data.warnings" :key="i" class="meta">· {{ w }}</div>
    </div>
    <div v-if="exists && data.problems && data.problems.length" class="cnp-warn">
      <div class="meta"><b>当前存盘的指南针有异常</b>（保存新版本可修复）：</div>
      <div v-for="(p, i) in data.problems" :key="i" class="meta">· {{ p }}</div>
    </div>
    <div v-if="historyNote" class="meta cnp-hist">{{ historyNote }}</div>
    <div v-if="!exists && !busy" class="meta cnp-empty">
      还没设过指南针。长篇建议在开写前锚定一次 —— global.md 是第 1 天定的，
      写到第 100 章时它是唯一还能对得上的锚。
    </div>
  </div>
</template>

<style scoped>
.cnp { margin-top: 14px; padding-top: 10px; border-top: 1px dashed var(--border); }
.cnp-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
.err-line { color: var(--danger, #c0392b); }
.cnp-form { display: flex; flex-direction: column; gap: 6px; }
.cnp-form textarea, .cnp-form input {
  font-size: 12px; line-height: 1.6; padding: 6px 8px; resize: vertical;
  border: 1px solid var(--border); border-radius: 6px;
  background: var(--field-inset, transparent); color: var(--foreground);
}
.cnp-row { display: flex; align-items: center; gap: 8px; }
.cnp-num { width: 90px; }
.cnp-warn {
  margin-top: 8px; padding: 6px 8px;
  border-left: 2px solid var(--warn, #b8860b);
  background: var(--field-inset, transparent);
}
.cnp-hist { margin-top: 6px; opacity: .8; }
.cnp-empty { padding: 4px 0 2px; }
</style>
