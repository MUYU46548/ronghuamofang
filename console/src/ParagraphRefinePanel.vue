<script setup>
import { ref, computed, onMounted, onUnmounted } from "vue";

const API = "http://127.0.0.1:8765";

async function api(path, method = "GET", body = null) {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  if (body) opt.body = JSON.stringify(body);
  const r = await fetch(API + path, opt);
  let data = {};
  try { data = await r.json(); } catch (e) { /* empty */ }
  return { status: r.status, data };
}

const chapterN = ref(null);
const paragraphs = ref([]);
const loading = ref(false);
const selectedPara = ref(null);
const feedback = ref("");
const running = ref(false);
const jobResult = ref(null);
const toast = ref("");
const showHistory = ref(false);
const paraHistory = ref([]);
const diffData = ref(null);
const styleDrift = ref(null);
const showDiff = ref(false);
const viewMode = ref("list"); // "list" | "full"
const selectionParaIdx = ref(null);
const multiSelectedParas = ref([]);
const showFloatBtn = ref(false);
const floatBtnPos = ref({ x: 0, y: 0 });
const selectedTextSnippet = ref("");

let toastTimer = null;
let selectionTimer = null;
function say(msg) {
  toast.value = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toast.value = ""), 4500);
}

async function loadParagraphs(n) {
  loading.value = true;
  chapterN.value = n;
  selectedPara.value = null;
  feedback.value = "";
  jobResult.value = null;
  showHistory.value = false;
  diffData.value = null;
  styleDrift.value = null;
  showDiff.value = false;
  try {
    const r = await api(`/chapters/paragraphs?n=${n}`);
    if (r.status === 200 && r.data.ok) {
      paragraphs.value = r.data.paragraphs;
      say(`已加载第 ${n} 章，共 ${r.data.count} 段`);
    } else {
      say(`加载失败: ${r.data.error || r.status}`);
      paragraphs.value = [];
    }
  } catch (e) {
    say(`错误: ${e.message}`);
    paragraphs.value = [];
  } finally {
    loading.value = false;
  }
}

function selectPara(idx) {
  selectedPara.value = idx;
  feedback.value = "";
  jobResult.value = null;
  showHistory.value = false;
  diffData.value = null;
  styleDrift.value = null;
  showDiff.value = false;
}

async function loadHistory() {
  if (selectedPara.value === null) return;
  try {
    const r = await api(`/chapters/paragraph/history?n=${chapterN.value}&p=${selectedPara.value}`);
    if (r.status === 200 && r.data.ok) {
      paraHistory.value = r.data.history || [];
      showHistory.value = true;
      say(`已加载 ${paraHistory.value.length} 条历史`);
    } else {
      say(`无历史记录`);
      paraHistory.value = [];
      showHistory.value = true;
    }
  } catch (e) {
    say(`加载历史失败: ${e.message}`);
  }
}

async function restoreFromHistory(historyId) {
  if (!confirm(`确定回退到 h${historyId}？当前段落将先保存为新历史记录`)) return;
  try {
    const r = await api("/chapters/paragraph/restore", "POST", {
      chapter: chapterN.value,
      paragraph_index: selectedPara.value,
      history_id: historyId,
    });
    if (r.status === 200 && r.data.ok) {
      say(`已回退到 h${historyId}`);
      showHistory.value = false;
      await loadParagraphs(chapterN.value);
    } else {
      say(`回退失败: ${r.data.message || r.data.error}`);
    }
  } catch (e) {
    say(`错误: ${e.message}`);
  }
}

async function loadDiff() {
  if (selectedPara.value === null) return;
  try {
    const r = await api(`/chapters/paragraph/diff?n=${chapterN.value}&p=${selectedPara.value}`);
    if (r.status === 200 && r.data.ok) {
      diffData.value = r.data;
      showDiff.value = true;
    } else {
      say(`无 diff 数据: ${r.data.error}`);
    }
  } catch (e) {
    say(`diff 失败: ${e.message}`);
  }
}

async function runRefine() {
  if (selectedPara.value === null || !feedback.value.trim()) {
    say("请选择段落并填写修改意见");
    return;
  }
  running.value = true;
  jobResult.value = null;
  diffData.value = null;
  styleDrift.value = null;
  try {
    const r = await api("/refine/paragraph", "POST", {
      chapter: chapterN.value,
      paragraph_index: selectedPara.value,
      feedback: feedback.value.trim(),
      dry_run: false,
    });
    if (r.status === 202) {
      say(`已提交: ${r.data.job_id}`);
      await pollJob(r.data.job_id);
    } else if (r.status === 400) {
      say(`参数错误: ${r.data.error}`);
    } else {
      say(`提交失败: ${r.data.error || r.status}`);
    }
  } catch (e) {
    say(`错误: ${e.message}`);
  } finally {
    running.value = false;
  }
}

async function runDryRun() {
  if (selectedPara.value === null || !feedback.value.trim()) {
    say("请选择段落并填写修改意见");
    return;
  }
  running.value = true;
  try {
    const r = await api("/refine/paragraph", "POST", {
      chapter: chapterN.value,
      paragraph_index: selectedPara.value,
      feedback: feedback.value.trim(),
      dry_run: true,
    });
    if (r.status === 202) {
      say(`dry-run: ${r.data.job_id}`);
    } else {
      say(`失败: ${r.data.error || r.status}`);
    }
  } catch (e) {
    say(`错误: ${e.message}`);
  } finally {
    running.value = false;
  }
}

async function checkStyleDrift() {
  if (selectedPara.value === null) return;
  try {
    const r = await api(`/style/drift?mode=chapter&n=${chapterN.value}&p=${selectedPara.value}`);
    if (r.status === 200 && r.data.ok) {
      styleDrift.value = r.data;
    } else {
      say(`风格检测失败: ${r.data.error}`);
    }
  } catch (e) {
    say(`错误: ${e.message}`);
  }
}

async function pollJob(jobId, tries = 150) {
  for (let i = 0; i < tries; i++) {
    await new Promise(r => setTimeout(r, 2000));
    const r = await api("/jobs/" + jobId);
    if (r.status !== 200) { say("查询失败"); return; }
    jobResult.value = r.data;
    if (r.data.state !== "running") {
      if (r.data.state === "ok") {
        say("段落重写完成！");
        await loadParagraphs(chapterN.value);
        await checkStyleDrift();
      } else {
        say(`失败: ${r.data.result || r.data.error || r.data.state}`);
      }
      return;
    }
  }
  say("任务仍在运行");
}

function truncate(text, max = 60) {
  return text.length > max ? text.slice(0, max) + "..." : text;
}

function formatDiffOp(op) {
  if (op.op === "=") return op.text;
  if (op.op === "add") return `<span class="diff-add">[+${escapeHtml(op.text)}+]</span>`;
  if (op.op === "del") return `<span class="diff-del">[-${escapeHtml(op.text)}-]</span>`;
  return "";
}

function escapeHtml(t) {
  return t.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function driftClass(val) {
  if (val > 0.3) return "drift-high";
  if (val > 0.15) return "drift-med";
  return "drift-low";
}

function driftLabel(val) {
  if (val > 0.3) return "⚠ 偏高";
  if (val > 0.15) return "中等";
  return "正常";
}

// ---------- 全文选中模式：自动识别段落索引 ----------
function handleTextSelection(event) {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed) {
    showFloatBtn.value = false;
    return;
  }
  const text = selection.toString().trim();
  if (!text || text.length < 2) {
    showFloatBtn.value = false;
    return;
  }
  // 找到包含选中内容的段落
  let matchedIdx = null;
  for (let i = 0; i < paragraphs.value.length; i++) {
    if (paragraphs.value[i].text.includes(text.slice(0, Math.min(40, text.length)))) {
      matchedIdx = i;
      break;
    }
  }
  if (matchedIdx === null) {
    showFloatBtn.value = false;
    return;
  }
  selectionParaIdx.value = matchedIdx;
  selectedTextSnippet.value = text.length > 50 ? text.slice(0, 50) + "..." : text;

  // 浮动按钮位置：选区的右上角
  const range = selection.getRangeAt(0);
  const rect = range.getBoundingClientRect();
  const containerRect = event.currentTarget.getBoundingClientRect?.() || { left: 0, top: 0 };
  floatBtnPos.value = {
    x: Math.min(rect.right - containerRect.left, containerRect.width - 160),
    y: rect.top - containerRect.top - 40,
  };
  showFloatBtn.value = true;
}

function hideFloatBtnDelayed() {
  clearTimeout(selectionTimer);
  selectionTimer = setTimeout(() => {
    showFloatBtn.value = false;
  }, 1200);
}

function selectViaText(paraIdx) {
  selectPara(paraIdx);
  showFloatBtn.value = false;
  say(`已选中段落 #${paraIdx}（文本选中触发）`);
}

function toggleMultiSelect(idx) {
  const arr = multiSelectedParas.value;
  const pos = arr.indexOf(idx);
  if (pos >= 0) arr.splice(pos, 1);
  else arr.push(idx);
}

function clearMultiSelect() {
  multiSelectedParas.value = [];
}

async function runMultiRefine() {
  if (multiSelectedParas.value.length === 0 || !feedback.value.trim()) {
    say("请先勾选多个段落并填写修改意见");
    return;
  }
  running.value = true;
  try {
    // 批量多段重写：逐个提交
    const idxs = [...multiSelectedParas.value].sort((a, b) => a - b);
    say(`正在批量重写 ${idxs.length} 个段落...`);
    for (const idx of idxs) {
      selectedPara.value = idx;
      const r = await api("/refine/paragraph", "POST", {
        chapter: chapterN.value,
        paragraph_index: idx,
        feedback: feedback.value.trim(),
        dry_run: false,
      });
      if (r.status !== 202) {
        say(`段落 #${idx} 提交失败: ${r.data.error || r.status}`);
        running.value = false;
        return;
      }
      await pollJob(r.data.job_id);
    }
    multiSelectedParas.value = [];
    await loadParagraphs(chapterN.value);
  } finally {
    running.value = false;
  }
}

defineExpose({ loadParagraphs });

onMounted(() => {
  if (chapterN.value) loadParagraphs(chapterN.value);
});
</script>

<template>
  <div class="para-refine">
    <div class="para-head">
      <h3>段落级精修工作台</h3>
      <div class="head-hint">选择段落 → 填写意见 → 重写（不动其他段落，改前自动备份）</div>
    </div>

    <div class="para-controls">
      <label>章节号：</label>
      <input
        type="number"
        min="1"
        :value="chapterN || ''"
        @keydown.enter="loadParagraphs($event.target.valueAsNumber)"
        placeholder="章节号"
        class="para-input"
      />
      <button class="mini" :disabled="loading" @click="loadParagraphs($event.target.previousElementSibling.previousElementSibling.valueAsNumber)">
        {{ loading ? "加载中..." : "加载段落" }}
      </button>
      <span class="spacer"></span>
      <button
        class="mini"
        :class="{ primary: viewMode === 'list' }"
        @click="viewMode = 'list'"
        title="列表视图"
      >列表</button>
      <button
        class="mini"
        :class="{ primary: viewMode === 'full' }"
        @click="viewMode = 'full'"
        title="全文视图——选中文本自动识别段落"
      >全文</button>
    </div>

    <div v-if="!loading && paragraphs.length === 0" class="empty">
      输入章节号并点击"加载段落"开始
    </div>

    <div v-if="paragraphs.length > 0" class="para-body">
      <!-- 全文视图：选中文本自动识别段落 -->
      <div v-if="viewMode === 'full'" class="para-fulltext-wrap">
        <div class="para-fulltext-head">
          <span>第 {{ chapterN }} 章 · 全文视图</span>
          <span class="head-hint">选中文本 → 自动识别段落 → 点击浮动按钮选中</span>
          <div class="batch-bar" v-if="multiSelectedParas.length > 0">
            <span class="batch-count">已选 {{ multiSelectedParas.length }} 段</span>
            <button class="mini" @click="clearMultiSelect">清空</button>
            <button class="mini primary" :disabled="running || !feedback.trim()" @click="runMultiRefine">
              批量重写
            </button>
          </div>
        </div>
        <div
          class="para-fulltext"
          @mouseup="handleTextSelection($event)"
          @mouseleave="hideFloatBtnDelayed"
        >
          <p
            v-for="p in paragraphs"
            :key="p.index"
            class="full-para"
            :class="{
              selected: selectedPara === p.index,
              'multi-selected': multiSelectedParas.includes(p.index),
            }"
          >
            <input
              type="checkbox"
              class="para-check"
              :checked="multiSelectedParas.includes(p.index)"
              @change="toggleMultiSelect(p.index)"
              title="勾选以批量重写"
            />
            <span class="para-index-tag">#{{ p.index }}</span>
            <span class="para-content">{{ p.text }}</span>
          </p>

          <!-- 浮动按钮：选中文本时出现 -->
          <button
            v-if="showFloatBtn"
            class="float-select-btn"
            :style="{ left: floatBtnPos.x + 'px', top: floatBtnPos.y + 'px' }"
            @click="selectViaText(selectionParaIdx)"
            @mouseleave="hideFloatBtnDelayed"
          >
            ✏ 精修选中文段
            <span class="float-snippet" v-if="selectedTextSnippet">「{{ selectedTextSnippet }}」</span>
          </button>
        </div>
      </div>

      <!-- 列表视图 -->
      <div v-else class="para-list">
        <div class="para-list-head">
          <span>第 {{ chapterN }} 章 · {{ paragraphs.length }} 段</span>
        </div>
        <div
          v-for="p in paragraphs"
          :key="p.index"
          class="para-item"
          :class="{ selected: selectedPara === p.index }"
          @click="selectPara(p.index)"
        >
          <span class="para-index">#{{ p.index }}</span>
          <span class="para-text-preview">{{ truncate(p.text, 80) }}</span>
          <span class="para-chars">{{ p.chars }} 字</span>
        </div>
      </div>

      <div class="para-editor">
        <div v-if="selectedPara === null" class="editor-empty">
          点击左侧段落选择要重写的段落
        </div>
        <div v-else class="editor-content">
          <div class="editor-section">
            <div class="editor-section-head">
              <span>目标段落 #{{ selectedPara }}（{{ paragraphs[selectedPara]?.chars }} 字）</span>
              <div class="section-actions">
                <button class="mini" @click="loadDiff">查看 diff</button>
                <button class="mini" @click="loadHistory">历史</button>
                <button class="mini" @click="checkStyleDrift">风格</button>
              </div>
            </div>
            <div class="editor-para-text">{{ paragraphs[selectedPara]?.text }}</div>
          </div>

          <div class="editor-section">
            <label class="editor-label">修改意见</label>
            <textarea
              v-model="feedback"
              class="editor-textarea"
              placeholder="如：把这段改成白描风格，减少心理描写，用动作替代内心独白"
              rows="3"
            ></textarea>
          </div>

          <div class="editor-actions">
            <button class="mini primary" :disabled="running || !feedback.trim()" @click="runRefine">
              {{ running ? "执行中..." : "执行重写" }}
            </button>
            <button class="mini" :disabled="running || !feedback.trim()" @click="runDryRun">
              dry-run（预览）
            </button>
          </div>

          <div v-if="styleDrift" :class="['drift-alert', driftClass(styleDrift.paragraph?.drift?.overall_drift || 0)]">
            <div class="drift-head">
              风格偏差：{{ (styleDrift.paragraph?.drift?.overall_drift || 0).toFixed(3) }}
              {{ driftLabel(styleDrift.paragraph?.drift?.overall_drift || 0) }}
              <span v-if="styleDrift.paragraph?.drift?.is_significant" class="drift-warn">⚠ 建议检查</span>
            </div>
            <div v-if="styleDrift.paragraph?.drift?.high_drift_dimensions?.length" class="drift-dims">
              高偏差：{{ styleDrift.paragraph.drift.high_drift_dimensions.join(', ') }}
            </div>
          </div>

          <div v-if="showDiff && diffData" class="diff-panel">
            <div class="diff-head">
              <span>段落 diff</span>
              <span class="diff-summary" v-if="diffData.summary">
                变化率 {{ (diffData.summary.change_ratio * 100).toFixed(1) }}%
                <span v-if="diffData.summary.is_major_change" class="diff-major">⚠ 重大变化</span>
              </span>
              <button class="mini" @click="showDiff = false">关闭</button>
            </div>
            <div class="diff-content" v-html="diffData.diff?.map(formatDiffOp).join('')"></div>
          </div>

          <div v-if="showHistory" class="history-panel">
            <div class="history-head">
              <span>改写历史 ({{ paraHistory.length }})</span>
              <button class="mini" @click="showHistory = false">关闭</button>
            </div>
            <div v-if="paraHistory.length === 0" class="history-empty">暂无历史</div>
            <div v-for="h in paraHistory" :key="h.id" class="history-item">
              <div class="history-meta">
                <span class="history-id">h{{ h.id }}</span>
                <span class="history-time">{{ h.timestamp }}</span>
                <span class="history-chars">{{ h.original_chars }}->{{ h.rewritten_chars }} 字</span>
                <button class="mini" @click="restoreFromHistory(h.id)">回退到此</button>
              </div>
              <div class="history-original">原：{{ truncate(h.original, 80) }}</div>
              <div class="history-rewritten">改：{{ truncate(h.rewritten, 80) }}</div>
              <div class="history-fb">意见：{{ h.feedback }}</div>
            </div>
          </div>

          <div v-if="jobResult" class="job-result" :class="jobResult.state">
            <span v-if="jobResult.state === 'ok'">完成</span>
            <span v-else-if="jobResult.state === 'running'">运行中</span>
            <span v-else>{{ jobResult.result || jobResult.error || jobResult.state }}</span>
          </div>
        </div>
      </div>
    </div>

    <div v-if="toast" class="toast">{{ toast }}</div>
  </div>
</template>

<style scoped>
.para-refine { position: relative; }
.para-head { margin-bottom: 16px; }
.para-head h3 { margin: 0 0 4px; font-size: 16px; }
.head-hint { font-size: 12px; color: var(--muted); }
.para-controls { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.para-input { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; width: 100px; }
.mini { padding: 6px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); cursor: pointer; font-size: 13px; color: var(--ink); }
.mini:hover { background: var(--accent-soft); color: #fff; }
.mini.primary { background: var(--accent); color: #fff; }
.mini:disabled { opacity: 0.5; cursor: not-allowed; }
.mini.danger { background: var(--bad); color: #fff; }
.spacer { flex: 1; }
.para-body { display: flex; gap: 12px; min-height: 450px; }

/* --- 全文视图 --- */
.para-fulltext-wrap { flex: 1; display: flex; flex-direction: column; gap: 8px; }
.para-fulltext-head { display: flex; align-items: center; gap: 10px; font-size: 13px; font-weight: 600; color: var(--ink); }
.para-fulltext-head .head-hint { font-size: 11px; font-weight: normal; color: var(--muted); }
.batch-bar { display: flex; align-items: center; gap: 6px; margin-left: auto; font-size: 12px; }
.batch-count { color: var(--accent); font-weight: 600; }
.para-fulltext { position: relative; border: 1px solid var(--border); border-radius: 8px; padding: 12px; background: var(--bg); max-height: 500px; overflow-y: auto; font-size: 13px; line-height: 1.8; user-select: text; }
.full-para { margin: 0 0 10px; padding: 6px 8px; border-radius: 6px; display: flex; align-items: flex-start; gap: 6px; border: 1px solid transparent; transition: background 0.15s, border-color 0.15s; }
.full-para:hover { background: rgba(155, 143, 196, 0.04); border-color: rgba(155, 143, 196, 0.15); }
.full-para.selected { background: rgba(155, 143, 196, 0.12); border-color: var(--accent); }
.full-para.multi-selected { background: rgba(127, 196, 127, 0.1); border-color: var(--ok); }
.para-check { margin-top: 3px; cursor: pointer; flex: 0 0 auto; }
.para-index-tag { font-size: 11px; font-weight: 700; color: var(--accent); flex: 0 0 28px; }
.para-content { flex: 1; white-space: pre-wrap; }

.float-select-btn {
  position: absolute;
  z-index: 20;
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 6px 12px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 2px 12px rgba(0,0,0,0.2);
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-width: 200px;
  animation: fadeIn 0.15s;
}
.float-select-btn:hover { background: var(--accent-soft); }
.float-snippet { font-size: 10px; font-weight: normal; opacity: 0.9; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 180px; }
.para-list { flex: 0 0 320px; border: 1px solid var(--border); border-radius: 8px; overflow-y: auto; max-height: 500px; }
.para-list-head { padding: 8px 12px; border-bottom: 1px solid var(--border); font-size: 12px; color: var(--muted); position: sticky; top: 0; background: var(--card); z-index: 1; }
.para-item { display: flex; align-items: center; gap: 6px; padding: 6px 10px; cursor: pointer; border-bottom: 1px solid rgba(127,127,127,0.08); font-size: 12px; }
.para-item:hover { background: rgba(155, 143, 196, 0.06); }
.para-item.selected { background: rgba(155, 143, 196, 0.12); border-left: 3px solid var(--accent); }
.para-index { font-weight: 700; color: var(--accent); flex: 0 0 28px; }
.para-text-preview { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink); }
.para-chars { font-size: 11px; color: var(--muted); flex: 0 0 40px; text-align: right; }
.para-editor { flex: 1; border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; background: var(--card); overflow-y: auto; max-height: 600px; }
.editor-empty { color: var(--muted); font-size: 13px; padding: 40px 0; text-align: center; }
.editor-section { margin-bottom: 14px; }
.editor-section-head { display: flex; align-items: center; margin-bottom: 6px; font-size: 12px; font-weight: 600; color: var(--accent); }
.section-actions { display: flex; gap: 6px; margin-left: auto; }
.editor-para-text { font-size: 13px; line-height: 1.7; padding: 10px 12px; background: var(--bg); border-radius: 6px; border: 1px solid var(--border); white-space: pre-wrap; max-height: 120px; overflow-y: auto; }
.editor-label { display: block; font-size: 12px; font-weight: 500; color: var(--muted); margin-bottom: 6px; }
.editor-textarea { width: 100%; padding: 8px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; background: var(--bg); color: var(--ink); resize: vertical; }
.editor-textarea:focus { outline: none; border-color: var(--accent); }
.editor-actions { display: flex; gap: 8px; margin-bottom: 10px; }
.diff-panel { border: 1px solid var(--border); border-radius: 6px; padding: 10px 12px; margin-bottom: 10px; background: var(--bg); }
.diff-head { display: flex; align-items: center; gap: 8px; font-size: 12px; font-weight: 600; margin-bottom: 6px; }
.diff-summary { color: var(--muted); font-weight: normal; }
.diff-major { color: var(--bad); font-weight: 600; }
.diff-content { font-size: 13px; line-height: 1.8; word-break: break-all; white-space: pre-wrap; }
.diff-add { background: rgba(127, 196, 127, 0.2); color: var(--ok); padding: 0 2px; }
.diff-del { background: rgba(196, 127, 127, 0.2); color: var(--bad); text-decoration: line-through; padding: 0 2px; }
.history-panel { border: 1px solid var(--border); border-radius: 6px; padding: 10px 12px; margin-bottom: 10px; background: var(--bg); }
.history-head { display: flex; align-items: center; gap: 8px; font-size: 12px; font-weight: 600; margin-bottom: 8px; }
.history-empty { font-size: 12px; color: var(--muted); text-align: center; padding: 10px; }
.history-item { padding: 8px; border: 1px solid var(--border); border-radius: 6px; margin-bottom: 6px; background: var(--card); }
.history-meta { display: flex; align-items: center; gap: 8px; font-size: 11px; margin-bottom: 4px; }
.history-id { font-weight: 700; color: var(--accent); }
.history-time { color: var(--muted); }
.history-chars { color: var(--muted); margin-left: auto; }
.history-original { font-size: 12px; color: var(--muted); }
.history-rewritten { font-size: 12px; color: var(--ink); }
.history-fb { font-size: 11px; color: var(--muted); margin-top: 2px; }
.drift-alert { padding: 8px 12px; border-radius: 6px; margin-bottom: 10px; font-size: 12px; }
.drift-low { background: rgba(127, 196, 127, 0.08); }
.drift-med { background: rgba(201, 162, 106, 0.1); }
.drift-high { background: rgba(196, 127, 127, 0.1); border: 1px solid rgba(196, 127, 127, 0.3); }
.drift-head { font-weight: 600; }
.drift-warn { margin-left: 8px; color: var(--bad); }
.drift-dims { color: var(--muted); margin-top: 2px; }
.job-result { padding: 8px 12px; border-radius: 6px; font-size: 13px; }
.job-result.ok { background: rgba(127, 196, 127, 0.1); color: var(--ok); }
.job-result.running { background: rgba(155, 143, 196, 0.1); color: var(--accent); }
.job-result.failed, .job-result.error { background: rgba(196, 127, 127, 0.1); color: var(--bad); }
.toast { position: fixed; bottom: 20px; right: 20px; background: var(--ink); color: #fff; padding: 10px 16px; border-radius: 10px; font-size: 13px; z-index: 100; }
</style>
