<script setup>
import { ref, computed, onMounted, onUnmounted } from "vue";

import { API } from "./apiBase.js";
// 写请求必须走 guiApi（2026-11-11）：`agent_mode: true` 时写端点要双因子
// （来源头 + 主进程签发的 token）。本文件原来的 api() 只带 Content-Type，
// 于是"批准"在真实 GUI 里也会 403 —— 正是用户最恨的"点了没用"。
import { postJSON } from "./guiApi.js";

const emit = defineEmits(['goto-refine']);
// 自任务引擎（engine: self）下“运行审查”必然失败 —— 父组件把引擎态传进来，
// 按钮直接禁用并说清原因，别让作者点一次才被告知用不了（0.9.1 验收第 13 条）。
const props = defineProps({ selfEngine: { type: Boolean, default: false } });

async function api(path, method = "GET", body = null) {
  const opt = { method, headers: { "Content-Type": "application/json" } };
  if (body) opt.body = JSON.stringify(body);
  const r = await fetch(API + path, opt);
  let data = {};
  try { data = await r.json(); } catch (e) { /* empty */ }
  return { status: r.status, data };
}

// ---------- 状态 ----------
const report = ref(null);
const loading = ref(false);
const saving = ref(false);
const running = ref(false);
const savedCount = ref(0); // 已落盘到 decisions.json 的决策数（仅展示）
const revision = ref(0); // 决策改动计数
const savedRevision = ref(0); // 最近一次成功落盘时的计数
const batchProgress = ref(null); // { current, total, index, status, message }
let progressTimer = null;
const toast = ref("");
const decisions = ref({}); // { "chapter_finding_id": { action, feedback } }
const expandedChapters = ref(new Set());
const commenting = ref({}); // { "chapter_finding_id": true }
const commentText = ref({}); // { "chapter_finding_id": "text" }

let toastTimer = null;
function say(msg) {
  toast.value = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toast.value = ""), 4500);
}

function toggleComment(chapterNo, findingId) {
  const key = `${chapterNo}_${findingId}`;
  commenting.value[key] = !commenting.value[key];
  if (commenting.value[key]) {
    commentText.value[key] = "";
  }
}

function goToRefine(chapterNo) {
  emit('goto-refine', chapterNo);
}

async function submitComment(chapterNo, findingId) {
  const key = `${chapterNo}_${findingId}`;
  const text = (commentText.value[key] || "").trim();
  if (!text) return;
  const r = await api("/review/comment", "POST", {
    chapter: chapterNo,
    finding_id: findingId,
    comment: text,
  });
  if (r.status === 200 && r.data.ok) {
    say("评论已添加");
    commenting.value[key] = false;
    commentText.value[key] = "";
    await loadReport();
  } else {
    say("评论失败: " + (r.data.error || r.status));
  }
}

// ---------- 三份稿对照（零 token，只读盘） ----------
// 为什么放在审稿页：原稿 / 检查稿 / 润色稿的差额，就是“检查与润色认为该删什么”的实证清单。
// 六本书的经验全在这三份稿里，不给入口等于继续烂在硬盘上。
const ts = ref(null);        // GET /review/three_state 的返回
const tsOpen = ref(false);   // 默认收起：不点就不扫盘
const tsBusy = ref(false);
const tsErr = ref("");

const TS_CLUE = {
  dialogue: "对白",
  long_narration: "长旁白",
  near_duplicate: "与上一段接近重复",
  unclassified: "未分类",
};
const TS_STEP = { "raw→checked": "原稿 → 检查稿", "checked→refined": "检查稿 → 润色稿" };
// 步骤顺序按流程走（与 scripts/three_state_diff.py 的 STEPS 同源，别按字母序）。
const TS_ORDER = ["raw→checked", "checked→refined"];

function clueLabel(c) {
  return TS_CLUE[c] || c;
}

const tsRows = computed(() => {
  const s = ts.value && ts.value.summary;
  if (!s) return [];
  const lc = s.layer_chars || {};
  const pairs = s.pairs || {};
  const order = TS_ORDER.filter((k) => k in pairs)
    .concat(Object.keys(pairs).filter((k) => !TS_ORDER.includes(k)));
  return order.map((step) => {
    const parts = step.split("→");
    return {
      step,
      label: TS_STEP[step] || step,
      count: pairs[step] || 0,
      chars: (s.removed_chars || {})[step] || 0,
      net: (lc[parts[1]] || 0) - (lc[parts[0]] || 0),
    };
  });
});

async function loadThreeState() {
  tsBusy.value = true;
  tsErr.value = "";
  try {
    const r = await api("/review/three_state");
    if (r.status === 200 && r.data && r.data.ok) ts.value = r.data;
    else tsErr.value = "读不到对照：" + ((r.data && r.data.error) || ("HTTP " + r.status));
  } catch (e) {
    tsErr.value = "读不到对照（出错）：" + String(e.message || e);
  } finally {
    tsBusy.value = false;
  }
}

function toggleThreeState() {
  tsOpen.value = !tsOpen.value;
  if (tsOpen.value && !ts.value) loadThreeState();
}

// ---------- 读不通的句子（零 token，只读盘） ----------
// 与“AI 腔”分开：腔调是像不像 AI，读不通是一句也没读懂。这里只报结构缺件。
const ur = ref(null);
const urOpen = ref(false);
const urBusy = ref(false);
const urErr = ref("");
const UR_LABEL = { fragment_sentence: "碎句", dangling_connector: "半句话" };

async function loadUnreadable() {
  urBusy.value = true;
  urErr.value = "";
  try {
    const r = await api("/review/unreadable");
    if (r.status === 200 && r.data && r.data.ok) ur.value = r.data;
    else urErr.value = "扫不了：" + ((r.data && r.data.error) || ("HTTP " + r.status));
  } catch (e) {
    urErr.value = "扫不了（出错）：" + String(e.message || e);
  } finally {
    urBusy.value = false;
  }
}

function toggleUnreadable() {
  urOpen.value = !urOpen.value;
  if (urOpen.value && !ur.value) loadUnreadable();
}

// ---------- 章职能核查（零 token，只读盘） ----------
// 七维管对错、审美管好看，中间这层一直空着：这一章有没有兑现章大纲写的事。
const cf = ref(null);
const cfOpen = ref(false);
const cfBusy = ref(false);
const cfErr = ref("");

async function loadChapterFunction() {
  cfBusy.value = true;
  cfErr.value = "";
  try {
    const r = await api("/review/chapter_function");
    if (r.status === 200 && r.data && r.data.ok) cf.value = r.data;
    else cfErr.value = "核查失败：" + ((r.data && r.data.error) || ("HTTP " + r.status));
  } catch (e) {
    cfErr.value = "核查失败（出错）：" + String(e.message || e);
  } finally {
    cfBusy.value = false;
  }
}

function toggleChapterFunction() {
  cfOpen.value = !cfOpen.value;
  if (cfOpen.value && !cf.value) loadChapterFunction();
}

// ---------- 计算属性 ----------
const chapters = computed(() => report.value?.chapters || []);
const totalFindings = computed(() => {
  return chapters.value.reduce((sum, c) => sum + (c.findings?.length || 0), 0);
});
const acceptedCount = computed(() => {
  return Object.values(decisions.value).filter(d => d.action === "accept").length;
});
const ignoredCount = computed(() => {
  return Object.values(decisions.value).filter(d => d.action === "ignore").length;
});
const pendingCount = computed(() => {
  return totalFindings.value - acceptedCount.value - ignoredCount.value;
});
const hasDecision = computed(() => acceptedCount.value > 0);
// 决策有改动未落盘 → 运行批量精修前必须先 POST /review/decisions
const decisionsDirty = computed(() => revision.value !== savedRevision.value);
const progressPct = computed(() => {
  const p = batchProgress.value;
  if (!p || !p.total) return 0;
  return Math.min(100, Math.round(((p.index || 0) / p.total) * 100));
});

// ---------- 方法 ----------
function toggleChapter(n) {
  const s = expandedChapters.value;
  if (s.has(n)) s.delete(n);
  else s.add(n);
}

function setDecision(chapterNo, findingId, action, feedback = "") {
  const uniqueKey = `${chapterNo}_${findingId}`;
  if (action === "ignore") {
    decisions.value[uniqueKey] = { action: "ignore", feedback: "" };
  } else {
    decisions.value[uniqueKey] = { action: "accept", feedback };
  }
  revision.value++;
}

function acceptAll() {
  for (const c of chapters.value) {
    for (const f of (c.findings || [])) {
      decisions.value[`${c.n}_${f.id}`] = { action: "accept", feedback: "" };
    }
  }
  revision.value++;
  say("已接受全部 " + totalFindings.value + " 条");
}

function ignoreAll() {
  for (const c of chapters.value) {
    for (const f of (c.findings || [])) {
      decisions.value[`${c.n}_${f.id}`] = { action: "ignore", feedback: "" };
    }
  }
  revision.value++;
  say("已忽略全部");
}

function resetDecisions() {
  decisions.value = {};
  revision.value++;
  say("已重置（记得保存决策）");
}

async function runReview() {
  loading.value = true;
  try {
    const r = await api("/review/run", "POST", { stream: false });
    if (r.status === 202) {
      say("审查任务已提交: " + r.data.job_id);
      await pollJob(r.data.job_id);
    } else if (r.status === 409) {
      say("已有任务在运行: " + (r.data.error || "请稍候"));
    } else {
      say("提交失败: " + (r.data.error || r.status));
    }
  } catch (e) {
    say("错误: " + e.message);
  } finally {
    loading.value = false;
  }
}

async function pollJob(jobId, tries = 300) {
  for (let i = 0; i < tries; i++) {
    await new Promise(r => setTimeout(r, 2000));
    const r = await api("/jobs/" + jobId);
    if (r.status !== 200) {
      say("任务状态查询失败: " + (r.data.error || r.status));
      return;
    }
    if (r.data.state !== "running") {
      if (r.data.state === "ok") {
        say("任务完成");
        await loadReport();
      } else {
        say("任务失败: " + (r.data.result || r.data.error || r.data.state || "未知错误"));
      }
      return;
    }
  }
  say("任务仍在运行（轮询超时，可点“刷新报告”查看）");
}

async function loadReport() {
  const r = await api("/review/report");
  if (r.status === 200 && r.data.chapters) {
    report.value = r.data;
    // 初始化决策 - 用 "章节号_findingID" 作为全局唯一 key
    for (const c of r.data.chapters) {
      for (const f of (c.findings || [])) {
        const uniqueKey = `${c.n}_${f.id}`;
        if (!decisions.value[uniqueKey]) {
          decisions.value[uniqueKey] = { action: "pending", feedback: "" };
        }
      }
    }
    // 回填已保存的决策数，避免"已有决策却提示未保存"
    await loadSavedDecisions();
    // 本会话基线：刚载入的报告视为"无需保存"，任何后续决策改动都会把 dirty 置为 true
    revision.value = 0;
    savedRevision.value = 0;
  } else if (r.status === 404) {
    report.value = null;
    say("暂无审查报告 —— 点击“运行审查”开始分析章节");
  } else {
    say("审查报告读取失败: " + (r.data.error || r.status));
  }
}

async function loadSavedDecisions() {
  const r = await api("/review/decisions");
  if (r.status === 200 && Array.isArray(r.data.decisions)) {
    // 回填到界面（问题总账第 7 条）：以前只数了个条数 —— 重开报告时已保存的
    // accept/ignore 全被当成 pending，用户看不出自己上次勾了什么；再点保存时
    // 这些决策又因为“只提交非 pending”被悄悄丢掉（界面上写着“下次打开还在”，
    // 实际勾选状态是假的）。现在以盘上 `review_report.decisions.json` 为准回填。
    let n = 0;
    for (const d of r.data.decisions) {
      if (!d || !d.finding_id) continue;
      if (d.action !== "accept" && d.action !== "ignore") continue;
      n++;
      decisions.value[`${d.chapter}_${d.finding_id}`] = {
        action: d.action, feedback: d.feedback || "" };
    }
    savedCount.value = n;
    savedRevision.value = revision.value;
  }
}

// 保存决策：返回是否成功（runBatchRefine 依赖它做“先保存再精修”）
async function saveDecisions(silent = false) {
  saving.value = true;
  try {
    const payload = {
      decisions: Object.entries(decisions.value)
        .filter(([_, d]) => d.action !== "pending")
        .map(([uniqueKey, d]) => {
          // "chapter_finding_id" → { finding_id, chapter }
          const underscoreIdx = uniqueKey.indexOf("_");
          const chapter = parseInt(uniqueKey.slice(0, underscoreIdx), 10);
          const findingId = uniqueKey.slice(underscoreIdx + 1);
          return {
            finding_id: findingId,
            chapter,
            action: d.action,
            feedback: d.feedback || "",
          };
        }),
    };
    const r = await api("/review/decisions", "POST", payload);
    if (r.status === 200) {
      savedCount.value = payload.decisions.length;
      savedRevision.value = revision.value;
      // 保存后按盘上重读（第 7 条）：勾选状态以 decisions.json 为准，
      // 不拿“我提交了什么”当“盘上是什么”。
      await loadSavedDecisions();
      if (r.data.warning) say(r.data.warning);
      else if (!silent) say("决策已保存（" + (r.data.count ?? payload.decisions.length) + " 条），可运行批量精修");
      return true;
    }
    say("保存失败: " + (r.data.error || r.status));
    return false;
  } catch (e) {
    say("错误: " + e.message);
    return false;
  } finally {
    saving.value = false;
  }
}

async function runBatchRefine() {
  if (!report.value) { say("暂无审查报告，先运行审查"); return; }
  if (!hasDecision.value) { say("请先“接受”至少一条审查发现"); return; }
  running.value = true;
  batchProgress.value = null;
  try {
    // 1) 决策必须先落盘：batch_refine 从 review_report.decisions.json 读取，不读 UI 内存
    if (decisionsDirty.value) {
      const ok = await saveDecisions(true);
      if (!ok) { say("决策保存失败，已中止批量精修"); return; }
      say("决策已保存，正在启动批量精修…");
    }
    // 2) 再启动批量精修（后端会校验报告/决策文件是否存在）
    const r = await api("/batch_refine/run", "POST", { decisions_from_file: true });
    if (r.status === 202) {
      say("批量精修已提交: " + r.data.job_id);
      pollBatchProgress();
      await pollJob(r.data.job_id);
    } else if (r.status === 409) {
      say("已有任务在运行: " + (r.data.error || "请稍候"));
    } else {
      say("提交失败: " + (r.data.error || r.status));
    }
  } catch (e) {
    say("错误: " + e.message);
  } finally {
    running.value = false;
  }
}

async function pollBatchProgress() {
  if (progressTimer) clearInterval(progressTimer);
  progressTimer = setInterval(async () => {
    const r = await api("/batch_refine/progress");
    if (r.status !== 200) {
      clearInterval(progressTimer);
      progressTimer = null;
      return;
    }
    batchProgress.value = r.data;
    if (r.data.status === "done" || r.data.status === "failed" || r.data.status === "idle") {
      clearInterval(progressTimer);
      progressTimer = null;
    }
  }, 2000);
}

// ---------- 工具 ----------
function sevClass(severity) {
  return { error: "sev-error", warn: "sev-warn", info: "sev-info" }[severity] || "sev-info";
}
function sevIcon(severity) {
  return { error: "🔴", warn: "🟡", info: "🔵" }[severity] || "⚪";
}
function typeLabel(type) {
  return {
    outline_gap: "大纲缺漏",
    character_inconsistency: "角色不一致",
    transition_issue: "衔接问题",
    style_violation: "文风违规",
    other: "其他",
  }[type] || type;
}

onMounted(() => {
  loadReport();
});

onUnmounted(() => {
  if (progressTimer) { clearInterval(progressTimer); progressTimer = null; }
  clearTimeout(toastTimer);
});
/* 产物待批（2026-11-11，产物闸）：执行方（agent / 模型）写的是**候选**，
   不是成品。作者在这里点批 —— 为什么放在审稿页签：这一页就是"做决定"的地方
   （对应"界面决定、对话干活"）。批准端点仅 GUI，agent 调用会被 403。 */
const products = ref([]);
const prodMsg = ref("");
const prodBusy = ref("");
const prodConfirm = ref("");     // 两步确认：第一次点只问，第二次才动手
/* 批准前先看一眼（2026-11-12）：看不到内容就点"批准"，只能靠猜 —— 那不叫作主。
   只读候选区，分阶段给出"比原稿改了几段"（阶段 5 最有用的一个数）。 */
const prodView = ref({});
const prodViewBusy = ref("");

function prodKey(it) { return it.stage + "|" + it.rel; }

function stateLabel(s) {
  return ({ pending: "等批准", stale: "改过，需重批",
            approved: "已批准", rejected: "已驳回" })[s] || s;
}

async function loadProducts(silent = false) {
  try {
    const r = await api("/review/products/pending");
    if (r.status === 200 && r.data && r.data.ok) {
      products.value = r.data.items || [];
      if (!silent) prodMsg.value = "";
    } else {
      prodMsg.value = "读不到待批的版本：" + ((r.data && r.data.error) || r.status);
    }
  } catch (e) {
    prodMsg.value = "读不到待批的版本（出错）：" + e.message;
  }
}

async function decideProduct(it, action) {
  const key = prodKey(it) + "|" + action;
  if (prodConfirm.value !== key) {
    prodConfirm.value = key;
    prodMsg.value = action === "approve"
      ? "再点一次就批准这一版；下次运行时会换进你的稿子"
      : "再点一次就驳回这一版（你的稿子不动，这份还留着，可改完再批）";
    return;
  }
  prodConfirm.value = "";
  prodBusy.value = prodKey(it);
  try {
    const d = await postJSON("/review/products/decide",
                             { stage: it.stage, rel: it.rel, action });
    prodMsg.value = d.message || "已记下";
    await loadProducts(true);      // 静默刷新：别把刚收到的提示一句话就抹掉
  } catch (e) {
    prodMsg.value = "没生效：" + e.message;
  } finally {
    prodBusy.value = "";
  }
}

async function togglePreview(it) {
  const key = prodKey(it);
  if (prodView.value[key]) {
    const next = { ...prodView.value };
    delete next[key];
    prodView.value = next;
    return;
  }
  prodViewBusy.value = key;
  try {
    const r = await api("/review/products/content?stage=" + it.stage +
                        "&rel=" + encodeURIComponent(it.rel));
    if (r.status === 200 && r.data && r.data.ok) {
      prodView.value = { ...prodView.value, [key]: r.data };
    } else {
      prodMsg.value = "看不到这份内容：" + ((r.data && r.data.error) || r.status);
    }
  } catch (e) {
    prodMsg.value = "看不到这份内容（出错）：" + e.message;
  } finally {
    prodViewBusy.value = "";
  }
}

function viewMeta(v) {
  const bits = [];
  if (typeof v.chars === "number") bits.push(v.chars + " 字");
  if (typeof v.changed_paragraphs === "number") bits.push("比原稿改了 " + v.changed_paragraphs + " 段");
  if (v.truncated) bits.push("只显示前 " + (v.text || "").length + " 字");
  return bits.join(" · ");
}

onMounted(() => { loadProducts(); });
</script>

<template>
  <div class="review-console">
    <!-- 头部 -->
    <div class="review-head">
      <h3>审稿 → 修稿（一路走完）</h3>
      <div class="head-actions">
        <button class="mini" :disabled="loading || props.selfEngine"
                :title="props.selfEngine ? '当前引擎是自任务（engine: self，不调模型），审查要用模型。切到 direct / hermes 再用，或在外部执行方里跑。' : ''"
                @click="runReview">
          {{ loading ? "审查中…" : "运行审查" }}
        </button>
        <button class="mini" @click="loadReport">刷新报告</button>
      </div>
    </div>

    <!-- 产物待批（2026-11-11 产物闸）：执行方写候选，作者在这里点批。
         放在"无报告提示"之前，因为它跟有没有审查报告无关 —— 只要有候选就该看得见。 -->
    <section class="card product-gate">
      <h4>
        等你点头的版本
        <span v-if="products.length" class="meta">（{{ products.length }}）</span>
      </h4>
      <div class="meta prod-note">
        执行方（agent 或模型）写出来的东西先放在一边，<b>还没进你的稿子</b>。
        你点“批准”之后，下次运行才会把它换进稿子；点“驳回”你的稿子一个字都不动，
        那份东西还留着，可以改完再批。
      </div>
      <div v-if="prodMsg" class="meta prod-msg">{{ prodMsg }}</div>
      <div v-if="!products.length" class="meta">没有等你点头的版本。</div>
      <div v-for="it in products" :key="prodKey(it)" class="prod-item">
        <div class="prod-row">
          <span class="pill">{{ it.label || ("阶段 " + it.stage) }}</span>
          <code class="prod-path">{{ it.rel }}</code>
          <span class="prod-state" :class="'prod-st-' + it.state">{{ stateLabel(it.state) }}</span>
          <span class="spacer"></span>
          <button class="mini" :disabled="prodViewBusy === prodKey(it)"
                  @click="togglePreview(it)">
            {{ prodView[prodKey(it)] ? "收起" : (prodViewBusy === prodKey(it) ? "读取中…" : "看一眼") }}
          </button>
          <button class="mini primary"
                  :disabled="prodBusy === prodKey(it)"
                  :class="{ danger: prodConfirm === prodKey(it) + '|approve' }"
                  :title="it.why || ''"
                  @click="decideProduct(it, 'approve')">
            {{ prodConfirm === prodKey(it) + "|approve" ? "确认批准？" : "批准" }}
          </button>
          <button class="mini" :disabled="prodBusy === prodKey(it)"
                  @click="decideProduct(it, 'reject')">驳回</button>
        </div>
        <div v-if="prodView[prodKey(it)]" class="prod-view">
          <div class="meta">{{ viewMeta(prodView[prodKey(it)]) }}</div>
          <pre>{{ prodView[prodKey(it)].text }}</pre>
        </div>
      </div>
    </section>

    <!-- 三份稿对照（2026-11-14）：零 token，只读盘。跟有没有审查报告无关，所以放在报告判断之前。 -->
    <section class="card ts-card">
      <div class="ts-head">
        <h4>三份稿对照<span class="meta">（原稿 → 检查稿 → 润色稿）</span></h4>
        <span class="spacer"></span>
        <button class="mini" :disabled="tsBusy" @click="toggleThreeState">
          {{ tsOpen ? (tsBusy ? "对照中…" : "收起") : "看哪一段被删" }}
        </button>
        <button v-if="tsOpen" class="mini" :disabled="tsBusy" @click="loadThreeState">重算</button>
      </div>
      <div class="meta ts-note">
        把盘上三份稿逐段对齐，说清每一段是在检查那一步没的、还是润色那一步没的。
        只列事实（对白 / 长旁白 / 与上一段接近重复 / 未分类），<b>不判定这段是废话</b>——该不该删由你判断。
      </div>
      <div v-if="tsErr" class="meta ts-err">{{ tsErr }}</div>
      <template v-if="tsOpen && ts && ts.exists">
        <table class="det-table">
          <thead>
            <tr><th>步骤</th><th>被删段落</th><th>被删字数</th><th>净变化</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in tsRows" :key="row.step">
              <td>{{ row.label }}</td>
              <td>{{ row.count }} 段</td>
              <td>{{ row.chars }} 字</td>
              <td :class="{ 'cell-bad': row.net < 0 }">{{ row.net > 0 ? "+" + row.net : row.net }}</td>
            </tr>
          </tbody>
        </table>
        <div class="meta ts-note">
          怎么算的：一段被改写也会同时记成“删旧 + 增新”，
          所以<b>被删字数不少于净减少字数</b>，净变化看最后一列。
        </div>
        <div v-for="c in ts.chapters" :key="c.chapter" class="ts-chapter">
          <div class="ts-chapter-head">
            第 {{ c.chapter }} 章
            <span class="meta">原稿 {{ c.layers.raw || 0 }} 段 · 检查稿 {{ c.layers.checked || 0 }} 段 · 润色稿 {{ c.layers.refined || 0 }} 段</span>
          </div>
          <div v-for="p in c.pairs" :key="p.from + '-' + p.to" class="ts-pair">
            <div v-if="p.removed.length" class="ts-pair-head">
              {{ p.from === "raw" ? "检查那一步删掉的" : "润色那一步删掉的" }}（{{ p.removed.length }} 段）
            </div>
            <div v-else class="meta">这一步没有整段删除。</div>
            <div v-for="(x, i) in p.removed" :key="i" class="ts-para">
              <span class="pill">{{ (x.clues || []).map(clueLabel).join(" · ") }}</span>
              <span class="meta">{{ x.text.length }} 字</span>
              <div class="ts-text">{{ x.text }}</div>
            </div>
          </div>
        </div>
      </template>
      <div v-else-if="tsOpen && ts" class="meta">{{ ts.hint }}</div>
    </section>

    <!-- 读不通的句子（2026-11-14）：零 token，只读盘。跟有无审查报告无关。 -->
    <section class="card ts-card">
      <div class="ts-head">
        <h4>读不通的句子<span class="meta">（不花钱，只看句子结构）</span></h4>
        <span class="spacer"></span>
        <button class="mini" :disabled="urBusy" @click="toggleUnreadable">
          {{ urOpen ? (urBusy ? "扫描中…" : "收起") : "扫一遍" }}
        </button>
      </div>
      <div class="meta ts-note">
        扫的是成品稿（有润色稿看润色稿，没有就看检查稿、原稿），找<b>没头没尾的句子</b>：
        单独成句的一个字，以及“包括 / 除了 / 对于……”后面没有主句的半句话。
        对白里的短句不算 —— 那是人物说话。
      </div>
      <div v-if="urErr" class="meta ts-err">{{ urErr }}</div>
      <template v-if="urOpen && ur">
        <div class="meta ts-note">
          共 {{ ur.total }} 处。语义上讲不通的句子（要读懂上下文才知道缺什么）机器判不了，
          这里没报不等于读得通。
        </div>
        <div v-for="c in ur.chapters" :key="c.chapter" class="ts-chapter">
          <div class="ts-chapter-head">
            第 {{ c.chapter }} 章
            <span class="meta">{{ c.layer || "-" }} · {{ c.chars || 0 }} 字 · {{ c.count || 0 }} 处</span>
          </div>
          <div v-if="c.error" class="meta">{{ c.error }}</div>
          <div v-else-if="!c.hits.length" class="meta">没扫到。</div>
          <div v-for="h in c.hits" :key="h.code" class="ts-pair">
            <div class="ts-pair-head">{{ UR_LABEL[h.code] || h.label }}（{{ h.count }} 处）</div>
            <div v-for="(s, i) in h.samples" :key="i" class="ts-para">
              <div class="ts-text">{{ s }}</div>
            </div>
            <div class="meta">{{ h.hint }}</div>
          </div>
        </div>
      </template>
    </section>

    <!-- 章职能核查（2026-11-14）：拿章大纲对正文，零 token、不给分。 -->
    <section class="card ts-card">
      <div class="ts-head">
        <h4>这章干成它该干的事了吗<span class="meta">（对照章大纲，不花钱）</span></h4>
        <span class="spacer"></span>
        <button class="mini" :disabled="cfBusy" @click="toggleChapterFunction">
          {{ cfOpen ? (cfBusy ? "核查中…" : "收起") : "查一遍" }}
        </button>
      </div>
      <div class="meta ts-note">
        拿章节大纲对一遍正文：<b>涉及角色 / 场景</b>是硬要求（大纲点名、正文里没有就报）；
        核心事件 / 小目标 / 钩子只比人名地名与编号 —— <b>没出现不等于没写</b>，可能只是换了说法。
        这一层不给分，只把“要你看一眼”的条目标出来。
      </div>
      <div v-if="cfErr" class="meta ts-err">{{ cfErr }}</div>
      <template v-if="cfOpen && cf">
        <div class="meta ts-note">
          共 {{ (cf.stats || {}).chapters || 0 }} 章（有正文 {{ (cf.stats || {}).with_body || 0 }} 章，
          需你过目 {{ (cf.stats || {}).need_attention || 0 }} 章）
        </div>
        <div v-for="c in cf.chapters" :key="c.chapter" class="ts-chapter">
          <div class="ts-chapter-head">
            第 {{ c.chapter }} 章 {{ c.title || "" }}
            <span v-if="c.needs_attention && c.needs_attention.length" class="pill cf-warn">
              {{ c.needs_attention.length }} 条待看
            </span>
            <span v-else-if="c.body" class="meta">没发现问题</span>
            <span v-else class="meta">没有正文，跳过了</span>
          </div>
          <div v-if="c.notes && c.notes.length" class="meta">{{ c.notes.join("；") }}</div>
          <div v-for="x in c.checks || []" :key="x.field" class="cf-row">
            <span class="pill" :class="'cf-' + x.level">{{ x.field }}</span>
            <span class="meta">{{ x.verdict }}</span>
          </div>
          <div v-if="c.needs_attention && c.needs_attention.length" class="cf-attention">
            <div v-for="(a, i) in c.needs_attention" :key="i">· {{ a }}</div>
          </div>
        </div>
      </template>
    </section>

    <!-- 无报告提示 -->
    <div v-if="!report" class="empty">
      <template v-if="props.selfEngine">
        当前是自任务引擎（engine: self，不调用模型），审查这一步用不了。<br>
        要用它：设置 → 引擎与模型 切到 direct / hermes，或把阶段 5 的任务书交给外部执行方。
      </template>
      <template v-else>暂无审查报告 —— 点击"运行审查"开始分析章节</template>
    </div>

    <!-- 报告内容 -->
    <div v-else>
      <!-- 确定性检查汇总 -->
      <section class="card">
        <h4>确定性检查汇总</h4>
        <table class="det-table">
          <thead>
            <tr><th>章</th><th>标题</th><th>字数</th><th>quality</th><th>文风</th></tr>
          </thead>
          <tbody>
            <tr v-for="(det, n) in report.deterministic" :key="n">
              <td>{{ n }}</td>
              <td class="title-cell">{{ det.title || "?" }}</td>
              <td :class="{ 'cell-bad': !det.word_ok }">{{ det.word_count }} {{ det.word_ok ? "✓" : "✗" }}</td>
              <td>{{ det.quality ?? "-" }}</td>
              <td>{{ det.style_issues?.length ? det.style_issues.length + " 项" : "✓" }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <!-- 操作栏 -->
      <section class="card">
        <div class="decision-bar">
          <div class="decision-stats">
            <span class="stat-accept">接受 {{ acceptedCount }}</span>
            <span class="stat-ignore">忽略 {{ ignoredCount }}</span>
            <span class="stat-pending">待定 {{ pendingCount }}</span>
          </div>
          <div class="decision-actions">
            <button class="mini" @click="acceptAll">全接受</button>
            <button class="mini" @click="ignoreAll">全忽略</button>
            <button class="mini" @click="resetDecisions">重置</button>
          </div>
        </div>
      </section>

      <!-- 章节审查发现 -->
      <section v-for="c in chapters" :key="c.n" class="card chapter-card">
        <div class="chapter-head" @click="toggleChapter(c.n)">
          <span class="stage-no" :class="{ lit: c.findings?.length }">{{ c.n }}</span>
          <span class="chapter-title">第 {{ c.n }} 章</span>
          <span class="finding-count">{{ c.findings?.length || 0 }} 条</span>
          <span class="spacer"></span>
          <span class="expand-icon">{{ expandedChapters.has(c.n) ? "▼" : "▶" }}</span>
        </div>

        <div v-if="expandedChapters.has(c.n)" class="chapter-body">
          <div v-if="!c.findings?.length" class="no-issues">无问题 ✓</div>
          <div v-for="f in c.findings" :key="f.id" class="finding-row" :class="sevClass(f.severity)">
            <div class="finding-header">
              <span class="sev-icon">{{ sevIcon(f.severity) }}</span>
              <span class="finding-type">{{ typeLabel(f.type) }}</span>
              <span class="finding-id">{{ f.id }}</span>
            </div>
            <div class="finding-detail">{{ f.detail }}</div>
            <div v-if="f.suggested_action" class="finding-suggestion">
              建议：{{ f.suggested_action }}
            </div>
            <div class="finding-actions">
              <button
                class="mini"
                :class="{ primary: decisions[`${c.n}_${f.id}`]?.action === 'accept' }"
                @click="setDecision(c.n, f.id, 'accept')"
              >接受</button>
              <button
                class="mini"
                :class="{ danger: decisions[`${c.n}_${f.id}`]?.action === 'ignore' }"
                @click="setDecision(c.n, f.id, 'ignore')"
              >忽略</button>
              <button
                class="mini"
                @click="toggleComment(c.n, f.id)"
              >💬</button>
              <button
                class="mini"
                @click="goToRefine(c.n)"
                title="打开段落级精修"
              >✏️ 精修</button>
              <input
                v-if="decisions[`${c.n}_${f.id}`]?.action === 'accept'"
                v-model="decisions[`${c.n}_${f.id}`].feedback"
                class="feedback-input"
                placeholder="补充意见（可选）"
              />
            </div>
            <!-- 评论列表 + 输入框 -->
            <div v-if="f.comments?.length || commenting[`${c.n}_${f.id}`]" class="comment-section">
              <div v-for="(cm, ci) in (f.comments || [])" :key="ci" class="comment-item">
                <span class="comment-user">{{ cm.user }}</span>
                <span class="comment-time">{{ cm.time }}</span>
                <div class="comment-text">{{ cm.text }}</div>
              </div>
              <div v-if="commenting[`${c.n}_${f.id}`]" class="comment-input-row">
                <input
                  v-model="commentText[`${c.n}_${f.id}`]"
                  class="comment-input"
                  placeholder="添加评论..."
                  @keyup.enter="submitComment(c.n, f.id)"
                />
                <button class="mini primary" @click="submitComment(c.n, f.id)">发送</button>
                <button class="mini" @click="commenting[`${c.n}_${f.id}`] = false">取消</button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 实时进度条 -->
      <section v-if="batchProgress && batchProgress.status === 'running'" class="card progress-card">
        <div class="progress-info">
          <span class="progress-msg">{{ batchProgress.message }}</span>
          <span class="progress-pct">{{ progressPct }}%</span>
        </div>
        <div class="progress-bar">
          <div class="progress-fill" :style="{ width: progressPct + '%' }"></div>
        </div>
      </section>

      <!-- 底部操作 -->
      <section class="card bottom-actions">
        <button class="mini primary" :disabled="saving || !hasDecision" @click="saveDecisions()">
          {{ saving ? "保存中…" : "保存决策" }}
        </button>
        <button
          class="mini primary"
          :disabled="running || !hasDecision || props.selfEngine"
          :title="props.selfEngine ? '当前引擎是自任务（engine: self，不调模型），批量精修要逐个调用模型。切到 direct / hermes 再用。' : ''"
          @click="runBatchRefine"
        >
          {{ running ? "精修中…" : "运行批量精修" }}
        </button>
        <span class="hint">
          决策已保存（下次打开这份报告还在）<template v-if="decisionsDirty"> ·
            <b>有改动未保存，运行精修时会自动先保存</b></template>
        </span>
      </section>
    </div>

    <!-- Toast -->
    <div v-if="toast" class="toast">{{ toast }}</div>
  </div>
</template>

<style scoped>
/* 产物待批（2026-11-11）：一行一个候选，路径要能读全（不许截断成看不出来源）。 */
.product-gate { margin-bottom: 14px; }
.prod-note { line-height: 1.7; margin: 4px 0 8px; }
.prod-msg { color: var(--warn, #b8860b); margin: 4px 0; }
.prod-row {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 8px 0; border-top: 1px solid var(--border);
}
.prod-row .spacer { flex: 1; }
.prod-item { border-top: 1px solid var(--border); }
.prod-item .prod-row { border-top: none; }
.prod-view { padding: 0 0 10px; }
.prod-view pre {
  max-height: 320px; overflow: auto; white-space: pre-wrap; word-break: break-word;
  margin: 6px 0 0; padding: 10px 12px; font-size: 12px; line-height: 1.7;
  background: var(--card); border: 1px solid var(--border); border-radius: 6px;
  color: var(--ink);
}
.prod-path { font-size: 12px; color: var(--muted); word-break: break-all; }
.prod-state { font-size: 12px; }
.prod-st-pending { color: var(--accent); }
.prod-st-stale { color: var(--warn, #b8860b); }
.prod-st-approved { color: var(--ok, #2e8b57); }
.prod-st-rejected { color: var(--bad); }

.review-console {
  position: relative;
}
.review-head {
  display: flex;
  align-items: center;
  margin-bottom: 12px;
}
.review-head h3 {
  margin: 0;
  flex: 1;
}
.head-actions {
  display: flex;
  gap: 8px;
}

.card h4 {
  margin: 0 0 10px;
  font-size: 14px;
}
/* 三份稿对照（2026-11-14）：段落原文要能读完，长段限高可滚。 */
.ts-card { margin-bottom: 14px; }
.ts-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ts-head h4 { margin: 0; }
.ts-head .meta { margin-left: 6px; }
.ts-head .spacer { flex: 1; }
.ts-note { line-height: 1.7; margin: 6px 0; }
.ts-err { color: var(--bad); }
.ts-chapter { margin-top: 14px; }
.ts-chapter-head { font-weight: 600; margin-bottom: 6px; }
.ts-chapter-head .meta { font-weight: 400; margin-left: 8px; }
.ts-pair { margin-bottom: 10px; }
.ts-pair-head { font-size: 12px; color: var(--muted); margin: 6px 0; }
.ts-para { padding: 6px 0 8px; border-top: 1px dashed var(--border); }
.ts-text {
  margin-top: 4px; font-size: 13px; line-height: 1.75; white-space: pre-wrap;
  word-break: break-word; max-height: 220px; overflow: auto;
}

/* 章职能核查（2026-11-14）：一行一个字段，强判据用警示色标出来。 */
.cf-row { display: flex; align-items: baseline; gap: 8px; padding: 3px 0; }
.cf-row .meta { flex: 1; line-height: 1.7; }
.cf-strong { color: var(--bad); }
.cf-weak { color: var(--warn, #b8860b); }
.cf-na { color: var(--muted); }
.cf-warn { color: var(--bad); }
.cf-attention {
  margin-top: 6px; padding: 6px 10px; border-radius: 6px; font-size: 12px; line-height: 1.7;
  border-left: 3px solid var(--warn, #b8860b); background: var(--field-dim);
}

.det-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.det-table th, .det-table td {
  padding: 6px 10px;
  text-align: left;
  border-bottom: 1px solid var(--border);
}
.det-table th {
  color: var(--muted);
  font-weight: 500;
}
.title-cell {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cell-bad {
  color: var(--bad);
}

.decision-bar {
  display: flex;
  align-items: center;
  gap: 16px;
}
.decision-stats {
  display: flex;
  gap: 12px;
  font-size: 13px;
}
.stat-accept { color: var(--ok); }
.stat-ignore { color: var(--muted); }
.stat-pending { color: var(--warn); }
.decision-actions {
  display: flex;
  gap: 6px;
}

.chapter-card {
  padding: 0;
  overflow: hidden;
}
.chapter-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  cursor: pointer;
  user-select: none;
}
.chapter-head:hover {
  background: rgba(155, 143, 196, 0.06);
}
.stage-no {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  background: var(--accent-soft);
  color: #fff;
  display: grid;
  place-items: center;
  font-weight: 700;
  font-size: 12px;
}
.stage-no.lit {
  background: var(--accent);
}
.chapter-title {
  font-weight: 500;
}
.finding-count {
  font-size: 12px;
  color: var(--muted);
}
.expand-icon {
  color: var(--muted);
  font-size: 10px;
}

.chapter-body {
  padding: 12px 16px;
  border-top: 1px solid var(--border);
}
.no-issues {
  color: var(--ok);
  font-size: 13px;
  padding: 8px 0;
}

.finding-row {
  padding: 10px 12px;
  border-radius: 8px;
  margin-bottom: 8px;
  border-left: 3px solid transparent;
}
.finding-row.sev-error {
  border-left-color: var(--bad);
  background: rgba(196, 127, 127, 0.06);
}
.finding-row.sev-warn {
  border-left-color: var(--warn);
  background: rgba(201, 162, 106, 0.06);
}
.finding-row.sev-info {
  border-left-color: var(--accent);
  background: rgba(155, 143, 196, 0.06);
}

.finding-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.sev-icon {
  font-size: 12px;
}
.finding-type {
  font-size: 12px;
  font-weight: 500;
  color: var(--ink);
}
.finding-id {
  font-size: 11px;
  color: var(--muted);
  margin-left: auto;
}
.finding-detail {
  font-size: 13px;
  line-height: 1.6;
  margin-bottom: 4px;
}
.finding-suggestion {
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 8px;
}
.finding-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.feedback-input {
  flex: 1;
  min-width: 150px;
  padding: 5px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 12px;
  background: var(--bg);
  color: var(--ink);
}

.bottom-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.hint {
  font-size: 12px;
  color: var(--muted);
}

/* 批量精修进度条 */
.progress-card {
  padding: 12px 16px;
}
.progress-info {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  font-size: 13px;
}
.progress-msg {
  color: var(--ink);
}
.progress-pct {
  color: var(--accent);
  font-weight: 600;
}
.progress-bar {
  height: 6px;
  background: var(--border);
  border-radius: 3px;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: var(--accent);
  border-radius: 3px;
  transition: width 0.3s ease;
}

.toast {
  position: fixed;
  bottom: 20px;
  right: 20px;
  background: var(--ink);
  color: #fff;
  padding: 10px 16px;
  border-radius: 10px;
  font-size: 13px;
  z-index: 100;
  animation: fadeIn 0.2s;
}
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}

.comment-section {
  margin-top: 12px;
  padding: 8px 12px;
  background: var(--bg);
  border-radius: 8px;
  border: 1px solid var(--border);
}

.comment-item {
  padding: 6px 0;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}
.comment-item:last-child {
  border-bottom: none;
}

.comment-user {
  font-weight: 600;
  color: var(--accent);
  margin-right: 8px;
}
.comment-time {
  color: var(--muted);
  font-size: 11px;
}
.comment-text {
  margin-top: 4px;
  color: var(--ink);
}

.comment-input-row {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
.comment-input {
  flex: 1;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
  background: var(--card);
  color: var(--ink);
}
.comment-input:focus {
  outline: none;
  border-color: var(--accent);
}
</style>
