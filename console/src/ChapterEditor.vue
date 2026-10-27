<script setup>
/**
 * 章节编辑器 + 段落批改（2026-10-26）。
 *
 * 用户第 1 条：“我之前提的画线批改之类的都没有，而且编辑还要强制用外部编辑器
 * 打开，没有自带编辑器。”
 * 第 2 条：“完全不是一个适合审阅、适合精修、适合讨论的形态。”
 *
 * ## 它解决什么
 *
 * 此前“章节”页签每章只有 原稿/检查稿/润色稿/对比/历史 五个按钮，全是**只读预览**；
 * 要改一个字必须打开外部编辑器，改完还得自己把标点洗干净（否则成品带半角引号）。
 * 这个组件把三件事放在同一个界面里：
 *
 *   1. 就地编辑 + 保存（零 token，写前自动备份，Ctrl+S）
 *   2. 按段落挑选 + 写意见 → 交给模型重写这一段（花 token，但只改你选的那段）
 *   3. 每层字数 / 篇幅下限实时反馈（写短了当场知道，而不是等体检）
 *
 * ## 两个刻意的取舍
 *
 * **层要一致**：批注只在“当前层”（= 后端 refine_paragraph 会选中的那层，即
 * refined > checked > raw 里最高的）上开放。三层段落数常常不同，让批注作用在
 * 另一层上 = 一定会改错段。切到别的层时批注区直接禁用并说明原因。
 *
 * **保存不等于通过**：保存只落盘，不改 progress、不打分、不跑体检。用户改完
 * 仍要自己决定要不要重跑阶段 7/8。界面不假装"改完就好了"。
 *
 * ## 与 ParagraphRefinePanel 的分工
 *
 * 那个是“独立精修工作台”（可切章、看风格偏差、批量重写），住在“精修”页签；
 * 这里是“审阅时顺手改”——读着正文发现问题就地处理，不必先切页签、再手输章号。
 * 两者共用同一组后端端点，判据只有一份。
 */
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from "vue";

const props = defineProps({
  api: { type: Function, required: true },
  n: { type: Number, required: true },
  // embedded=true：不画遮罩、不占满屏，作为“审阅台”的中栏内联使用。
  // 同一份实现服务两个场景，避免“抽屉版”和“内联版”两份代码漂移。
  embedded: { type: Boolean, default: false },
});
const emit = defineEmits(["say", "close", "saved"]);

const LAYER_LABEL = { raw: "原稿", checked: "检查稿", refined: "润色稿" };
const LAYER_HINT = {
  raw: "模型初稿，未做逻辑检查",
  checked: "逻辑检查后，尚未润色",
  refined: "润色后（成品层）",
};

const loading = ref(true);
const busy = ref(false);
const err = ref("");
const warn = ref("");
const layers = ref({});
const layer = ref("refined");
const currentLayer = ref("");
const target = ref([0, 0]);
const mtime = ref(0);
const mtimeText = ref("");

const mode = ref("edit");            // edit | batch
const text = ref("");
const savedText = ref("");
const dirty = computed(() => text.value !== savedText.value);

const paras = ref([]);
const parasLoading = ref(false);
const selPara = ref(null);
const feedback = ref("");
const refining = ref(false);
const refineElapsed = ref(0);
const refineResult = ref(null);
let refineTimer = null;

const areaRef = ref(null);

const layerInfo = computed(() => layers.value[layer.value] || {});
const canBatch = computed(() => !!layer.value && layer.value === currentLayer.value);
const words = computed(() => (text.value.match(/[\u4e00-\u9fff]/g) || []).length);
const wordBadge = computed(() => {
  const lo = target.value[0] || 0;
  const hi = target.value[1] || 0;
  if (!lo) return { cls: "", text: words.value + " 字" };
  if (words.value < lo) return { cls: "low", text: words.value + " 字（下限 " + lo + "）" };
  if (hi && words.value > hi) return { cls: "high", text: words.value + " 字（上限 " + hi + "）" };
  return { cls: "ok", text: words.value + " 字" };
});

function fmtElapsed(sec) {
  const s = Math.max(0, Math.floor(sec || 0));
  return s < 60 ? s + " 秒" : Math.floor(s / 60) + " 分 " + (s % 60) + " 秒";
}

async function loadLayers() {
  loading.value = true;
  err.value = "";
  const r = await props.api("/chapters/layers?n=" + props.n);
  loading.value = false;
  if (r.status !== 200 || !r.data.ok) {
    err.value = (r.data && r.data.error) || ("读取失败 HTTP " + r.status);
    return;
  }
  layers.value = r.data.layers || {};
  currentLayer.value = r.data.current_layer || "";
  target.value = r.data.target_words || [0, 0];
  const want = layer.value && layers.value[layer.value] && layers.value[layer.value].exists
    ? layer.value : currentLayer.value;
  layer.value = want || "refined";
  await loadContent();
}

async function loadContent() {
  const r = await props.api(
    "/chapters/content?n=" + props.n + "&layer=" + encodeURIComponent(layer.value));
  if (r.status !== 200 || !r.data.ok) {
    err.value = (r.data && r.data.error) || ("读取正文失败 HTTP " + r.status);
    text.value = "";
    savedText.value = "";
    return;
  }
  err.value = "";
  text.value = r.data.content || "";
  savedText.value = text.value;
  mtime.value = r.data.mtime || 0;
  mtimeText.value = r.data.mtime_text || "";
  target.value = r.data.target_words || target.value;
  refineResult.value = null;
  selPara.value = null;
}

async function loadParas() {
  parasLoading.value = true;
  const r = await props.api(
    "/chapters/paragraphs?n=" + props.n + "&layer=" + encodeURIComponent(layer.value));
  parasLoading.value = false;
  if (r.status === 200 && r.data.ok) {
    paras.value = r.data.paragraphs || [];
  } else {
    paras.value = [];
    err.value = (r.data && r.data.error) || "读取段落失败";
  }
}

async function switchLayer(l) {
  if (l === layer.value) return;
  if (dirty.value && !window.confirm("当前改动尚未保存，切换层会丢弃。继续？")) return;
  layer.value = l;
  await loadContent();
  if (mode.value === "batch") await loadParas();
}

async function save() {
  if (busy.value || !dirty.value) return;
  busy.value = true;
  err.value = "";
  warn.value = "";
  try {
    const r = await props.api("/chapters/save", "POST", {
      n: props.n, layer: layer.value, content: text.value, expected_mtime: mtime.value,
    });
    if (r.status === 409) {
      err.value = (r.data && r.data.error) ||
        "这一章刚被其他操作改写过，请重新加载后再保存";
      return;
    }
    if (r.status !== 200 || !r.data.ok) {
      err.value = (r.data && r.data.error) || ("保存失败 HTTP " + r.status);
      return;
    }
    text.value = text.value;            // 服务端已归一化，重新读一次才是真相
    await loadContent();
    if (r.data.title_added) {
      emit("say", "已保存（缺章标题，已按格式自动补上）");
    } else {
      emit("say", "已保存第 " + props.n + " 章" + (LAYER_LABEL[layer.value] || ""));
    }
    if (r.data.warning) warn.value = r.data.warning;
    if (r.data.backup) warn.value = (warn.value ? warn.value + "；" : "") + "旧稿已备份为 " + r.data.backup;
    emit("saved", props.n);
    await loadLayersQuiet();
  } catch (e) {
    err.value = "保存出错：" + (e && e.message ? e.message : e);
  } finally {
    busy.value = false;
  }
}

async function loadLayersQuiet() {
  const r = await props.api("/chapters/layers?n=" + props.n);
  if (r.status === 200 && r.data.ok) {
    layers.value = r.data.layers || {};
    currentLayer.value = r.data.current_layer || currentLayer.value;
  }
}

function pickPara(p) {
  selPara.value = p.index;
  refineResult.value = null;
}

async function submitRefine() {
  if (refining.value) return;
  if (selPara.value === null) {
    emit("say", "先在左边点选一个段落");
    return;
  }
  if (!feedback.value.trim()) {
    emit("say", "请写清楚这一段要怎么改（批注会原样交给模型）");
    return;
  }
  refining.value = true;
  refineElapsed.value = 0;
  refineResult.value = null;
  if (dirty.value) emit("say", "提示：正文有未保存改动，模型改的是盘上那一版");
  clearInterval(refineTimer);
  refineTimer = setInterval(() => { refineElapsed.value += 1; }, 1000);
  try {
    const r = await props.api("/refine/paragraph", "POST", {
      chapter: props.n,
      paragraph_index: selPara.value,
      feedback: feedback.value.trim(),
      dry_run: false,
    });
    if (r.status !== 202) {
      err.value = (r.data && r.data.error) || ("提交失败 HTTP " + r.status);
      return;
    }
    const jid = r.data.job_id;
    emit("say", "已提交段落重写（job " + jid + "）—— 只改第 " + (selPara.value + 1) + " 段");
    let polls = 0;
    await new Promise((resolve) => {
      const poll = setInterval(async () => {
        polls += 1;
        if (polls > 600) {
          clearInterval(poll);
          err.value = "段落重写等待超时（10 分钟），请查看日志";
          resolve();
          return;
        }
        let j;
        try {
          j = await props.api("/jobs/" + jid);
        } catch (e) { return; }
        if (!j || j.status !== 200 || (j.data && j.data.state === "running")) return;
        clearInterval(poll);
        const st = j.data && j.data.state;
        if (st === "ok") {
          refineResult.value = j.data.result || { ok: true };
          feedback.value = "";
        } else {
          err.value = "段落重写失败：" + ((j.data && j.data.result) || "未知错误");
        }
        resolve();
      }, 1500);
    });
    await loadContent();
    await loadLayersQuiet();
    if (mode.value === "batch") await loadParas();
  } catch (e) {
    err.value = "提交出错：" + (e && e.message ? e.message : e);
  } finally {
    clearInterval(refineTimer);
    refineTimer = null;
    refining.value = false;
  }
}

async function switchMode(m) {
  if (m === "batch" && !canBatch.value) {
    emit("say", "批注只对“当前层”" + (currentLayer.value ? "（" + LAYER_LABEL[currentLayer.value] + "）" : "")
      + "开放 —— 不同层的段落数不一样，改错层会改错段");
    return;
  }
  mode.value = m;
  if (m === "batch") await loadParas();
}

function tryClose() {
  if (dirty.value && !window.confirm("正文有未保存的改动，确定关闭？")) return;
  if (refining.value) {
    emit("say", "段落重写还在跑，完成后再关闭（或去看日志）");
    return;
  }
  emit("close");
}

function onKey(e) {
  if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {
    e.preventDefault();
    save();
  }
}

watch(mode, () => { nextTick(() => { if (areaRef.value) areaRef.value.focus(); }); });
// 审阅台里左右栏切换章号 → 同一实例换内容（不重建组件，避免遮罩闪一下）
watch(() => props.n, async (nv, ov) => {
  if (nv === ov) return;
  if (dirty.value && !window.confirm("当前改动尚未保存，切换章节会丢弃。继续？")) return;
  layer.value = "";
  refineResult.value = null;
  selPara.value = null;
  feedback.value = "";
  await loadLayers();
});

onMounted(async () => {
  window.addEventListener("keydown", onKey);
  await loadLayers();
  nextTick(() => { if (areaRef.value) areaRef.value.focus(); });
});
onUnmounted(() => {
  window.removeEventListener("keydown", onKey);
  clearInterval(refineTimer);
});
</script>

<template>
  <div :class="embedded ? 'ce-embed' : 'drawer-mask'"
       @click.self="embedded ? null : tryClose()">
    <div :class="embedded ? 'ce ce-embedded' : 'drawer drawer-xwide ce'">
      <div class="drawer-head">
        <b>第 {{ n }} 章</b>
        <span class="ce-layer-pills">
          <button v-for="l in ['raw', 'checked', 'refined']" :key="l"
                  class="mini ce-pill"
                  :class="{ on: layer === l, ghost: !(layers[l] && layers[l].exists) }"
                  :disabled="!(layers[l] && layers[l].exists)"
                  :title="(layers[l] && layers[l].exists) ? LAYER_HINT[l] : ('没有' + LAYER_LABEL[l])"
                  @click="switchLayer(l)">
            {{ LAYER_LABEL[l] }}
            <span v-if="layers[l] && layers[l].exists" class="ce-pill-n">{{ layers[l].words }}字</span>
          </button>
        </span>
        <span class="ce-wordbadge" :class="wordBadge.cls">{{ wordBadge.text }}</span>
        <span v-if="dirty" class="ce-dirty">未保存</span>
        <span class="spacer"></span>
        <button class="mini" :disabled="refining" :class="{ on: mode === 'edit' }" @click="switchMode('edit')">编辑</button>
        <button class="mini" :disabled="refining" :class="{ on: mode === 'batch' }"
                :title="canBatch ? '按段落挑选并提意见，让模型只重写这一段' : '批注只在当前层开放'"
                @click="switchMode('batch')">段落批注</button>
        <button class="mini primary" :disabled="!dirty || busy" @click="save">
          {{ busy ? "保存中…" : "保存" }}
        </button>
        <button class="mini" :disabled="busy" @click="loadContent">重新加载</button>
        <button v-if="!embedded" class="mini" @click="tryClose">关闭</button>
      </div>

      <div v-if="loading" class="ce-empty">读取中…</div>
      <div v-else-if="err" class="ce-err">{{ err }}</div>

      <div v-else class="ce-body">
        <!-- 左：正文编辑 / 段落挑选 -->
        <div class="ce-main">
          <div v-if="mode === 'edit'" class="ce-edit-wrap">
            <textarea ref="areaRef" v-model="text" class="ce-textarea" spellcheck="false"
                      placeholder="正文（Ctrl+S 保存）。缺“## 第N章”会自动补。"></textarea>
          </div>
          <div v-else class="ce-paras">
            <div v-if="parasLoading" class="meta">读取段落…</div>
            <div v-else-if="!paras.length" class="meta">这一段没有可编辑的段落</div>
            <div v-for="p in paras" :key="p.index" class="ce-para"
                 :class="{ on: selPara === p.index }" @click="pickPara(p)">
              <span class="ce-para-no">{{ p.index + 1 }}</span>
              <span class="ce-para-text">{{ p.text }}</span>
            </div>
          </div>
        </div>

        <!-- 右：批注 / 字数 / 版本信息 -->
        <div class="ce-side">
          <div class="ce-side-block">
            <div class="ce-side-title">这一层</div>
            <div class="meta">{{ LAYER_LABEL[layer] }} · {{ LAYER_HINT[layer] }}</div>
            <div class="meta">字数 {{ words }}<span v-if="target[0]"> / 目标 {{ target[0] }}–{{ target[1] }}</span></div>
            <div v-if="mtimeText" class="meta">最后写入 {{ mtimeText }}</div>
            <div v-if="currentLayer && layer !== currentLayer" class="ce-tip">
              当前稿是“{{ LAYER_LABEL[currentLayer] }}”。你正在改的是{{ LAYER_LABEL[layer] }}，
              批注功能对它关闭（会在“{{ LAYER_LABEL[currentLayer] }}”上改错段）。
            </div>
            <div v-if="warn" class="ce-warn">{{ warn }}</div>
          </div>

          <div class="ce-side-block">
            <div class="ce-side-title">段落批注</div>
            <template v-if="!canBatch">
              <div class="meta">切到“{{ LAYER_LABEL[currentLayer] }}”层才能批注。</div>
            </template>
            <template v-else>
              <div class="meta" v-if="selPara === null">在左边点选一个段落</div>
              <div class="meta" v-else>
                已选第 <b>{{ selPara + 1 }}</b> 段（{{ paras[selPara] ? paras[selPara].chars : 0 }} 字）
              </div>
              <textarea v-model="feedback" class="ce-feedback" :disabled="refining"
                        placeholder="这一段要怎么改？例如：把结尾的总结句删掉，改成她没说出口的动作描写。"></textarea>
              <button class="mini primary ce-batch-btn" :disabled="refining || selPara === null"
                      @click="submitRefine">
                {{ refining ? ("重写中… 已 " + fmtElapsed(refineElapsed)) : "让 AI 只改这一段" }}
              </button>
              <div v-if="refining" class="meta">只重写选中的那一段，其余段落原样保留；完成后正文会自动刷新。</div>
              <div v-if="refineResult" class="ce-ok">
                第 {{ (selPara === null ? 0 : selPara + 1) }} 段已重写
              </div>
            </template>
          </div>

          <div class="ce-side-block">
            <div class="ce-side-title">说明</div>
            <div class="meta">
              保存只落盘：不改阶段状态、不打分、不跑体检。旧稿会自动备份到
              <code>data/chapters/history/</code>，用“章节 → 历史”可回退。
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ce { width: min(1280px, 96vw); height: min(88vh, 900px); display: flex; flex-direction: column; }
/* 内联形态（审阅台中栏）：尺寸由父容器决定，不设遮罩 */
.ce-embed { flex: 1; min-width: 0; min-height: 0; display: flex; }
.ce-embedded { width: 100%; height: 100%; min-height: 0; }
.ce-layer-pills { display: inline-flex; gap: 6px; margin-left: 4px; }
.ce-pill { padding: 2px 8px; }
.ce-pill.on { background: var(--accent, #6b8afd); color: #fff; border-color: transparent; }
.ce-pill-n { opacity: 0.75; margin-left: 4px; font-size: 11px; }
.ce-wordbadge { margin-left: 10px; font-size: 12px; color: var(--muted, #888); }
.ce-wordbadge.low { color: var(--warn, #b8860b); }
.ce-wordbadge.high { color: var(--warn, #b8860b); }
.ce-wordbadge.ok { color: var(--ok, #2e8b57); }
.ce-dirty { margin-left: 8px; font-size: 12px; color: var(--warn, #b8860b); }
.ce-empty, .ce-err { padding: 24px; }
.ce-err { color: var(--danger, #c0392b); }

.ce-body { flex: 1; display: flex; min-height: 0; gap: 12px; padding: 10px 12px 12px; }
.ce-main { flex: 1; min-width: 0; display: flex; }
.ce-edit-wrap { flex: 1; display: flex; }
.ce-textarea {
  flex: 1; width: 100%; resize: none; padding: 14px 16px;
  font-family: "Source Han Serif SC", "Noto Serif SC", "Songti SC", serif;
  font-size: 15px; line-height: 1.9;
  border: 1px solid var(--border); border-radius: 8px;
  background: var(--field-inset, #fff); color: var(--foreground);
}
.ce-paras {
  flex: 1; overflow: auto; border: 1px solid var(--border); border-radius: 8px;
  background: var(--field-inset, #fff);
}
.ce-para {
  display: flex; gap: 10px; padding: 10px 12px; cursor: pointer;
  border-bottom: 1px solid var(--border);
}
.ce-para:hover { background: rgba(107, 138, 253, 0.06); }
.ce-para.on { background: rgba(107, 138, 253, 0.16); }
.ce-para-no {
  flex: none; width: 26px; text-align: right; font-size: 11px;
  color: var(--muted, #888); padding-top: 3px;
}
.ce-para-text { flex: 1; font-size: 14px; line-height: 1.85; white-space: pre-wrap; }

.ce-side { flex: none; width: 300px; overflow: auto; display: flex; flex-direction: column; gap: 12px; }
.ce-side-block {
  padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px;
}
.ce-side-title { font-weight: 600; margin-bottom: 6px; }
.ce-tip {
  margin-top: 6px; padding: 6px 8px; font-size: 12px; line-height: 1.6;
  border-left: 2px solid var(--warn, #b8860b); background: rgba(184, 134, 11, 0.08);
}
.ce-warn {
  margin-top: 6px; padding: 6px 8px; font-size: 12px; line-height: 1.6;
  border-left: 2px solid var(--warn, #b8860b); background: rgba(184, 134, 11, 0.08);
}
.ce-feedback {
  width: 100%; height: 96px; margin-top: 8px; padding: 8px 10px; resize: vertical;
  font-size: 13px; line-height: 1.7;
  border: 1px solid var(--border); border-radius: 6px;
  background: var(--field-inset, #fff); color: var(--foreground);
}
.ce-batch-btn { margin-top: 8px; width: 100%; }
.ce-ok {
  margin-top: 8px; padding: 6px 8px; font-size: 12px;
  border-left: 2px solid var(--ok, #2e8b57); background: rgba(46, 139, 87, 0.1);
}
code { font-size: 11px; }
</style>
