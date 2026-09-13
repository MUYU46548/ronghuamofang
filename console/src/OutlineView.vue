<!--
  OutlineView.vue — 「大纲」页签（任务 1/2/3/4 的 UI 宿主）

  布局：
    顶部工具栏：刷新 / 保存 / 版本对比 / 更多方案
    左侧 38%：树形大纲（根=书名 → 起承转合 → 关键节点 / 章节规划）
    右侧 62%：节点属性面板（原文可编辑 + 评分维度 + 涉及角色 + AI 修订）
  子视图（抽屉/对话框）：版本对比 diff、方案对比拼合、AI 修订意见
-->
<template>
  <div class="ov-wrap">
    <!-- ============ 工具栏 ============ -->
    <div class="card ov-toolbar">
      <div class="card-head" style="margin-bottom: 0;">
        <h3>结构化大纲
          <span v-if="dirty" class="pill st-gate">未保存</span>
          <span v-if="structure && structure.summary && structure.summary.verdict" class="pill"
                :class="verdictClass(structure.summary.verdict)">
            {{ structure.summary.verdict }} · OK {{ structure.summary.ok || 0 }} / WARN {{ structure.summary.warn || 0 }} / THIN {{ structure.summary.thin || 0 }}
          </span>
        </h3>
        <button class="mini" @click="load(true)">刷新</button>
        <button class="mini primary" :disabled="!dirty || saving" @click="save">
          {{ saving ? "保存中…" : "保存" }}
        </button>
        <button class="mini" @click="openVersionDiff">版本对比</button>
        <button class="mini" @click="openMulti">多方案</button>
      </div>
      <div class="meta" style="margin-top: 6px;">
        <template v-if="structure && structure.exists">
          预计章节数 <b>{{ structure.expected_chapters || "—" }}</b>
          · 关键节点 {{ nodes.length }} 条 · 章节规划 {{ plan.length }} 条
          · 双击节点可内联编辑；同层级可拖拽排序
        </template>
        <template v-else>尚未生成整体大纲（先运行阶段2）</template>
      </div>
      <div v-if="issueList.length" class="ov-issues">
        <span v-for="(it, i) in issueList" :key="i" class="ov-issue">⚠ {{ it }}</span>
      </div>
    </div>

    <div class="ov-body" :class="{ 'split-dragging': splitDragging }"
         :style="treeW ? { '--ov-tree-w': treeW + 'px' } : {}">
      <!-- ============ 左：树 ============ -->
      <div class="card ov-tree-card">
        <div class="card-head">
          <h3>大纲树</h3>
          <button class="mini" @click="expandAll(true)">全展开</button>
          <button class="mini" @click="expandAll(false)">全折叠</button>
          <button v-if="treeW" class="mini" @click="resetTreeW" title="恢复默认宽度">复位宽度</button>
        </div>
        <div class="ov-tree-scroll">
          <OutlineTree
            v-if="tree.length"
            :nodes="tree"
            :depth="0"
            :selected-id="selId"
            :expanded="expanded"
            :editable="true"
            :draggable="true"
            group="root"
            @select="onSelect"
            @toggle="onToggle"
            @reorder="onReorder"
            @edit="onInlineEdit"
          />
          <div v-else class="empty">暂无大纲内容</div>
        </div>
      </div>

      <!-- 拖动改树宽：双击分隔条复位 -->
      <div class="ov-splitter" :class="{ dragging: splitDragging }"
           title="拖动调整大纲树宽度（双击复位）"
           @pointerdown="startSplitDrag"
           @dblclick="resetTreeW"></div>

      <!-- ============ 右：属性面板 ============ -->
      <div class="card ov-prop-card">
        <div v-if="!sel" class="empty">在左侧树中选择一个节点 / 章节规划项</div>
        <template v-else>
          <div class="card-head">
            <h3>
              {{ sel.title }}
              <span v-if="sel.level" class="pill" :class="levelPill(sel.level)">{{ sel.level }} · {{ sel.score }}</span>
            </h3>
            <button class="mini" :disabled="!propDirty" @click="saveEntry">保存此条</button>
            <button class="mini" @click="revertEntry">还原</button>
          </div>

          <div class="ov-field">
            <label>原始文本（可编辑）</label>
            <textarea class="prompt-text ov-prop-text" v-model="propText"
                      @input="propDirty = true" spellcheck="false"></textarea>
            <div class="meta">{{ propText.length }} 字符
              <span v-if="propDirty" class="pill st-gate" style="margin-left:6px;">已修改</span>
            </div>
          </div>

          <div class="ov-field">
            <label>评分维度</label>
            <div class="ov-flags">
              <span v-for="f in flagList" :key="f.key" class="ov-flag"
                    :class="sel.flags && sel.flags[f.key] ? 'on' : 'off'">
                {{ f.label }} {{ sel.flags && sel.flags[f.key] ? "✓" : "✗" }}
              </span>
            </div>
            <div v-if="sel.reasons && sel.reasons.length" class="meta" style="margin-top:4px;">
              {{ sel.reasons.join("；") }}
            </div>
          </div>

          <div class="ov-field">
            <label>涉及角色（自动关联 setting.json）</label>
            <div class="ov-chars">
              <span v-for="c in (sel.chars || [])" :key="c" class="pill st-done">{{ c }}</span>
              <span v-if="!sel.chars || !sel.chars.length" class="meta">未匹配到已知角色</span>
            </div>
          </div>

          <!-- 任务2：逐节点 AI 精修 -->
          <div class="ov-field ov-ai">
            <label>AI 精修（只修订此条，不动其他内容）</label>
            <button class="mini primary" :disabled="aiLoading || isBusy" @click="aiOpen = true">
              {{ aiLoading ? "修订中…" : "AI 修订此节点" }}
            </button>
            <button class="mini" :disabled="aiLoading || isBusy" @click="undoRefine">撤销上次修订</button>
          </div>

          <!-- 变更 diff（任务2 结果展示） -->
          <div v-if="aiResult" class="ov-field">
            <label>修订结果（v{{ aiResult.version }} 备份，评分 {{ aiResult.score }} / {{ aiResult.level }}）</label>
            <div class="ov-aidiff">
              <div class="ov-aidiff-row"><span class="ov-aidiff-tag del">原</span>
                <span class="ov-aidiff-text">{{ aiResult.old_text }}</span></div>
              <div class="ov-aidiff-row"><span class="ov-aidiff-tag add">新</span>
                <span class="ov-aidiff-text">
                  <template v-for="(p, i) in aiCharDiff" :key="i">
                    <span :class="p.op === 'add' ? 'cd-add' : p.op === 'del' ? 'cd-del' : ''">{{ p.op === 'del' ? '' : p.text }}</span>
                  </template>
                </span></div>
            </div>
          </div>
        </template>
      </div>
    </div>

    <!-- ============ AI 修订意见对话框 ============ -->
    <div v-if="aiOpen" class="drawer-mask" @click.self="aiOpen = false">
      <div class="dialog">
        <h3>AI 修订「{{ sel && sel.title }}」</h3>
        <div class="meta">只重写这一条（保留事件核心），不影响其他节点与四节结构；修订前自动备份到 data/outline/history/。</div>
        <textarea v-model="aiFeedback" rows="4"
                  placeholder="例如：侧重露汐；结尾留悬念"></textarea>
        <div class="dialog-actions">
          <button class="mini" @click="aiOpen = false">取消</button>
          <button class="mini primary" :disabled="!aiFeedback.trim()" @click="submitAi">提交修订</button>
        </div>
      </div>
    </div>

    <!-- ============ 版本对比（任务4） ============ -->
    <div v-if="verOpen" class="drawer-mask" @click.self="verOpen = false">
      <div class="drawer drawer-wide">
        <div class="drawer-head">
          <b>大纲版本对比</b>
          <span class="spacer"></span>
          <select v-model="v1" class="model-select" @change="loadVersionDiff">
            <option v-for="v in versions" :key="v.version" :value="v.version">{{ v.label }}</option>
          </select>
          <span class="meta">→</span>
          <select v-model="v2" class="model-select" @change="loadVersionDiff">
            <option v-for="v in versions" :key="v.version" :value="v.version">{{ v.label }}</option>
          </select>
          <button class="mini" @click="exportDiff">导出 diff</button>
          <button class="mini danger" @click="restoreV1">恢复此版本</button>
          <button class="mini" @click="verOpen = false">关闭</button>
        </div>
        <div class="ov-verinfo">
          <span class="pill st-done">v1 {{ scoreLabel(diffData && diffData.score_diff && diffData.score_diff.v1) }}</span>
          <span class="pill st-done">v2 {{ scoreLabel(diffData && diffData.score_diff && diffData.score_diff.v2) }}</span>
          <span class="meta" v-if="diffCounts">
            新增 {{ diffCounts.add }} · 删除 {{ diffCounts.del }} · 修改 {{ diffCounts.change }} · 未变 {{ diffCounts.same }}
          </span>
        </div>
        <div class="ov-diff-scroll">
          <div v-for="(s, i) in (diffData && diffData.segments) || []" :key="i"
               class="ov-diff-seg" :class="'seg-' + s.type">
            <span class="ov-diff-title">{{ s.title }}</span>
            <span v-if="s.type === 'change'">
              <span class="cd-del">{{ s.content }}</span>
              <span class="cd-arrow"> → </span>
              <span class="cd-add">{{ s.content_v2 }}</span>
            </span>
            <span v-else-if="s.type === 'add'" class="cd-add">{{ s.content }}</span>
            <span v-else-if="s.type === 'del'" class="cd-del">{{ s.content }}</span>
            <span v-else>{{ s.content }}</span>
          </div>
          <div v-if="diffData && !diffData.segments.length" class="empty">两个版本完全一致</div>
        </div>
      </div>
    </div>

    <!-- ============ 多方案对比拼合（任务3） ============ -->
    <div v-if="multiOpen" class="drawer-mask" @click.self="closeMulti">
      <div class="drawer drawer-xwide">
        <div class="drawer-head">
          <b>多方案对比</b>
          <span v-if="drafts.length" class="meta">{{ drafts.length }} 份方案</span>
          <span class="spacer"></span>
          <button class="mini" :disabled="multiRunning" @click="runMulti">
            {{ multiRunning ? "生成中…" : "生成 3 版" }}
          </button>
          <button class="mini" :disabled="!drafts.length" @click="compose">拼合为最终版</button>
          <button class="mini danger" :disabled="!drafts.length" @click="discardDrafts">放弃并清理</button>
          <button class="mini" @click="closeMulti">关闭</button>
        </div>
        <div v-if="!drafts.length" class="empty">尚无方案 —— 点击「生成 3 版」串行生成（约需数分钟）</div>
        <div v-else class="ov-multi">
          <div v-for="d in drafts" :key="d.id" class="ov-multi-col">
            <div class="ov-multi-head">
              <b>方案 {{ d.id }}</b>
              <span class="pill" :class="verdictClass(d.summary && d.summary.verdict)">
                {{ d.summary && d.summary.verdict }}
              </span>
            </div>
            <div class="ov-multi-score">
              OK {{ (d.summary && d.summary.ok) || 0 }} / WARN {{ (d.summary && d.summary.warn) || 0 }} / THIN {{ (d.summary && d.summary.thin) || 0 }}
            </div>
            <label class="ov-multi-act">
              <input type="radio" :value="d.id" v-model="actSource" /> 四节（起承转合）取此方案的
            </label>
            <div class="ov-multi-sec">
              <div class="ov-multi-sec-title">关键节点</div>
              <label v-for="(n, i) in d.nodes" :key="'n' + i" class="ov-multi-item">
                <input type="checkbox" v-model="checked['n' + d.id + '_' + i]" />
                <span :class="'dot-' + n.level"></span>{{ n.text }}
              </label>
            </div>
            <div class="ov-multi-sec">
              <div class="ov-multi-sec-title">章节规划</div>
              <label v-for="(p, i) in d.plan" :key="'p' + i" class="ov-multi-item">
                <input type="checkbox" v-model="checked['p' + d.id + '_' + i]" />
                <span :class="'dot-' + p.level"></span>{{ p.text }}
              </label>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="localToast" class="toast">{{ localToast }}</div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from "vue";
import OutlineTree from "./OutlineTree.vue";
import { charDiff } from "./diff.js";

const props = defineProps({
  api: { type: Function, required: true },      // (path, method, body) => {status, data}
  isBusy: { type: Boolean, default: false },
  bookName: { type: String, default: "" },
});
const emit = defineEmits(["say", "structure-loaded"]);

const structure = ref(null);
const raw = ref("");
const savedRaw = ref("");
const selId = ref("");
const expanded = ref({});
const propText = ref("");
const propDirty = ref(false);
const saving = ref(false);
const localToast = ref("");
let toastTimer = null;

function say(msg) {
  localToast.value = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (localToast.value = ""), 4000);
  emit("say", msg);
}

const nodes = computed(() => (structure.value && structure.value.nodes) || []);
const plan = computed(() => (structure.value && structure.value.plan) || []);
const issueList = computed(() => {
  const a = (structure.value && structure.value.issues) || [];
  return a;
});
const dirty = computed(() => raw.value !== savedRaw.value);

/* ---------- 树宽可拖（长节点文案不再被挤成竖排） ---------- */
const TREE_W_KEY = "mofang.outline.treeW";
const treeW = ref(0);            // 0 = 用 CSS 默认（42%）
const splitDragging = ref(false);

function loadTreeW() {
  try {
    const v = Number(localStorage.getItem(TREE_W_KEY) || 0);
    if (v >= 260) treeW.value = v;
  } catch (_) { /* 忽略不可用的 localStorage */ }
}

function startSplitDrag(e) {
  const body = e.currentTarget.parentElement;         // .ov-body
  const card = body && body.querySelector(".ov-tree-card");
  if (!body || !card) return;
  const bodyW = body.getBoundingClientRect().width;
  const startX = e.clientX;
  const startW = card.getBoundingClientRect().width;
  splitDragging.value = true;

  function onMove(ev) {
    const w = Math.max(260, Math.min(startW + (ev.clientX - startX), bodyW * 0.78));
    treeW.value = Math.round(w);
  }
  function onUp() {
    splitDragging.value = false;
    window.removeEventListener("pointermove", onMove);
    try { localStorage.setItem(TREE_W_KEY, String(treeW.value)); } catch (_) { /* noop */ }
  }
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp, { once: true });
  e.preventDefault();
}

function resetTreeW() {
  treeW.value = 0;
  try { localStorage.removeItem(TREE_W_KEY); } catch (_) { /* noop */ }
}

/* ---------- 树数据（根=书名 → act → 条目） ---------- */
/* 树内摘要：act 的正文与节点 tail 都可能是一整段，直接塞进树会撑成几十行
   （实测有一条 31 行的巨型节点，什么都看不清）。
   显示用 tail 截断，**完整文本另存 fullTail** —— 内联编辑的预填与悬浮提示都用它，
   否则"双击编辑再保存"会把用户原文截掉。完整文本始终在右侧属性面板可编辑。 */
const TREE_TAIL_CHARS = 44;
function clipTail(s, n = TREE_TAIL_CHARS) {
  const t = String(s == null ? "" : s).replace(/\s+/g, " ").trim();
  return t.length > n ? t.slice(0, n) + "…" : t;
}

const tree = computed(() => {
  if (!structure.value || !structure.value.exists) return [];
  const byAct = { 起: [], 承: [], 转: [], 合: [] };
  // 关键节点按章节区间归 act（后端已给出？节点未带 act，这里按顺序均分）
  const ns = nodes.value;
  ns.forEach((n, i) => {
    const frac = ns.length > 1 ? i / (ns.length - 1) : 0;
    const act = frac <= 0.25 ? "起" : frac <= 0.5 ? "承" : frac <= 0.75 ? "转" : "合";
    byAct[act].push({ ...n, _kind: "node" });
  });
  plan.value.forEach((p) => {
    const act = p.act && byAct[p.act] ? p.act : "起";
    byAct[act].push({ ...p, _kind: "plan" });
  });

  const actChildren = (name) => {
    const list = byAct[name] || [];
    const nodeKids = list.filter((x) => x._kind === "node")
      .map((x) => ({ ...x, title: x.title, tail: clipTail(x.tail), fullTail: x.tail || "",
                     badge: x.level ? x.level : "", children: [] }));
    const planKids = list.filter((x) => x._kind === "plan")
      .map((x) => ({ ...x, title: x.title, tail: clipTail(x.tail), fullTail: x.tail || "",
                     children: [] }));
    // 章节规划作为子分组挂在 act 下（三级：act → 规划分组 → 条目）
    const kids = [...nodeKids];
    if (planKids.length) {
      kids.push({
        id: "grp_plan_" + name, title: "章节规划（" + planKids.length + "）", tail: "",
        fullTail: "", children: planKids,
      });
    }
    return kids;
  };

  return [{
    id: "root_book", title: props.bookName || (structure.value.title || "（未命名书）"),
    tail: "", fullTail: "", children: ["起", "承", "转", "合"].map((a) => {
      const actText = (structure.value.acts.find((x) => x.name === a) || {}).text || "";
      return {
        id: "act_" + a, title: a, tail: clipTail(actText), fullTail: actText,
        children: actChildren(a),
      };
    }),
  }];
});

/* ---------- 选中 ---------- */
const sel = computed(() => {
  if (!selId.value) return null;
  return [...nodes.value, ...plan.value].find((x) => x.id === selId.value) || null;
});
const flagList = [
  { key: "dense", label: "篇幅" },
  { key: "event", label: "事件" },
  { key: "conflict", label: "冲突" },
  { key: "role", label: "角色" },
  { key: "scene", label: "场景" },
];

function onSelect(node) {
  if (!node || !node.id) return;
  if (String(node.id).startsWith("act_") || node.id === "root_book" || String(node.id).startsWith("grp_")) {
    // 选中 act/分组：仅展开，不显示属性
    return;
  }
  if (propDirty.value && selId.value && !window.confirm("当前条目有未保存修改，切换将丢失。继续？")) return;
  selId.value = node.id;
  const target = [...nodes.value, ...plan.value].find((x) => x.id === node.id);
  propText.value = target ? (target.text || "") : "";
  propDirty.value = false;
}
function onToggle({ id, open }) {
  expanded.value = { ...expanded.value, [id]: open };
}
function expandAll(open) {
  const m = {};
  ["root_book", "act_起", "act_承", "act_转", "act_合"].forEach((k) => (m[k] = open));
  ["起", "承", "转", "合"].forEach((a) => (m["grp_plan_" + a] = open));
  expanded.value = m;
}

/* ---------- 拖拽排序（同层级；写回 global.md 顺序） ---------- */
async function onReorder({ group, from, to, fromId, toId }) {
  if (group !== "root") return;     // 只允许同 act 内、同类型内
  const all = [...nodes.value, ...plan.value];
  const a = all.find((x) => x.id === fromId);
  const b = all.find((x) => x.id === toId);
  if (!a || !b) return;
  const kindA = nodes.value.some((x) => x.id === fromId) ? "node" : "plan";
  const kindB = nodes.value.some((x) => x.id === toId) ? "node" : "plan";
  if (kindA !== kindB) return say("不允许跨类型拖拽（关键节点 ↔ 章节规划）");

  const seq = kindA === "node" ? [...nodes.value] : [...plan.value];
  const i = seq.findIndex((x) => x.id === fromId);
  let j = seq.findIndex((x) => x.id === toId);
  if (i < 0 || j < 0) return;
  const [moved] = seq.splice(i, 1);
  j = seq.findIndex((x) => x.id === toId);
  const insertAt = to > from ? j + 1 : j;
  seq.splice(insertAt, 0, moved);

  // 重新生成整份文本：按重排后的顺序渲染
  const newRaw = renderGlobal({
    title: (structure.value.title || ""),
    acts: Object.fromEntries(structure.value.acts.map((x) => [x.name, x.text])),
    nodes: kindA === "node" ? seq : nodes.value,
    plan: kindA === "plan" ? seq : plan.value,
    expected_chapters: structure.value.expected_chapters,
  });
  raw.value = newRaw;
  await refreshStructureOnly();
  say("已调整顺序（记得点保存写盘）");
}

/* ---------- 内联编辑（任务1） ---------- */
function onInlineEdit({ id, text }) {
  const target = [...nodes.value, ...plan.value].find((x) => x.id === id);
  if (!target) return;
  const newRaw = raw.value.replace(new RegExp("^(\\s*[-*]\\s*)" + escapeRe(target.text) + "\\s*$", "m"),
                                   "$1" + text);
  if (newRaw === raw.value) { say("未能在原文中定位该条目"); return; }
  raw.value = newRaw;
  if (selId.value === id) { propText.value = text; propDirty.value = false; }
  refreshStructureOnly();
  say("已内联修改（点「保存」写盘）");
}

function escapeRe(s) {
  return String(s || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/* ---------- 属性面板保存此条 ---------- */
function saveEntry() {
  const target = sel.value;
  if (!target) return;
  const newRaw = raw.value.replace(new RegExp("^(\\s*[-*]\\s*)" + escapeRe(target.text) + "\\s*$", "m"),
                                   "$1" + propText.value.trim());
  if (newRaw === raw.value) { say("未能在原文中定位该条目"); return; }
  raw.value = newRaw;
  propDirty.value = false;
  refreshStructureOnly();
  say("已更新此条（点「保存」写盘）");
}
function revertEntry() {
  propText.value = sel.value ? sel.value.text : "";
  propDirty.value = false;
}

/* ---------- 载入 / 保存 ---------- */
async function load(showMsg = false) {
  const r = await props.api("/outline/structure");
  if (r.status !== 200) { say("加载失败: " + (r.data.error || r.status)); return; }
  structure.value = r.data;
  raw.value = r.data.raw || "";
  savedRaw.value = raw.value;
  propDirty.value = false;
  emit("structure-loaded", r.data);
  if (!selId.value && r.data.nodes && r.data.nodes.length) {
    selId.value = r.data.nodes[0].id;
    propText.value = r.data.nodes[0].text || "";
  } else if (sel.value) {
    propText.value = sel.value.text || "";
  }
  if (showMsg) say("已刷新");
}
async function refreshStructureOnly() {
  const r = await props.api("/outline/structure");
  if (r.status === 200) {
    const prevRaw = raw.value;
    structure.value = r.data;
    raw.value = prevRaw;                 // 保留本地编辑中的文本
    if (sel.value) propText.value = sel.value.text || "";
    emit("structure-loaded", r.data);
  }
}
async function save() {
  saving.value = true;
  const r = await props.api("/outline/save", "POST", { content: raw.value });
  saving.value = false;
  if (r.status !== 200 || !r.data.ok) { say("保存失败: " + (r.data.error || r.status)); return; }
  savedRaw.value = raw.value;
  if (r.data.structure) { structure.value = r.data.structure; }
  say("已保存（备份 " + (r.data.backup || "") + "）");
}

/* ---------- AI 节点精修（任务2） ---------- */
const aiOpen = ref(false);
const aiFeedback = ref("");
const aiLoading = ref(false);
const aiResult = ref(null);
const aiCharDiff = computed(() => {
  if (!aiResult.value) return [];
  return charDiff(aiResult.value.old_text || "", aiResult.value.new_text || "");
});
async function submitAi() {
  if (!sel.value) return;
  aiOpen.value = false;
  aiLoading.value = true;
  const r = await props.api("/refine/outline/node", "POST",
                            { node_id: sel.value.id, feedback: aiFeedback.value });
  if (r.status !== 202) { aiLoading.value = false; say("提交失败: " + (r.data.error || r.status)); return; }
  const jid = r.data.job_id;
  const poll = setInterval(async () => {
    const j = await props.api("/jobs/" + jid);
    if (j.status !== 200) return;
    if (j.data.state === "running") return;
    clearInterval(poll);
    aiLoading.value = false;
    if (j.data.state === "ok" && j.data.result && typeof j.data.result === "object") {
      aiResult.value = j.data.result;
      aiFeedback.value = "";
      await load(false);
      say("已修订「" + (j.data.result.entry_id || "") + "」（v" + j.data.result.version + "）");
    } else {
      say("修订失败: " + (j.data.result || "未知错误"));
    }
  }, 1500);
}
async function undoRefine() {
  if (!window.confirm("撤销上次节点修订？将从 history 恢复上一版大纲。")) return;
  aiLoading.value = true;
  const r = await props.api("/refine/outline/undo", "POST", {});
  aiLoading.value = false;
  if (r.status !== 200 || !r.data.ok) { say("撤销失败: " + (r.data.error || r.status)); return; }
  aiResult.value = null;
  await load(false);
  say("已撤销并恢复上一版");
}

/* ---------- 版本对比（任务4） ---------- */
const verOpen = ref(false);
const versions = ref([]);
const v1 = ref(1);
const v2 = ref(0);
const diffData = ref(null);
const diffCounts = computed(() => diffData.value && diffData.value.counts);
async function openVersionDiff() {
  const r = await props.api("/outline/history");
  if (r.status !== 200) { say("版本列表加载失败"); return; }
  versions.value = r.data.versions || [];
  if (versions.value.length) {
    v1.value = versions.value[versions.value.length - 1].version;   // 最早
    v2.value = 0;                                                   // 当前
  }
  verOpen.value = true;
  loadVersionDiff();
}
async function loadVersionDiff() {
  const r = await props.api("/outline/diff?v1=" + v1.value + "&v2=" + v2.value);
  if (r.status !== 200) { say("diff 加载失败: " + (r.data.error || r.status)); diffData.value = { segments: [] }; return; }
  diffData.value = r.data;
}
function scoreLabel(s) {
  if (!s) return "—";
  return "OK " + (s.ok || 0) + "/WARN " + (s.warn || 0) + "/THIN " + (s.thin || 0);
}
async function restoreV1() {
  if (!window.confirm("确定把 " + v1.value + " 版覆盖到当前 global.md？\n\n当前版会先备份到 history/，可再恢复。")) return;
  const r = await props.api("/outline/restore", "POST", { version: v1.value });
  if (r.status !== 200 || !r.data.ok) { say("恢复失败: " + (r.data.error || r.status)); return; }
  verOpen.value = false;
  await load(false);
  say("已恢复 " + v1.value + " 版");
}
function exportDiff() {
  const d = diffData.value;
  if (!d) return;
  const lines = ["# 大纲版本对比 v" + d.v1 + " → v" + d.v2, "",
                 "- v1 评分: " + scoreLabel(d.score_diff && d.score_diff.v1),
                 "- v2 评分: " + scoreLabel(d.score_diff && d.score_diff.v2), ""];
  for (const s of d.segments || []) {
    const tag = s.type === "add" ? "[新增]" : s.type === "del" ? "[删除]" : s.type === "change" ? "[修改]" : "[未变]";
    lines.push(tag + " " + s.title);
    lines.push("  v1: " + (s.content || ""));
    if (s.type === "change") lines.push("  v2: " + (s.content_v2 || ""));
    lines.push("");
  }
  const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "outline_diff_v" + d.v1 + "_v" + d.v2 + ".md";
  a.click();
  URL.revokeObjectURL(a.href);
  say("diff 已导出");
}

/* ---------- 多方案（任务3） ---------- */
const multiOpen = ref(false);
const drafts = ref([]);
const actSource = ref(1);
const multiRunning = ref(false);
const checked = ref({});
async function openMulti() {
  multiOpen.value = true;
  await loadDrafts();
}
async function loadDrafts() {
  const r = await props.api("/outline/drafts");
  if (r.status !== 200) { say("方案加载失败"); return; }
  drafts.value = r.data.drafts || [];
  if (drafts.value.length) actSource.value = drafts.value[0].id;
  const c = {};
  drafts.value.forEach((d) => {
    d.nodes.forEach((_, i) => (c["n" + d.id + "_" + i] = d.id === drafts.value[0].id));
    d.plan.forEach((_, i) => (c["p" + d.id + "_" + i] = d.id === drafts.value[0].id));
  });
  checked.value = c;
}
async function runMulti() {
  multiRunning.value = true;
  const r = await props.api("/stage/2/run-multi", "POST", { count: 3 });
  if (r.status !== 202) { multiRunning.value = false; say("提交失败: " + (r.data.error || r.status)); return; }
  const jid = r.data.job_id;
  const poll = setInterval(async () => {
    const j = await props.api("/jobs/" + jid);
    if (j.status !== 200 || j.data.state === "running") return;
    clearInterval(poll);
    multiRunning.value = false;
    await loadDrafts();
    say(j.data.state === "ok" ? "方案生成完成" : "生成失败: " + (j.data.result || ""));
  }, 2000);
}
async function compose() {
  const sels = [];
  drafts.value.forEach((d) => {
    d.nodes.forEach((_, i) => { if (checked.value["n" + d.id + "_" + i]) sels.push({ draft: d.id, kind: "node", index: i }); });
    d.plan.forEach((_, i) => { if (checked.value["p" + d.id + "_" + i]) sels.push({ draft: d.id, kind: "plan", index: i }); });
  });
  if (!sels.length) return say("请至少勾选一个条目");
  if (!window.confirm("按勾选拼合为最终 global.md？\n\n· 将覆盖当前大纲（先自动备份）\n· 拼合后清理临时方案文件")) return;
  const r = await props.api("/outline/compose", "POST", { selections: sels, act_source: actSource.value, cleanup: true });
  if (r.status !== 200 || !r.data.ok) { say("拼合失败: " + (r.data.error || r.status)); return; }
  multiOpen.value = false;
  drafts.value = [];
  if (r.data.warnings && r.data.warnings.length) {
    say("已拼合（注意：" + r.data.warnings.join("；") + "）");
  } else {
    say("已拼合为最终版");
  }
  await load(false);
}
async function discardDrafts() {
  if (!window.confirm("放弃并清理全部临时方案文件？")) return;
  await props.api("/outline/drafts/cleanup", "POST", {});
  drafts.value = [];
  say("已清理临时方案");
}
function closeMulti() {
  multiOpen.value = false;
}

/* ---------- 渲染工具 ---------- */
function renderGlobal(st) {
  const acts = st.acts || {};
  const L = [st.title && st.title.includes("大纲") ? st.title : (st.title || "") + " 整体大纲", ""];
  ["起", "承", "转", "合"].forEach((a) => {
    L.push("## " + a);
    L.push((acts[a] || "").trim() || "（待补充）");
    L.push("");
  });
  L.push("## 关键节点");
  (st.nodes || []).forEach((n) => L.push("- " + n.text));
  L.push("");
  L.push("## 预计章节数");
  L.push(String(st.expected_chapters || 0));
  L.push("");
  L.push("## 章节规划");
  (st.plan || []).forEach((p) => L.push("- " + p.text));
  L.push("");
  return L.join("\n");
}
function levelPill(lv) {
  return lv === "OK" ? "st-done" : lv === "WARN" ? "st-gate" : "st-failed";
}
function verdictClass(v) {
  return v === "PASS" ? "st-done" : v === "WARN" ? "st-gate" : "st-failed";
}

onMounted(() => { loadTreeW(); load(false); });
defineExpose({ load });
</script>
