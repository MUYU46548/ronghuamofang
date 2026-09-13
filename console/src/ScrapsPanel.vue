<script setup>
import { ref, computed, onMounted } from "vue";

const API = "http://127.0.0.1:8765";

async function api(path, method = "GET", body = null) {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  if (body) opt.body = JSON.stringify(body);
  const r = await fetch(API + path, opt);
  let data = {};
  try { data = await r.json(); } catch (e) { /* empty */ }
  return { status: r.status, data };
}

// ---------- 状态 ----------
const list = ref(null);          // /scraps/list 响应
const loading = ref(false);
const busy = ref(false);
const toast = ref("");
let toastTimer = null;
function say(msg) {
  toast.value = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toast.value = ""), 6000);
}

const openClusters = ref(new Set());
const current = ref(null);        // { name, lines: [{no, text}] }
const checked = ref({});          // { 文件名: { 行号: true } }
const extraPoints = ref("");      // 手工追加的信息点（一行一条）
const cardName = ref("");
const cardType = ref("角色卡");
const CARD_TYPES = ["角色卡", "场景卡", "概念卡", "组织卡", "物品卡"];
const LS_KEY = "mofang.scraps.v1";

function loadLS() {
  try {
    const raw = JSON.parse(localStorage.getItem(LS_KEY) || "{}");
    checked.value = raw.checked || {};
    cardType.value = raw.cardType || "角色卡";
    cardName.value = raw.cardName || "";
  } catch (e) { /* 忽略损坏的本地状态 */ }
}
function saveLS() {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({
      checked: checked.value, cardType: cardType.value, cardName: cardName.value,
    }));
  } catch (e) { /* 忽略 */ }
}

// ---------- 数据 ----------
const clusters = computed(() => list.value?.clusters || []);
const stats = computed(() => list.value?.stats || {});
const lookaheads = computed(() => list.value?.lookaheads || []);

const scrapIndex = computed(() => {
  const m = {};
  for (const c of clusters.value) {
    for (const s of c.scraps || []) m[s.name] = { ...s, cluster: c.name };
  }
  return m;
});

const checkedCount = computed(() => {
  let n = 0;
  for (const f of Object.keys(checked.value)) {
    n += Object.values(checked.value[f] || {}).filter(Boolean).length;
  }
  return n;
});

const points = computed(() => {
  const out = [];
  const cur = current.value;
  if (cur) {
    const meta = scrapIndex.value[cur.name] || {};
    for (const ln of cur.lines) {
      if (checked.value[cur.name]?.[ln.no]) {
        out.push({
          content: ln.text,
          ts: meta.ts || "",
          cluster: meta.cluster || "",
          confidence: meta.ts_source === "mtime" ? "low" : "high",
          source_file: cur.name,
        });
      }
    }
  }
  for (const s of (extraPoints.value || "").split("\n")) {
    if (s.trim()) out.push({ content: s.trim(), confidence: "high" });
  }
  return out;
});

const sources = computed(() => {
  const seen = new Set();
  const out = [];
  for (const p of points.value) {
    const f = p.source_file;
    if (f && !seen.has(f)) {
      seen.add(f);
      const meta = scrapIndex.value[f] || {};
      out.push(`${f}（${meta.ts || "无日期"} / ${meta.ts_source || "?"}）`);
    }
  }
  return out;
});

// 当前碎片所属簇里的"前瞻备忘"，默认带进待定区
const currentOpenQuestions = computed(() => {
  const cur = current.value;
  if (!cur) return [];
  const meta = scrapIndex.value[cur.name] || {};
  const c = clusters.value.find((x) => x.name === meta.cluster);
  return (c?.lookaheads || []).map((la) => `${la.sentence}（来自 ${la.file}）`);
});

// ---------- 动作 ----------
async function load() {
  loading.value = true;
  const r = await api("/scraps/list");
  loading.value = false;
  if (r.status !== 200 || !r.data.ok) {
    list.value = null;
    say("读取碎片失败：" + (r.data.error || r.status));
    return;
  }
  list.value = r.data;
  if (!openClusters.value.size) {
    // 默认展开有前瞻备忘的簇，其余折叠
    openClusters.value = new Set(
      (r.data.clusters || []).filter((c) => (c.lookaheads || []).length).map((c) => c.name));
  }
}

function toggleCluster(name) {
  const s = new Set(openClusters.value);
  if (s.has(name)) s.delete(name); else s.add(name);
  openClusters.value = s;
}

async function openScrap(name) {
  const r = await api("/scraps/read?name=" + encodeURIComponent(name));
  if (r.status !== 200 || !r.data.ok) { say("读取失败：" + (r.data.error || r.status)); return; }
  const text = r.data.content || "";
  const lines = text.split(/\r?\n/).map((t, i) => ({ no: i + 1, text: t })).filter((l) => l.text.trim());
  current.value = { name, lines };
  if (!cardName.value) cardName.value = name.replace(/\.[^.]+$/, "");
}

function toggleLine(file, no) {
  const byFile = { ...(checked.value[file] || {}) };
  if (byFile[no]) delete byFile[no]; else byFile[no] = true;
  checked.value = { ...checked.value, [file]: byFile };
  saveLS();
}

function clearChecked(file) {
  if (file) {
    const c = { ...checked.value };
    delete c[file];
    checked.value = c;
  } else {
    checked.value = {};
    extraPoints.value = "";
  }
  saveLS();
}

async function promote() {
  if (!points.value.length) { say("请先勾选信息点（或手工追加）"); return; }
  if (!cardName.value.trim()) { say("请填写卡片名（如：月神）"); return; }
  busy.value = true;
  const r = await api("/scraps/promote", "POST", {
    card_name: cardName.value.trim(),
    card_type: cardType.value,
    points: points.value,
    open_questions: currentOpenQuestions.value,
    sources: sources.value,
  });
  busy.value = false;
  if (r.status !== 200 || !r.data.ok) { say("生成失败：" + (r.data.error || r.status)); return; }
  const bak = r.data.backup ? `（覆盖前已备份：${r.data.backup}）` : "";
  say(`已写入 ${r.data.path}${bak} —— 下次跑 stage1 时进入设定归并`);
  saveLS();
}

async function del(name) {
  if (!window.confirm(`确定删除碎片「${name}」？\n\n删除前会先做一次项目快照，并另存一份到 materials/original_scraps/_backup/。`)) return;
  const r = await api("/scraps/delete", "POST", { name, confirm: true });
  if (r.status !== 200 || !r.data.ok) { say("删除失败：" + (r.data.error || r.status)); return; }
  say(`已删除 ${name}（备份：${r.data.backup || "无"}）`);
  if (current.value?.name === name) current.value = null;
  load();
}

function tsLabel(s) {
  if (!s.ts) return "无日期";
  return s.ts_source === "mtime" ? `≈${s.ts}(文件时间)` : s.ts;
}

onMounted(() => { loadLS(); load(); });
</script>

<template>
  <div>
    <div class="card-head" style="margin-bottom: 10px;">
      <h4 style="margin: 0;">原始碎片（自由命名 · 私人数据 · 不进 Git）</h4>
      <span class="meta">{{ list?.dir || "materials/original_scraps" }}</span>
      <span class="spacer"></span>
      <button class="mini" :disabled="loading" @click="load">{{ loading ? "读取中…" : "刷新" }}</button>
    </div>

    <div v-if="!list" class="empty">正在读取碎片…（若目录为空，请把随手写的碎片放进 materials/original_scraps/）</div>
    <template v-else>
      <div class="meta" style="margin-bottom: 10px;">
        碎片 {{ stats.scrap_count }} 个 / 簇 {{ stats.cluster_count }} 个（合并簇 {{ stats.merged_cluster_count }}）/
        前瞻备忘 {{ stats.lookahead_count }} 条
        <span v-if="list.index_stale" style="color: var(--warn);">
          · 有改动尚未反映到索引（下次跑 stage1 会自动刷新）
        </span>
      </div>
      <div v-if="list.warnings?.length" class="meta" style="color: var(--warn); margin-bottom: 10px;">
        <div v-for="(w, i) in list.warnings" :key="i">· {{ w }}</div>
      </div>

      <div class="scraps-grid">
        <!-- 左：簇分组 -->
        <div class="scraps-pane">
          <div v-if="!clusters.length" class="empty">暂无碎片</div>
          <div v-for="c in clusters" :key="c.name" class="cluster">
            <div class="cluster-head" @click="toggleCluster(c.name)">
              <span class="caret">{{ openClusters.has(c.name) ? "▾" : "▸" }}</span>
              <strong>{{ c.name }}</strong>
              <span class="tag">{{ c.kind === "merged" ? "合并簇" : "独立" }}</span>
              <span class="meta">{{ c.count }} 个<span v-if="c.ts_range"> · {{ c.ts_range[0] }} 起</span></span>
            </div>
            <div v-if="openClusters.has(c.name)" class="cluster-body">
              <div v-if="c.keywords?.length" class="meta">特征词：{{ c.keywords.join("、") }}</div>
              <div v-if="c.evidence?.length" class="meta evidence">
                <div v-for="(e, i) in c.evidence" :key="i">合并依据：{{ e }}</div>
              </div>
              <div v-for="s in c.scraps" :key="s.name" class="scrap-row"
                   :class="{ active: current?.name === s.name }"
                   @click="openScrap(s.name)">
                <div class="scrap-main">
                  <span>{{ s.name }}</span>
                  <span v-if="s.negated?.length" class="tag" title="这些词在文中是被排除的（如“和X无关”）">排除 {{ s.negated.length }}</span>
                  <span v-if="s.lookahead?.length" class="tag warn">前瞻 {{ s.lookahead.length }}</span>
                </div>
                <div class="meta">{{ tsLabel(s) }} · {{ s.chars }} 字
                  <span v-if="checked[s.name]" style="color: var(--ok);">
                    · 已勾 {{ Object.values(checked[s.name]).filter(Boolean).length }} 点
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 右：原文勾选 + 组卡 -->
        <div class="scraps-pane">
          <div v-if="!current" class="empty">← 点左侧碎片，勾选其中的信息点</div>
          <template v-else>
            <div class="card-head" style="margin-bottom: 8px;">
              <strong>{{ current.name }}</strong>
              <span class="spacer"></span>
              <button class="mini" @click="openScrap(current.name)">重新载入</button>
              <button class="mini danger" @click="del(current.name)">删除碎片</button>
            </div>
            <div class="lines">
              <div v-for="ln in current.lines" :key="ln.no" class="line"
                   :class="{ on: checked[current.name]?.[ln.no] }"
                   @click="toggleLine(current.name, ln.no)">
                <span class="box">{{ checked[current.name]?.[ln.no] ? "✓" : "" }}</span>
                <span class="line-no">{{ ln.no }}</span>
                <span class="line-text">{{ ln.text }}</span>
              </div>
            </div>

            <h4 style="margin: 12px 0 6px;">手工追加信息点（一行一条，可选）</h4>
            <textarea class="prompt-text" style="min-height: 64px;" v-model="extraPoints"
                      spellcheck="false" placeholder="例如：月神左耳的抓痕是第一版实验体留下的"></textarea>

            <h4 style="margin: 12px 0 6px;">生成素材卡</h4>
            <div class="form-row">
              <span class="art-label">卡片名</span>
              <input class="text-input" v-model="cardName" placeholder="如：月神" />
              <span class="art-label">类型</span>
              <select class="text-input" v-model="cardType">
                <option v-for="t in CARD_TYPES" :key="t" :value="t">{{ t }}</option>
              </select>
            </div>
            <div class="meta" style="margin: 6px 0;">
              将写入 <code>materials/raw/{{ (cardName || "未命名") }}_{{ cardType }}.md</code>
              —— 同名文件会先备份到 <code>materials/raw/_backup/</code> 再覆盖。
            </div>
            <div class="card-head">
              <span class="meta">已选 {{ checkedCount }} 行 + 手工 {{ extraPoints.split("\n").filter(s => s.trim()).length }} 条</span>
              <span class="spacer"></span>
              <button class="mini" @click="clearChecked(null)">清空勾选</button>
              <button class="mini primary" :disabled="busy" @click="promote">
                {{ busy ? "写入中…" : "生成本地卡片" }}
              </button>
            </div>
            <div v-if="currentOpenQuestions.length" class="meta" style="margin-top: 8px;">
              <div>将一并写入【待定区】（{{ currentOpenQuestions.length }} 条前瞻备忘）：</div>
              <div v-for="(q, i) in currentOpenQuestions" :key="i">· {{ q }}</div>
            </div>
          </template>
        </div>
      </div>
    </template>

    <div v-if="toast" class="toast">{{ toast }}</div>
  </div>
</template>

<style scoped>
.scraps-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }
.scraps-pane { min-width: 0; }
.cluster { border: 1px solid var(--border); border-radius: 10px; margin-bottom: 8px; overflow: hidden; }
.cluster-head { display: flex; align-items: center; gap: 8px; padding: 8px 10px; cursor: pointer; background: var(--bg); }
.caret { color: var(--muted); width: 12px; }
.cluster-body { padding: 8px 10px 10px; }
.evidence { color: var(--muted); }
.tag { background: var(--accent-soft); color: var(--ink); border-radius: 8px; font-size: 11px; padding: 1px 6px; }
.tag.warn { background: var(--warn); color: #fff; }
.scrap-row { padding: 6px 8px; border-radius: 8px; cursor: pointer; border: 1px solid transparent; }
.scrap-row:hover { background: var(--bg); }
.scrap-row.active { border-color: var(--accent); background: var(--bg); }
.scrap-main { display: flex; align-items: center; gap: 6px; }
.lines { max-height: 320px; overflow: auto; border: 1px solid var(--border); border-radius: 10px; padding: 6px; }
.line { display: flex; gap: 8px; padding: 4px 6px; border-radius: 6px; cursor: pointer; }
.line:hover { background: var(--bg); }
.line.on { background: var(--bg); }
.box { width: 15px; height: 15px; flex: none; margin-top: 2px; border: 1px solid var(--accent); border-radius: 4px;
        font-size: 11px; line-height: 14px; text-align: center; color: var(--accent); }
.line-no { color: var(--muted); font-size: 11px; flex: none; width: 20px; text-align: right; }
.line-text { word-break: break-all; }
.form-row { display: flex; align-items: center; gap: 8px; }
.form-row .text-input { flex: 1; }
code { background: var(--bg); padding: 1px 4px; border-radius: 4px; font-size: 12px; }
</style>
