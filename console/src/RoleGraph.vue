<script setup>
// 角色关系可视化的**渲染层**（d3-force 力导向图，SVG）。
//
// 设计取舍：
// - 纯展示组件：取数在父级 App.vue，props 收 `setting` / `appearances` / `alias`；
// - 数据装配全部委托 roleGraphData.js（可在 Node 里断言）；
// - SVG 由 d3 直接持有（命令式），不用 Vue 渲染每次 tick —— 60fps 下 Vue 重渲染不值当；
//   只有「选中角色详情」走 Vue 响应式。
// - 浅色主题硬编码色值（不依赖未实现的 c-* 类）。
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from "vue";
import {
  forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide, forceX, forceY,
} from "d3-force";
import { drag } from "d3-drag";
import { zoom, zoomIdentity } from "d3-zoom";
import { scaleSqrt } from "d3-scale";
import { select } from "d3-selection";
import { buildGraph, levelStyle, neighborsOf } from "./roleGraphData.js";
import { RELATION_TYPES, relationTypeOf } from "./relationTypes.js";

const props = defineProps({
  setting: { type: Object, default: null },
  appearances: { type: Object, default: null },
  alias: { type: Object, default: () => ({}) },
});
const emit = defineEmits(["refresh"]);

const wrap = ref(null);
const svgHost = ref(null);
const sel = ref(null);              // 选中节点
const err = ref("");
const showUnmatched = ref(false);
const busyRefresh = ref(false);

const graph = computed(() => buildGraph(props.setting, props.appearances, props.alias));
const stats = computed(() => graph.value.stats);
const candidates = computed(() => graph.value.candidates);

// 未匹配名按**名称**归并（同一名字可能被多个角色引用），并列出引用来源
const unmatchedGroups = computed(() => {
  const m = new Map();
  for (const u of graph.value.unmatched) {
    if (!m.has(u.name)) m.set(u.name, { name: u.name, from: [], sample: "" });
    const g = m.get(u.name);
    if (!g.from.includes(u.from)) g.from.push(u.from);
    if (!g.sample && u.detail) g.sample = u.detail;
  }
  return [...m.values()].sort((a, b) => b.from.length - a.from.length
    || a.name.localeCompare(b.name, "zh"));
});

const selLinks = computed(() => {
  if (!sel.value) return [];
  const k = sel.value.key;
  return graph.value.links
    .filter((l) => (l.source === k || l.target === k))
    .map((l) => ({
      other: l.source === k ? l.target : l.source,
      entries: l.entries,
      type: l.type,
    }))
    .sort((a, b) => a.other.localeCompare(b.other, "zh"));
});

let sim = null, gRoot = null, nodeSel = null, linkSel = null, dragMoved = false;
let linkCopy = [];
let zoomBeh = null, viewW = 900, viewH = 580;
let userInteracted = false;   // 用户缩放/拖拽过之后就不再自动适配视口
let teardown = false;

function radiusScale(maxTotal) {
  // 出场次数 → 半径；无数据时全部同尺寸（8px）
  return scaleSqrt().domain([0, Math.max(1, maxTotal)]).range([8, 30]);
}

const STATUS_FILL = {
  major: "#f3f1fa", minor: "#f5f4fb", background: "#f7f7fa", absent: "#fafafc", ghost: "#f2f3f7",
};

function lockBadge(g, r) {
  // 右上角锁形标（设定集 locked 硬约束可视化）：锁体 rect + 锁梁半圆
  const bw = Math.max(6.5, r * 0.55), bh = bw * 0.72;
  const bx = r * 0.45, by = -r - bh * 0.1;
  g.append("rect")
    .attr("x", bx).attr("y", by).attr("width", bw).attr("height", bh)
    .attr("rx", 1.5).attr("fill", "#c9a26a");
  g.append("path")
    .attr("d", `M${bx + bw * 0.2},${by} a${bw * 0.3},${bw * 0.3} 0 0 1 ${bw * 0.6},0`)
    .attr("fill", "none").attr("stroke", "#c9a26a").attr("stroke-width", 1.2);
}

function applyHighlight() {
  if (!nodeSel || !linkSel) return;
  const nbr = sel.value ? neighborsOf(sel.value, linkCopy) : null;
  nodeSel.attr("class", (d) => {
    let c = "rg-node" + (d.ghost ? " ghost" : "");
    if (nbr) c += nbr.has(d.key) ? (d.key === sel.value.key ? " sel" : " hl") : " dim";
    return c;
  });
  linkSel.attr("class", (d) => {
    let c = "rg-link" + (d.ghost ? " ghostlink" : "");
    if (nbr) {
      const k = sel.value.key;
      const active = (d.source.key === k || d.target.key === k) && nbr.has(d.source.key) && nbr.has(d.target.key);
      c += active ? " hl" : " dim";
    }
    return c;
  });
}

function build() {
  err.value = "";
  const host = svgHost.value;
  if (!host) return;
  if (sim) { sim.stop(); sim = null; }
  const data = graph.value;
  const w = Math.max(560, wrap.value?.clientWidth || 900);
  // 画布高度跟着宽度走：太扁/太方都会让力导向图适配后两侧留大空白
  const h = Math.round(Math.min(640, Math.max(460, w * 0.5)));
  const root = select(host);
  root.selectAll("*").remove();
  const svg = root
    .attr("viewBox", `0 0 ${w} ${h}`)
    .attr("width", "100%")
    .attr("height", h);
  viewW = w;
  viewH = h;

  const nodes = data.nodes.map((n) => ({ ...n }));
  linkCopy = data.links.map((l) => ({ ...l }));
  const rScale = radiusScale(data.stats.maxTotal);

  // 缩放/平移容器
  gRoot = svg.append("g");
  let gLinks = gRoot.append("g").attr("class", "rg-links");
  let gNodes = gRoot.append("g").attr("class", "rg-nodes");

  userInteracted = false;
  zoomBeh = zoom().scaleExtent([0.3, 3]).on("zoom", (ev) => {
    if (ev.sourceEvent) userInteracted = true;   // 只有用户操作才算，程序化 fit 不算
    gRoot.attr("transform", ev.transform);
  });
  svg.call(zoomBeh);

  // 边（悬停提示由每条线的 <title> 承担，见下）
  linkSel = gLinks
    .selectAll("line")
    .data(linkCopy)
    .join("line")
    .attr("class", (d) => "rg-link" + (d.ghost ? " ghostlink" : ""))
    .attr("stroke", (d) => relationTypeOf(d.type).color)
    .attr("stroke-width", (d) => (d.entries.length > 1 ? 2 : 1.4))
    .attr("stroke-linecap", "round")
    .attr("opacity", 0.55);

  linkSel.append("title").text((d) =>
    d.entries.map((e) => `${e.from} → ${e.text}`).join("\n"));

  // 节点
  nodeSel = gNodes
    .selectAll("g")
    .data(nodes, (d) => d.key)
    .join("g")
    .attr("class", (d) => "rg-node" + (d.ghost ? " ghost" : ""))
    .style("cursor", (d) => (d.ghost ? "default" : "pointer"));

  nodeSel
    .append("circle")
    .attr("r", (d) => rScale(d.total))
    .attr("fill", (d) => STATUS_FILL[d.level] || "#f7f7fa")
    .attr("stroke", (d) => levelStyle(d.level).stroke)
    .attr("stroke-width", (d) => levelStyle(d.level).width)
    .attr("stroke-dasharray", (d) => levelStyle(d.level).dash);

  nodeSel
    .append("text")
    .attr("class", "rg-label")
    .attr("text-anchor", "middle")
    .attr("dy", (d) => rScale(d.total) + 13)
    .text((d) => d.name);
  nodeSel.each(function (d) {
    if (d.locked && !d.ghost) lockBadge(select(this), rScale(d.total));
    select(this).append("title").text(
      [
        d.name + (d.ghost ? "（未入册，仅出现在别人的关系描述里）" : ""),
        d.role ? "定位：" + d.role : "",
        d.locked ? "🔒 locked（设定集硬约束）" : "",
        `出场：${d.total} 次 / ${d.chapters} 章` + (d.first ? `（首见第 ${d.first} 章）` : ""),
        `分级：${levelStyle(d.level).label}`,
        `关系数：${d.degree}`,
      ].filter(Boolean).join("\n")
    );
  });

  // 拖拽（幽灵节点不可拖）
  const dragBeh = drag()
    .filter((ev, d) => !d.ghost)
    .on("start", (ev, d) => {
      dragMoved = false;
      userInteracted = true;          // 用户手工排布后不再自动适配视口
      if (!ev.active) sim.alphaTarget(0.25).restart();
      d.fx = d.x; d.fy = d.y;
    })
    .on("drag", (ev, d) => {
      dragMoved = true;
      d.fx = ev.x; d.fy = ev.y;
    })
    .on("end", (ev, d) => {
      if (!ev.active) sim.alphaTarget(0);
      // 拖拽固定：保留 fx/fy，方便用户手工布局；「重新布局」按钮释放
    });
  nodeSel.call(dragBeh);

  // 选中 / 取消
  nodeSel.on("click", (ev, d) => {
    ev.stopPropagation();
    if (dragMoved) return;
    sel.value = sel.value && sel.value.key === d.key ? null : d;
    applyHighlight();
  });
  svg.on("click", () => {
    sel.value = null;
    applyHighlight();
  });

  // 力导向：先**离线收敛**到稳定布局（确定性、可断言），再一次性绘制并适配视口。
  // 直接靠计时器跑会「第一次打开时节点乱飞 + 挤在一角」，离线 320 tick 后布局稳定。
  const draw = () => {
    linkSel
      .attr("x1", (d) => d.source.x).attr("y1", (d) => d.source.y)
      .attr("x2", (d) => d.target.x).attr("y2", (d) => d.target.y);
    nodeSel.attr("transform", (d) => `translate(${d.x},${d.y})`);
  };
  sim = forceSimulation(nodes)
    .force("link", forceLink(linkCopy).id((d) => d.key)
      .distance((l) => 96 + 26 * Math.min(3, l.entries.length))
      .strength(0.35))
    .force("charge", forceManyBody().strength((d) => -300 - rScale(d.total) * 6))
    .force("center", forceCenter(w / 2, h / 2))
    .force("collide", forceCollide().radius((d) => rScale(d.total) + 16))
    // 横向弱、纵向强 → 布局偏扁，正好填满宽面板（否则适配后左右留大空白）
    .force("x", forceX(w / 2).strength(0.035))
    .force("y", forceY(h / 2).strength(0.095))
    .alphaDecay(0.035)
    .on("tick", draw)
    .on("end", () => { if (!teardown && !userInteracted) fitToContent(); });

  sim.stop();
  for (let i = 0; i < 320; i++) sim.tick();
  draw();
  fitToContent();
}

/** 按节点包围盒把图缩放到视口内，避免挤在角落 + 大片空白。
 *  pad 取得比较大：节点标签是居中的文本，可能比节点本身宽得多，
 *  只按圆心算包围盒会把边缘的长标签裁掉。 */
function fitToContent() {
  if (!sim || !gRoot || !svgHost.value || teardown) return;
  const ns = sim.nodes();
  if (!ns.length) return;
  const pad = 96;
  const xs = ns.map((n) => n.x), ys = ns.map((n) => n.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const bw = Math.max(1, maxX - minX), bh = Math.max(1, maxY - minY);
  const k = Math.min(1.3, Math.max(0.35,
    Math.min((viewW - pad * 2) / bw, (viewH - pad * 2) / bh)));
  const t = zoomIdentity
    .translate(viewW / 2 - (k * (minX + maxX)) / 2, viewH / 2 - (k * (minY + maxY)) / 2)
    .scale(k);
  select(svgHost.value).call(zoomBeh.transform, t);
}

function relayout() {
  if (!sim) return;
  for (const n of sim.nodes()) { n.fx = null; n.fy = null; }
  userInteracted = false;
  sim.alpha(1).restart();
}

async function refreshData() {
  busyRefresh.value = true;
  try { emit("refresh"); } finally { busyRefresh.value = false; }
}

onMounted(async () => { await nextTick(); safeBuild(); });
onUnmounted(() => { teardown = true; if (sim) sim.stop(); });
watch([() => props.setting, () => props.appearances, () => props.alias], async () => {
  sel.value = null;
  await nextTick();
  safeBuild();
});

/** 渲染失败必须被吞掉：关系图挂了不能让其他页签受影响。 */
function safeBuild() {
  try {
    build();
  } catch (e) {
    err.value = (e && e.message ? e.message : String(e)).slice(0, 200);
    if (sim) { sim.stop(); sim = null; }
    console.error("[RoleGraph] 渲染失败", e);
  }
}

const legend = RELATION_TYPES;
const levelLegend = [
  { k: "major", label: "主要（实线粗）" },
  { k: "minor", label: "次要（实线）" },
  { k: "background", label: "背景/提及（细灰）" },
  { k: "absent", label: "未出场（虚线，写了没用）" },
  { k: "ghost", label: "未入册（灰色点线）" },
];
</script>

<template>
  <div class="rg-wrap" ref="wrap">
    <div v-if="err" class="empty">关系图渲染失败：{{ err }}</div>

    <div class="rg-head">
      <span class="meta">
        {{ stats.nodeCount }} 节点（{{ stats.realCount }} 已入册 + {{ stats.ghostCount }} 未入册）
        · {{ stats.linkCount }} 条关系
      </span>
      <span v-if="!appearances" class="pill st-gate">无出场数据 · 节点均一尺寸</span>
      <span class="spacer"></span>
      <button class="mini" @click="showUnmatched = !showUnmatched">
        未匹配清单 ({{ stats.ghostCount }} 名 / {{ stats.unmatchedCount }} 条)
      </button>
      <button class="mini" @click="relayout">重新布局</button>
      <button class="mini" :disabled="busyRefresh" @click="refreshData">
        {{ busyRefresh ? "刷新中…" : "刷新出场数据" }}
      </button>
    </div>

    <div v-if="showUnmatched" class="rg-unmatched">
      <div v-if="!unmatchedGroups.length" class="meta">所有关系都对上了角色名 ✅</div>
      <template v-else>
        <div class="meta">
          {{ unmatchedGroups.length }} 个对端名没在设定集里找到同名角色（共
          {{ stats.unmatchedCount }} 条关系），已画成灰色虚线「未入册」节点（不可拖拽）。
          若其实是别名，可在 <code>data/setting/alias.json</code> 里写
          <code>{ "未匹配名": "正式角色名" }</code> 后刷新。
        </div>
        <div v-for="g in unmatchedGroups" :key="g.name" class="rg-unmatched-row">
          <b>{{ g.name }}</b>
          <span class="meta">← 出现在「{{ g.from.join("、") }}」的关系里</span>
        </div>
      </template>
    </div>

    <div v-if="candidates.length" class="meta rg-cand">
      出场统计发现的新角色候选（大纲有、设定集无）：{{ candidates.join("、") }}
      —— 建议跑 <code>python scripts/rosa_postprocess.py --role-records</code> 生成设定草稿。
    </div>

    <div class="rg-body">
      <svg ref="svgHost" class="rg-svg"></svg>

      <aside class="rg-side">
        <template v-if="sel">
          <div class="card-head">
            <b>{{ sel.name }}</b>
            <span v-if="sel.locked" class="pill st-gate">locked</span>
            <span v-if="sel.ghost" class="pill st-gate">未入册</span>
          </div>
          <div class="meta" style="margin-bottom: 8px;">
            {{ levelStyle(sel.level).label }} · 出场 {{ sel.total }} 次 / {{ sel.chapters }} 章
            <template v-if="sel.first"> · 首见第 {{ sel.first }} 章</template>
          </div>
          <div v-if="sel.ghost" class="meta">
            这个名字只出现在别人的关系描述里，设定集中没有对应角色卡。
          </div>
          <template v-else>
            <div class="rg-field"><label>定位</label><div>{{ sel.role || "（无）" }}</div></div>
            <div class="rg-field"><label>特征</label><div>{{ sel.traits || "（无）" }}</div></div>
            <div class="rg-field"><label>形象</label><div>{{ sel.appearance || "（无）" }}</div></div>
            <div class="rg-field"><label>经历</label><div>{{ sel.experience || "（无）" }}</div></div>
          </template>
          <div class="rg-field">
            <label>关系（{{ selLinks.length }}）</label>
            <div v-for="(l, i) in selLinks" :key="i" class="rg-rel">
              <span class="rg-dot" :style="{ background: relationTypeOf(l.type).color }"></span>
              <b>{{ l.other }}</b>
              <span class="meta">{{ relationTypeOf(l.type).label }}</span>
              <div class="meta">{{ l.entries.map(e => e.text).join("；") }}</div>
            </div>
            <div v-if="!selLinks.length" class="meta">（无已解析关系）</div>
          </div>
        </template>
        <div v-else class="empty">
          点节点查看详情；拖拽可固定位置；滚轮缩放、空白处拖拽平移。
        </div>
      </aside>
    </div>

    <div class="rg-legends">
      <div class="rg-legend">
        <b class="meta">关系类型</b>
        <span v-for="t in legend" :key="t.key" class="rg-legend-item">
          <span class="rg-dot" :style="{ background: t.color }"></span>{{ t.label }}
        </span>
      </div>
      <div class="rg-legend">
        <b class="meta">描边分级 / 圆大小 = 出场次数</b>
        <span v-for="l in levelLegend" :key="l.k" class="rg-legend-item">{{ l.label }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.rg-wrap { min-width: 0; }
.rg-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.rg-body { display: grid; grid-template-columns: minmax(0, 1fr) 300px; gap: 14px; align-items: start; }
.rg-svg { width: 100%; display: block; background: var(--card); border: 1px solid var(--border);
          border-radius: 12px; }
.rg-side { border: 1px solid var(--border); border-radius: 12px; padding: 10px 12px; background: var(--card);
           max-height: 580px; overflow: auto; }
.rg-unmatched { border: 1px solid var(--border); border-radius: 12px; padding: 10px 12px;
                background: var(--card); margin-bottom: 10px; }
.rg-unmatched-row { display: flex; gap: 8px; align-items: baseline; padding: 2px 0; }
.rg-cand { margin-bottom: 8px; }
.rg-field { margin-bottom: 10px; }
.rg-field > label { display: block; color: var(--muted); font-size: 12px; margin-bottom: 2px; }
.rg-rel { border-top: 1px dashed var(--border); padding: 6px 0; }
.rg-legends { display: flex; flex-direction: column; gap: 4px; margin-top: 10px; }
.rg-legend { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.rg-legend-item { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: var(--muted); }
.rg-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; flex: none; }

/* d3 创建的元素没有 Vue 的 scoped 属性，必须用 :deep() 才会命中 */
:deep(.rg-link) { stroke-linecap: round; }
:deep(.rg-link.ghostlink) { stroke-dasharray: 3 3; opacity: 0.35; }
:deep(.rg-link.dim) { opacity: 0.1; }
:deep(.rg-link.hl) { opacity: 0.95; stroke-width: 2.4; }
:deep(.rg-node circle) { transition: fill 0.12s ease; }
:deep(.rg-node .rg-label) {
  font-size: 11.5px; fill: var(--ink); pointer-events: none;
  /* 白色描边垫底：节点密集时标签互相压字也能读 */
  paint-order: stroke; stroke: #fff; stroke-width: 3px; stroke-linejoin: round;
}
:deep(.rg-node.ghost .rg-label) { fill: #9aa0ad; font-style: italic; }
:deep(.rg-node.dim) { opacity: 0.22; }
:deep(.rg-node.hl circle) { stroke: var(--accent); stroke-width: 2.6; }
:deep(.rg-node.sel circle) { fill: #e6e2f3; stroke: var(--accent); stroke-width: 3; }
</style>
