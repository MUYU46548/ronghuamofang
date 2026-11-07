<script setup>
// 情节图（2026-11-02，视频 v0.5 对标②：“复杂情节可收起、选中一步只看相关”）。
//
// 数据：GET /outline/graph（零 token，解析章大纲）——节点=章，实线=顺序衔接，
// 虚线=相邻 3 章内共享角色。
// 三条交互对应视频里那三件：按卷收起、选中只看相关、导成图片。
//
// 为什么复用 d3-force 而不新写：RoleGraph 已经在用同一套（拖拽/缩放/力导向），
// 换个数据源比再造一个渲染器省事，也不会两套交互手感不一致。
import { ref, onMounted, onUnmounted, nextTick } from "vue";
import {
  forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide,
} from "d3-force";
import { drag } from "d3-drag";
import { select } from "d3-selection";

const props = defineProps({ api: { type: Function, required: true } });
const emit = defineEmits(["say"]);
function say(m) { emit("say", m); }

const graph = ref({ nodes: [], links: [], volumes: [], note: "" });
const hiddenVols = ref([]);
const picked = ref(null);
const loading = ref(false);
const host = ref(null);
let sim = null;

// 卷分类色（B4）：改引用 style.css 的 --viz-*，图表才会跟着主题明暗切换。
// D3 必须用 .style() 而不是 .attr()：属性值不认 var()，内联样式才认。
const VOL_COLORS = ["var(--viz-1)", "var(--viz-2)", "var(--viz-3)",
                    "var(--viz-4)", "var(--viz-5)", "var(--viz-6)"];

function colorOf(vol) { return VOL_COLORS[(vol - 1) % VOL_COLORS.length]; }

async function load() {
  loading.value = true;
  try {
    const r = await props.api("/outline/graph");
    if (r.status === 200 && r.data.ok) {
      graph.value = r.data;
      picked.value = null;
      await nextTick();
      render();
    } else {
      say("读情节图失败：" + (r.data?.error || r.status));
    }
  } finally {
    loading.value = false;
  }
}

function toggleVol(v) {
  const i = hiddenVols.value.indexOf(v);
  if (i >= 0) hiddenVols.value.splice(i, 1);
  else hiddenVols.value.push(v);
  render();
}

function pick(n) {
  picked.value = picked.value === n ? null : n;
  render();
}

/** 选中时"只看相关"：自己 + 直接邻居 + 相邻的连线。 */
function relatedIds() {
  if (!picked.value) return null;
  const keep = new Set([picked.value]);
  for (const l of graph.value.links) {
    const s = typeof l.source === "object" ? l.source.id : l.source;
    const t = typeof l.target === "object" ? l.target.id : l.target;
    if (s === picked.value) keep.add(t);
    if (t === picked.value) keep.add(s);
  }
  return keep;
}

function render() {
  if (!host.value) return;
  host.value.innerHTML = "";
  const all = graph.value.nodes || [];
  if (!all.length) return;

  const keep = relatedIds();
  const visible = all.filter((n) => !hiddenVols.value.includes(n.vol));
  const visIds = new Set(visible.map((n) => n.id));
  const nodes = visible.map((n) => ({ ...n }));          // 每次重建，避免 d3 改原对象
  const ids = new Set(nodes.map((n) => n.id));
  const links = (graph.value.links || []).filter((l) => {
    const s = typeof l.source === "object" ? l.source.id : l.source;
    const t = typeof l.target === "object" ? l.target.id : l.target;
    return ids.has(s) && ids.has(t);
  }).map((l) => ({ ...l }));

  const W = host.value.clientWidth || 900;
  const H = 420;
  const svg = select(host.value).append("svg")
    .attr("xmlns", "http://www.w3.org/2000/svg")
    .attr("width", W).attr("height", H)
    .attr("viewBox", `0 0 ${W} ${H}`)
    .style("background", "var(--card)");

  svg.append("rect").attr("width", W).attr("height", H).style("fill", "var(--card)");

  const link = svg.append("g").selectAll("line").data(links).join("line")
    .style("stroke", (d) => {
      if (keep && !(keep.has(d.source.id) || keep.has(d.source)) ) return "var(--border)";
      return d.kind === "seq" ? "var(--muted)" : "var(--accent)";
    })
    .attr("stroke-width", (d) => (d.kind === "seq" ? 1.6 : 1))
    .attr("stroke-dasharray", (d) => (d.kind === "role" ? "4 3" : null));

  const node = svg.append("g").selectAll("g").data(nodes).join("g")
    .style("cursor", "pointer")
    .on("click", (e, d) => pick(d.id));

  node.append("circle")
    .attr("r", (d) => 10 + Math.min(8, (d.events || []).length * 2))
    .style("fill", (d) => colorOf(d.vol))
    .attr("fill-opacity", (d) => (keep && !keep.has(d.id) ? 0.18 : 0.92))
    .style("stroke", (d) => (picked.value === d.id ? "var(--ink)" : "var(--card)"))
    .attr("stroke-width", (d) => (picked.value === d.id ? 2.5 : 1.5));

  node.append("text")
    .text((d) => "第" + d.n + "章")
    .attr("text-anchor", "middle").attr("dy", 26)
    .attr("font-size", 11).style("fill", "var(--ink)")
    .attr("fill-opacity", (d) => (keep && !keep.has(d.id) ? 0.25 : 1));

  sim = forceSimulation(nodes)
    .force("link", forceLink(links).id((d) => d.id).distance(70).strength(0.35))
    .force("charge", forceManyBody().strength(-220))
    .force("center", forceCenter(W / 2, H / 2))
    .force("collide", forceCollide(28))
    .on("tick", () => {
      link.attr("x1", (d) => d.source.x).attr("y1", (d) => d.source.y)
        .attr("x2", (d) => d.target.x).attr("y2", (d) => d.target.y);
      node.attr("transform", (d) => `translate(${d.x},${d.y})`);
    });

  node.call(drag()
    .on("start", (e, d) => { if (!e.active) sim.alphaTarget(0.25).restart(); d.fx = d.x; d.fy = d.y; })
    .on("drag", (e, d) => { d.fx = e.x; d.fy = e.y; })
    .on("end", (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));
}

/** 导成图片：直接把 SVG 画到 canvas 再下载（不引第三方库）。 */
function exportPng() {
  const svgEl = host.value?.querySelector("svg");
  if (!svgEl) return say("还没有图可以导出");
  const clone = svgEl.cloneNode(true);
  const w = Number(clone.getAttribute("width")) || 900;
  const h = Number(clone.getAttribute("height")) || 420;
  // 导出时把"选中变灰"复原成正常，别把变淡的图发给别人
  clone.querySelectorAll("[fill-opacity]").forEach((el) => el.setAttribute("fill-opacity", "1"));
  const xml = new XMLSerializer().serializeToString(clone);
  const img = new Image();
  img.onload = () => {
    const canvas = document.createElement("canvas");
    canvas.width = w * 2; canvas.height = h * 2;
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    const a = document.createElement("a");
    a.download = "情节图.png";
    a.href = canvas.toDataURL("image/png");
    a.click();
  };
  img.onerror = () => say("导出失败：SVG 转图片出错");
  img.src = "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(xml)));
}

onMounted(load);
onUnmounted(() => { if (sim) sim.stop(); });
</script>

<template>
  <div class="card" style="margin-top: 12px;">
    <div class="card-head">
      <h3>情节图</h3>
      <span class="meta">{{ graph.note }}</span>
      <span class="spacer"></span>
      <button class="mini" :disabled="loading" @click="load">{{ loading ? "读取中…" : "刷新" }}</button>
      <button class="mini" :disabled="!graph.nodes.length" @click="exportPng">导成图片</button>
    </div>

    <div v-if="!graph.nodes.length" class="meta" style="padding: 10px 0;">
      还没有章大纲 —— 先跑阶段 3，这里会按章画出情节。
    </div>

    <template v-else>
      <div style="display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 8px;">
        <span class="meta">点卷收起/展开；点一章只看和它相关的：</span>
        <button v-for="v in graph.volumes" :key="v.vol" class="mini"
                :style="{ borderColor: colorOf(v.vol) }"
                @click="toggleVol(v.vol)">
          卷{{ v.vol }}（{{ v.chapters.length }} 章）{{ hiddenVols.includes(v.vol) ? " · 已收起" : "" }}
        </button>
        <button v-if="picked" class="mini primary" @click="pick(picked)">
          取消选中（{{ picked }}）
        </button>
      </div>
      <div ref="host" style="width: 100%; min-height: 420px; border: 1px solid var(--border); border-radius: 10px; overflow: hidden;"></div>
      <div v-if="picked" class="meta" style="margin-top: 6px;">
        {{ (graph.nodes.find((n) => n.id === picked) || {}).label }}
        · {{ (graph.nodes.find((n) => n.id === picked) || {}).function }}
        <span v-if="(graph.nodes.find((n) => n.id === picked) || {}).hook">
          · 钩子：{{ (graph.nodes.find((n) => n.id === picked) || {}).hook }}
        </span>
      </div>
    </template>
  </div>
</template>
