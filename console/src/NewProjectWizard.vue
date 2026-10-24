<script setup>
// 新建项目向导（一键创建）：⓪ 选预设 → ① 基本信息 → ② 处理当前工作区 → ③ 确认创建。
// 选了出厂预设：铺设素材 + 写配置（走 preset_apply），书名/章数/题材带出可改。
// 不选：走原有纯手工路径，行为与合并前逐字一致。
// 后端 /project/create 会：快照 → 归档当前项目 → 初始化空工作区 → 写 project.yaml。
import { ref, computed, watch } from "vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  api: { type: Function, required: true },
  current: { type: String, default: "" },
  hasWork: { type: Boolean, default: false },   // 当前工作区是否有数据
  defaults: { type: Object, default: () => ({}) },
});
const emit = defineEmits(["close", "created"]);

const step = ref(0);
const busy = ref(false);
const err = ref("");
const form = ref({ name: "", genre: "奇幻", chapters: 12, style_notes: "", archive_current: true });
const touched = ref({ name: false, chapters: false });   // 未交互不报错（别一打开就飘红）

// ⓪ 出厂预设（2026-10-21，roadmap v0.20 首跑向导）
const factoryPresets = ref([]);
const presetNote = ref("");
const pickedPreset = ref(null);      // 选中的预设行（含 name/chapters/genre/id）
const presetBusy = ref(false);
const presetErr = ref("");
const presetApplied = ref(false);    // 已铺设成功 → 书名/章数从预设带出

const GENRES = ["奇幻", "科幻", "都市", "悬疑", "历史", "言情", "仙侠", "其他"];

async function loadPresets() {
  const r = await props.api("/factory/presets");
  if (r.status === 200 && r.data?.ok) {
    factoryPresets.value = r.data.presets || [];
    presetNote.value = r.data.note || "";
  } else {
    presetErr.value = r.data?.error || ("HTTP " + r.status);
  }
}

function pickPreset(p) {
  pickedPreset.value = p;
  presetApplied.value = false;
  // 书名/章数/题材从预设带出，仍可改（预设是起点不是枷锁）
  form.value.name = p.name || "";
  if (p.chapters) form.value.chapters = p.chapters;
  if (p.genre) form.value.genre = p.genre;
  touched.value = { name: false, chapters: false };
}

async function applyPicked() {
  if (!pickedPreset.value || presetBusy.value) return;
  presetBusy.value = true;
  presetErr.value = "";
  const r = await props.api("/factory/preset/apply", "POST", {
    id: pickedPreset.value.id,
    archive_current: form.value.archive_current,
  });
  presetBusy.value = false;
  if (r.status === 200 && r.data?.ok) {
    presetApplied.value = true;
  } else {
    presetErr.value = r.data?.error || r.data?.message || ("HTTP " + r.status);
  }
}

watch(() => props.open, (v) => {
  if (!v) return;
  step.value = 0;
  busy.value = false;
  err.value = "";
  touched.value = { name: false, chapters: false };
  pickedPreset.value = null;
  presetApplied.value = false;
  presetErr.value = "";
  form.value = {
    name: "",
    genre: props.defaults?.genre || "奇幻",
    chapters: props.defaults?.chapters || 12,
    style_notes: "",
    archive_current: true,   // 默认归档，最安全
  };
  loadPresets();
});

const nameErr = computed(() => {
  const n = (form.value.name || "").trim();
  if (pickedPreset.value && presetApplied.value && !n) return "";  // 预设已带出书名
  if (!n) return "请填写书名";
  if (/[\\/:*?"<>|]/.test(n)) return "书名不能包含 \\ / : * ? \" < > | 这些字符";
  return "";
});
const chErr = computed(() => {
  const c = Number(form.value.chapters);
  if (!Number.isInteger(c) || c < 1 || c > 999) return "章节数须为 1-999 的整数";
  return "";
});
const step1Ok = computed(() => !nameErr.value && !chErr.value);
// 选了预设但还没铺设 → 不能进下一步（否则书名/章数是空的）
const step0Ok = computed(() => !pickedPreset.value || presetApplied.value);
// 第 2 步：有数据却没勾归档 → 创建必被后端 400 拒（绝不静默清空书稿），
// 所以按钮直接置灰，不让用户走到「确认创建」再被弹回来（2026-10-24 用户点名）。
// 已铺设预设的例外：旧项目在铺设时就归档过了，工作区本身就是新项目，无需再勾。
const step2Ok = computed(() =>
  presetApplied.value || !props.hasWork || form.value.archive_current);

function next() {
  if (step.value === 0 && !step0Ok.value) return;   // 选了预设还没铺设 → 不放行
  if (step.value === 1 && !step1Ok.value) return;
  if (step.value === 2 && !step2Ok.value) return;
  step.value += 1;
}
function prev() { if (step.value > 0) step.value -= 1; }

async function submit() {
  if (busy.value) return;
  busy.value = true;
  err.value = "";
  const r = await props.api("/project/create", "POST", {
    name: form.value.name.trim(),
    genre: form.value.genre,
    chapters: Number(form.value.chapters),
    style_notes: form.value.style_notes,
    archive_current: form.value.archive_current,
    // 第 0 步已铺设预设 → 后端跳过归档/初始化，保住刚铺的素材
    preset_applied: presetApplied.value,
  });
  busy.value = false;
  if (r.status === 200 && r.data?.ok) {
    emit("created", r.data.message || "项目已创建");
  } else {
    err.value = r.data?.error || r.data?.message || ("HTTP " + r.status);
    step.value = 2;   // 回退到「当前工作区」这一步，多半是没勾归档/归档失败
  }
}
</script>

<template>
  <div v-if="open" class="drawer-mask" @click.self="!busy && $emit('close')">
    <div class="dialog" style="width: min(620px, 94vw);">
      <div style="display:flex; align-items:center; gap:10px; margin-bottom:6px;">
        <h3 style="margin:0;">新建项目</h3>
        <span class="meta">{{ step }} / 4</span>
        <span class="spacer"></span>
        <button class="mini" :disabled="busy" @click="$emit('close')">取消</button>
      </div>
      <div class="wiz-steps">
        <span class="wiz-step" :class="{ on: step === 0, done: step > 0 }">0 选预设</span>
        <span class="wiz-step" :class="{ on: step === 1, done: step > 1 }">1 基本信息</span>
        <span class="wiz-step" :class="{ on: step === 2, done: step > 2 }">2 当前工作区</span>
        <span class="wiz-step" :class="{ on: step === 3 }">3 确认创建</span>
      </div>

      <!-- 步骤 0：选预设（可跳过） -->
      <template v-if="step === 0">
        <div class="meta" style="line-height:1.8; margin-bottom:10px;">
          出厂预设自带<b>配套素材 + 配置</b>（题材、章数、故事种子），选中即铺设，
          <b>试跑才有素材可写</b>。也可跳过，走下方纯手工填写。
        </div>

        <div v-if="presetErr" class="wiz-warn" style="margin-bottom:8px;">{{ presetErr }}</div>

        <div v-if="factoryPresets.length" class="wiz-presets">
          <div v-for="p in factoryPresets" :key="p.id"
               class="wiz-preset" :class="{ on: pickedPreset && pickedPreset.id === p.id }"
               @click="pickPreset(p)">
            <div class="wiz-preset-name">{{ p.name || p.id }}</div>
            <div class="meta">{{ p.genre }} · {{ p.chapters }} 章
              <span v-if="p.summary">· {{ p.summary }}</span>
            </div>
          </div>
        </div>
        <div v-else-if="!presetErr" class="meta" style="margin-bottom:8px;">
          {{ presetNote || "没有可用的出厂预设（examples/presets/ 为空）。直接下一步手工填写即可。" }}
        </div>

        <div v-if="pickedPreset" class="wiz-preset-act">
          <template v-if="!presetApplied">
            <button class="mini primary" :disabled="presetBusy" @click="applyPicked">
              {{ presetBusy ? "铺设中…" : "铺设「" + (pickedPreset.name || pickedPreset.id) + "」" }}
            </button>
            <span class="meta">会快照并归档当前项目，再铺入素材 + 写配置</span>
          </template>
          <template v-else>
            <span class="pill st-done">已铺设</span>
            <span class="meta">素材与配置已就位；书名/章数已带出，仍可在下一步修改</span>
          </template>
        </div>
        <div v-if="presetErr && pickedPreset" class="wiz-warn" style="margin-top:8px;">
          铺设失败：{{ presetErr }}
        </div>
      </template>

      <!-- 步骤 1：基本信息 -->
      <template v-else-if="step === 1">
        <label class="wiz-label">书名 *</label>
        <input v-model="form.name" class="text-input" style="width:100%;" placeholder="如：示例书名"
               @input="touched.name = true" @blur="touched.name = true" @keyup.enter="next" />
        <div v-if="touched.name && nameErr" class="wiz-err">{{ nameErr }}</div>

        <div class="wiz-grid">
          <div>
            <label class="wiz-label">类型</label>
            <select v-model="form.genre" class="model-select" style="width:100%;">
              <option v-for="g in GENRES" :key="g" :value="g">{{ g }}</option>
            </select>
          </div>
          <div>
            <label class="wiz-label">章节数 *</label>
            <input v-model.number="form.chapters" type="number" min="1" max="999"
                   class="text-input" style="width:100%;"
                   @input="touched.chapters = true" @blur="touched.chapters = true" />
          </div>
        </div>
        <div v-if="touched.chapters && chErr" class="wiz-err">{{ chErr }}</div>

        <label class="wiz-label">风格笔记（可选，写入 config/project.yaml 的 book.style_notes）</label>
        <textarea v-model="form.style_notes" rows="3" class="prompt-text" style="width:100%;"
                  placeholder="如：多用短句，少用形容词；对白带方言味。留空则完全不注入。"></textarea>
        <div class="meta" style="margin-top:6px;">创建后仍可在「设置」页签随时修改。</div>
      </template>

      <!-- 步骤 2：当前工作区 -->
      <template v-else-if="step === 2">
        <div class="meta" style="line-height:1.9; margin-bottom:10px;">
          当前项目：<b>{{ current || "（未命名）" }}</b>
          <span v-if="presetApplied" class="pill st-done">已铺设预设</span>
          <span v-else-if="hasWork" class="pill st-gate">工作区有数据</span>
          <span v-else class="pill">工作区为空</span>
        </div>

        <!-- 已铺设预设：旧项目在铺设时就归档过了，工作区本身就是新项目 -->
        <template v-if="presetApplied">
          <div class="meta" style="line-height:1.9;">
            出厂预设「<b>{{ (pickedPreset && (pickedPreset.name || pickedPreset.id)) || "" }}</b>」已铺设：
            素材与配置都在工作区里，<b>原项目已在铺设时归档</b>。
            本次创建<b>只改写书名/章数等信息，不会动素材</b>，直接下一步即可。
          </div>
        </template>

        <template v-else-if="hasWork">
          <label class="wiz-check">
            <input type="checkbox" v-model="form.archive_current" />
            <span>把当前项目归档为「<b>{{ current || form.name }}</b>」再新建
              <span class="meta">（推荐：书稿完整保留在 data/books/，可随时在「项目」页签恢复）</span>
            </span>
          </label>
          <div v-if="!form.archive_current" class="wiz-warn">
            未勾选归档时无法继续：工作区还有数据，创建会被拒绝（系统不会静默清空书稿）。
            勾选上面的归档即可继续。
          </div>
          <div class="meta" style="margin-top:10px;">
            创建前会自动快照一次（history/），归档失败则整个操作中止、不做任何改动。
          </div>
        </template>
        <div v-else class="meta">
          工作区是空的，无需归档，直接创建即可。
        </div>

        <div v-if="err" class="wiz-warn" style="margin-top:10px;">创建失败：{{ err }}</div>
      </template>

      <!-- 步骤 3：确认 -->
      <template v-else>
        <table class="cost-table">
          <tbody>
            <tr><td style="width:110px;">书名</td><td><b>{{ form.name.trim() }}</b></td></tr>
            <tr><td>类型</td><td>{{ form.genre }}</td></tr>
            <tr><td>章节数</td><td>{{ form.chapters }}</td></tr>
            <tr><td>风格笔记</td><td>{{ form.style_notes.trim() || "（不注入）" }}</td></tr>
            <tr><td>当前项目</td><td>
              {{ presetApplied ? "已铺设预设（原项目已归档），素材保留"
                 : hasWork ? (form.archive_current ? "归档为「" + (current || form.name) + "」" : "不归档（会被拒绝）")
                         : "空工作区，无需处理" }}
            </td></tr>
          </tbody>
        </table>
        <div class="meta" style="margin-top:10px;">
          {{ presetApplied
             ? "确认后将执行：只改写 config/project.yaml（工作区与素材原样保留）。"
             : "确认后将执行：快照 → 归档当前项目（如勾选）→ 初始化空工作区 → 写入 config/project.yaml。" }}
          之后到「流水线」页签点「执行下一步」即可开跑。
        </div>
        <div v-if="err" class="wiz-warn" style="margin-top:8px;">{{ err }}</div>
      </template>

      <div class="dialog-actions">
        <span v-if="step === 0 && !step0Ok" class="meta">已选预设 → 先点「铺设」才能继续（或取消选择走手工）</span>
        <span v-else-if="step === 1 && !step1Ok" class="meta">填写书名（1-999 章）后才能继续</span>
        <span v-else-if="step === 2 && !step2Ok" class="meta">勾选「归档当前项目」后才能继续（不勾选创建会被拒绝）</span>
        <button class="mini" v-if="step > 0" :disabled="busy" @click="prev">上一步</button>
        <span class="spacer"></span>
        <button class="mini primary" v-if="step < 3"
                :disabled="!step0Ok || (step === 1 && !step1Ok) || (step === 2 && !step2Ok)"
                @click="next">
          {{ step === 0 && pickedPreset && !presetApplied ? "铺设后继续" : "下一步" }}
        </button>
        <button class="mini primary" v-else :disabled="busy" @click="submit">
          {{ busy ? "创建中…" : "确认创建" }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 出厂预设卡片（2026-10-21）。用主题 token，暗色下才看得清。 */
.wiz-presets { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 10px; }
.wiz-preset {
  padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px;
  cursor: pointer; transition: border-color .12s, background .12s;
}
.wiz-preset:hover { border-color: var(--accent); }
.wiz-preset.on { border-color: var(--accent); background: var(--accent-soft, rgba(120,160,255,.12)); }
.wiz-preset-name { font-weight: 600; margin-bottom: 2px; }
.wiz-preset-act { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
</style>
