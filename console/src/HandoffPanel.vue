<script setup>
/**
 * 交接稿面板（2026-10-20，roadmap v0.10“可带走”）。
 *
 * 一键把“写到哪了 + 接下来写什么”拼成 data/handoff/第N章后.md：
 *   作品身份 / 写作要求 / 设定摘要 / 未回收伏笔 / 最近章节 / 下一章大纲（全文内联）。
 * 纯确定性拼装（零 token），可直接把全文粘给任何模型 —— 换模型/换会话不再靠重跑。
 *
 * 关键取舍：**缺料明说，不编造**。下一章大纲没生成、滚动摘要为空，稿里写明
 * “尚未生成 + 怎么补”而不是拿空段糊过去 —— 接手方必须知道哪里是空的。
 *
 * 2026-11-27：样式迁到 Tailwind + 主题变量（写作约束之后第二个整组件）。
 * 外壳那三条声明与全局工具类 `.sep-dashed` 逐字相同 → 直接复用，不另起类名。
 * 三颗按钮换 `UiButton`：导出是这一块唯一要点的动作 → primary；复制 = default；打开文件 = ghost
 * （原来用 `.mini.ghost`，字色 opacity 0.35 看着像禁用；ghost 档用 --muted 字色，弱但不瞎）。
 * 有意的一处像素变化：预览块圆角 6px → var(--r-sm)=8px（6px 不在两档圆角尺度里）。
 */
import { ref, computed } from "vue";

import UiButton from "./components/ui/UiButton.vue";
import { API } from "./apiBase.js";
import { copyToClipboard } from "./clipboard.js";

const emit = defineEmits(["say"]);

const busy = ref(false);
const result = ref(null);
const err = ref("");

const problems = computed(() => (result.value && result.value.problems) || []);

async function exportNow() {
  busy.value = true;
  err.value = "";
  try {
    const r = await fetch(API + "/handoff/export", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Mofang-Source": "gui" },
      body: JSON.stringify({}),
    });
    const d = await r.json().catch(() => ({}));
    if (r.status === 200 && d.ok) {
      result.value = d;
      emit("say", "交接稿已导出：" + d.path);
    } else {
      err.value = d.error || ("导出失败：HTTP " + r.status);
    }
  } catch (e) {
    err.value = String(e);
  } finally {
    busy.value = false;
  }
}

async function copyAll() {
  if (!result.value) return;
  const ok = await copyToClipboard(result.value.text);
  emit("say", ok === false ? "复制失败" : "交接稿全文已复制 —— 直接粘给接手的模型/会话");
}

async function openFile() {
  if (!result.value) return;
  // openArtifact 只收工作区相对路径；导出路径可能是绝对（API 场景）或相对（CLI 场景）
  const norm = String(result.value.path).replace(/\\/g, "/");
  const rel = norm.includes("/data/handoff/")
    ? "data/handoff/" + norm.split("/data/handoff/").pop()
    : norm;
  if (window.mofangAPI?.openArtifact) {
    const r = await window.mofangAPI.openArtifact(rel);
    if (r && r.ok === false) emit("say", r.error || "打开失败");
  } else {
    emit("say", "交接稿路径：" + result.value.path);
  }
}

</script>

<template>
  <div class="sep-dashed">
    <div class="row-baseline mb-2">
      <b class="text-[color:var(--ink)]">交接稿</b>
      <span class="meta">换模型 / 换会话前导出一份，新会话只靠它就能接写下一章（零 token）</span>
      <span class="spacer"></span>
      <UiButton variant="primary" :disabled="busy" @click="exportNow">
        {{ busy ? "拼装中…" : result ? "重新导出" : "导出交接稿" }}
      </UiButton>
      <UiButton :disabled="!result" @click="copyAll">复制全文</UiButton>
      <UiButton variant="ghost" :disabled="!result" @click="openFile">打开文件</UiButton>
    </div>

    <div v-if="err" class="meta text-[color:var(--bad)]">{{ err }}</div>

    <template v-if="result">
      <div class="meta mb-1.5">
        已写到第 {{ result.chapter }} 章 · 交接给<b>第 {{ result.next_chapter }} 章</b>
        · {{ result.path }}
      </div>
      <div v-if="problems.length"
           class="mb-2 border-l-2 border-[var(--warn)] bg-[var(--field-inset)] px-2 py-1.5">
        <div class="meta"><b>缺资料</b>（稿里已明说哪里是空的，接手方不会拿到编造的内容）：</div>
        <div v-for="(p, i) in problems" :key="i" class="meta">· {{ p }}</div>
      </div>
      <pre class="m-0 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-[var(--r-sm)] border border-[var(--border)] bg-[var(--field-inset)] px-3 py-2.5 font-[family-name:var(--mono)] text-[length:var(--fs-ui)] leading-[1.6] text-[color:var(--ink)]">{{ result.text }}</pre>
    </template>

    <div v-else-if="!busy" class="meta pb-0.5 pt-1">
      还没导出过。点“导出交接稿”拼装当前状态 —— 设定摘要、未回收伏笔、最近章节、
      下一章大纲都会内联进一份 markdown。
    </div>
  </div>
</template>
