<!--
  新手序列引导（I-1，2026-11-03）

  ## 要治的病
  作者原话：“随便找个破游戏的新手教程都更好”。旧版是五条并列文本 + 三个就绪
  小圆点，不告诉你“现在在哪一步、下一步点哪个按钮”。

  ## 做法
  两步式界面：
    · 收起态 = 一条横幅：下一步是什么 + 怎么做（一句话）+ [带我去] + 进度；
    · 展开态 = 完整 8 步序列，✓ 已完成 / ● 当前 / ○ 未到，当前步给出具体点击路径。

  ## 判据来自后端
  `GET /onboarding/status`（utils 层确定性计算，零 token）。本组件不自己判断
  “素材有没有 / 该不该审批”，否则同一件事会出现两份口径。
-->
<template>
  <section v-if="visible && data" class="ob-card">
    <!-- 收起态：一条“下一步”横幅 -->
    <div class="ob-head">
      <span class="ob-badge">上手</span>
      <div class="ob-next">
        <template v-if="data.next">
          <div class="ob-next-title">
            下一步：<b>{{ data.next.title }}</b>
            <span class="ob-count">已完成 {{ data.progress.done }} / {{ data.progress.total }}</span>
          </div>
          <div class="ob-next-how">{{ data.next.how }}</div>
        </template>
        <template v-else>
          <div class="ob-next-title">八步全部完成 <span class="ob-count">已完成 8 / 8</span></div>
          <div class="ob-next-how">成品在导出页签；想再写一本 → 项目页签新建项目。</div>
        </template>
      </div>
      <span class="spacer"></span>
      <button v-if="data.next" class="mini primary" :disabled="busy" @click="go(data.next)">
        带我去 →
      </button>
      <button class="mini" @click="open = !open">{{ open ? "收起步骤" : "全部步骤" }}</button>
      <button class="mini" title="关闭引导（随时可点“重看引导”再打开）" @click="$emit('dismiss')">✕</button>
    </div>

    <div class="ob-bar"><div class="ob-bar-in" :style="{ width: pct + '%' }"></div></div>

    <!-- 展开态：完整序列 -->
    <ol v-if="open" class="ob-steps">
      <li v-for="(s, i) in data.steps" :key="s.id" class="ob-step" :class="'is-' + s.state">
        <span class="ob-dot" :title="stateLabel(s.state)">
          {{ s.state === "done" ? "✓" : (s.state === "current" ? "●" : i + 1) }}
        </span>
        <div class="ob-step-main">
          <div class="ob-step-title">
            {{ s.title }}
            <span v-if="s.state === 'current'" class="ob-cur-tag">该你了</span>
          </div>
          <div class="ob-step-ev">{{ s.evidence }}</div>
          <div v-if="s.state === 'current' && s.how" class="ob-step-how">点这里：{{ s.how }}</div>
        </div>
        <button v-if="s.state === 'current'" class="mini primary" @click="go(s)">带我去</button>
        <button v-else-if="s.state === 'todo' && s.action && s.action.kind === 'tab'"
                class="mini ob-jump" :title="'跳到 ' + s.title" @click="go(s)">去看</button>
      </li>
    </ol>

    <div v-if="err" class="ob-err">{{ err }}</div>

    <!-- 辅助入口（保留旧五步卡里的两个能力，不让它们随旧卡一起消失） -->
    <div class="ob-foot">
      <button class="mini" @click="$emit('help')">使用手册</button>
      <button class="mini" @click="$emit('agent')">接入外部 Agent（可选）</button>
      <span class="meta">卡住了先看手册；Ctrl+K 可搜所有操作</span>
    </div>
  </section>
</template>

<script setup>
import { computed, ref, watch } from "vue";

const props = defineProps({
  api: { type: Function, required: true },
  visible: { type: Boolean, default: false },
  // 父组件每次 refresh 自增；本组件据此重算（状态变了就换“下一步”）
  reloadKey: { type: [Number, String], default: 0 },
});
const emit = defineEmits(["dismiss", "go", "help", "agent"]);

const data = ref(null);
const err = ref("");
const open = ref(false);
const busy = ref(false);

const pct = computed(() => {
  const p = data.value && data.value.progress;
  if (!p || !p.total) return 0;
  return Math.round((p.done / p.total) * 100);
});

function stateLabel(s) {
  return s === "done" ? "已完成" : (s === "current" ? "当前这一步" : "还没轮到");
}

async function load() {
  if (!props.visible) return;
  busy.value = true;
  try {
    const r = await props.api("/onboarding/status");
    if (r && r.status === 200 && r.data && r.data.ok) {
      data.value = r.data;
      err.value = "";
    } else {
      err.value = "引导状态读不到：" + ((r && r.data && r.data.error) || (r && r.status) || "无响应");
    }
  } catch (e) {
    err.value = "引导状态读不到：" + (e && e.message ? e.message : e);
  } finally {
    busy.value = false;
  }
}

/** “带我去”：把后端给的 action 原样交给父组件（页面跳转 / 开向导）。 */
function go(step) {
  emit("go", step.action || { kind: "tab", tab: "pipeline" });
  open.value = false;
}

watch(() => [props.visible, props.reloadKey], load, { immediate: true });
</script>
