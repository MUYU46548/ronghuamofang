<template>
  <div v-if="hasData" class="quality-trend">
    <div class="qt-head">
      <h4>章节质量趋势</h4>
      <span class="meta" v-if="avg != null">平均 {{ avg }} / 10</span>
      <span class="meta" v-else>暂无评分</span>
    </div>
    <svg :viewBox="`0 0 ${W} ${H}`" class="qt-chart" preserveAspectRatio="none">
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
        <text :x="d.x" :y="d.y - 10" text-anchor="middle" class="qt-dot-label">{{ d.n }}</text>
      </g>
    </svg>
    <!-- 低分章推荐行动 -->
    <div v-if="lowList.length" class="qt-low">
      <span class="meta">低于阈值（{{ threshold }}）：</span>
      <span v-for="n in lowList" :key="n" class="pill st-failed">{{ n }} 章</span>
      <button class="mini" @click="$emit('gotoReview')">去审稿</button>
      <button class="mini" @click="$emit('gotoRefine')">去精修</button>
    </div>
  </div>
</template>

<script>
export default {
  name: 'QualityTrend',
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

<style scoped>
.quality-trend {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 12px 14px;
  margin-bottom: 14px;
}
.qt-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
.qt-head h4 { margin: 0; font-size: 14px; }
.qt-head .meta { font-size: 12px; color: var(--muted); }
.qt-chart {
  width: 100%;
  height: auto;
  display: block;
}
.qt-dot-label {
  font-size: 10px;
  fill: var(--muted);
  font-weight: 600;
  pointer-events: none;
}
.qt-low {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  flex-wrap: wrap;
}
.qt-low .meta { font-size: 12px; }
.qt-low .pill { font-size: 11px; padding: 2px 7px; }
.qt-low .mini { padding: 4px 9px; font-size: 12px; }
</style>
