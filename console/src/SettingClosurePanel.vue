<script setup>
/**
 * 设定库闭环 · 素材裁决面板（2026-10-20，台账三·「设定库闭环」）。
 *
 * 数据源：GET /setting/conflicts（零 token，`utils.material_state.audit`）
 * 写操作：POST /setting/status {name, action: adopt|reject|uncertain, reason?}
 *
 * 为什么要有这个面板：端点早就齐了（`/setting/conflicts`、`/canon`、`/status`），
 * 但只有 MCP 能调 —— 桌面端用户看不到"哪两个素材卡在同一字段上打架"，
 * 于是"机器裁不了的分歧"永远悬着，设定集里写的还是归并时随便取的那一个。
 *
 * 三态语义（与后端一致）：
 *   adopt     —— 采纳当前条目（清掉待裁决标记）
 *   reject    —— **必须填原因**，先写墓碑（按名字生效，条目不存在也算数）
 *   uncertain —— 先挂着，别装作已解决
 *
 * 不提供"自动合并"：字段分歧该取哪一版只有作者知道，
 * 给个按钮自动选等于把设定权交给规则。
 */
import { ref, computed, onMounted } from "vue";

import { API } from "./apiBase.js";

const emit = defineEmits(["say"]);
function say(m) { emit("say", m); }

const data = ref(null);
const busy = ref(false);
const err = ref("");
const rejecting = ref(null);       // {name, reason}
const acting = ref("");

async function load() {
  busy.value = true;
  err.value = "";
  try {
    const r = await fetch(API + "/setting/conflicts");
    const d = await r.json().catch(() => ({}));
    if (r.status === 200 && d.ok) {
      data.value = d;
    } else {
      data.value = null;
      err.value = d.error || ("读取失败：HTTP " + r.status);
    }
  } catch (e) {
    err.value = String(e);
  }
  busy.value = false;
}

const conflicts = computed(() => data.value?.review_conflicts || []);
const counts = computed(() => data.value?.counts || {});
const tombstones = computed(() => data.value?.tombstones || []);
// 同名同字段合并成一组：三条分歧摊成三行会让人以为要分别拍板三次
const groups = computed(() => {
  const map = new Map();
  for (const c of conflicts.value) {
    const key = c.name + "||" + c.field;
    if (!map.has(key)) map.set(key, { name: c.name, field: c.field, rows: [] });
    map.get(key).rows.push(c);
  }
  return [...map.values()];
});

async function act(name, action, reason = "") {
  acting.value = name + ":" + action;
  try {
    const r = await fetch(API + "/setting/status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, action, reason }),
    });
    const d = await r.json().catch(() => ({}));
    if (r.status === 200 && d.ok) {
      say(d.message || ("已记录：" + name + " → " + action));
      await load();
    } else {
      say("拍板失败：" + (d.error || ("HTTP " + r.status)));
    }
  } catch (e) {
    say("拍板失败：" + String(e));
  }
  acting.value = "";
}

function startReject(name) {
  rejecting.value = { name, reason: "" };
}

async function confirmReject() {
  const r = rejecting.value;
  if (!r) return;
  if (!r.reason.trim()) { say("否决必须写原因（后端也会拒）"); return; }
  const name = r.name;
  rejecting.value = null;
  await act(name, "reject", r.reason.trim());
}

function valueLine(v) {
  return (v.file ? v.file + "：" : "") + v.value;
}

onMounted(load);
defineExpose({ load });
</script>

<template>
  <div class="card">
    <div class="card-head">
      <h3>素材裁决（设定库闭环）</h3>
      <span class="spacer"></span>
      <span class="meta" v-if="data && data.audited_at">审计于 {{ String(data.audited_at).replace("T", " ") }}</span>
      <button class="mini" :disabled="busy" @click="load">{{ busy ? "读取中…" : "重新审计" }}</button>
    </div>
    <div class="meta">
      机器裁不了的字段分歧（同名卡在不同素材里说法不一）。拍板会写进设定集与墓碑：
      <b>否决按名字生效</b>（下次归并不会再把它带回来）。
    </div>

    <div v-if="err" class="empty" style="padding:14px 0;">{{ err }}</div>
    <div v-else-if="!data" class="meta" style="padding:10px 0;">读取中…</div>

    <!-- checked=false 必须显式说出来：空清单 != 没有冲突 -->
    <div v-else-if="!data.checked" class="sc-unchecked">
      没查成：{{ data.reason || "原因未知" }}
    </div>

    <template v-else>
      <div class="sc-summary">
        <span class="sc-stat"><b>{{ data.card_count ?? 0 }}</b> 张素材卡</span>
        <span class="sc-stat" :class="{ 'sc-bad': conflicts.length }">
          <b>{{ conflicts.length }}</b> 处待裁决</span>
        <span class="sc-stat"><b>{{ counts.adopted ?? 0 }}</b> 已采纳</span>
        <span class="sc-stat"><b>{{ tombstones.length }}</b> 条墓碑</span>
      </div>

      <div v-if="!groups.length" class="meta sc-ok">没有待裁决分歧。</div>

      <div v-for="g in groups" :key="g.name + g.field" class="sc-item">
        <div class="sc-head">
          <b>{{ g.name }}</b>
          <span class="sc-field">{{ g.field }}</span>
          <span class="spacer"></span>
          <button class="mini" :disabled="!!acting" @click="act(g.name, 'adopt')">采纳当前</button>
          <button class="mini" :disabled="!!acting" @click="act(g.name, 'uncertain')">先挂着</button>
          <button class="mini danger" :disabled="!!acting" @click="startReject(g.name)">否决该条目</button>
        </div>
        <div v-for="(c, i) in g.rows" :key="i" class="sc-vals">
          <div v-for="(v, j) in c.values" :key="j" class="sc-val">· {{ valueLine(v) }}</div>
        </div>
      </div>

      <div v-if="rejecting" class="sc-reject">
        <div class="meta">否决「{{ rejecting.name }}」——原因会写进墓碑，以后自己能看懂：</div>
        <input v-model="rejecting.reason" class="sc-input"
               placeholder="例：与第 3 章定稿的设定冲突，取『档案室整理员』那一版" />
        <div class="sc-reject-actions">
          <button class="mini" @click="rejecting = null">取消</button>
          <button class="mini danger" :disabled="!rejecting.reason.trim()" @click="confirmReject">确认否决</button>
        </div>
      </div>

      <details v-if="tombstones.length" class="sc-tombs">
        <summary>已否决名单（{{ tombstones.length }}）</summary>
        <div class="meta">{{ tombstones.join("、") }}</div>
      </details>
    </template>
  </div>
</template>

<style scoped>
.sc-summary { display: flex; gap: 18px; flex-wrap: wrap; align-items: baseline; margin: 10px 0 6px; }
.sc-stat b { font-size: 16px; }
.sc-bad { color: var(--bad); }
.sc-ok { color: var(--ok); }
.sc-unchecked {
  margin: 10px 0; padding: 10px 12px; border-radius: 8px;
  border-left: 3px solid var(--warn); background: var(--field-dim);
}
.sc-item {
  border: 1px solid var(--border); border-radius: 8px;
  padding: 10px 12px; margin: 8px 0; background: var(--field-inset);
}
.sc-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.sc-field {
  font-size: 11.5px; padding: 1px 7px; border-radius: 4px;
  border: 1px solid var(--border); background: var(--field); color: var(--muted);
}
.sc-vals { margin-top: 6px; font-size: 12.5px; line-height: 1.75; opacity: 0.85; }
.sc-val { word-break: break-word; }
.sc-reject {
  margin-top: 10px; padding: 10px 12px; border-radius: 8px;
  border: 1px solid var(--border); background: var(--field-dim);
}
.sc-input {
  width: 100%; margin-top: 6px; padding: 8px 10px; font-size: 13px;
  border: 1px solid var(--border); border-radius: 8px;
  background: var(--field); color: var(--ink); font-family: inherit;
}
.sc-input:focus { outline: none; border-color: var(--accent); }
.sc-reject-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 8px; }
.sc-tombs { margin-top: 12px; }
.sc-tombs summary { cursor: pointer; font-size: 12.5px; color: var(--muted); }
</style>
