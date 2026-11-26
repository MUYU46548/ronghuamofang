<template>
  <div class="dsh-diag">
    <div class="dsh-head">
      <h4>DSH 诊断</h4>
      <button class="mini" :disabled="loading" @click="load">
        {{ loading ? "自检中…" : "重新自检" }}
      </button>
    </div>
    <div class="meta dsh-lead">
      三端：墨坊本体 ↔ DSH 基座 ↔ dsh-novelforge 插件。只读安装包与 DSH 的配置档，不改任何配置。
    </div>

    <div v-if="error" class="dsh-error">{{ error }}</div>
    <template v-else-if="data">
      <div class="dsh-facts">
        <span>profile <b>{{ data.profile.name }}</b></span>
        <span>墨坊 <b>v{{ data.app.version || "?" }}</b></span>
        <span>DSH <b>{{ data.dsh.version || "未读到" }}</b></span>
        <span>插件 <b>{{ data.plugin.version || "未装" }}</b></span>
        <span>bundles <b>{{ data.profile.bundles.length }}</b></span>
      </div>
      <div v-for="c in data.checks" :key="c.key" class="dsh-row" :class="'st-' + c.status">
        <span class="dsh-badge">{{ statusLabel(c.status) }}</span>
        <span class="dsh-label">{{ c.label }}</span>
        <span class="dsh-detail">{{ c.detail }}</span>
        <span v-if="c.action" class="dsh-action">{{ c.action }}</span>
      </div>
      <div class="meta dsh-time">检查于 {{ localTime(data.checked_at) }}</div>
    </template>
    <div v-else-if="!loading" class="meta">还没自检。</div>
  </div>
</template>

<script setup>
import { ref, onMounted } from "vue";
import { API } from "./apiBase.js";

const data = ref(null);
const error = ref("");
const loading = ref(false);

const STATUS = { ok: "正常", unknown: "未验", warn: "注意", bad: "有问题" };
const statusLabel = (s) => STATUS[s] || s;

function localTime(iso) {
  try { return new Date(iso).toLocaleString(); } catch { return iso || ""; }
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const r = await fetch(API + "/dsh/diagnose", { cache: "no-store" });
    const body = await r.json().catch(() => null);
    if (!r.ok || !body || body.ok === false) {
      throw new Error((body && body.error) || ("HTTP " + r.status));
    }
    data.value = body;
  } catch (e) {
    error.value = "DSH 诊断失败：" + String((e && e.message) || e);
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<style scoped>
.dsh-diag {
  margin: 14px 0 6px;
  padding-top: 10px;
  border-top: 1px solid rgba(127, 127, 127, 0.22);
}
.dsh-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.dsh-head h4 { margin: 0; }
.dsh-lead { margin: 4px 0 8px; }
.dsh-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  font-size: 12px;
  opacity: 0.78;
  margin-bottom: 4px;
}
.dsh-row {
  display: grid;
  grid-template-columns: 54px 110px 1fr;
  gap: 3px 8px;
  align-items: baseline;
  padding: 5px 0;
  border-bottom: 1px dashed rgba(127, 127, 127, 0.22);
  font-size: 13px;
}
.dsh-badge {
  justify-self: start;
  padding: 0 7px;
  border: 1px solid currentColor;
  border-radius: 9px;
  font-size: 12px;
  line-height: 18px;
  white-space: nowrap;
}
.dsh-label { font-weight: 600; }
.dsh-detail { word-break: break-all; }
.dsh-action {
  grid-column: 2 / -1;
  font-size: 12px;
  opacity: 0.72;
}
.st-ok .dsh-badge { color: var(--ok); }
.st-unknown .dsh-badge { color: var(--muted); }
.st-warn .dsh-badge { color: var(--warn); }
.st-bad .dsh-badge { color: var(--bad); }
.dsh-error { color: var(--bad); font-size: 12px; white-space: pre-wrap; }
.dsh-time { margin-top: 6px; }
</style>
