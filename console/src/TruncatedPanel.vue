<script setup>
/**
 * 残稿面板（2026-10-20，预警 P1“残稿可见可采纳”）。
 *
 * 数据源：GET /truncated（只读盘，零 token）。
 *
 * 为什么必须做：`finish_reason=length` 时我们不落盘（纪律正确），但残缺文本一直
 * 隔离在 `data/state/truncated/` 里，**人看不见**。失败之后用户只看到“失败”，
 * 不知道模型其实已经写了 3000 字。
 *
 * 三条界面纪律：
 *  1. **采纳 != 完成**：采纳后仍标“残稿”，按钮变成“已补齐”，系统不会当它完成。
 *  2. **不自动采纳**：截断点之后的情节可能整段缺失，必须人看一眼。
 *  3. 太短的残稿不给采纳按钮，并说明原因（多半刚开始就断了，重写更省）。
 */
import { ref, onMounted } from "vue";

import { API } from "./apiBase.js";

const emit = defineEmits(["say"]);

const items = ref([]);
const busy = ref(false);
const err = ref("");
const loaded = ref(false);
const chapterOf = ref({});
const minChars = ref(300);

async function load() {
  busy.value = true;
  err.value = "";
  try {
    const r = await fetch(API + "/truncated");
    const d = await r.json().catch(() => ({}));
    if (r.status === 200 && d.ok) {
      items.value = d.items || [];
      minChars.value = d.min_chars || 300;
      const map = {};
      for (const it of items.value) {
        if (it.adopted_as) continue;
        map[it.name] = it.chapter_hint || "";
      }
      chapterOf.value = map;
    } else {
      items.value = [];
      err.value = d.error || ("读取失败：HTTP " + r.status);
    }
  } catch (e) {
    err.value = String(e);
  } finally {
    busy.value = false;
    loaded.value = true;
  }
}

async function post(path, body) {
  const r = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Mofang-Source": "gui" },
    body: JSON.stringify(body),
  });
  const d = await r.json().catch(() => ({}));
  return { status: r.status, data: d };
}

async function adopt(it) {
  const no = chapterOf.value[it.name];
  if (!no) {
    emit("say", "先填“采纳为第几章”");
    return;
  }
  busy.value = true;
  try {
    let r = await post("/truncated/adopt", { name: it.name, chapter: no });
    // 目标章已有内容 → 后端拒绝并说明；确认后才带 replace 重试
    if (r.status !== 200 && String(r.data.error || "").includes("覆盖")) {
      if (window.confirm(`第 ${no} 章已有内容。\n\n确定用这份残稿覆盖它？\n（旧内容会被替换，建议先确认那一章不是你要留的）`)) {
        r = await post("/truncated/adopt", { name: it.name, chapter: no, replace: true });
      } else {
        return;
      }
    }
    if (r.status !== 200) {
      emit("say", "采纳失败：" + (r.data.error || r.status));
      return;
    }
    emit("say", r.data.message || "已采纳");
    await load();
  } finally {
    busy.value = false;
  }
}

async function clearMark(no) {
  if (!window.confirm(`确认第 ${no} 章已经补齐/改好？\n\n清除标记后，系统会重新按正常判据判定这一章是否完成。`)) return;
  busy.value = true;
  try {
    const r = await post("/truncated/clear", { chapter: no });
    emit("say", r.status === 200 ? (r.data.message || "已清除")
                                : ("失败：" + (r.data.error || r.status)));
    await load();
  } finally {
    busy.value = false;
  }
}

onMounted(load);
defineExpose({ load });
</script>

<template>
  <div class="tsp">
    <div class="tsp-head">
      <b>残稿（写到一半被截断的正文）</b>
      <span class="meta" v-if="loaded">共 {{ items.length }} 份</span>
      <span class="spacer"></span>
      <button class="mini" :disabled="busy" @click="load">刷新</button>
    </div>
    <div v-if="err" class="meta err-line">{{ err }}</div>
    <div v-else-if="loaded && !items.length" class="meta tsp-empty">
      没有残稿。被截断的输出会隔离存放，以后可以在这里看见并采纳为草稿。
    </div>
    <table v-else-if="items.length" class="tsp-table">
      <thead>
        <tr>
          <th>时间</th>
          <th>字数</th>
          <th>开头</th>
          <th>采纳为第</th>
          <th>状态</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="it in items" :key="it.name">
          <td class="meta">{{ it.at }}</td>
          <td :class="it.adoptable ? '' : 'warn-text'">{{ it.chars }}</td>
          <td class="tsp-preview">{{ it.preview }}</td>
          <td>
            <div class="tsp-adopt" v-if="!it.adopted_as">
              <input v-model.number="chapterOf[it.name]" type="number" min="1" max="999"
                     class="tsp-ch" placeholder="章号" />
              <button class="mini" :disabled="busy || !it.adoptable" @click="adopt(it)"
                      :title="it.adoptable ? '把这份残稿写成该章草稿' : ('太短（少于 ' + minChars + ' 字），建议直接重写')">
                采纳
              </button>
            </div>
            <span v-else class="meta">—</span>
          </td>
          <td>
            <template v-if="it.adopted_as">
              <span class="pill st-gate">第 {{ it.adopted_as }} 章 · 残稿</span>
              <button class="mini" :disabled="busy" @click="clearMark(it.adopted_as)">已补齐</button>
            </template>
            <span v-else-if="!it.adoptable" class="meta">太短，不建议采纳</span>
            <span v-else class="meta">未采纳</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.tsp { margin-top: 14px; padding-top: 10px; border-top: 1px dashed var(--border); }
.tsp-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
.tsp-empty { padding: 4px 0 2px; }
.err-line { color: var(--danger, #c0392b); }
.tsp-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.tsp-table th, .tsp-table td {
  text-align: left; padding: 5px 6px; border-bottom: 1px solid var(--border); vertical-align: top;
}
.tsp-table th { font-weight: 600; opacity: .75; }
.tsp-preview {
  max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: var(--muted, #777);
}
.tsp-adopt { display: flex; gap: 6px; align-items: center; }
.tsp-ch {
  width: 62px; padding: 3px 6px; font-size: 12px;
  background: var(--field); color: var(--text);
  border: 1px solid var(--border); border-radius: 4px;
}
.warn-text { color: var(--warn, #b8860b); }
</style>
