<script setup>
/**
 * 判定正例集（基准集）——2026-11-04，作者拍板 `mofang.judge.benchmark`。
 *
 * 为什么需要它：判定器说"这稿不行"容易，说"我没误报"要有一份**作者认可的稿子**做对照。
 * 为什么必须用户导入：正例 = 作者的审美样本，写死在代码里等于把某个人的标准固化成出厂值。
 *
 * 数据：GET /judge/benchmark（含免责声明）；导入/移除/恢复都是 GUI-only。
 */
import { ref, computed, onMounted } from "vue";
import { API } from "./apiBase.js";
import { postJSON } from "./guiApi.js";

const emit = defineEmits(["say"]);

const data = ref(null);
const busy = ref(false);
const err = ref("");
const hint = ref("");

const name = ref("");
const path = ref("");
const pasted = ref("");
const mode = ref("file");          // file | paste
const accepted = ref(false);

const entries = computed(() => (data.value && data.value.entries) || []);
const removed = computed(() => (data.value && data.value.removed) || []);
const disclaimer = computed(() => (data.value && data.value.disclaimer) || {});
const verified = computed(() => !!(data.value && data.value.verified));

function say(msg) {
  hint.value = msg;
  emit("say", msg);
}

async function get(pathUrl) {
  const r = await fetch(API + pathUrl);
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200) throw new Error(d.error || "HTTP " + r.status);
  return d;
}

async function post(path, body) {
  return postJSON(path, body);
}

async function load() {
  busy.value = true;
  err.value = "";
  try {
    data.value = await get("/judge/benchmark?removed=1");
  } catch (e) {
    err.value = String(e.message || e);
  } finally {
    busy.value = false;
  }
}

async function pickFile() {
  const dlg = window.mofangAPI && window.mofangAPI.openFileDialog;
  if (!dlg) return say("这个环境没有系统文件对话框 —— 直接在输入框里粘路径");
  try {
    const r = await dlg({
      title: "选择一份你觉得写得好的稿子（.md / .txt）",
      properties: ["openFile"],
      filters: [{ name: "文本稿", extensions: ["md", "txt"] }],
    });
    if (r && !r.canceled && r.filePaths && r.filePaths[0]) {
      path.value = r.filePaths[0];
      if (!name.value) {
        const leaf = r.filePaths[0].split(/[\\/]/).pop() || "";
        name.value = leaf.replace(/\.(md|txt)$/i, "");
      }
    }
  } catch (e) {
    say("文件对话框不可用：" + (e && e.message ? e.message : e));
  }
}

async function doImport() {
  if (!accepted.value) return say("先勾选下方确认（版权与使用范围）");
  const body = { name: name.value, accept_license: true };
  if (mode.value === "file") body.path = path.value;
  else body.text = pasted.value;
  if (mode.value === "file" && !body.path) return say("先选择或粘贴文件路径");
  if (mode.value === "paste" && !body.text.trim()) return say("先把稿子粘进文本框");
  busy.value = true;
  err.value = "";
  try {
    const d = await post("/judge/benchmark/import", body);
    say(d.message || "已导入");
    path.value = "";
    pasted.value = "";
    name.value = "";
    accepted.value = false;
    await load();
  } catch (e) {
    err.value = String(e.message || e);
  } finally {
    busy.value = false;
  }
}

async function removeEntry(e) {
  busy.value = true;
  err.value = "";
  try {
    const d = await post("/judge/benchmark/remove", { id: e.id });
    say(d.message || "已移出");
    await load();
  } catch (err2) {
    err.value = String(err2.message || err2);
  } finally {
    busy.value = false;
  }
}

async function restoreEntry(e) {
  busy.value = true;
  err.value = "";
  try {
    const d = await post("/judge/benchmark/restore", { id: e.id });
    say(d.message || "已恢复");
    await load();
  } catch (err2) {
    err.value = String(err2.message || err2);
  } finally {
    busy.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="card">
    <div class="card-head">
      <h3>判定对照稿（基准集）</h3>
      <span v-if="verified" class="pill ok">已配置 {{ entries.length }} 份</span>
      <span v-else class="pill warn">未配置 · “不误报”无法验证</span>
      <span class="spacer"></span>
      <button class="mini" :disabled="busy" @click="load">刷新</button>
    </div>

    <p class="meta">
      对照稿 = 你自己认可的好稿。判定器拿它做对照，才能说“没误报”。
      稿子只存在本机项目目录 <code>data/judge/benchmark/</code>，不上传、不进代码仓库，
      随时可以移除（移除不删原件副本，可恢复）。
    </p>

    <div class="disc">
      <div class="disc-text">{{ disclaimer.text }}</div>
      <label class="ack">
        <input v-model="accepted" type="checkbox" :disabled="busy" />
        我已阅读并确认（版本 {{ disclaimer.version }}）
      </label>
    </div>

    <div class="form">
      <div class="tabs-sub">
        <button :class="{ on: mode === 'file' }" @click="mode = 'file'">选文件</button>
        <button :class="{ on: mode === 'paste' }" @click="mode = 'paste'">粘贴正文</button>
      </div>

      <div v-if="mode === 'file'" class="row">
        <input v-model="path" class="grow" placeholder="稿件路径（.md / .txt），或点右侧选文件" />
        <button class="mini" :disabled="busy" @click="pickFile">选文件</button>
      </div>
      <textarea
        v-else
        v-model="pasted"
        class="grow"
        rows="5"
        placeholder="把对照稿正文粘在这里（至少 300 字）"
      ></textarea>

      <div class="row">
        <input v-model="name" class="grow" placeholder="给它起个名字（如：圣所改写稿-雨夜段）" />
        <button class="mini primary" :disabled="busy || !accepted" @click="doImport">导入</button>
      </div>
    </div>

    <div v-if="err" class="err">{{ err }}</div>
    <div v-if="hint" class="meta">{{ hint }}</div>

    <div v-if="entries.length" class="list">
      <div v-for="e in entries" :key="e.id" class="item">
        <span class="name">{{ e.name }}</span>
        <span class="meta">{{ e.chars }} 字 · {{ e.imported_at }}</span>
        <span class="spacer"></span>
        <button class="mini bad-btn" :disabled="busy" @click="removeEntry(e)">移除</button>
      </div>
    </div>
    <div v-else class="meta">还没有对照稿。</div>

    <details v-if="removed.length" class="removed">
      <summary>已移除（{{ removed.length }}）—— 可恢复</summary>
      <div v-for="e in removed" :key="e.id" class="item">
        <span class="name">{{ e.name }}</span>
        <span class="meta">{{ e.chars }} 字</span>
        <span class="spacer"></span>
        <button class="mini" :disabled="busy" @click="restoreEntry(e)">恢复</button>
      </div>
    </details>
  </div>
</template>

<style scoped>
.card { margin-bottom: 12px; }
.disc { border: 1px solid var(--border); border-radius: 6px; padding: 8px; margin: 8px 0; }
.disc-text { font-size: 12px; color: var(--muted-foreground); line-height: 1.6; }
.ack { display: flex; align-items: center; gap: 6px; margin-top: 6px; font-size: 12px; }
.form { display: flex; flex-direction: column; gap: 6px; }
/* 输入框外观（2026-11-11，第三次提同一处）：
   此前 .grow 只管宽度，这两个框是**浏览器默认样式**（白底、系统边框、内边距不对），
   跟全站输入框（--field 底 + 6px 圆角 + 1px 边框 + 13px 字）放在一起就格格不入。
   根因是这个组件没引项目里那套控件外观 —— 不是配色问题，是没定义。 */
.form input:not([type="checkbox"]),
.form textarea {
  padding: 8px 10px;
  font-size: 13px;
  line-height: 1.6;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--field);
  color: var(--ink);
}
.form input:not([type="checkbox"]):focus,
.form textarea:focus { outline: none; border-color: var(--accent); }
/* 一行 = 一个输入框 + 一个按钮：必须**同高**（原 align-items:center + 各自 padding，
   导致右侧按钮比输入框矮一截，看着像悬浮在半空）。 */
.row { display: flex; gap: 6px; align-items: stretch; }
.grow { flex: 1; min-width: 0; }
.item { display: flex; align-items: center; gap: 8px; padding: 4px 0; border-top: 1px solid var(--border); }
.name { font-weight: 600; }
.err { color: var(--bad); margin: 6px 0; }
.bad-btn { color: var(--bad); }
.removed { margin-top: 8px; }
</style>
