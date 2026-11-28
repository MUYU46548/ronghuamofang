<script setup>
/**
 * 用量总览（2026-11-28）—— 成本页的主图表区。
 *
 * 参照两份成熟用量面板的版式（用户 2026-11-28 指定"可直接抄市面上现有同类，
 * 图表做好，可视化做好"）：
 *   - 顶部一行大数 + 同比/预测副行（"Monthly 68% used / On pace for 96%"）
 *   - 主折线图：实线是已用量，虚线+填充是预测外推，横虚线是上限
 *   - 下面三张卡：限额（三条进度条）/ 模型（占比 + 请求与 token 数）/ 剩余空间
 *
 * ## 三条口径纪律（不做就会变成好看的谎）
 *
 * 1. **金额与 token 不能并列当主指标**：订阅引擎下 cost_yuan 恒 0，
 *    摆一个“¥0.00”会让人以为免费或者记账坏了。所以主指标永远是 token；
 *    只有 `money_metered=true` 时才显示金额卡。
 * 2. **预测必须标明是外推**，且样本不足时不给数字（后端 insufficient 时前端只说
 *    "数据不够，不外推"）。虚线 + 条纹填充本身就是"这不是实测"的视觉语言。
 * 3. **本期是滚动窗口，不是自然月**。后端 `period.note` 已经把话说清，
 *    这里原样透出来 —— 不改成"本月"这种更顺口但错误的说辞。
 *
 * 样式：Tailwind + 主题变量，无 `<style scoped>`。
 * SVG 用属性写 `var(--…)`（合法且跟主题走），散点/端点用卡片色描边做视觉分隔。
 */
import { computed } from "vue";

import UiBadge from "./components/ui/UiBadge.vue";
import UiTable from "./components/ui/UiTable.vue";

const props = defineProps({
  // GET /costs/overview 的回包；null = 还没加载
  data: { type: Object, default: null },
});

const W = 720;          // viewBox 宽（随容器缩放）
const H = 220;
const PAD_L = 58;       // 左侧留给 Y 轴刻度
const PAD_R = 16;
const PAD_T = 14;
const PAD_B = 30;       // 底部留给日期

const daily = computed(() => (props.data && props.data.daily) || []);
const period = computed(() => (props.data && props.data.period) || {});
const forecast = computed(() => (props.data && props.data.forecast) || {});
const byModel = computed(() => (props.data && props.data.by_model) || []);

const hasData = computed(() => daily.value.length > 0);
const hasForecast = computed(() => forecast.value.status === "ok");

// ---- Y 轴量程：要同时装下实测峰值、预测终点、以及上限（若上限不太离谱）----
// 上限 1000 万是常态，硬画进来会把实测压成一条贴地直线 —— 所以只有上限
// 落在实测峰值的 4 倍以内才画上限线，否则单独用文字说"离上限还有 N%"。
const peak = computed(() =>
  Math.max(1, ...daily.value.map((d) => d.tokens_total || 0)));

const showLimit = computed(() => {
  const lim = period.value.token_limit;
  return !!lim && lim <= peak.value * 4;
});

const yMax = computed(() => {
  const cands = [peak.value];
  if (hasForecast.value && forecast.value.projected_tokens) {
    cands.push(forecast.value.projected_tokens / Math.max(1, period.value.days || 30)
               * 1.25);
  }
  if (showLimit.value) cands.push(period.value.token_limit);
  const m = Math.max(...cands);
  // 取整到"好看的刻度"：1/2/5 × 10^n
  const mag = Math.pow(10, Math.floor(Math.log10(m || 1)));
  const n = m / mag;
  const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
  return step * mag;
});

function yOf(v) {
  const inner = H - PAD_T - PAD_B;
  return PAD_T + inner * (1 - Math.min(1, (v || 0) / yMax.value));
}

// 零值那天：折线会落在底边（y == H - PAD_B），与面积填充的底边重合，
// 视觉上看不出"那天确实有数据点、值是 0"。给它抬 1.5px —— 
// 足够看见，又不会被误读成"那天有点量"。
function yOfDot(v) {
  const inner = H - PAD_T - PAD_B;
  const y = yOf(v);
  return (v || 0) === 0 ? y - 1.5 : y;
}

const gridY = computed(() => {
  const out = [];
  for (let i = 0; i <= 4; i++) out.push(PAD_T + (H - PAD_T - PAD_B) * (i / 4));
  return out;
});

const gridLabel = computed(() => {
  const out = [];
  for (let i = 0; i <= 4; i++) out.push(yMax.value * (1 - i / 4));
  return out;
});

// ---- X 轴：数据段只占左侧，右侧留出预测延伸区 ----
// 2026-11-28 实测踩过：原来 xOf 把 n 天铺满整个宽度，于是最后一个实测点
// 正好落在最右边界（x == W - PAD_R），预测段的 x0 与 x1 重合 —— 折线退化成
// 一条竖线、条纹区域宽度为 0，vision 目检"看不到条纹"就是这个原因。
// 现在：有预测时数据段只占 72%，剩下 28% 专给外推。
const FORECAST_SHARE = 0.28;

const dataSpan = computed(() => (hasForecast.value ? 1 - FORECAST_SHARE : 1));

function xOf(i, n) {
  const inner = (W - PAD_L - PAD_R) * dataSpan.value;
  return PAD_L + (n <= 1 ? inner / 2 : inner * (i / (n - 1)));
}

const points = computed(() => {
  const n = daily.value.length;
  return daily.value.map((d, i) =>
    `${xOf(i, n).toFixed(1)},${yOfDot(d.tokens_total).toFixed(1)}`).join(" ");
});

// 面积填充（实测）：线 + 底边闭合
const areaPts = computed(() => {
  const n = daily.value.length;
  if (!n) return "";
  const base = (H - PAD_B).toFixed(1);
  return `${xOf(0, n).toFixed(1)},${base} ${points.value} ${xOf(n - 1, n).toFixed(1)},${base}`;
});

// ---- 预测段：从最后一个实测点，按日均速度斜向拉到右侧外推区末端 ----
// 纵坐标口径：起点是“最后一天的实测”，终点是“按日均速度应该有的一天”。
// 两者可比（都是单日量），所以斜率的含义是"按这个日均速度还会怎么走" ——
// 不是把 30 天总量画成一个日值（旧版就是那样，会得到一根冲天的斜线）。
const forecastLine = computed(() => {
  const n = daily.value.length;
  if (!n || !hasForecast.value) return null;
  const rate = forecast.value.rate_per_active_day || 0;
  if (!rate) return null;
  const lastVal = daily.value[n - 1].tokens_total || 0;
  const x0 = xOf(n - 1, n);
  const x1 = W - PAD_R;
  return {
    x0, y0: yOf(lastVal), x1, y1: yOf(rate),
    endLabel: fmtTok(forecast.value.projected_tokens || 0),
    rate,
  };
});

const forecastArea = computed(() => {
  const f = forecastLine.value;
  if (!f || f.x1 - f.x0 < 1) return "";
  const base = (H - PAD_B).toFixed(1);
  return `${f.x0.toFixed(1)},${base} ${f.x0.toFixed(1)},${f.y0.toFixed(1)} `
       + `${f.x1.toFixed(1)},${f.y1.toFixed(1)} ${f.x1.toFixed(1)},${base}`;
});

const limitY = computed(() =>
  showLimit.value ? yOf(period.value.token_limit) : null);

// ---- X 轴标签：最多 5 个，均匀取 ----
const xTicks = computed(() => {
  const n = daily.value.length;
  if (!n) return [];
  const want = Math.min(5, n);
  const idx = [];
  for (let i = 0; i < want; i++) {
    idx.push(Math.round((n - 1) * (i / Math.max(1, want - 1))));
  }
  return [...new Set(idx)].map((i) => ({
    i, x: xOf(i, n),
    label: (daily.value[i].date || "").slice(5),   // MM-DD
  }));
});

// ---- 顶栏大数 ----
const headline = computed(() => {
  const p = period.value;
  if (p.used_pct == null) return { pct: "—", sub: "token 用量上限未启用" };
  return {
    pct: Math.round(p.used_pct) + "%",
    sub: `本期已用 ${fmtTok(p.tokens_total)} / 上限 ${fmtTok(p.token_limit)}`,
  };
});

const paceLine = computed(() => {
  const f = forecast.value;
  if (!f || f.status !== "ok") return "";
  const pct = period.value.token_limit
    ? Math.round((f.projected_tokens / period.value.token_limit) * 100) : null;
  const tail = pct != null && pct > 100 ? ` · 照此会超上限` : "";
  return `日均 ${fmtTok(f.rate_per_active_day)}（有数据 ${f.active_days} 天）`
       + (pct != null ? ` · 预计期末 ${pct}%${tail}` : "");
});

// ---- 三条限额进度条：只用真实存在的口径 ----
// 墨坊只有一个闸门（单轮累计 token 上限），没有 5 小时 / 每周这种多档订阅限额，
// **不能凭空造**。这里给的是真实存在的口径：本轮累计与预测期末。
const limits = computed(() => {
  const p = period.value;
  const lim = p.token_limit || 0;
  const used = p.tokens_total || 0;
  const rows = [];
  if (lim) {
    rows.push({ name: "本轮已用 / 上限", pct: Math.min(100, used / lim * 100),
                detail: `${fmtTok(used)} / ${fmtTok(lim)}` });
  }
  const f = forecast.value;
  if (f && f.status === "ok" && lim) {
    rows.push({ name: "预计期末（外推）", pct: Math.min(100, f.projected_tokens / lim * 100),
                detail: `${fmtTok(f.projected_tokens)} / ${fmtTok(lim)}`, projected: true });
  }
  return rows;
});

const models = computed(() => {
  const total = byModel.value.reduce((s, m) => s + (m.tokens || 0), 0) || 1;
  return byModel.value.map((m) => ({ ...m, share: (m.tokens / total) * 100 }));
});

const MODEL_COLS = [
  { key: "model", label: "模型" },
  { key: "calls", label: "请求", cls: "w-[80px]" },
  { key: "tokens", label: "token", cls: "w-[110px]" },
  { key: "share", label: "用量占比", cls: "w-[150px]" },
];

// ---- 格式化（本组件自足，不依赖 App.vue 的私有函数）----
function fmtTok(v) {
  const n = Number(v || 0);
  if (!isFinite(n)) return "0";
  if (n >= 1e8) return (n / 1e8).toFixed(2) + " 亿";
  if (n >= 1e4) return (n / 1e4).toFixed(1) + " 万";
  return n.toLocaleString("zh-CN");
}
</script>

<template>
  <div v-if="data && !hasData"
       class="rounded-[var(--r-lg)] border border-[var(--border)] bg-[var(--card)] px-4 py-6 text-center">
    <div class="meta">{{ data.note || "还没有用量记录 —— 跑过任一阶段之后这里会有图表。" }}</div>
  </div>

  <template v-else-if="hasData">
    <!-- 顶部：大数 + 预测副行 -->
    <div class="mb-3 rounded-[var(--r-lg)] border border-[var(--border)] bg-[var(--card)] px-4 py-3.5">
      <div class="mb-1 flex items-baseline gap-3">
        <span class="text-[26px] font-bold leading-none text-[color:var(--ink)]">{{ headline.pct }}</span>
        <span class="text-[length:var(--fs-ui)] text-[color:var(--muted)]">本期用量</span>
        <span class="spacer"></span>
        <UiBadge v-if="period.money_metered" variant="accent">按量计价</UiBadge>
        <UiBadge v-else variant="warn">订阅流量 · 只记 token</UiBadge>
      </div>
      <div class="meta">{{ headline.sub }}</div>
      <div v-if="paceLine" class="meta mt-0.5">{{ paceLine }}</div>
      <div v-if="forecast.hits_limit_on" class="meta mt-0.5">
        照当前速度，预计 <b>{{ forecast.hits_limit_on }}</b> 撞到单轮累计上限
      </div>

      <!-- 主折线图 -->
      <svg :viewBox="`0 0 ${W} ${H}`" class="mt-3 block h-auto w-full" preserveAspectRatio="none">
        <defs>
          <!-- 实测面积：淡色渐变（同色系，不给新色值） -->
          <linearGradient id="costUsageFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="var(--ok)" stop-opacity="0.22"/>
            <stop offset="100%" stop-color="var(--ok)" stop-opacity="0.02"/>
          </linearGradient>
          <!-- 预测区条纹：视觉上明确"这不是实测" -->
          <pattern id="costForecastHatch" width="6" height="6"
                   patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="6" stroke="var(--warn)" stroke-width="2" opacity="0.35"/>
          </pattern>
        </defs>

        <!-- 网格 + Y 轴刻度 -->
        <g v-for="(gy, i) in gridY" :key="'g' + i">
          <line :x1="PAD_L" :y1="gy" :x2="W - PAD_R" :y2="gy"
                stroke="var(--border)" stroke-dasharray="2,4"/>
          <text :x="PAD_L - 8" :y="gy + 3.5" text-anchor="end"
                class="text-[10px] [fill:var(--muted)]">{{ fmtTok(gridLabel[i]) }}</text>
        </g>

        <!-- 上限线 -->
        <line v-if="limitY !== null" :x1="PAD_L" :y1="limitY" :x2="W - PAD_R" :y2="limitY"
              stroke="var(--bad)" stroke-dasharray="5,4" stroke-width="1.5" opacity="0.65"/>
        <text v-if="limitY !== null" :x="W - PAD_R" :y="limitY - 5" text-anchor="end"
              class="text-[10px] [fill:var(--bad)]">本轮用量上限</text>

        <!-- 预测区（画在实测之下，避免盖住实测线） -->
        <polygon v-if="forecastArea" :points="forecastArea"
                 fill="url(#costForecastHatch)" opacity="0.55"/>

        <!-- 实测面积 + 折线 -->
        <polygon :points="areaPts" fill="url(#costUsageFill)"/>
        <polyline :points="points" fill="none" stroke="var(--ok)"
                  stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>

        <!-- 预测虚线 -->
        <line v-if="forecastLine" :x1="forecastLine.x0" :y1="forecastLine.y0"
              :x2="forecastLine.x1" :y2="forecastLine.y1"
              stroke="var(--warn)" stroke-width="2" stroke-dasharray="5,4"/>
        <circle v-if="forecastLine" :cx="forecastLine.x1" :cy="forecastLine.y1" r="4"
                fill="var(--warn)" stroke="var(--card)" stroke-width="2"/>
        <text v-if="forecastLine" :x="forecastLine.x1 - 4" :y="forecastLine.y1 - 9"
              text-anchor="end" class="text-[10px] font-semibold [fill:var(--warn)]">
          预计 {{ forecastLine.endLabel }}
        </text>

        <!-- 实测末点 -->
        <circle :cx="xOf(daily.length - 1, daily.length)"
                :cy="yOf(daily[daily.length - 1].tokens_total)" r="4.5"
                fill="var(--ok)" stroke="var(--card)" stroke-width="2"/>

        <!-- X 轴日期 -->
        <text v-for="t in xTicks" :key="'x' + t.i" :x="t.x" :y="H - 10"
              text-anchor="middle" class="text-[10px] [fill:var(--muted)]">{{ t.label }}</text>
      </svg>

      <div class="mt-2 flex flex-wrap items-center gap-4 text-[length:var(--fs-ui)] text-[color:var(--muted)]">
        <span class="inline-flex items-center gap-1.5">
          <span class="inline-block h-2.5 w-[3px] rounded-sm bg-[var(--ok)]"></span>实测用量
        </span>
        <span v-if="hasForecast" class="inline-flex items-center gap-1.5">
          <span class="inline-block h-2.5 w-[3px] rounded-sm bg-[var(--warn)]"></span>预测（外推）
        </span>
        <span v-if="showLimit" class="inline-flex items-center gap-1.5">
          <span class="inline-block h-0 w-[10px] border-t-2 border-dashed border-[var(--bad)]"></span>本轮用量上限
        </span>
        <span class="spacer"></span>
        <span>{{ period.start }} ~ {{ period.end }}（{{ period.days }} 天滚动窗口）</span>
      </div>
      <div v-if="period.note" class="meta mt-1 opacity-80">{{ period.note }}</div>
      <div v-if="forecast.note" class="meta mt-0.5 opacity-80">{{ forecast.note }}</div>
    </div>

    <!-- 下半：限额 / 模型 / 明细 -->
    <div class="grid gap-3" style="grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));">
      <!-- 限额 -->
      <div class="rounded-[var(--r-lg)] border border-[var(--border)] bg-[var(--card)] px-3.5 py-3">
        <div class="mb-2 flex items-baseline gap-2">
          <b class="text-[length:var(--fs-body)] text-[color:var(--ink)]">用量与上限</b>
          <span class="meta">离上限还有多远</span>
        </div>
        <div v-if="!limits.length" class="meta">本轮用量上限未启用 —— 跑多少都不会自动停，请留意花费。</div>
        <div v-for="l in limits" :key="l.name" class="mb-2.5 last:mb-0">
          <div class="mb-1 flex items-baseline gap-2 text-[length:var(--fs-ui)]">
            <span class="text-[color:var(--ink)]">{{ l.name }}</span>
            <span class="spacer"></span>
            <span :class="l.pct >= 90 ? 'text-[color:var(--bad)]' : 'text-[color:var(--muted)]'">
              {{ l.pct.toFixed(1) }}%
            </span>
          </div>
          <div class="h-1.5 w-full overflow-hidden rounded-full bg-[var(--field-dim)]">
            <div class="h-full rounded-full transition-[width]"
                 :class="l.projected
                   ? 'bg-[var(--warn)] opacity-70'
                   : (l.pct >= 90 ? 'bg-[var(--bad)]' : 'bg-[var(--ok)]')"
                 :style="{ width: Math.max(0.6, l.pct) + '%' }"></div>
          </div>
          <div class="meta mt-0.5">
            {{ l.detail }}<span v-if="l.projected"> · 外推，不是实测</span>
          </div>
        </div>
      </div>

      <!-- 模型 -->
      <div class="rounded-[var(--r-lg)] border border-[var(--border)] bg-[var(--card)] px-3.5 py-3">
        <div class="mb-2 flex items-baseline gap-2">
          <b class="text-[length:var(--fs-body)] text-[color:var(--ink)]">按模型</b>
          <span class="meta">本期哪个模型花得最多</span>
        </div>
        <UiTable :columns="MODEL_COLS" :rows="models" row-key="model">
          <template #cell-tokens="{ row }">{{ fmtTok(row.tokens) }}</template>
          <template #cell-share="{ row }">
            <div class="flex items-center gap-2">
              <div class="h-1.5 w-[80px] overflow-hidden rounded-full bg-[var(--field-dim)]">
                <div class="h-full rounded-full bg-[var(--accent)]"
                     :style="{ width: Math.max(0.5, row.share) + '%' }"></div>
              </div>
              <span class="text-[color:var(--muted)]">{{ row.share.toFixed(1) }}%</span>
            </div>
          </template>
        </UiTable>
      </div>
    </div>
  </template>
</template>
