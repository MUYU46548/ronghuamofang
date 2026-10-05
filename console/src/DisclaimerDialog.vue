<script setup>
// 「免责声明」弹窗：A 版首启强制 + B 版完整查看。
// 模式：
//   first-run：A 版（短版七段）——必须滚动读完+勾选同意；无遮罩点击关闭、无 ESC 旁路、
//             tab 焦点困于弹窗内（真锁模态，非视觉遮挡——梅林验收断言 5）；
//             按钮 =「不同意并退出」（window.close()）+「我已阅读并同意」（禁用直至勾选）。
//             A 版内可点「查看完整声明」切换 B 视图（不能强迫先同意才给看全文），B 视图可返回。
//   view：B 版（完整八节）——普通弹窗，底部显示既往确认时间；关闭即返回。
// 入口：首启自动（App.vue onMounted）/ 关于弹窗按钮 / 命令面板 / 设置页。
// 文案：./disclaimer.js 单一事实源（哨兵草案 A/B 双版），暮雨划线改字后只改那个文件。
import { ref, computed, watch, nextTick, onBeforeUnmount } from "vue";
import {
  DISCLAIMER_VERSION, DISCLAIMER_ACK_KEY,
  DISCLAIMER_SHORT_SECTIONS, DISCLAIMER_FULL_SECTIONS,
} from "./disclaimer.js";

const props = defineProps({
  open: { type: Boolean, default: false },
  mode: { type: String, default: "view" },   // 'first-run' | 'view'
  ackedAt: { type: String, default: "" },
});
const emit = defineEmits(["close", "ack"]);

// ---- 视图状态（first-run 模式下 A/B 切换）----
const detailView = ref(false);   // false=A 短版 / true=B 完整版
const sections = computed(() => detailView.value ? DISCLAIMER_FULL_SECTIONS : DISCLAIMER_SHORT_SECTIONS);
const forced = computed(() => props.mode === "first-run");

// ---- 强制模式交互状态 ----
const scrolledToEnd = ref(true);   // 内容不足一屏时无需滚动即视为已到底
const agreed = ref(false);
const bodyEl = ref(null);
const dlgEl = ref(null);

function checkScroll() {
  const el = bodyEl.value;
  if (!el) return;
  scrolledToEnd.value = el.scrollTop + el.clientHeight >= el.scrollHeight - 24;
}

watch(() => props.open, async (v) => {
  if (v) {
    agreed.value = false;
    detailView.value = false;
    await nextTick();
    if (bodyEl.value) checkScroll();
    // 焦点移入弹窗（配合 focus trap）
    await nextTick();
    const first = dlgEl.value?.querySelector("button, input, [tabindex]");
    if (first) first.focus();
  }
});

// ---- 同意 / 退出 ----
function onAgree() {
  try {
    localStorage.setItem(DISCLAIMER_ACK_KEY, JSON.stringify({ v: DISCLAIMER_VERSION, at: new Date().toISOString() }));
  } catch { /* localStorage 不可用时按已同意处理（不阻塞使用） */ }
  emit("ack");
}

function onExit() {
  // 不同意 = 退出软件。走 window.close()：触发主进程 before-quit 钩子（quitGuard 存盘确认照常工作）
  window.close();
}

// ---- focus trap（真锁模态：tab 循环困于弹窗内，梅林验收断言 5）----
function onKeydownTab(e) {
  if (e.key !== "Tab") return;
  const dlg = dlgEl.value;
  if (!dlg) return;
  const focusables = dlg.querySelectorAll('button, input, select, textarea, a[href], [tabindex]:not([tabindex="-1"])');
  if (!focusables.length) return;
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  const active = dlg.contains(document.activeElement) ? document.activeElement : null;
  if (e.shiftKey) {
    if (!active || active === first) { e.preventDefault(); last.focus(); }
  } else {
    if (!active || active === last) { e.preventDefault(); first.focus(); }
  }
}

const keyHandler = (e) => {
  if (!forced.value || !props.open) return;
  // 强制模式：ESC 不关（view 模式 ESC 关闭交给 App.vue 全局处理）
  if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); }
  onKeydownTab(e);
};

watch(() => [props.open, props.mode], ([o]) => {
  if (o && forced.value) document.addEventListener("keydown", keyHandler, true);
  else document.removeEventListener("keydown", keyHandler, true);
}, { immediate: true });

onBeforeUnmount(() => document.removeEventListener("keydown", keyHandler, true));

function fmtTime(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    return d.toLocaleString("zh-CN", { dateStyle: "medium", timeStyle: "short" });
  } catch { return iso; }
}
</script>

<template>
  <div v-if="open" class="drawer-mask disclaimer-mask" :class="{ forced }"
       @keydown="onKeydownTab">
    <div class="dialog disclaimer-dlg" ref="dlgEl" role="dialog"
         aria-modal="true" aria-label="免责与知情声明">
      <div class="card-head" style="margin-bottom: 6px;">
        <h3>{{ forced ? (detailView ? "免责与知情声明 · 完整版" : "使用前请确认 · 免责与知情声明") : "免责与知情声明 · 完整版" }}</h3>
        <span v-if="!forced" class="meta">v{{ DISCLAIMER_VERSION }}</span>
      </div>

      <div class="disclaimer-body" ref="bodyEl" @scroll="checkScroll">
        <template v-if="sections.length">
          <template v-for="(s, i) in sections" :key="i">
            <h4>{{ s.title }}</h4>
            <p v-for="(p, j) in s.paras" :key="'p' + j">{{ p }}</p>
            <table v-if="s.table" class="cost-table" style="margin: 4px 0 6px;">
              <thead>
                <tr><th v-for="h in s.table.head" :key="h">{{ h }}</th></tr>
              </thead>
              <tbody>
                <tr v-for="(row, r) in s.table.rows" :key="r">
                  <td v-for="(c, ci) in row" :key="ci">{{ c }}</td>
                </tr>
              </tbody>
            </table>
            <p v-for="(p, j) in (s.parasAfterTable || [])" :key="'pa' + j">{{ p }}</p>
          </template>
        </template>
      </div>

      <!-- 首启强制模式 -->
      <template v-if="forced">
        <template v-if="!detailView">
          <div class="disclaimer-ack">
            <label class="disclaimer-check">
              <input type="checkbox" v-model="agreed" :disabled="!scrolledToEnd" />
              我已阅读并同意以上全部内容
            </label>
            <span class="spacer"></span>
            <button class="mini danger" @click="onExit">不同意并退出</button>
            <button class="mini primary" :disabled="!agreed" @click="onAgree">我已阅读并同意</button>
          </div>
          <div class="meta" style="margin-top: 6px;">
            未滚到条款底部前无法勾选 · 不同意则退出软件（不强迫使用） ·
            <a href="javascript:void(0)" @click="detailView = true" style="color: var(--accent);">查看完整声明 →</a>
            · 声明更新后会在下次启动时再次弹出
          </div>
        </template>
        <template v-else>
          <div class="dialog-actions">
            <button class="mini" @click="detailView = false">← 返回确认页</button>
          </div>
        </template>
      </template>

      <!-- 查看模式 -->
      <template v-else>
        <div class="dialog-actions">
          <span class="meta" v-if="ackedAt">您已于 {{ fmtTime(ackedAt) }} 确认本声明（v{{ DISCLAIMER_VERSION }}）</span>
          <span class="meta" v-else style="color: var(--bad);">本机尚未确认过本声明</span>
          <span class="spacer"></span>
          <button class="mini primary" @click="$emit('close')">关闭</button>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
/* 强制弹窗必须压住所有普通抽屉（全局 drawer-mask 为 z-20，命令面板更高） */
.disclaimer-mask { z-index: 60; }
.disclaimer-mask.forced {
  /* 首启强制：加深遮罩，无点击关闭暗示（模板层刻意不给 @click.self） */
  background: rgba(40, 44, 58, 0.55);
}
.disclaimer-dlg {
  width: min(640px, 92vw);
  max-height: 86vh;
  display: flex;
  flex-direction: column;
}
.disclaimer-body {
  overflow-y: auto;
  flex: 1;
  min-height: 200px;
  max-height: 56vh;
  padding: 2px 6px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--field-inset, rgba(127, 127, 127, 0.06));
}
.disclaimer-body h4 { margin: 12px 0 4px; font-size: 13.5px; }
.disclaimer-body h4:first-child { margin-top: 4px; }
.disclaimer-body p { margin: 0 0 6px; font-size: 13px; line-height: 1.75; }
.disclaimer-ack {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
}
.disclaimer-check { display: flex; align-items: center; gap: 6px; font-size: 13px; cursor: pointer; }
.disclaimer-check input { cursor: pointer; }
</style>
