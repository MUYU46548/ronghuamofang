<template>
  <div data-panel="setting-closure" class="card">
    <div class="card-head">
      <h3>素材裁决（把设定库这摊收干净）</h3>
      <span class="spacer"></span>
      <span class="meta mr-2" v-if="data && data.audited_at">
        审计于 {{ String(data.audited_at).replace("T", " ") }}</span>
      <UiButton :disabled="busy" @click="load">{{ busy ? "读取中…" : "重新审计" }}</UiButton>
    </div>
    <div class="meta">
      机器裁不了的字段分歧（同名卡在不同素材里说法不一）。拍板会写进设定集和否决记录：
      <b>否决按名字生效</b>（下次归并不会再把它带回来）。
    </div>

    <div v-if="err" class="empty py-3.5">{{ err }}</div>
    <div v-else-if="!data" class="meta py-2.5">读取中…</div>

    <!-- checked=false 必须显式说出来：空清单 != 没有冲突 -->
    <div v-else-if="!data.checked"
         class="my-2.5 rounded-[var(--r-sm)] border-l-[3px] border-[var(--warn)] bg-[var(--field-dim)] px-3 py-2.5">
      没查成：{{ data.reason || "原因未知" }}
    </div>

    <template v-else>
      <div class="my-2.5 flex flex-wrap items-baseline gap-4">
        <span class="meta"><b class="text-[18px] text-[color:var(--ink)]">{{ data.card_count ?? 0 }}</b> 张素材卡</span>
        <span class="meta"><b class="text-[18px]" :class="conflicts.length ? 'text-[color:var(--bad)]' : 'text-[color:var(--ink)]'">{{ conflicts.length }}</b> 处待裁决</span>
        <span class="meta"><b class="text-[18px] text-[color:var(--ink)]">{{ counts.adopted ?? 0 }}</b> 已采纳</span>
        <span class="meta"><b class="text-[18px] text-[color:var(--ink)]">{{ tombstones.length }}</b> 条否决记录</span>
      </div>

      <!-- 0 张素材卡：这是“没扫到”，不是“没矛盾”——用警告色，别用绿。 -->
      <div v-if="!groups.length && !data.card_count"
           class="meta text-[color:var(--warn)] leading-[1.7]">
        没有待裁决分歧 —— 但这不等于素材之间没矛盾：这次一份素材卡都没认出来。
        {{ data.hint }}
      </div>
      <div v-else-if="!groups.length && !manual.length" class="meta text-[color:var(--ok)]">没有待裁决分歧。</div>

      <div v-for="g in groups" :key="g.name + g.field" class="my-2 rounded-[var(--r-sm)] border border-[var(--border)] bg-[var(--field-inset)] px-3 py-2.5">
        <div class="row-wrap">
          <b class="text-[color:var(--ink)]">{{ g.name }}</b>
          <UiBadge variant="neutral">{{ g.field }}</UiBadge>
          <span class="spacer"></span>
          <UiButton :disabled="!!acting" @click="act(g.name, 'adopt')">采纳当前</UiButton>
          <UiButton :disabled="!!acting" @click="act(g.name, 'uncertain')">先挂着</UiButton>
          <UiButton variant="danger" :disabled="!!acting" @click="startReject(g.name)">否决该条目</UiButton>
        </div>
        <div v-for="(c, i) in g.rows" :key="i" class="mt-1.5">
          <div v-for="(v, j) in c.values" :key="j"
               class="text-[length:var(--fs-ui)] leading-[1.7] text-[color:var(--ink)]">· {{ valueLine(v) }}</div>
        </div>
      </div>

      <!-- 人工整理的分歧（2026-11-14）：素材是自由文本时，自动检测认不出卡格式，
           只能先由人/Agent 把分歧摆齐，这里逐条选一版。裁决写进 decision_ledger.json。 -->
      <div v-if="manual.length" class="mt-4 border-t border-[var(--border)] pt-3">
        <h4 class="m-0 mb-1.5 text-[length:var(--fs-body)] font-semibold text-[color:var(--ink)]">
          要你选一版的分歧
          <span class="meta">（{{ manualPending }} 条待裁 / 共 {{ manual.length }} 条）</span>
        </h4>
        <div class="meta">
          这些是从你的设定稿、名词表、角色名册里读出来的：同一件事有两种说法。
          每条选一版，或者保持设定集现状。<b>裁决只记账，不会自动改设定集</b>——改不改是下一步。
        </div>
        <div v-for="m in manual" :key="m.id" class="my-2 rounded-[var(--r-sm)] border border-[var(--border)] bg-[var(--field-inset)] px-3 py-2.5">
          <div class="row-wrap">
            <b class="text-[color:var(--ink)]">{{ m.name }}</b>
            <UiBadge variant="neutral">{{ m.field }}</UiBadge>
            <span class="text-[length:var(--fs-ui)] text-[color:var(--muted)]">#{{ m.id }}</span>
            <span v-if="m.current" class="meta ml-2">设定集现在：{{ m.current }}</span>
          </div>
          <div v-if="m.question" class="my-1.5 text-[length:var(--fs-ui)] leading-[1.7] text-[color:var(--ink)]">{{ m.question }}</div>
          <div v-for="(v, i) in m.values" :key="i"
               class="flex flex-wrap items-baseline gap-2.5 py-0.5">
            <span class="text-[length:var(--fs-ui)] text-[color:var(--ink)]">{{ v.value }}</span>
            <span v-if="v.file" class="meta flex-1">{{ v.file }}</span>
            <UiButton :disabled="!!resolving" @click="startResolve(m, v.value)">选这版</UiButton>
          </div>
          <div v-if="m.suggestion" class="meta my-1.5">整理者的建议：{{ m.suggestion }}（可以推翻）</div>
          <div v-if="m.decided"
               class="mt-1.5 rounded-[var(--r-sm)] border-l-[3px] border-[var(--ok)] bg-[var(--field-dim)] px-2.5 py-1.5 text-[length:var(--fs-ui)] leading-[1.7] text-[color:var(--ink)]">
            已裁决：<b>{{ chosenLabel(m.decided.chosen) }}</b>（{{ m.decided.at }}）—— {{ m.decided.reason }}
          </div>
          <div v-else class="mt-1.5 flex gap-2">
            <UiButton :disabled="!!resolving" @click="startResolve(m, '__keep__')">保持现状</UiButton>
            <UiButton variant="danger" :disabled="!!resolving" @click="startResolve(m, '__reject__')">这些说法都不采用</UiButton>
          </div>
        </div>
      </div>

      <div v-if="resolving" class="my-2.5 rounded-[var(--r-sm)] border border-[var(--border)] bg-[var(--field-dim)] px-3 py-2.5">
        <div class="meta">
          裁决“{{ resolving.id }} → {{ chosenLabel(resolving.chosen) }}”——为什么选它：
        </div>
        <UiInput v-model="resolving.reason" class="mt-2"
                 placeholder="例：4-4 稿给了改姓的制度理由（无姓者不能立契），另两稿只写“姓氏待定”" />
        <div class="mt-2 flex justify-end gap-2">
          <UiButton @click="resolving = null">取消</UiButton>
          <UiButton variant="primary" :disabled="!resolving.reason.trim() || resolvingBusy"
                    @click="confirmResolve">{{ resolvingBusy ? "记录中…" : "确认裁决" }}</UiButton>
        </div>
      </div>

      <div v-if="rejecting" class="my-2.5 rounded-[var(--r-sm)] border border-[var(--border)] bg-[var(--field-dim)] px-3 py-2.5">
        <div class="meta">否决“{{ rejecting.name }}”——原因会写进墓碑，以后自己能看懂：</div>
        <UiInput v-model="rejecting.reason" class="mt-2"
                 placeholder="例：与第 3 章定稿的设定冲突，取‘档案室整理员’那一版" />
        <div class="mt-2 flex justify-end gap-2">
          <UiButton @click="rejecting = null">取消</UiButton>
          <UiButton variant="danger" :disabled="!rejecting.reason.trim()" @click="confirmReject">确认否决</UiButton>
        </div>
      </div>

      <details v-if="tombstones.length" class="mt-3">
        <summary class="cursor-pointer text-[length:var(--fs-ui)] text-[color:var(--muted)]">
          已否决名单（{{ tombstones.length }}）</summary>
        <div class="meta">{{ tombstones.join("、") }}</div>
      </details>
    </template>
  </div>
</template>

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

import UiBadge from "./components/ui/UiBadge.vue";
import UiButton from "./components/ui/UiButton.vue";
import UiInput from "./components/ui/UiInput.vue";

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
