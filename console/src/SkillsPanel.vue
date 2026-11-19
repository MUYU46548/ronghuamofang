<script setup>
/**
 * 写作手法（用户导入的 Skill，2026-11-19）。
 *
 * 要治的病：作者的写法（视角、节奏、忌讳、句式偏好）没有地方存，只能每次跟 agent 讲一遍。
 * 但**内置文风预设已被作者 2026-10-20 砍掉**（文风只能他自己定），所以这里只提供
 * “把自己写的规则装进来”的口子，一行模板都不预置。
 *
 * 数据源：
 *   GET  /skills                    清单（现扫 materials/skills/，不带正文）
 *   GET  /skills/detail?id=x        单份全文（装前必须看过）
 *   POST /skills/create             新建（写完仍是“未确认”）
 *   POST /skills/approve            看过全文后确认（记 sha；改过自动失效）
 *   POST /skills/binding            启用/停用 + 绑定阶段（write / revise）
 *   POST /skills/delete             移到回收目录（不删）
 *
 * 四条界面口径（都不是装饰）：
 *   1. 未确认 → 红字明说“不会注入”，不许让作者以为已经生效；
 *   2. 有问题（含脚本 / 超字数 / 缺 SKILL.md）→ 整份不注入，问题原样列出来；
 *   3. 删除写“移到回收”，不写“删除”；
 *   4. 只列 write / revise 两个阶段 —— 其他阶段没有注入点，不摆假按钮。
 */
import { ref, computed, onMounted } from "vue";

const props = defineProps({ api: { type: Function, required: true } });
const emit = defineEmits(["say"]);
function say(m) { emit("say", m); }

const items = ref([]);
const stagesAll = ref({ write: "写稿", revise: "润色" });
const maxSkill = ref(4000);
const maxTotal = ref(4000);
const baseDir = ref("materials/skills");
const busy = ref(false);
const err = ref("");

const detail = ref(null);       // 正在看的那份（含正文）
const showNew = ref(false);
const draft = ref({ id: "", name: "", stages: ["write"], text: "" });

const stageList = computed(() => Object.keys(stagesAll.value || {}));
const draftChars = computed(() => (draft.value.text || "").trim().length);
const draftOver = computed(() => draftChars.value > maxSkill.value);

async function load() {
  err.value = "";
  busy.value = true;
  try {
    const r = await props.api("/skills");
    if (r.status === 200 && r.data?.ok) {
      items.value = r.data.skills || [];
      stagesAll.value = r.data.stages || stagesAll.value;
      maxSkill.value = r.data.max_skill_chars || 4000;
      maxTotal.value = r.data.max_total_chars || 4000;
      baseDir.value = r.data.dir || baseDir.value;
    } else {
      err.value = r.data?.error || ("读取失败：HTTP " + r.status);
    }
  } catch (e) {
    err.value = "读取失败：" + String(e);
  }
  busy.value = false;
}

async function open(id) {
  try {
    const r = await props.api("/skills/detail?id=" + encodeURIComponent(id));
    if (r.status === 200 && r.data?.ok) detail.value = r.data;
    else say("打不开：" + (r.data?.error || ("HTTP " + r.status)));
  } catch (e) {
    say("打不开：" + String(e));
  }
}

async function act(path, body, okMsg) {
  if (busy.value) return;
  busy.value = true;
  try {
    const r = await props.api(path, "POST", body);
    if (r.status === 200 && r.data?.ok) {
      say(r.data.message || okMsg);
      await load();
      if (detail.value && body.id === detail.value.id) await open(body.id);
      else if (detail.value && !items.value.some((x) => x.id === detail.value.id)) detail.value = null;
    } else {
      say("操作失败：" + (r.data?.error || r.data?.message || ("HTTP " + r.status)));
    }
  } catch (e) {
    say("操作失败：" + String(e));
  }
  busy.value = false;
}

function toggleStage(it, st) {
  const cur = it.stages || [];
  const next = cur.includes(st) ? cur.filter((x) => x !== st) : cur.concat([st]);
  if (!next.length) { say("至少要绑一个阶段；要停用请用左边的开关"); return; }
  act("/skills/binding", { id: it.id, stages: next }, "已改绑定");
}

async function create() {
  if (!draft.value.id.trim()) { say("先给这份写法起一个 id（字母数字短横）"); return; }
  if (draftOver.value) { say("太长了：先压到 " + maxSkill.value + " 字以内"); return; }
  await act("/skills/create", {
    id: draft.value.id.trim(),
    name: draft.value.name.trim() || draft.value.id.trim(),
    stages: draft.value.stages,
    text: draft.value.text,
  }, "已新建");
  if (!err.value && items.value.some((x) => x.id === draft.value.id.trim())) {
    showNew.value = false;
    draft.value = { id: "", name: "", stages: ["write"], text: "" };
  }
}

const state = (it) => {
  if (it.problems && it.problems.length && it.problems.some((p) => !/还没确认过|被改过/.test(p))) return "bad";
  if (!it.approved) return "wait";
  if (!it.enabled) return "off";
  return "on";
};

onMounted(load);
defineExpose({ load });
</script>

<template>
  <div class="card">
    <div class="card-head">
      <h3>写作手法（自己导入）</h3>
      <span class="meta">纯文本 · 确认后生效 · 按阶段绑定 · 不调模型</span>
      <span class="spacer"></span>
      <button class="mini" :disabled="busy" @click="load">{{ busy ? "读取中…" : "刷新" }}</button>
      <button class="mini" :disabled="busy" @click="showNew = !showNew; detail = null">
        {{ showNew ? "收起" : "新建一份" }}
      </button>
    </div>
    <div class="meta">
      放你自己的写法：<code>{{ baseDir }}/&lt;名字&gt;/SKILL.md</code>，或在这里粘贴新建。
      只会注入到绑定的阶段（<b>写稿</b> / <b>润色</b>），没绑不生效。
      <b>只收纯文本</b>：目录里出现脚本文件整份拒装；字数、退化、复读这些硬规矩照样管着它。
    </div>

    <div v-if="err" class="empty" style="padding:12px 0;">{{ err }}</div>

    <!-- 新建 -->
    <div v-if="showNew" class="sk-new">
      <div class="sk-row">
        <label>id</label>
        <input v-model="draft.id" placeholder="cold-tone（字母数字短横，会当目录名）" />
        <label>显示名</label>
        <input v-model="draft.name" placeholder="冷峻短句" />
      </div>
      <div class="sk-row">
        <label>绑定阶段</label>
        <label v-for="s in stageList" :key="s" class="sk-chk">
          <input type="checkbox" :value="s"
                 :checked="draft.stages.includes(s)"
                 @change="draft.stages = draft.stages.includes(s) ? draft.stages.filter(x => x !== s) : draft.stages.concat([s])" />
          {{ stagesAll[s] }}
        </label>
        <span class="spacer"></span>
        <span :class="{ 'sk-over': draftOver }">{{ draftChars }} / {{ maxSkill }} 字</span>
      </div>
      <textarea v-model="draft.text" class="sk-input" spellcheck="false"
                placeholder="一条一行，写“必须”和“禁止”：&#10;&#10;必须：全篇第三人称限知，只写主视角能看见的&#10;禁止：用“她感到/他知道”解释心理，用动作代替&#10;句长：多数句子不超过 25 字"></textarea>
      <div class="sk-row">
        <span class="meta">新建后是<b>未确认</b>状态：看一遍全文点确认才会注入。</span>
        <span class="spacer"></span>
        <button class="mini" :disabled="busy || draftOver" @click="create">新建</button>
      </div>
    </div>

    <!-- 列表 -->
    <div v-if="!items.length && !showNew" class="sk-empty">
      还没有导入任何写法。没有不是问题 —— 不用它也能写，只是这本书的写法得你每次亲口讲。
    </div>

    <div v-for="it in items" :key="it.id" class="sk-item" :class="'sk-' + state(it)">
      <div class="sk-head">
        <b>{{ it.name }}</b>
        <span class="meta">{{ it.id }} · {{ it.chars }} 字</span>
        <span class="sk-badge">{{ { on: "生效中", wait: "未确认", off: "已停用", bad: "装坏了" }[state(it)] }}</span>
        <span class="spacer"></span>
        <label class="sk-chk" v-for="s in stageList" :key="s">
          <input type="checkbox" :value="s" :checked="(it.stages || []).includes(s)"
                 :disabled="busy" @change="toggleStage(it, s)" />
          {{ stagesAll[s] }}
        </label>
        <button class="mini" :disabled="busy" @click="it.enabled ? act('/skills/binding', { id: it.id, enabled: false }, '已停用') : act('/skills/binding', { id: it.id, enabled: true }, '已启用')">
          {{ it.enabled ? "停用" : "启用" }}
        </button>
        <button class="mini" :disabled="busy" @click="open(it.id)">看全文</button>
        <button class="mini" :disabled="busy" @click="act('/skills/delete', { id: it.id }, '已移到回收')">移到回收</button>
      </div>
      <ul v-if="it.problems && it.problems.length" class="sk-prob">
        <li v-for="(p, i) in it.problems" :key="i">{{ p }}</li>
      </ul>
      <div v-if="!it.approved && it.problems && it.problems.length === 0" class="meta">
        未确认：不会注入。
      </div>

      <!-- 全文 -->
      <div v-if="detail && detail.id === it.id" class="sk-detail">
        <div class="meta">
          全文 {{ detail.chars }} 字（确认后才会进任务；之后再改文件需要重新确认）
        </div>
        <pre class="sk-text">{{ detail.text }}</pre>
        <div class="sk-row">
          <span class="spacer"></span>
          <button class="mini" :disabled="busy || state(it) === 'bad'" @click="act('/skills/approve', { id: it.id }, '已确认')">
            我看过了，确认
          </button>
          <button class="mini" @click="detail = null">收起</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sk-new, .sk-item {
  margin-top: 10px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--field-dim);
}
.sk-item { background: var(--field); }
.sk-item.sk-bad { border-color: var(--bad); }
.sk-item.sk-wait { border-color: var(--warn); }
.sk-head { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.sk-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 8px 0; font-size: 12.5px; }
.sk-row label { color: var(--muted); }
.sk-row input[type="text"], .sk-row input:not([type]) {
  padding: 5px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--field);
  color: var(--ink);
  font-family: inherit;
  font-size: 12.5px;
}
.sk-chk { display: inline-flex; gap: 4px; align-items: center; color: var(--muted); font-size: 12px; }
.sk-badge {
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 11px;
  background: var(--field-dim);
  color: var(--muted);
  border: 1px solid var(--border);
}
.sk-on .sk-badge { color: var(--ok); border-color: var(--ok); }
.sk-wait .sk-badge { color: var(--warn); border-color: var(--warn); }
.sk-bad .sk-badge { color: var(--bad); border-color: var(--bad); }
.sk-prob {
  margin: 6px 0 0;
  padding-left: 18px;
  color: var(--bad);
  font-size: 12px;
  line-height: 1.7;
}
.sk-input {
  width: 100%;
  min-height: 180px;
  box-sizing: border-box;
  padding: 10px 12px;
  font-size: 13px;
  line-height: 1.75;
  font-family: inherit;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--field);
  color: var(--ink);
  resize: vertical;
}
.sk-input:focus { outline: none; border-color: var(--accent); }
.sk-over { color: var(--bad); }
.sk-empty {
  margin: 8px 0;
  padding: 10px 12px;
  border-left: 3px solid var(--warn);
  border-radius: 8px;
  background: var(--field-dim);
  font-size: 12.5px;
  line-height: 1.7;
}
.sk-detail { margin-top: 8px; border-top: 1px dashed var(--border); padding-top: 8px; }
.sk-text {
  max-height: 320px;
  overflow: auto;
  margin: 6px 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--field-dim);
  color: var(--ink);
  font-size: 12.5px;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
