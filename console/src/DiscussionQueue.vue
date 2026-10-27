<!--
  DiscussionQueue.vue — “审核”页签里的讨论提案队列（P0-1 落地协议，2026-10-18）

  ## 它解决什么

  用户的真实工作流是“与 agent 讨论十几次 → 结论以【优化点】烤进大纲 → 从大纲
  往下写”，但产品里一直没有讨论入口 —— 唯一的修改通道是审批门的通过/打回二元
  选择。落地协议补上这条通道：agent 通过 `POST /outline/discussion/apply` 提交
  提案（只落草稿区，不碰章大纲），用户在这里看到队列、读提案、批准才真正写回。

  ## 与沙盒审核队列的边界（两个队列并存，别搞混）

  - **本队列**：讨论提案。批准会**把优化点追加进 data/outline/chapters/NN.md**
    （写前自动备份章文件）。这是“改我的大纲”，目的地是项目内的章大纲。
  - **沙盒队列**（下方）：Obsidian 沙盒产物。批准**只贴标签不动文件**，粘贴进
    vault 的动作始终由你手工完成。

  ## 红线：agent 可提案、不可自批

  `/outline/discussion/review` 已列入 `FORBIDDEN_IN_AGENT_MODE`（审批族，与
  /approve 同档）。所以这里的“批准”按钮只有你能点 —— 这不是 UI 限制，是后端
  fail-closed 守卫。
-->
<template>
  <div class="dq-wrap">
    <div class="card dq-toolbar">
      <div class="card-head" style="margin-bottom: 0;">
        <h3>
          讨论提案
          <span v-if="pendingCount" class="pill st-gate">{{ pendingCount }} 条待审</span>
          <span v-else-if="loaded" class="pill st-done">没有待审提案</span>
        </h3>
        <button class="mini" :disabled="loading" @click="load()">
          {{ loading ? "刷新中…" : "刷新" }}
        </button>
        <label class="dq-filter">
          <input type="checkbox" v-model="showAll" @change="load()" />
          显示全部
        </label>
      </div>
      <div class="meta" style="margin-top: 6px;">
        agent 与你讨论后把结论落成提案，<strong>批准才会写回章大纲</strong>（追加
        【优化点】，写前自动备份章文件）。驳回只作废提案，不碰大纲。
      </div>
      <div class="dq-stats">
        <span class="pill st-gate">待审 {{ stats.pending || 0 }}</span>
        <span class="pill st-done">已通过 {{ stats.approved || 0 }}</span>
        <span class="pill st-failed">已驳回 {{ stats.rejected || 0 }}</span>
      </div>
    </div>

    <div class="card dq-list-card">
      <div v-if="loading && !items.length" class="empty">加载中…</div>
      <div v-else-if="!items.length" class="empty">
        <template v-if="showAll">还没有讨论提案</template>
        <template v-else>没有待审提案 ✅<br /><span class="meta">agent 提交讨论结论后会出现在这里</span></template>
      </div>
      <div v-else class="dq-list">
        <div v-for="it in items" :key="it.path" class="dq-item" :class="'dq-' + it.status">
          <div class="dq-item-main">
            <div class="dq-item-title">
              <span class="pill" :class="statusClass(it.status)">{{ statusLabel(it.status) }}</span>
              <code class="dq-path">{{ shortPath(it.path) }}</code>
            </div>
            <div class="meta dq-item-meta">
              <span v-if="it.source">来源 {{ it.source }}</span>
              <span>更新 {{ it.updated_at || it.created_at || "—" }}</span>
              <span v-if="it.reviewed_at">审于 {{ it.reviewed_at }}</span>
            </div>
            <div v-if="it.note" class="dq-note">备注：{{ it.note }}</div>
          </div>

          <div class="dq-item-actions">
            <button class="mini" :disabled="busyPath === it.path" @click="view(it)">读提案</button>
            <button v-if="it.status !== 'approved'" class="mini primary"
                    :disabled="busyPath === it.path" @click="askApprove(it)">批准并写回</button>
            <button v-if="it.status !== 'rejected'" class="mini danger"
                    :disabled="busyPath === it.path" @click="askReject(it)">驳回</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 批准确认：说清会发生什么（写回哪一章、有备份） -->
    <div v-if="approveOpen" class="drawer-mask" @click.self="approveOpen = false">
      <div class="dialog">
        <h3>批准提案并写回章大纲</h3>
        <div class="meta">
          将把提案里的优化点<strong>追加</strong>到对应章的大纲末尾（编号自动顺延），
          写前会备份章文件到 <code>data/outline/chapters/</code> 下的 <code>.bak</code>。
          不改章正文，只加批注层。
        </div>
        <pre class="dq-preview-sm">{{ approveContent }}</pre>
        <div class="dialog-actions">
          <button class="mini" @click="approveOpen = false">再看看</button>
          <button class="mini primary" :disabled="busyPath === (approveTarget && approveTarget.path)"
                  @click="doApprove">确认写回</button>
        </div>
      </div>
    </div>

    <!-- 驳回原因 -->
    <div v-if="rejectOpen" class="drawer-mask" @click.self="rejectOpen = false">
      <div class="dialog">
        <h3>驳回提案</h3>
        <div class="meta">驳回原因记进提案状态库（不写进章大纲），你自己回头也看得懂。</div>
        <textarea v-model="rejectNote" rows="3"
                  placeholder="例如：方向不对 / 与设定冲突 / 这轮讨论先放着"></textarea>
        <div class="dialog-actions">
          <button class="mini" @click="rejectOpen = false">取消</button>
          <button class="mini danger" :disabled="!rejectNote.trim()" @click="doReject">确认驳回</button>
        </div>
      </div>
    </div>

    <!-- 读提案（全文） -->
    <div v-if="viewOpen" class="drawer-mask" @click.self="viewOpen = false">
      <div class="drawer drawer-wide">
        <div class="drawer-head">
          <b>{{ viewPath }}</b>
          <span class="spacer"></span>
          <button class="mini" @click="viewOpen = false">关闭</button>
        </div>
        <div class="meta" style="margin-bottom: 6px;">
          这是 agent 提交的<strong>待审提案</strong>。批准会把其中的优化点写回章大纲，
          驳回只作废本提案。
        </div>
        <pre class="dq-preview">{{ viewContent }}</pre>
        <div class="dialog-actions">
          <button class="mini" @click="viewOpen = false">关闭</button>
          <button v-if="viewStatus !== 'approved'" class="mini primary"
                  @click="viewOpen = false; askApprove(viewTarget)">批准并写回</button>
        </div>
      </div>
    </div>

    <div v-if="localToast" class="toast">{{ localToast }}</div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from "vue";

const props = defineProps({ api: { type: Function, required: true } });
const emit = defineEmits(["say", "stats"]);

const items = ref([]);
const stats = ref({});
const loading = ref(false);
const loaded = ref(false);
const showAll = ref(false);
const busyPath = ref("");

const localToast = ref("");
let toastTimer = null;
function toast(msg) {
  localToast.value = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (localToast.value = ""), 3200);
  emit("say", msg);
}

const pendingCount = computed(() =>
  items.value.filter((i) => i.status === "pending").length);

async function load(silent) {
  loading.value = true;
  try {
    const q = showAll.value ? "?status=all" : "";
    const r = await props.api("/outline/discussions" + q);
    if (r.status === 200 && r.data.ok) {
      items.value = r.data.items || [];
      stats.value = r.data.stats || {};
      emit("stats", stats.value);
    } else if (!silent) {
      toast(r.data.error || "加载失败");
    }
    loaded.value = true;
  } catch (e) {
    if (!silent) toast(e.message || String(e));
  } finally {
    loading.value = false;
  }
}

function statusLabel(s) {
  return { pending: "待审", approved: "已通过", rejected: "已驳回" }[s] || s;
}
function statusClass(s) {
  return { pending: "st-gate", approved: "st-done", rejected: "st-failed" }[s] || "";
}
function shortPath(p) {
  // 提案文件名带章号与时间戳，路径前缀对用户是噪声 —— 只留文件名
  return String(p || "").split("/").pop();
}

// ---- 读提案 ----
const viewOpen = ref(false);
const viewPath = ref("");
const viewContent = ref("");
const viewStatus = ref("");
const viewTarget = ref(null);
async function view(it) {
  busyPath.value = it.path;
  try {
    const r = await props.api("/outline/discussion/draft?path=" + encodeURIComponent(it.path));
    if (r.status === 200 && r.data.ok) {
      viewPath.value = it.path;
      viewContent.value = r.data.content;
      viewStatus.value = it.status;
      viewTarget.value = it;
      viewOpen.value = true;
    } else {
      toast(r.data.error || "读取失败");
    }
  } catch (e) {
    toast(e.message || String(e));
  } finally {
    busyPath.value = "";
  }
}

// ---- 批准（写回章大纲）----
const approveOpen = ref(false);
const approveTarget = ref(null);
const approveContent = ref("");
function askApprove(it) {
  approveTarget.value = it;
  approveContent.value = (viewContent.value && viewTarget.value && viewTarget.value.path === it.path)
    ? viewContent.value
    : "（打开提案时会显示全文）";
  approveOpen.value = true;
}
async function doApprove() {
  const it = approveTarget.value;
  if (!it) return;
  busyPath.value = it.path;
  try {
    const r = await props.api("/outline/discussion/review", "POST",
                              { path: it.path, action: "approve" });
    if (r.status === 200 && r.data.ok) {
      toast(r.data.message || "已写回章大纲");
      approveOpen.value = false;
      await load(true);
    } else {
      toast(r.data.error || r.data.message || "批准失败");
    }
  } catch (e) {
    toast(e.message || String(e));
  } finally {
    busyPath.value = "";
  }
}

// ---- 驳回 ----
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
  if (!it || !rejectNote.value.trim()) return;
  busyPath.value = it.path;
  try {
    const r = await props.api("/outline/discussion/review", "POST",
                              { path: it.path, action: "reject", note: rejectNote.value.trim() });
    if (r.status === 200 && r.data.ok) {
      toast("已驳回");
      rejectOpen.value = false;
      await load(true);
    } else {
      toast(r.data.error || "驳回失败");
    }
  } catch (e) {
    toast(e.message || String(e));
  } finally {
    busyPath.value = "";
  }
}

onMounted(() => load(true));
defineExpose({ load });
</script>

<style scoped>
.dq-wrap { display: flex; flex-direction: column; gap: 12px; }
.dq-filter { margin-left: auto; font-size: 12px; opacity: .8; display: flex; align-items: center; gap: 4px; }
.dq-stats { display: flex; gap: 8px; margin-top: 8px; }
.dq-list { display: flex; flex-direction: column; gap: 8px; }
.dq-item {
  display: flex; align-items: flex-start; gap: 12px;
  padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px;
  background: var(--card);
}
.dq-item.dq-approved { opacity: .65; }
.dq-item.dq-rejected { opacity: .55; }
.dq-item-main { flex: 1; min-width: 0; }
.dq-item-title { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.dq-path { font-size: 12px; word-break: break-all; }
.dq-item-meta { display: flex; gap: 12px; margin-top: 4px; flex-wrap: wrap; font-size: 12px; }
.dq-note { margin-top: 6px; font-size: 12px; color: var(--warn); }
.dq-item-actions { display: flex; gap: 6px; flex-shrink: 0; flex-wrap: wrap; }
.dq-preview, .dq-preview-sm {
  white-space: pre-wrap; word-break: break-word;
  font-size: 13px; line-height: 1.6;
  max-height: 52vh; overflow-y: auto;
  padding: 12px; border: 1px solid var(--border); border-radius: 6px;
  background: var(--bg, transparent);
}
.dq-preview-sm { max-height: 24vh; font-size: 12px; }
@media (max-width: 720px) {
  .dq-item { flex-direction: column; }
}
</style>
