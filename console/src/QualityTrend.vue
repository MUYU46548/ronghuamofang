<template>
  <!-- 2026-11-27：样式迁到 Tailwind + 主题变量（scoped 样式块已摘除）。
       三处有意保留的实现细节，别顺手"优化"掉：
       1. `preserveAspectRatio="none"` + `h-auto` —— 图随容器宽度缩放，纵向不额外留白；
          改成默认的 xMidYMid 会让 640×160 的 viewBox 在宽卡片里上下留白、点线变小。
       2. 散点的 `stroke="var(--card)"` 是给圆点描一圈卡片色，让相邻点不糊在一起 ——
          这是**视觉分隔**，不是边框，别改成 --border。
       3. 阈值线/网格用 SVG 属性写 `var(--…)`：属性里用 CSS 变量是合法的，且能跟主题走；
          test_ui_tokens【4】只禁 `.attr("fill", "#…")` 这种写死色值。
       圆角 12px 与内边距按全局尺度收敛（--r-lg / --sp-3 --sp-4），像素变化 ≤2px。 -->
  <div v-if="hasData"
       class="mb-3.5 rounded-[var(--r-lg)] border border-[var(--border)] bg-[var(--card)] px-3.5 py-3">
    <div class="mb-2 flex items-center gap-2.5">
      <h4 class="m-0 text-[length:var(--fs-body)] font-semibold text-[color:var(--ink)]">章节质量趋势</h4>
      <span class="meta" v-if="avg != null">平均 {{ avg }} / 10</span>
      <span class="meta" v-else>暂无评分</span>
    </div>
    <svg :viewBox="`0 0 ${W} ${H}`" class="block h-auto w-full" preserveAspectRatio="none">
      <!-- 阈值线 -->
      <line :x1="PAD" :y1="thrY" :x2="W - PAD" :y2="thrY"
            stroke="var(--bad)" stroke-dasharray="4,3" opacity="0.5"/>
      <!-- 网格 -->
      <line v-for="gy in gridY" :key="gy"
            :x1="PAD" :y1="gy" :x2="W - PAD" :y2="gy"
            stroke="var(--border)" stroke-dasharray="2,4"/>
      <!-- 折线 -->
      <polyline v-if="pts" :points="pts" fill="none"
                stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>
      <!-- 散点 -->
      <g v-for="d in dotData" :key="d.n">
        <circle :cx="d.x" :cy="d.y" r="5"
                :fill="d.q >= threshold ? 'var(--ok)' : 'var(--bad)'"
                stroke="var(--card)" stroke-width="2"/>
        <text :x="d.x" :y="d.y - 10" text-anchor="middle"
              class="pointer-events-none text-[11px] font-semibold [fill:var(--muted)]">{{ d.n }}</text>
      </g>
    </svg>
    <!-- 低分章推荐行动 -->
    <div v-if="lowList.length" class="mt-2 flex flex-wrap items-center gap-1.5">
      <span class="meta">低于阈值（{{ threshold }}）：</span>
      <span v-for="n in lowList" :key="n" class="pill st-failed">{{ n }} 章</span>
      <UiButton @click="$emit('gotoReview')">去审稿</UiButton>
      <UiButton @click="$emit('gotoRefine')">去精修</UiButton>
    </div>
  </div>
</template>

<script>
import UiButton from "./components/ui/UiButton.vue";

export default {
  name: 'QualityTrend',
  components: { UiButton },
  props: {
    chapters: { type: Array, default: () => [] },
    threshold: { type: Number, default: 6 },
  },
  emits: ['gotoReview', 'gotoRefine'],
  data() {
    return { W: 640, H: 160, PAD: 32 };
  },
  computed: {
    hasData() {
      return this.chapters.some(c => c.quality != null);
    },
    avg() {
      const qs = this.chapters.filter(c => c.quality != null).map(c => c.quality);
      if (!qs.length) return null;
      return (qs.reduce((a, b) => a + b, 0) / qs.length).toFixed(1);
    },
    gridY() {
      const lines = [];
      for (let v = 0; v <= 10; v += 2) {
        lines.push(this.PAD + (this.H - 2 * this.PAD) * (1 - v / 10));
      }
      return lines;
    },
    thrY() {
      return this.PAD + (this.H - 2 * this.PAD) * (1 - this.threshold / 10);
    },
    dotData() {
      const cs = this.chapters.filter(c => c.quality != null);
      if (!cs.length) return [];
      const step = cs.length > 1 ? (this.W - 2 * this.PAD) / (cs.length - 1) : 0;
      return cs.map((c, i) => ({
        n: c.n,
        q: c.quality,
        x: this.PAD + i * step,
        y: this.PAD + (this.H - 2 * this.PAD) * (1 - c.quality / 10),
      }));
    },
    pts() {
      const d = this.dotData;
      if (!d.length) return '';
      return d.map(p => `${p.x},${p.y}`).join(' ');
    },
    lowList() {
      return this.chapters.filter(c => c.quality != null && c.quality < this.threshold).map(c => c.n);
    },
  },
};
</script>
