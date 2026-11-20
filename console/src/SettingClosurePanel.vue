<script setup>
/**
 * 设定库闭环 · 素材裁决面板（2026-10-20，台账三·“设定库闭环”）。
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
// 裁决是 GUI-only 端点（作者签字），必须走双因子；自己拼 fetch 会 403。
import { postJSON } from "./guiApi.js";

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
// 人工整理的分歧（自由文本素材用）：与自动检出分开渲染 —— 它要“选哪一版”，
// 不是“采纳/否决这个名字”，两种动作混在一张卡里会点错。
const manual = computed(() => conflicts.value.filter((c) => c.source === "manual"));
const manualPending = computed(() => manual.value.filter((c) => !c.decided).length);
const autoConflicts = computed(() => conflicts.value.filter((c) => c.source !== "manual"));
const counts = computed(() => data.value?.counts || {});
const tombstones = computed(() => data.value?.tombstones || []);
// 同名同字段合并成一组：三条分歧摊成三行会让人以为要分别拍板三次
const groups = computed(() => {
  const map = new Map();
  for (const c of autoConflicts.value) {
    const key = c.name + "||" + c.field;
    if (!map.has(key)) map.set(key, { name: c.name, field: c.field, rows: [] });
    map.get(key).rows.push(c);
  }
  return [...map.values()];
});

// ---------- 人工分歧的裁决（写进 decision_ledger.json，只追加） ----------
const resolving = ref(null);   // {id, chosen, reason}
const resolvingBusy = ref(false);

function startResolve(m, chosen) {
  resolving.value = { id: m.id, chosen, reason: "" };
}

async function confirmResolve() {
  const r = resolving.value;
  if (!r) return;
  if (!r.reason.trim()) { say("裁决必须写理由 —— 三天后翻回来看，没有理由的裁决等于没裁"); return; }
  resolvingBusy.value = true;
  try {
    const d = await postJSON("/setting/conflict/resolve",
                             { id: r.id, chosen: r.chosen, reason: r.reason.trim() });
    say(d.note || "已记账");
    resolving.value = null;
    await load();
  } catch (e) {
    say("裁决失败：" + String(e.message || e));
  }
  resolvingBusy.value = false;
}

function chosenLabel(chosen) {
  if (chosen === "__keep__") return "保持设定集现状";
  if (chosen === "__reject__") return "这些说法都不采用";
  return chosen;
}

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
      <h3>素材裁决（把设定库这摊收干净）</h3>
      <span class="spacer"></span>
      <span class="meta" v-if="data && data.audited_at">审计于 {{ String(data.audited_at).replace("T", " ") }}</span>
      <button class="mini" :disabled="busy" @click="load">{{ busy ? "读取中…" : "重新审计" }}</button>
    </div>
    <div class="meta">
      机器裁不了的字段分歧（同名卡在不同素材里说法不一）。拍板会写进设定集和否决记录：
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
        <span class="sc-stat"><b>{{ tombstones.length }}</b> 条否决记录</span>
      </div>

      <div v-if="!groups.length && !data.card_count" class="meta sc-warn">
        没有待裁决分歧 —— 但这不等于素材之间没矛盾：这次一份素材卡都没认出来。
        {{ data.hint }}
      </div>
      <div v-else-if="!groups.length && !manual.length" class="meta sc-ok">没有待裁决分歧。</div>

      <div v-for="g in groups" :key="g.name + g.field" class="sc-item">
        <div class="sc-head row-wrap">
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

      <!-- 人工整理的分歧（2026-11-14）：素材是自由文本时，自动检测认不出卡格式，
           只能先由人/Agent 把分歧摆齐，这里逐条选一版。裁决写进 decision_ledger.json。 -->
      <div v-if="manual.length" class="sc-manual">
        <h4>
          要你选一版的分歧
          <span class="meta">（{{ manualPending }} 条待裁 / 共 {{ manual.length }} 条）</span>
        </h4>
        <div class="meta">
          这些是从你的设定稿、名词表、角色名册里读出来的：同一件事有两种说法。
          每条选一版，或者保持设定集现状。<b>裁决只记账，不会自动改设定集</b>——改不改是下一步。
        </div>
        <div v-for="m in manual" :key="m.id" class="sc-item">
          <div class="sc-head row-wrap">
            <b>{{ m.name }}</b>
            <span class="sc-field">{{ m.field }}</span>
            <span class="sc-id">#{{ m.id }}</span>
            <span v-if="m.current" class="meta sc-current">设定集现在：{{ m.current }}</span>
          </div>
          <div v-if="m.question" class="sc-q">{{ m.question }}</div>
          <div v-for="(v, i) in m.values" :key="i" class="sc-val sc-val-pick">
            <span class="sc-val-text">{{ v.value }}</span>
            <span v-if="v.file" class="meta">{{ v.file }}</span>
            <button class="mini" :disabled="!!resolving" @click="startResolve(m, v.value)">选这版</button>
          </div>
          <div v-if="m.suggestion" class="meta sc-suggest">整理者的建议：{{ m.suggestion }}（可以推翻）</div>
          <div v-if="m.decided" class="sc-decided">
            已裁决：<b>{{ chosenLabel(m.decided.chosen) }}</b>（{{ m.decided.at }}）—— {{ m.decided.reason }}
          </div>
          <div v-else class="sc-actions">
            <button class="mini" :disabled="!!resolving" @click="startResolve(m, '__keep__')">保持现状</button>
            <button class="mini danger" :disabled="!!resolving" @click="startResolve(m, '__reject__')">这些说法都不采用</button>
          </div>
        </div>
      </div>

      <div v-if="resolving" class="sc-reject">
        <div class="meta">
          裁决“{{ resolving.id }} → {{ chosenLabel(resolving.chosen) }}”——为什么选它：
        </div>
        <input v-model="resolving.reason" class="sc-input"
               placeholder="例：4-4 稿给了改姓的制度理由（无姓者不能立契），另两稿只写“姓氏待定”" />
        <div class="sc-reject-actions">
          <button class="mini" @click="resolving = null">取消</button>
          <button class="mini primary" :disabled="!resolving.reason.trim() || resolvingBusy"
                  @click="confirmResolve">{{ resolvingBusy ? "记录中…" : "确认裁决" }}</button>
        </div>
      </div>

      <div v-if="rejecting" class="sc-reject">
        <div class="meta">否决“{{ rejecting.name }}”——原因会写进墓碑，以后自己能看懂：</div>
        <input v-model="rejecting.reason" class="sc-input"
               placeholder="例：与第 3 章定稿的设定冲突，取‘档案室整理员’那一版" />
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
/* 0 张素材卡：这是“没扫到”，不是“没矛盾”——用警告色，别用绿。 */
.sc-warn { color: var(--warn, #b8860b); line-height: 1.7; }

/* 人工整理的分歧（2026-11-14）：两方并排，各给一个“选这版”，裁决后可回看理由。 */
.sc-manual { margin-top: 16px; border-top: 1px solid var(--border); padding-top: 12px; }
.sc-manual h4 { margin: 0 0 6px; font-size: 14px; }
.sc-id { font-size: 12px; color: var(--muted); }
.sc-current { margin-left: 8px; }
.sc-q { margin: 4px 0 6px; font-size: 13px; line-height: 1.7; }
.sc-val-pick { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.sc-val-text { font-size: 13px; }
.sc-val-pick .meta { flex: 1; }
.sc-suggest { margin: 6px 0; }
.sc-actions { display: flex; gap: 8px; margin-top: 6px; }
.sc-decided {
  margin-top: 6px; padding: 6px 10px; border-radius: 6px; font-size: 12px; line-height: 1.7;
  border-left: 3px solid var(--ok); background: var(--field-dim); color: var(--ink);
}
.sc-unchecked {
  margin: 10px 0; padding: 10px 12px; border-radius: 8px;
  border-left: 3px solid var(--warn); background: var(--field-dim);
}
.sc-item {
  border: 1px solid var(--border); border-radius: 8px;
  padding: 10px 12px; margin: 8px 0; background: var(--field-inset);
}
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
