<script setup>
// 新建项目向导（一键创建）：① 基本信息 → ② 处理当前工作区 → ③ 确认创建。
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

const step = ref(1);
const busy = ref(false);
const err = ref("");
const form = ref({ name: "", genre: "奇幻", chapters: 12, style_notes: "", archive_current: true });
const touched = ref({ name: false, chapters: false });   // 未交互不报错（别一打开就飘红）

const GENRES = ["奇幻", "科幻", "都市", "悬疑", "历史", "言情", "仙侠", "其他"];

watch(() => props.open, (v) => {
  if (!v) return;
  step.value = 1;
  busy.value = false;
  err.value = "";
  touched.value = { name: false, chapters: false };
  form.value = {
    name: "",
    genre: props.defaults?.genre || "奇幻",
    chapters: props.defaults?.chapters || 12,
    style_notes: "",
    archive_current: true,   // 默认归档，最安全
  };
});

const nameErr = computed(() => {
  const n = (form.value.name || "").trim();
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

function next() {
  if (step.value === 1 && !step1Ok.value) return;
  step.value += 1;
}
function prev() { if (step.value > 1) step.value -= 1; }

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
        <span class="meta">{{ step }} / 3</span>
        <span class="spacer"></span>
        <button class="mini" :disabled="busy" @click="$emit('close')">取消</button>
      </div>
      <div class="wiz-steps">
        <span class="wiz-step" :class="{ on: step === 1, done: step > 1 }">1 基本信息</span>
        <span class="wiz-step" :class="{ on: step === 2, done: step > 2 }">2 当前工作区</span>
        <span class="wiz-step" :class="{ on: step === 3 }">3 确认创建</span>
      </div>

      <!-- 步骤 1：基本信息 -->
      <template v-if="step === 1">
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
          <span v-if="hasWork" class="pill st-gate">工作区有数据</span>
          <span v-else class="pill">工作区为空</span>
        </div>

        <template v-if="hasWork">
          <label class="wiz-check">
            <input type="checkbox" v-model="form.archive_current" />
            <span>把当前项目归档为「<b>{{ current || form.name }}</b>」再新建
              <span class="meta">（推荐：书稿完整保留在 data/books/，可随时在「项目」页签恢复）</span>
            </span>
          </label>
          <div v-if="!form.archive_current" class="wiz-warn">
            不勾选归档时，如果工作区还有数据，创建会被拒绝（系统不会静默清空书稿）。
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
              {{ hasWork ? (form.archive_current ? "归档为「" + (current || form.name) + "」" : "不归档（会被拒绝）")
                         : "空工作区，无需处理" }}
            </td></tr>
          </tbody>
        </table>
        <div class="meta" style="margin-top:10px;">
          确认后将执行：快照 → 归档当前项目（如勾选）→ 初始化空工作区 → 写入 config/project.yaml。
          之后到「流水线」页签点「执行下一步」即可开跑。
        </div>
        <div v-if="err" class="wiz-warn" style="margin-top:8px;">{{ err }}</div>
      </template>

      <div class="dialog-actions">
        <span v-if="step === 1 && !step1Ok" class="meta">填写书名（1-999 章）后才能继续</span>
        <button class="mini" v-if="step > 1" :disabled="busy" @click="prev">上一步</button>
        <span class="spacer"></span>
        <button class="mini primary" v-if="step < 3" :disabled="step === 1 && !step1Ok" @click="next">
          下一步
        </button>
        <button class="mini primary" v-else :disabled="busy" @click="submit">
          {{ busy ? "创建中…" : "确认创建" }}
        </button>
      </div>
    </div>
  </div>
</template>
