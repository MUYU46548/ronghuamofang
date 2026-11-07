<template>
  <div class="wl-panel">
    <h4>词表（病灶词 / 合规词）</h4>
    <div class="meta wl-intro">
      两张表都是一行一条，<code>#</code> 开头是注释，<code>regex:</code> 前缀表示按正则匹配。
      改完点“保存”就存进 <code>data/wordlists/</code>（属于你的私有数据，不进 Git）。
      <b>清空某张表 = 不用自定义表</b>：病灶词会回落到出厂内置 20 条并在下面提示，
      合规词则不检查。
    </div>

    <div v-if="loading" class="meta">读取中…</div>
    <div v-else-if="err" class="wl-err">{{ err }}</div>
    <template v-else>
      <div class="wl-grid">
        <div class="wl-col">
          <div class="wl-head">
            <b>病灶词</b>
            <span class="pill" :class="cliche.source === 'gui' ? 'st-done' : 'st-pending'">
              {{ cliche.source_label }}
            </span>
            <span class="meta">这行字里 {{ countOf(cliche.text) }} 条 · 实际生效 {{ cliche.terms }} 条</span>
          </div>
          <div class="meta wl-note">
            你希望系统标出来的“AI 腔”套话（例：微微一笑、眼中闪过一丝）。
            它只出现在扫描报告里（<code>nfctl cliche</code> / 成书提炼），<b>不会自动改你的正文</b>。
          </div>
          <textarea v-model="cliche.text" class="wl-text" spellcheck="false"
                    placeholder="一行一条，例如：&#10;微微一笑&#10;眼中闪过一丝&#10;regex:仿佛[^，。！？]{0,12}一般"></textarea>
          <div class="meta wl-warn" v-if="cliche.warning">⚠ {{ cliche.warning }}</div>
          <div class="meta wl-err" v-if="cliche.error">{{ cliche.error }}</div>
        </div>

        <div class="wl-col">
          <div class="wl-head">
            <b>合规词</b>
            <span class="pill" :class="compliance.source === 'gui' ? 'st-done' : 'st-pending'">
              {{ compliance.source_label }}
            </span>
            <span class="meta">这行字里 {{ countOf(compliance.text) }} 条 · 实际生效 {{ compliance.terms }} 条</span>
          </div>
          <div class="meta wl-note">
            你自己定的禁词（人名、题材、平台要求都行）。校对时命中就<b>列出来给你看</b>，
            只提醒、不拦截验收 —— 写不写由你判断。
          </div>
          <textarea v-model="compliance.text" class="wl-text" spellcheck="false"
                    placeholder="一行一条，例如：&#10;（某个不想出现的词）&#10;（某个必须检查的称呼）"></textarea>
          <div class="meta wl-warn" v-if="compliance.warning">⚠ {{ compliance.warning }}</div>
          <div class="meta wl-err" v-if="compliance.error">{{ compliance.error }}</div>
        </div>
      </div>

      <div class="wl-actions">
        <button class="mini" @click="fillBuiltin" title="把出厂内置的 20 条病灶词填进左边，方便你在此基础上改">
          填入内置 20 条
        </button>
        <button class="mini" :disabled="busy" @click="load">重新读取</button>
        <span class="spacer"></span>
        <span v-if="dirty" class="pill st-gate">未保存</span>
        <span class="meta" v-if="msg">{{ msg }}</span>
        <button class="mini primary" :disabled="busy || !dirty" @click="save">
          {{ busy ? "保存中…" : "保存" }}
        </button>
      </div>
      <div class="meta wl-path">
        文件位置：<code>{{ cliche.path }}</code> · <code>{{ compliance.path }}</code>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";

const props = defineProps({ api: { type: Function, required: true } });
const emit = defineEmits(["say"]);

// 内置 20 条：与后端 utils/book_extract/wordlist.DEFAULT_WORDLIST 同源。
// 前端只做“一键填空”这个便利动作 —— 真正生效的永远是盘上那份文件，
// 所以这里硬编码一份不算第二真源（填进去也要保存才生效）。
const BUILTIN = [
  "眼底闪过一丝", "勾唇", "眸光", "薄唇轻启", "邪魅一笑",
  "似有若无", "莫名的", "一丝", "regex:仿佛[^，。！？\\n]{0,12}一般", "如墨的长发",
  "语气不禁", "心中一动", "面色一变", "淡淡地道", "微微一笑",
  "深吸一口气", "目光深邃", "嘴角勾起", "萦绕", "氤氲",
];

const loading = ref(true);
const busy = ref(false);
const err = ref("");
const msg = ref("");
const cliche = ref({ text: "", terms: 0, source: "", source_label: "", warning: "", error: "", path: "" });
const compliance = ref({ text: "", terms: 0, source: "", source_label: "", warning: "", error: "", path: "" });
const orig = ref({ cliche: "", compliance: "" });

const dirty = computed(() => cliche.value.text !== orig.value.cliche
  || compliance.value.text !== orig.value.compliance);

function payload(r) {
  if (!r || r.status !== 200 || (r.data && r.data.ok === false)) {
    const e = (r && r.data && r.data.error) || (r && r.status) || "请求失败";
    throw new Error(String(e));
  }
  return r.data || {};
}

/** 只数“会生效的行”（跳过空行与注释），与后端解析口径一致。 */
function countOf(text) {
  return String(text || "").split(/\r?\n/)
    .map((s) => s.trim())
    .filter((s) => s && !s.startsWith("#")).length;
}

function apply(d) {
  const strip = (o) => ({
    text: o.text || "", terms: o.terms || 0, source: o.source || "",
    source_label: o.source_label || "", warning: o.warning || "",
    error: o.error || "", path: o.path || "",
  });
  cliche.value = strip(d.cliche || {});
  compliance.value = strip(d.compliance || {});
  orig.value = { cliche: cliche.value.text, compliance: compliance.value.text };
}

async function load() {
  loading.value = true;
  err.value = "";
  msg.value = "";
  try {
    apply(payload(await props.api("/config/wordlists")));
  } catch (e) {
    err.value = "读取词表失败：" + e.message;
  } finally {
    loading.value = false;
  }
}

function fillBuiltin() {
  cliche.value.text = BUILTIN.join("\n") + "\n";
  emit("say", "已把内置 20 条填进词表，改完记得点保存（不保存不生效）");
}

async function save() {
  busy.value = true;
  msg.value = "";
  try {
    const d = payload(await props.api("/config/wordlists", "POST", {
      cliche: cliche.value.text,
      compliance: compliance.value.text,
    }));
    // 用服务器回读的结果刷新（生效档位/条数由后端算），不要拿本地变量当地真源
    apply(d);
    msg.value = d.message || "已保存";
    emit("say", msg.value + "（下一次扫描/校对生效）");
  } catch (e) {
    msg.value = "保存失败：" + e.message;
  } finally {
    busy.value = false;
  }
}

onMounted(load);
</script>

<style scoped>
.wl-panel { margin-top: 16px; }
.wl-intro { line-height: 1.75; margin-bottom: 10px; }
.wl-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
@media (max-width: 980px) { .wl-grid { grid-template-columns: 1fr; } }
.wl-col { min-width: 0; display: flex; flex-direction: column; gap: 6px; }
.wl-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.wl-note { line-height: 1.7; }
.wl-text {
  width: 100%; min-height: 190px; resize: vertical; padding: 10px 12px;
  font-family: "Cascadia Mono", Consolas, "Microsoft YaHei Mono", monospace;
  font-size: 12.5px; line-height: 1.7;
  border: 1px solid var(--border); border-radius: 8px;
  background: var(--field-inset, #fff); color: var(--ink);
}
.wl-warn { color: var(--warn, #b8860b); line-height: 1.6; }
.wl-err, .wl-panel .wl-err { color: var(--bad); line-height: 1.6; }
.wl-actions { display: flex; align-items: center; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
.wl-path { margin-top: 6px; opacity: 0.8; }
.st-done { color: var(--ok, #2e8b57); }
.st-pending { color: var(--muted, #888); }
</style>
