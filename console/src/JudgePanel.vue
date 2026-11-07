<script setup>
/**
 * 判定报告面板（P0，0.7.17）：整本一页 → 单章详情 → 标准设置。
 *
 * 三条来自产品定义的硬约束，改这个组件前先读：
 * 1. **不允许出现分数**。档位只有三档 + 不可判定；没有 total/quality/score。
 * 2. **任何"判不了"都不得显示"合格"**：未勾事件 → 明说"还没判（你还没勾完）"。
 * 3. 页头两句固定串必须常显：
 *    “这是拿现有稿件补跑的判定，不是新跑时的拦截”“本报告不改变现有成品文件”。
 */
import { ref, computed, onMounted, nextTick } from "vue";
import { API } from "./apiBase.js";
import { postJSON } from "./guiApi.js";
import JudgeEvents from "./JudgeEvents.vue";

// archived：归档搬空了工作区 —— 外层要重载项目列表/阶段/章节（2026-10-31）
const emit = defineEmits(["say", "archived", "goto-paragraph"]);

const view = ref("report");          // report | detail | caliber
const report = ref(null);
const detail = ref(null);
const caliber = ref(null);
const artifact = ref(null);
const preview = ref(null);
const busy = ref(false);
const err = ref("");
const openDefect = ref(null);
/* 指到即到（F07）：高亮哪个段落 */
const focusedPara = ref(null);
function focusPara(idx) {
  if (typeof idx !== "number") return;
  focusedPara.value = idx;
  nextTick(() => {
    const el = document.querySelector(`.judge-body [data-para="${idx}"]`);
    if (el && el.scrollIntoView) el.scrollIntoView({ block: "center", behavior: "smooth" });
  });
}
function gotoParagraph(idx) {
  emit("goto-paragraph", { chapter: detail.value?.chapter, index: idx });
}        // 展开的缺陷条目 id
const note = ref("");                // 裁定理由输入
const feel = ref("");                // 打中了 / 没打中 / 拿不准
const calEdit = ref({});             // 口径编辑（id -> 新值）
const rejectReason = ref("");        // B2 整本否掉的理由（必填 ≥8 字）
const rejectBusy = ref(false);
const archiveBusy = ref(false);      // 一键归档
const compareData = ref(null);       // I6 三稿对照（含三份正文）

// M4（2026-11-04 作者拍板 B）：第三条证据 = 模型先出候选、作者逐条终裁。
// 界面上守三条：① 候选只显示成“待你裁”，不许看起来像结论；
// ② 终裁按钮是人点的（模型写不了 /judge/feelings/settle，越层直接 400）；
// ③ 推翻与驳回必须留理由 —— 没有理由的推翻等于随手一点。
const third = ref(null);             // 本章第三条证据（来自 /judge/chapter）
const feelOpen = ref(null);          // 正在推翻/驳回的候选 id
const feelAction = ref("override");
const feelPick = ref("打中了");
const feelWhy = ref("");

// R05：这条判据在**这本书**里的一个真实例子（取自正文切句，不是人编的）
function refFor(rid) {
  return (report.value && report.value.run && report.value.run.refs
          && report.value.run.refs[rid]) || null;
}
// R1：判据失效告警（命中骤降）。只显示，不改档。
const alerts = computed(() => (report.value && report.value.run
                               && report.value.run.alerts) || []);
const bookReject = computed(() => report.value && report.value.book_reject);

async function submitBookReject() {
  const why = rejectReason.value.trim();
  if (why.length < 8) {
    emit("say", "理由至少 8 个字 —— 否掉整本是要留档的决定，半年后你得看得懂");
    return;
  }
  if (!window.confirm("确认“整本否掉”？\n\n· 只落一条判断记录（含理由与时间），"
                      + "不会删除或改动任何稿件\n· 之后可以撤销")) return;
  rejectBusy.value = true;
  try {
    await call("POST", "/judge/book_reject", { reason: why });
    rejectReason.value = "";
    await loadReport();
    emit("say", "已记下：整本否掉");
  } catch (e) { err.value = String(e.message || e); }
  rejectBusy.value = false;
}

// B2 延伸（2026-10-31）：一键**归档**整本 —— 不是一键清除。
//
// 用户原话：“直接一键清理产物风险太高。归档之后是用户自己的事情，
// 我们已经做到了保护措施。”所以这里只搬到 data/books/<书名>/，
// 不删任何东西；之后要不要彻底删，是人在“项目”页自己点（那条还进回收站）。
async function archiveBook() {
  const why = rejectReason.value.trim()
    || (bookReject.value && bookReject.value.reason) || "";
  if (!window.confirm(
    "归档这本书？\n\n"
    + "· 整本搬到 data/books/（稿子、素材、配置一起搬）\n"
    + "· 当前工作区会变空\n"
    + "· 什么都没删：之后在“项目”页可以恢复，也可以由你自己删除归档\n"
    + "· 搬之前会自动打一个回退点（快照）")) return;
  archiveBusy.value = true;
  err.value = "";
  try {
    const d = await call("POST", "/judge/book_archive", { confirm: true, reason: why });
    emit("say", "已归档 → " + (d.path || d.name)
      + "（工作区已清空，可在“项目”页恢复或删除）");
    emit("archived", d);
  } catch (e) { err.value = String(e.message || e); }
  archiveBusy.value = false;
}

async function clearBookReject() {
  rejectBusy.value = true;
  try {
    await call("POST", "/judge/book_reject/clear", {});
    await loadReport();
    emit("say", "已撤销“整本否掉”（旧记录留在历史里）");
  } catch (e) { err.value = String(e.message || e); }
  rejectBusy.value = false;
}

async function call(method, path, body) {
  // 写请求必须走 guiApi.postJSON（2026-11-04 修）：它带 X-Mofang-Source + 主进程签发的
  // X-Mofang-Token。自己拼 fetch 只带 Content-Type → agent_mode=true 时被 /judge/events、
  // /judge/sentence、/judge/feelings/settle 这些**作者专属写端点**判成“非受信来源”403，
  // 而按钮看起来“点了没反应”（错误显示在面板顶部，用户正滚在下面）。
  if (method === "POST") return postJSON(path, body);
  const r = await fetch(API + path);
  const d = await r.json().catch(() => ({}));
  if (r.status >= 400) throw new Error(d.error || ("HTTP " + r.status));
  return d;
}

async function loadReport() {
  busy.value = true; err.value = "";
  try {
    report.value = await call("GET", "/judge/report");
    artifact.value = await call("GET", "/judge/artifact");
  } catch (e) { err.value = String(e.message || e); }
  busy.value = false;
}

async function exportList() {
  busy.value = true; err.value = "";
  try {
    const d = await call("POST", "/judge/export", {});
    emit("say", "已导出：" + d.path);
  } catch (e) { err.value = String(e.message || e); }
  busy.value = false;
}

function caliberChanges() {
  const set = {};
  (caliber.value?.criteria || []).forEach((c) => {
    const v = calEdit.value[c.id];
    if (v !== undefined && String(v) !== String(c.value)) set[c.id] = v;
  });
  return set;
}

let previewTimer = null;
function onCalChange() {
  if (previewTimer) clearTimeout(previewTimer);
  previewTimer = setTimeout(async () => {
    const set = caliberChanges();
    if (!Object.keys(set).length) { preview.value = null; return; }
    try { preview.value = await call("POST", "/judge/caliber/preview", { set }); }
    catch (e) { preview.value = null; }
  }, 400);
}

async function runBacktest() {
  busy.value = true; err.value = "";
  try {
    const _rep = await call("POST", "/judge/run", {});
    await loadReport();
    emit("say", "回测完成：" + summaryLine.value);
  } catch (e) { err.value = String(e.message || e); }
  busy.value = false;
}

async function openChapter(n) {
  busy.value = true; err.value = ""; openDefect.value = null;
  try {
    detail.value = await call("GET", "/judge/chapter?n=" + n);
    third.value = detail.value.third_evidence || null;
    view.value = "detail";
  } catch (e) { err.value = String(e.message || e); }
  busy.value = false;
}

// 第三条证据：作者终裁（采纳 / 推翻 / 驳回）。
// 采纳 = 认下模型的感受；推翻 = 你给感受与理由；驳回 = 这句根本不算证据。
async function settleFeeling(p, action, extra) {
  busy.value = true; err.value = "";
  try {
    const decisions = [Object.assign({ id: p.id, action }, extra || {})];
    const d = await call("POST", "/judge/feelings/settle",
                         { chapter: detail.value.chapter, decisions });
    feelOpen.value = null; feelPick.value = "打中了"; feelWhy.value = "";
    await openChapter(detail.value.chapter);
    emit("say", d.message || "已终裁");
  } catch (e) {
    const m = String(e.message || e);
    err.value = m;
    // 错误同时走 toast：面板的错误行在顶部，用户正滚在下面时看不见 = “点了没反应”
    emit("say", "终裁没成功：" + m);
  }
  busy.value = false;
}

function openFeelEditor(p, action) {
  feelOpen.value = p.id;
  feelAction.value = action;
  feelPick.value = p.feeling || "打中了";
  feelWhy.value = "";
}

function confirmFeel(p) {
  const why = feelWhy.value.trim();
  if (why.length < 4) {
    emit("say", "理由至少 4 个字 —— 推翻与驳回都要留下“为什么”");
    return;
  }
  if (feelAction.value === "override") {
    settleFeeling(p, "override", { feeling: feelPick.value, reason: why });
  } else {
    settleFeeling(p, "reject", { reason: why });
  }
}

// I6 三稿对照：原文 / 检查稿 / 润色稿并排。
//
// 数据现成（`/judge/destroy` 早就在算缩水率与删句），缺的是**正文本身** ——
// 所以这里带 `text=1` 取三份全文；默认不带（一页 1.8 万字符，多数调用不需要）。
//
// 刻意**不做逐行对齐**：段数一变（删段/合段）行就对不齐，看着整齐其实是错位，
// 比不对齐更误导。三栏各自滚动，只靠段号对应。
async function openCompare() {
  if (!detail.value) return;
  const n = detail.value.chapter;
  try {
    const d = await call("GET", "/judge/destroy?n=" + n + "&text=1");
    compareData.value = d;
    view.value = "compare";
  } catch (e) { err.value = String(e.message || e); }
}

// 三列的参数（缺哪一版就明说缺，不显示空栏冒充"没变化"）
const compareCols = computed(() => {
  const d = compareData.value;
  if (!d) return [];
  const texts = d.texts || {};
  const words = (d.destroy && d.destroy.words) || {};
  const why = { checked: "没跑过检查阶段（阶段 5）", refined: "没跑过润色阶段（阶段 6）" };
  return [
    { key: "raw", title: "原文（写作阶段生成的）", only: false },
    { key: "checked", title: "检查稿（阶段 5）", only: true },
    { key: "refined", title: "润色稿（阶段 6）", only: true },
  ].map((c) => {
    const t = texts[c.key];
    const present = typeof t === "string" && t.trim().length > 0;
    return {
      ...c, present, words: words[c.key],
      paras: present
        ? t.replace(/\r\n/g, "\n").split(/\n{2,}/).map((x) => x.trim())
            .filter(Boolean).map((x, i) => {
              // 章标题（`## 第1章 …`）不该和正文一个样子 —— 三栏并排时
              // 它退化成一段普通文字，读者会以为那是正文第一段。
              // 井号在这里只是 markdown 记号，显示时去掉（保留"第1章 夜雨"本身）。
              const heading = /^#{1,3}\s/.test(x);
              return { i: i + 1, heading,
                       text: heading ? x.replace(/^#{1,3}\s+/, "") : x };
            })
        : [],
      missingWhy: why[c.key] || "文件不存在",
    };
  });
});

// 勾选保存后要**重新拉一次单章**：事件数变了，档位与理由也跟着变
// （界面若不刷新，用户会看到"勾了 3 件"却仍是"待作者勾选"）。
async function onEventsSaved() {
  if (!detail.value) return;
  try {
    detail.value = await call("GET", "/judge/chapter?n=" + detail.value.chapter);
    third.value = detail.value.third_evidence || null;
  } catch (e) { err.value = String(e.message || e); }
}

async function saveSentence(d) {
  if (!feel.value) { emit("say", "先选：打中了 / 没打中 / 拿不准"); return; }
  if (!note.value.trim()) { emit("say", "理由必填 —— 无理由的裁定等于随手点击"); return; }
  busy.value = true; err.value = "";
  try {
    await call("POST", "/judge/sentence", {
      chapter: detail.value.chapter, paragraph_index: d.paragraph_index,
      sentence: d.sentence, feeling: feel.value, reason: note.value.trim(),
    });
    openDefect.value = null; feel.value = ""; note.value = "";
    await openChapter(detail.value.chapter);
    emit("say", "裁定已记");
  } catch (e) { err.value = String(e.message || e); }
  busy.value = false;
}

async function openCaliber() {
  busy.value = true; err.value = "";
  try {
    caliber.value = await call("GET", "/judge/caliber");
    const e = {};
    (caliber.value.criteria || []).forEach((c) => { e[c.id] = c.value; });
    calEdit.value = e;
    view.value = "caliber";
  } catch (e) { err.value = String(e.message || e); }
  busy.value = false;
}

async function saveCaliber() {
  const set = {};
  (caliber.value.criteria || []).forEach((c) => {
    const v = calEdit.value[c.id];
    if (v !== undefined && String(v) !== String(c.value)) set[c.id] = v;
  });
  if (!Object.keys(set).length) { emit("say", "没有改动"); return; }
  busy.value = true; err.value = "";
  try {
    const d = await call("POST", "/judge/caliber", { set });
    emit("say", d.message || ("已改为 v" + d.version));
    await openCaliber();
  } catch (e) { err.value = String(e.message || e); }
  busy.value = false;
}

// M4 迁移（2026-11-04）：老工作区的口径表是 v1 的 14 条，没有“产出方”这一列。
// 补登记是一次**显式**动作（产新版本、旧版进 history）——
// 读盘时自动顶上会让“表里没写”看起来像“表里写了”，而 M4 要的正是登记过的事实。
async function upgradeCaliber() {
  busy.value = true; err.value = "";
  try {
    const d = await call("POST", "/judge/caliber/upgrade", {});
    if (d.changed) {
      emit("say", "已补登记 → v" + d.version
        + "（新增 " + ((d.added || []).join("、") || "无")
        + "；补产出方 " + (d.filled || []).length + " 项）");
    } else {
      emit("say", d.message || "登记表已是最新");
    }
    await openCaliber();
  } catch (e) { err.value = String(e.message || e); }
  busy.value = false;
}

const noticeA = "这是拿现有稿件补跑的判定，不是新跑时的拦截";
const noticeB = "本报告不改变现有成品文件";

const summaryLine = computed(() => {
  const s = (report.value && report.value.run && report.value.run.summary) || {};
  if (!s.total) return "";
  return (s.total + " 章：" + (s.ok || 0) + " 可以开始逐字改 · " + (s.spot || 0)
          + " 要定点重写 · " + (s.rewrite || 0) + " 要重写本章；全书 "
          + (s.defects || 0) + " 处要改");
});

const rows = computed(() => (report.value && report.value.run && report.value.run.chapters) || []);

const demoText = computed(() => {
  const d = report.value && report.value.evidence && report.value.evidence.demo;
  if (!d) return "";
  const items = Array.isArray(d.items) ? d.items : [];
  return items.length ? ("第 " + items.join("、") + " 章（" + d.note + "）") : String(d.note || "");
});

function verdictText(c) {
  if (!c) return "";
  if (c.verdict === "可开始逐字改") return "✓ 可以开始逐字改";
  if (c.verdict === "需定点重写") {
    const p = (c.paragraph_set || []).slice(0, 3).join("、");
    return "⚠ 要定点重写" + (p ? "（第 " + p + " 段" + ((c.paragraph_set || []).length > 3 ? " 等" : "") + "）" : "");
  }
  if (c.verdict === "需重写本章") return "✕ 要重写本章";
  return "？ 还没判（你还没勾完）";
}

function verdictClass(c) {
  if (!c || !c.verdict) return "jd-undecided";
  if (c.verdict === "可开始逐字改") return "jd-ok";
  if (c.verdict === "需定点重写") return "jd-warn";
  if (c.verdict === "需重写本章") return "jd-bad";
  return "jd-undecided";
}

onMounted(loadReport);
</script>

<template>
  <div class="judge">
    <!-- 整本一页（I1）：常驻摘要条 -->
    <template v-if="view === 'report'">
      <div class="judge-bar">
        <b>判定</b>
        <span v-if="report && report.exists" class="judge-sum">{{ summaryLine }}</span>
        <span v-else class="meta">还没跑过回测</span>
        <span class="spacer"></span>
        <span v-if="report && report.stale" class="judge-stale" title="判定标准改过，旧结论不再成立">
          ⚠ 判定标准改过了，需重判
        </span>
        <span v-if="report && report.exists" class="meta">
          v{{ report.caliber.version }} · {{ report.run.at }}
        </span>
        <button class="mini primary" :disabled="busy" @click="runBacktest">
          {{ report && report.exists ? "重跑判定" : "开始回测" }}</button>
        <button class="mini" :disabled="busy" @click="openCaliber">改标准</button>
        <button class="mini" :disabled="busy || !report || !report.exists"
                @click="exportList">导出清单</button>
      </div>
      <div class="judge-notice">{{ noticeA }} · {{ noticeB }}</div>
      <!-- R18 证据分级：能力证据 / demo 证据必须分开说，不许混 -->
      <div v-if="report && report.exists && report.evidence" class="meta judge-evidence">
        能力证据：{{ (report.evidence.capability || {}).note }} ·
        demo 证据：{{ demoText }}
      </div>
      <!-- M4 第三条证据（2026-11-04）：模型提了多少、你裁了多少 —— 看不见就等于没做 -->
      <div v-if="report && report.third_evidence && report.third_evidence.total" class="meta">
        第三条证据：模型提了 {{ report.third_evidence.total }} 条候选，你已终裁
        {{ (report.third_evidence.counts.accepted || 0)
           + (report.third_evidence.counts.overridden || 0)
           + (report.third_evidence.counts.rejected || 0) }} 条
        <template v-if="report.third_evidence.counts.pending">
          · <b>{{ report.third_evidence.counts.pending }} 条待你裁</b>
        </template>
        · 判据产出方：规则 {{ (report.producers || {}).rules || 0 }} /
        模型 {{ (report.producers || {}).model || 0 }} /
        作者 {{ (report.producers || {}).author || 0 }}
      </div>
      <!-- B2 整本否掉：作者判定整本不行时，横幅常驻（可撤销） -->
      <div v-if="bookReject" class="judge-bookreject">
        <b>你已否掉整本</b>：{{ bookReject.reason }}
        <span class="meta">（{{ bookReject.at }}）</span>
        <button class="mini" :disabled="rejectBusy" @click="clearBookReject">撤销</button>
        <button class="mini" :disabled="archiveBusy" @click="archiveBook">
          {{ archiveBusy ? "归档中…" : "归档这本书" }}
        </button>
        <div class="meta">这是一条判断记录，不会删除或改动任何稿件。
          归档 = 整本搬进 data/books/（同样不删），之后由你决定恢复还是删除。</div>
      </div>

      <!-- R1 判据失效告警（评审高危项）：命中骤降时出声，不判档 -->
      <div v-if="alerts.length" class="judge-alert">
        <b>⚠ 判定标准可能已失效，建议人工抽检</b>
        <div v-for="a in alerts" :key="a.chapter" class="meta">{{ a.note }}</div>
        <div class="meta">系统分不清“稿子真改好了”和“判定标准对新腔调睡着了”——
          这里只提醒你抽检一章，不改任何结论。</div>
      </div>

      <!-- R07 成品过期横幅：必须给两项凭据（字数 + 时间），只给徽标 = 未实现 -->
      <div v-if="artifact && artifact.exists && artifact.stale" class="judge-artifact">
        <b>⚠ 成品已过期</b>：{{ artifact.name }}（{{ artifact.built_at }} 出的）
        <div v-for="(e, i) in artifact.evidence" :key="i" class="meta">{{ e }}</div>
        <div class="meta">这一期只标注，不自动重出 —— 重出要真跑评审与润色（会花钱）。</div>
      </div>
      <div v-else-if="artifact && artifact.exists" class="meta">
        成品 {{ artifact.name }} 与源稿一致（{{ artifact.built_at }}）
      </div>
      <div v-if="err" class="judge-err">{{ err }}</div>

      <div v-if="!report || !report.exists" class="meta judge-empty">
        还没有判定记录。点“开始回测”会用盘上现有的稿跑一遍（零 token、断网也能跑）。
        <br />这一步只读，不会改动任何稿件或成品。
      </div>

      <table v-else class="judge-table">
        <thead>
          <tr><th>章</th><th>档位</th><th>要改</th><th>前 3 处</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="c in rows" :key="c.chapter">
            <td class="judge-ch">第 {{ c.chapter }} 章</td>
            <td :class="verdictClass(c)">{{ verdictText(c) }}</td>
            <td>{{ c.defect_count }} 处</td>
            <td class="judge-top">
              <span v-for="d in c.top_defects" :key="d.rule_id + d.paragraph_index"
                    class="judge-top-item">第 {{ d.paragraph_index }} 段“{{ d.sentence }}”</span>
              <span v-if="(c.defect_count || 0) > 3" class="meta">
                再看 {{ c.defect_count - 3 }} 处</span>
            </td>
            <td><button class="mini" @click="openChapter(c.chapter)">看这章</button></td>
          </tr>
        </tbody>
      </table>

      <!-- B2 整本否掉（业务界定 A3 / S07）：放整本页最底部 —— 看完所有章才做这个决定 -->
      <div v-if="!bookReject" class="judge-reject-box">
        <div class="judge-reject-head">
          <b>整本否掉</b>
          <span class="meta">看完全部之后，如果你的判断是“这本书不行”而不是“某几章要改”</span>
        </div>
        <div class="judge-reject-row">
          <input v-model="rejectReason" class="judge-reject-input"
                 placeholder="为什么不行（至少 8 个字 —— 半年后你得看得懂当时为什么）"
                 @keyup.enter="submitBookReject" />
          <button class="mini" :disabled="rejectBusy || rejectReason.trim().length < 8"
                  @click="submitBookReject">记下这个判断</button>
        </div>
        <div class="meta">只落一条记录（理由 + 时间），<b>不会删除或改动任何稿件</b>。
          之后可以撤销，旧记录留在历史里。</div>
        <div class="judge-archive-row">
          <span class="meta">不再改这本了 →</span>
          <button class="mini" :disabled="archiveBusy" @click="archiveBook">
            {{ archiveBusy ? "归档中…" : "归档这本书" }}
          </button>
          <span class="meta">搬到 data/books/，<b>不删任何东西</b>；之后恢复或删除都由你决定。</span>
        </div>
      </div>
    </template>

    <!-- 单章详情（I2 + I3） -->
    <template v-else-if="view === 'detail' && detail">
      <div class="judge-bar">
        <button class="mini" @click="view = 'report'">← 回整本</button>
        <b>第 {{ detail.chapter }} 章</b>
        <button class="mini" @click="openCompare" title="原文 / 检查稿 / 润色稿并排">
          三稿对照</button>
        <span :class="verdictClass(detail.verdict)">{{ verdictText(detail.verdict) }}</span>
        <span class="meta">这章有 {{ detail.defect_count }} 处要改</span>
        <span class="spacer"></span>
        <span class="meta">口径 v{{ detail.caliber.version }}</span>
      </div>
      <div class="judge-notice">{{ noticeA }}</div>
      <div v-if="err" class="judge-err">{{ err }}</div>

      <!-- 事件勾选（F03b）：唯一硬门，系统不替作者数。
           2026-10-31 抽成 JudgeEvents 组件 —— 分章页（I3 的原位置）用的是同一份实现。 -->
      <JudgeEvents :chapter="detail.chapter" :events="detail.outline_events"
                   :hint="detail.event_hint || ''"
                   :event-min="detail.event_min || 2"
                   :density-note="detail.density && detail.density.flagged ? detail.density.reason : ''"
                   @say="(m) => emit('say', m)"
                   @saved="onEventsSaved" />

      <!-- 第三条证据（M4=B，2026-11-04）：模型先出候选，你终裁。候选不是结论。 -->
      <!-- 没有候选时也要**显示这个块**：整块藏起来 = 用户以为功能没做（2026-11-04 补）。 -->
      <div v-if="third" class="judge-third">
        <div class="judge-third-head">
          <b>第三条证据（模型先出，你终裁）</b>
          <span v-if="third.counts.pending" class="judge-warn">待你裁 {{ third.counts.pending }} 条</span>
          <span v-else class="meta">没有待裁候选</span>
          <span class="meta">采纳 {{ third.counts.accepted }} · 推翻 {{ third.counts.overridden }}
            · 驳回 {{ third.counts.rejected }}</span>
          <span class="meta">你的裁定 {{ third.author_verdicts }} 条（其中来自模型
            {{ third.author_from_model }} 条）</span>
        </div>
        <div class="meta">模型只提候选：它写不了档位，也写不了你的终裁（越层直接拒收）。</div>
        <div v-if="!third.total" class="meta judge-third-why">
          还没有候选。让 Agent 提案（<code>POST /judge/feelings/propose</code>），
          或跑 <code>python scripts/judge_feelings.py --chapter {{ detail.chapter }}</code>
          （需要 engine: direct，且会花钱）；正式跑之前用 <code>--dry-run</code> 可零调用看提示词。
        </div>
        <div v-for="p in third.pending" :key="p.id" class="judge-third-item">
          <div class="judge-defect-row" @click="focusPara(p.paragraph_index)">
            <span class="judge-para">第 {{ p.paragraph_index }} 段</span>
            <span class="judge-rule">{{ p.feeling }}</span>
            <span class="judge-quote">“{{ p.sentence }}”</span>
          </div>
          <div class="meta judge-third-why">理由：{{ p.reason }}</div>
          <div class="judge-do">
            <button class="mini primary" :disabled="busy" @click="settleFeeling(p, 'accept')">采纳</button>
            <button class="mini" :disabled="busy" @click="openFeelEditor(p, 'override')">推翻…</button>
            <button class="mini" :disabled="busy" @click="openFeelEditor(p, 'reject')">驳回…</button>
          </div>
          <div v-if="feelOpen === p.id" class="judge-do">
            <select v-if="feelAction === 'override'" v-model="feelPick" class="judge-ev-class">
              <option>打中了</option>
              <option>没打中</option>
              <option>拿不准</option>
            </select>
            <input v-model="feelWhy" class="judge-reason" placeholder="理由（至少 4 个字）" />
            <button class="mini primary" :disabled="busy" @click="confirmFeel(p)">确定</button>
            <button class="mini" @click="feelOpen = null">取消</button>
          </div>
        </div>
      </div>

      <div class="judge-cols">
        <div class="judge-defects">
          <div v-for="d in detail.defects" :key="d.id + d.paragraph_index + d.rule_id"
               class="judge-defect" :class="{ open: openDefect === d.id }">
            <div class="judge-defect-row"
                 @click="openDefect = openDefect === d.id ? null : d.id; focusPara(d.paragraph_index)">
              <span class="judge-para">第 {{ d.paragraph_index || "—" }} 段</span>
              <span class="judge-rule">{{ d.rule_name }}<template v-if="d.count > 1"> ×{{ d.count }}</template></span>
              <span class="judge-quote">“{{ d.sentence }}”</span>
            </div>
            <div v-if="openDefect === d.id" class="judge-card">
              <div><b>原句</b>：{{ d.sentence }}</div>
              <div><b>为什么</b>：{{ d.why }}</div>
              <div><b>你怎么办</b>：{{ d.fix_type }}</div>
              <div class="judge-do">
                <button class="mini" :class="{ primary: feel === '打中了' }" @click="feel = '打中了'">打中了</button>
                <button class="mini" :class="{ primary: feel === '没打中' }" @click="feel = '没打中'">没打中</button>
                <button class="mini" :class="{ primary: feel === '拿不准' }" @click="feel = '拿不准'">拿不准</button>
                <input v-model="note" class="judge-reason" placeholder="一句理由（必填）" />
                <button class="mini primary" :disabled="busy" @click="saveSentence(d)">记录裁定</button>
                <button class="mini" v-if="d.paragraph_index"
                        @click="gotoParagraph(d.paragraph_index)"
                        title="带着这一段去段落精修工作台自己改">去精修这一段</button>
              </div>
            </div>
          </div>
          <div v-if="!detail.defects.length" class="meta">这章没命中任何规则。</div>
        </div>
        <div class="judge-body">
          <!-- 指到即到（2026-11-02，F07）：点缺陷行 → 正文滚到那一段并高亮。
               以前缺陷单只说"第 3 段"，人还得自己在几百段里找。 -->
          <div v-for="p in detail.paragraphs" :key="p.paragraph_index"
               class="judge-para-body"
               :class="{ 'judge-para-focus': focusedPara === p.paragraph_index }"
               :data-para="p.paragraph_index">
            <span class="judge-para-no">{{ p.paragraph_index }}</span>{{ p.text }}
          </div>
        </div>
      </div>
    </template>

    <!-- I6 三稿对照：原文 / 检查稿 / 润色稿 -->
    <template v-else-if="view === 'compare' && compareData">
      <div class="judge-bar">
        <button class="mini" @click="view = 'detail'">← 回单章</button>
        <b>第 {{ compareData.chapter }} 章 · 三稿对照</b>
        <span class="meta" v-if="compareData.destroy && compareData.destroy.shrink">
          缩减：原→检
          {{ Math.round(((compareData.destroy.shrink['raw->checked'] || 0) * 100)) }}%
          · 检→润
          {{ Math.round(((compareData.destroy.shrink['checked->refined'] || 0) * 100)) }}%
        </span>
        <span class="spacer"></span>
        <span class="meta">阈值 {{ Math.round((compareData.destroy.threshold || 0) * 100) }}%
          <template v-if="compareData.destroy.destroyed"> · 越过界线（疑似整章重新生成）</template>
        </span>
      </div>
      <div class="judge-notice">
        三栏<b>各自滚动，不做逐行对齐</b> —— 段数一变行就错位，那样看着整齐、实际更误导。
        用段号横向找同一段。缺的那一版会明说“不存在”，而不是显示空栏冒充“没变化”。
      </div>
      <div class="jc-wrap">
        <div v-for="col in compareCols" :key="col.key" class="jc-col"
             :class="{ 'jc-absent': !col.present }">
          <div class="jc-head">
            <b>{{ col.title }}</b>
            <span class="meta" v-if="col.present">{{ col.words }} 字 · {{ col.paras.length }} 段</span>
            <span class="meta" v-else>不存在</span>
          </div>
          <div class="jc-body">
            <template v-if="col.present">
              <div v-for="p in col.paras" :key="p.i" class="jc-para"
                   :class="{ 'jc-h': p.heading }">
                <span class="jc-no">{{ p.i }}</span>{{ p.text }}
              </div>
            </template>
            <div v-else class="jc-missing">
              这一版不存在：{{ col.missingWhy }}。<br>
              <span class="meta">不是“和原文一样”，是根本没生成过。</span>
            </div>
          </div>
        </div>
      </div>
    </template>

    <!-- 合格标准（I4 精简版） -->
    <template v-else-if="view === 'caliber' && caliber">
      <div class="judge-bar">
        <button class="mini" @click="view = 'report'">← 回整本</button>
        <b>合格标准</b>
        <span class="meta">现在生效：第 {{ caliber.version }} 版（{{ caliber.fingerprint }}）</span>
        <span class="spacer"></span>
        <button class="mini primary" :disabled="busy" @click="saveCaliber">保存（产新版本）</button>
      </div>
      <div class="judge-notice">改标准会产新版本，旧结论保留为历史；改完要在报告页点“重跑判定”。</div>
      <!-- M4 迁移（2026-11-04）：老表没有“产出方”。补登记是显式动作、产新版本。 -->
      <div v-if="caliber.needs_upgrade && caliber.needs_upgrade.needed" class="judge-preview">
        <b>这张表还缺“产出方”（M4）</b>
        <div class="meta">
          缺产出方：{{ (caliber.needs_upgrade.criteria_without_producer || []).join("、") || "无" }}
          <template v-if="(caliber.needs_upgrade.missing_factory_ids || []).length">
            · 未登记判据：{{ caliber.needs_upgrade.missing_factory_ids.join("、") }}
          </template>
        </div>
        <div class="meta">补登记只补“谁产的 / 谁终裁”，不改任何界线；产新版本，旧版进 history。</div>
        <button class="mini primary" :disabled="busy" @click="upgradeCaliber">补登记产出方</button>
      </div>
      <!-- R06 影响面预览：改数字立刻算，但点“保存”之前一个字都不写 -->
      <div v-if="preview" class="judge-preview">
        <b>影响面预览（还没生效）</b>
        <div>受影响 <b>{{ preview.affected_count }}</b> 章 ·
          预计转不合格 <b>{{ preview.to_fail_count }}</b> 章 ·
          需你重录 <b>{{ preview.manual_count }}</b> 条</div>
        <div class="meta">{{ preview.message }}</div>
        <div v-for="a in (preview.affected || []).slice(0, 6)" :key="a.chapter" class="meta">
          第 {{ a.chapter }} 章：{{ a.before }} → {{ a.after }}
        </div>
      </div>
      <div v-if="err" class="judge-err">{{ err }}</div>
      <table class="judge-table">
        <thead><tr><th>条目</th><th>值</th><th>类型</th><th>产出方 / 谁终裁</th>
          <th>谁在读（消费方）</th><th>本书里的一个例子</th></tr></thead>
        <tbody>
          <tr v-for="c in caliber.criteria" :key="c.id">
            <td>{{ c.id }} · {{ c.name }}</td>
            <td>
              <input v-if="c.editable" v-model="calEdit[c.id]" class="judge-cal-input"
                     @input="onCalChange" />
              <span v-else-if="c.producer === 'model'" class="meta">模型先出 · 你终裁</span>
              <span v-else class="meta">由你勾选（系统不测）</span>
            </td>
            <td>{{ c.kind }}</td>
            <td class="meta">{{ c.producer_name }}<br />终裁：{{ c.settled_by_name }}</td>
            <td class="meta">{{ c.consumer_name }}</td>
            <td class="judge-ref">
              <template v-if="refFor(c.id)">
                <div class="judge-ref-q">“{{ refFor(c.id).sentence }}”</div>
                <div class="meta">第 {{ refFor(c.id).chapter }} 章第
                  {{ refFor(c.id).paragraph_index }} 段</div>
              </template>
              <span v-else class="meta">
                {{ report && report.exists ? "最近一次回测里没命中（可能是好事，也可能是判据没生效）" : "还没跑过回测" }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </template>

    <div v-else class="meta">加载中…</div>
  </div>
</template>

<style scoped>
.judge { border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; margin-bottom: 12px; }
.judge-bar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.judge-bar b { font-size: 14px; }
.judge-sum { font-weight: 600; }
.judge-notice { font-size: 12px; color: var(--warn); margin: 6px 0; }
.judge-err { font-size: 12px; color: var(--bad); margin: 6px 0; }

/* ---- B2 整本否掉（2026-10-31）---- */
.judge-bookreject {
  margin: 8px 0;
  padding: 9px 12px;
  border: 1px solid var(--warn);
  border-left-width: 3px;
  border-radius: 8px;
  background: var(--field-dim);
  font-size: 12.5px;
  color: var(--ink);
  line-height: 1.8;
}
.judge-bookreject button { margin-left: 8px; }
.judge-reject-box {
  margin-top: 14px;
  padding: 10px 12px 11px;
  border: 1px dashed var(--border);
  border-radius: 9px;
  background: var(--field-inset);
}
.judge-reject-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.judge-reject-head b { font-size: 13px; color: var(--ink); }
.judge-reject-row {
  display: flex; align-items: center; gap: 8px; margin: 8px 0 6px;
}
.judge-reject-input {
  flex: 1;
  padding: 6px 9px;
  border: 1px solid var(--border);
  border-radius: 7px;
  background: var(--card);
  color: var(--ink);
  font-size: 12.5px;
}
.judge-reject-input:focus { outline: 1px solid var(--accent); }

/* ---- R1 判据失效告警 ---- */
.judge-alert {
  margin: 8px 0;
  padding: 9px 12px;
  border-left: 3px solid var(--bad);
  border-radius: 0 8px 8px 0;
  background: var(--field-dim);
  font-size: 12.5px;
  line-height: 1.8;
  color: var(--ink);
}
.judge-alert b { color: var(--ink-bad); }

/* ---- R05 判据引文列 ---- */
.judge-ref { max-width: 260px; font-size: 11.5px; }
.judge-ref-q { color: var(--ink); line-height: 1.6; }
.judge-empty { line-height: 1.7; padding: 8px 0; }
.judge-stale { font-size: 12px; color: var(--warn); }
.judge-artifact { border: 1px solid var(--warn); border-radius: 8px; padding: 6px 10px; margin: 6px 0; font-size: 13px; color: var(--warn); }
.judge-preview { border: 1px dashed var(--accent); border-radius: 8px; padding: 6px 10px; margin: 6px 0; font-size: 13px; }
.judge-evidence { color: var(--muted); }
.judge-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.judge-table th { text-align: left; font-weight: 600; color: var(--muted); padding: 6px 4px; }
.judge-table td { padding: 6px 4px; border-top: 1px solid var(--border); vertical-align: top; }
.judge-ch { white-space: nowrap; }
.judge-ok { color: var(--ok); }
.judge-warn { color: var(--warn); }
.judge-bad { color: var(--bad); }
.judge-undecided { color: var(--muted); }
.judge-top { font-size: 12px; line-height: 1.7; }
.judge-top-item { display: inline-block; margin-right: 10px; }
.judge-events { border: 1px dashed var(--border); border-radius: 8px; padding: 8px 10px; margin: 8px 0; }
/* ---- M4 第三条证据（模型先出、作者终裁）---- */
.judge-third { border: 1px dashed var(--border); border-radius: 8px; padding: 8px 10px; margin: 8px 0; }
.judge-third-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 4px; }
.judge-third-item { border-top: 1px solid var(--border); padding: 4px 0; }
.judge-third-item:first-of-type { border-top: 0; }
.judge-third-why { padding: 0 0 4px 2px; }
.judge-events-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.judge-event { display: flex; align-items: center; gap: 10px; padding: 3px 0; }
.judge-event label { display: flex; align-items: center; gap: 6px; }
.judge-ev-text { font-size: 13px; }
.judge-ev-class { background: var(--field); color: var(--ink); border: 1px solid var(--border); border-radius: 6px; }
.judge-hint { color: var(--warn); margin-top: 4px; }
.judge-evidence { width: 100%; margin-top: 6px; background: var(--field); color: var(--ink); border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px; }
.judge-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 8px; }
@media (max-width: 1200px) { .judge-cols { grid-template-columns: 1fr; } }
.judge-defects { max-height: 460px; overflow: auto; }
.judge-defect { border-bottom: 1px solid var(--border); }
.judge-defect-row { display: flex; gap: 10px; padding: 6px 2px; cursor: pointer; font-size: 13px; }
.judge-defect-row:hover { background: var(--field-hover); }
.judge-para { color: var(--muted); white-space: nowrap; }
.judge-rule { color: var(--accent); white-space: nowrap; }
.judge-quote { flex: 1; }
.judge-card { padding: 6px 2px 10px; font-size: 13px; line-height: 1.8; }
.judge-do { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin-top: 4px; }
.judge-reason { flex: 1; min-width: 160px; background: var(--field); color: var(--ink); border: 1px solid var(--border); border-radius: 6px; padding: 3px 8px; }
.judge-body { max-height: 460px; overflow: auto; font-size: 13px; line-height: 1.9; }
.judge-para-body { margin-bottom: 8px; }
/* 指到即到（F07）：点到的那一段给个明显的底，不然滚过去了也看不出来是哪段 */
.judge-para-body.judge-para-focus {
  background: var(--viz-note-bg);
  box-shadow: inset 3px 0 0 var(--viz-note-edge);
  border-radius: 4px;
  padding-left: 6px;
  margin-left: -6px;
}
.judge-para-no { color: var(--muted); font-size: 11px; margin-right: 6px; }
.judge-cal-input { width: 120px; background: var(--field); color: var(--ink); border: 1px solid var(--border); border-radius: 6px; padding: 2px 6px; }
</style>
