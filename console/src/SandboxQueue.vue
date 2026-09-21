<!--
  SandboxQueue.vue — 「审核」页签：沙盒产物的审核队列

  ## 它解决什么

  沙盒（data/state/obsidian_sandbox/，可指向你的 Obsidian 库内目录）是
  「准备粘贴进 vault」的中转区。产物写进来时自动登记为**待审**，但此前
  GUI 里看不到队列，也没有通过/驳回的动作 —— 用户只能开终端敲 CLI。

  本页签把这条链路补全：看得见队列 → 点得出结论 → 状态就地刷新。

  ## 三条必须说清的边界（都写在界面上，不靠用户猜）

  1. **只贴标签，不动文件**：通过/驳回只改
     data/state/sandbox_manifest.json，不写不删沙盒里的 .md，更不碰 vault。
     审错了点「退回待审」即可无损撤回。
  2. **不覆盖 vault**：本页签**没有**任何「写入 Obsidian」的按钮。
     真正的入库动作永远由你手工完成 —— 决策权在你手里。
  3. **改过的文件要重审**：产物内容变了（sha256 变），状态自动回到待审，
     防止旧审核为新内容背书。

  ## 孤儿文件

  手工拷进沙盒、或换过沙盒目录时，会出现「文件在、状态库没有」的文件。
  它们不在队列里，是审核盲区 —— 所以单独用一块醒目区列出，而不是静默吞掉。
-->
<template>
  <div class="sb-wrap">
    <!-- ============ 顶部：路径 + 统计 + 过滤 ============ -->
    <div class="card sb-toolbar">
      <div class="card-head" style="margin-bottom: 0;">
        <h3>
          审核队列
          <span v-if="pendingCount" class="pill st-gate">{{ pendingCount }} 份待审</span>
          <span v-else-if="loaded" class="pill st-done">全部已审</span>
        </h3>
        <button class="mini" :disabled="loading" @click="load(true)">
          {{ loading ? "刷新中…" : "刷新" }}
        </button>
        <label class="sb-filter">
          <input type="checkbox" v-model="showAll" @change="load()" />
          显示全部（含已通过 / 已驳回）
        </label>
      </div>
      <div class="meta sb-dir" :title="sandboxDir">
        <span class="sb-dir-label">沙盒目录</span>
        <code>{{ sandboxDir || "（未配置）" }}</code>
        <button v-if="sandboxDir" class="mini ghost" @click="copyDir">复制路径</button>
      </div>
      <div class="meta" style="margin-top: 6px;">
        通过 / 驳回<strong>只改审核状态</strong>，不动沙盒里的文件，也<strong>不会写入 Obsidian</strong>。
        粘贴进 vault 的动作始终由你手工完成；审错了点「退回待审」可无损撤回。
      </div>
      <div class="sb-stats">
        <span class="pill st-gate">待审 {{ stats.pending || 0 }}</span>
        <span class="pill st-done">已通过 {{ stats.approved || 0 }}</span>
        <span class="pill st-failed">已驳回 {{ stats.rejected || 0 }}</span>
      </div>
    </div>

    <!-- ============ 孤儿警告（审核盲区）============ -->
    <div v-if="orphans.length" class="card sb-orphans">
      <div class="card-head" style="margin-bottom: 4px;">
        <h3>⚠ {{ orphans.length }} 个文件未登记（不在审核队列里）</h3>
      </div>
      <div class="meta">
        这些文件在沙盒里，但状态库里没有记录 —— 通常是手工拷进来的，或换过沙盒目录。
        它们永远不会出现在下面的队列中，是审核盲区，所以在这里单独列出来。
      </div>
      <ul class="sb-orphan-list">
        <li v-for="o in orphans" :key="o">
          <code>{{ o }}</code>
          <button class="mini ghost" @click="copyText(o)">复制</button>
        </li>
      </ul>
      <div class="meta">
        要纳入审核：用导出的方式重新写一遍（如大纲页签的「提交审核包」），或直接用
        <code>sandbox_review.py --list</code> 查看。
      </div>
    </div>

    <!-- ============ 队列 ============ -->
    <div class="card sb-list-card">
      <div v-if="loading && !items.length" class="empty">加载中…</div>
      <div v-else-if="!items.length" class="empty">
        <template v-if="showAll">沙盒里还没有产物</template>
        <template v-else>没有待审产物 ✅<br /><span class="meta">有新产物写入沙盒时会出现在这里</span></template>
      </div>
      <div v-else class="sb-list">
        <div v-for="it in items" :key="it.path" class="sb-item" :class="'sb-' + it.status">
          <div class="sb-item-main">
            <div class="sb-item-title">
              <span class="pill" :class="statusClass(it.status)">{{ statusLabel(it.status) }}</span>
              <code class="sb-path">{{ it.path }}</code>
            </div>
            <div class="meta sb-item-meta">
              <span v-if="it.kind">类型 {{ it.kind }}</span>
              <span v-if="it.source">来源 {{ it.source }}</span>
              <span>更新 {{ it.updated_at || it.created_at || "—" }}</span>
              <span v-if="it.reviewed_at">审于 {{ it.reviewed_at }}</span>
            </div>
            <div v-if="it.note" class="sb-note">备注：{{ it.note }}</div>
          </div>

          <div class="sb-item-actions">
            <button class="mini" :disabled="busyPath === it.path" @click="view(it)">预览</button>
            <button v-if="it.status !== 'approved'" class="mini primary"
                    :disabled="busyPath === it.path" @click="act(it, 'approve')">通过</button>
            <button v-if="it.status !== 'rejected'" class="mini danger"
                    :disabled="busyPath === it.path" @click="askReject(it)">驳回</button>
            <button v-if="it.status !== 'pending'" class="mini ghost"
                    :disabled="busyPath === it.path" @click="act(it, 'reset')">退回待审</button>
          </div>
        </div>
      </div>
    </div>

    <!-- ============ 驳回原因对话框 ============ -->
    <div v-if="rejectOpen" class="drawer-mask" @click.self="rejectOpen = false">
      <div class="dialog">
        <h3>驳回「{{ rejectTarget && rejectTarget.path }}」</h3>
        <div class="meta">驳回原因会记进审核状态库（不进产物文件），你自己回头也看得懂。</div>
        <textarea v-model="rejectNote" rows="3"
                  placeholder="例如：第二节与设定冲突 / 人物称谓不统一"></textarea>
        <div class="dialog-actions">
          <button class="mini" @click="rejectOpen = false">取消</button>
          <button class="mini danger" :disabled="!rejectNote.trim()" @click="doReject">确认驳回</button>
        </div>
      </div>
    </div>

    <!-- ============ 预览 ============ -->
    <div v-if="viewOpen" class="drawer-mask" @click.self="viewOpen = false">
      <div class="drawer drawer-wide">
        <div class="drawer-head">
          <b>{{ viewPath }}</b>
          <span class="spacer"></span>
          <button class="mini" @click="copyText(viewContent)">复制全文</button>
          <button class="mini" @click="viewOpen = false">关闭</button>
        </div>
        <div class="meta" style="margin-bottom: 6px;">
          只读预览。这是沙盒中的草稿样本，**尚未**进入 Obsidian 设定库。
        </div>
        <pre class="sb-preview">{{ viewContent }}</pre>
      </div>
    </div>

    <div v-if="localToast" class="toast">{{ localToast }}</div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from "vue";

const props = defineProps({
  api: { type: Function, required: true },      // (path, method, body) => {status, data}
});
const emit = defineEmits(["say", "stats"]);

const items = ref([]);
const stats = ref({});
const orphans = ref([]);
const sandboxDir = ref("");
const loading = ref(false);
const loaded = ref(false);
const showAll = ref(false);
const busyPath = ref("");
const localToast = ref("");
let toastTimer = null;

function say(msg) {
  localToast.value = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (localToast.value = ""), 4200);
  emit("say", msg);
}

const pendingCount = computed(() => stats.value.pending || 0);

const STATUS_LABELS = { pending: "待审", approved: "已通过", rejected: "已驳回" };
function statusLabel(s) { return STATUS_LABELS[s] || s; }
function statusClass(s) {
  return s === "approved" ? "st-done" : s === "rejected" ? "st-failed" : "st-gate";
}

async function load(showMsg = false) {
  loading.value = true;
  const r = await props.api("/sandbox/queue" + (showAll.value ? "?all=1" : ""));
  loading.value = false;
  if (r.status !== 200) {
    say("加载失败: " + ((r.data && r.data.error) || r.status));
    return;
  }
  items.value = r.data.items || [];
  stats.value = r.data.stats || {};
  orphans.value = r.data.orphans || [];
  sandboxDir.value = r.data.sandbox_dir || "";
  loaded.value = true;
  emit("stats", stats.value);
  if (showMsg) say("已刷新");
}

/**
 * 审核动作。后端只改状态库（不碰文件），所以这里**不需要**二次确认弹窗 ——
 * 逐条确认会把「快速过一遍队列」变成折磨，且动作本身无损（有「退回待审」兜底）。
 * 唯一例外是驳回：必须填原因，走对话框。
 */
async function act(it, action) {
  busyPath.value = it.path;
  const r = await props.api("/sandbox/review", "POST", { path: it.path, action });
  busyPath.value = "";
  if (r.status !== 200 || !r.data.ok) {
    say("操作失败: " + ((r.data && r.data.error) || r.status));
    return;
  }
  // 后端回带了 stats 与条目新状态：就地更新，不用再补一次 GET（少一次往返）
  stats.value = r.data.stats || stats.value;
  emit("stats", stats.value);
  orphans.value = r.data.orphans || orphans.value;
  if (r.data.item) {
    if (action === "approve" || action === "reject") {
      if (showAll.value) {
        const i = items.value.findIndex((x) => x.path === it.path);
        if (i >= 0) items.value[i] = r.data.item;
      } else {
        // 默认视图只看待审：审完就从列表里移走
        items.value = items.value.filter((x) => x.path !== it.path);
      }
    } else {
      // reset：把条目放回列表（它重新变成待审）
      const i = items.value.findIndex((x) => x.path === it.path);
      if (i >= 0) items.value[i] = r.data.item;
      else items.value.unshift(r.data.item);
    }
  }
  say(r.data.message || "已更新");
}

const rejectOpen = ref(false);
const rejectTarget = ref(null);
const rejectNote = ref("");
function askReject(it) {
  rejectTarget.value = it;
  rejectNote.value = "";
  rejectOpen.value = true;
}
async function doReject() {
  const it = rejectTarget.value;
  if (!it) return;
  const note = rejectNote.value.trim();
  if (!note) return;
  busyPath.value = it.path;
  const r = await props.api("/sandbox/review", "POST",
                            { path: it.path, action: "reject", note });
  busyPath.value = "";
  rejectOpen.value = false;
  if (r.status !== 200 || !r.data.ok) {
    say("驳回失败: " + ((r.data && r.data.error) || r.status));
    return;
  }
  stats.value = r.data.stats || stats.value;
  emit("stats", stats.value);
  if (showAll.value) {
    const i = items.value.findIndex((x) => x.path === it.path);
    if (i >= 0) items.value[i] = r.data.item;
  } else {
    items.value = items.value.filter((x) => x.path !== it.path);
  }
  say("已驳回：" + it.path);
}

/* ---------- 预览：读沙盒里的实际文件内容 ---------- */
const viewOpen = ref(false);
const viewPath = ref("");
const viewContent = ref("");
async function view(it) {
  // 复用 /outline/chapters/get 不合适；沙盒产物走 /sandbox/queue 的路径，
  // 这里用 /kb 无关的通用读接口会引入耦合 —— 改为直接读沙盒文件的既有端点。
  // 若无通用读端点，则退化为「只展示元信息 + 提示用编辑器打开」。
  viewPath.value = it.path;
  viewContent.value = "";
  viewOpen.value = true;
  const r = await props.api("/sandbox/file?path=" + encodeURIComponent(it.path));
  if (r.status === 200 && typeof r.data.content === "string") {
    viewContent.value = r.data.content;
  } else {
    viewContent.value = "（无法预览：" + ((r.data && r.data.error) || r.status)
      + "）\n\n请用编辑器打开：" + sandboxDir.value + "\\" + it.path.replace(/\//g, "\\");
  }
}

/* ---------- 复制工具 ---------- */
function copyText(t) {
  writeClipboard(t);
  say("已复制");
}
function copyDir() {
  writeClipboard(sandboxDir.value);
  say("路径已复制");
}
function writeClipboard(t) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(t);
      return;
    }
  } catch (_) { /* 回落 */ }
  const ta = document.createElement("textarea");
  ta.value = t;
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand("copy"); } catch (_) { /* noop */ }
  document.body.removeChild(ta);
}

onMounted(() => load(false));
defineExpose({ load });
</script>
