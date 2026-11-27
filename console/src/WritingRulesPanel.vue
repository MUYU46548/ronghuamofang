<script setup>
/**
 * 作品级写作约束（M12，2026-11-04）。
 *
 * 要治的病：写作约束散在 style_notes / prompts / 讨论结论里，**没有每本书一份的文件**。
 * 新开的 agent 会话开工不知道这本书的规矩（术语、禁忌、称呼、视角、每章必须出现的东西）。
 *
 * 数据源：GET  /writing_rules  → {exists, text, chars, max_chars}
 *         POST /writing_rules  {text}（空 = 清空；超上限 400 并说明；写前自动备份）
 *
 * 三条坚持：
 *   1. 没写就明说“还没写”—— 空配置不许显示成“已配置”；
 *   2. 空文件**不注入**提示词（不占每章上下文），界面上也这么说；
 *   3. 超上限**拒收**并说明，不静默截断（截断会把一条规矩砍成半句）。
 */
import { ref, computed, onMounted } from "vue";
import UiButton from "./components/ui/UiButton.vue";

const props = defineProps({ api: { type: Function, required: true } });
const emit = defineEmits(["say"]);
function say(m) { emit("say", m); }

const text = ref("");
const saved = ref("");
const meta = ref(null);
const busy = ref(false);
const err = ref("");
const maxChars = ref(6000);

const dirty = computed(() => text.value !== saved.value);
const chars = computed(() => text.value.trim().length);
const over = computed(() => chars.value > maxChars.value);

async function load() {
  err.value = "";
  busy.value = true;
  try {
    const r = await props.api("/writing_rules");
    if (r.status === 200 && r.data?.ok) {
      meta.value = r.data;
      maxChars.value = r.data.max_chars || 6000;
      text.value = r.data.text || "";
      saved.value = text.value;
    } else {
      err.value = r.data?.error || ("读取失败：HTTP " + r.status);
    }
  } catch (e) {
    err.value = "读取失败：" + String(e);
  }
  busy.value = false;
}

async function save() {
  if (busy.value) return;
  if (over.value) { say("太长了：先压到 " + maxChars.value + " 字以内"); return; }
  busy.value = true;
  try {
    const r = await props.api("/writing_rules", "POST", { text: text.value });
    if (r.status === 200 && r.data?.ok) {
      say(r.data.message || "已保存作品写作约束");
      await load();
    } else {
      say("保存失败：" + (r.data?.error || r.data?.message || ("HTTP " + r.status)));
    }
  } catch (e) {
    say("保存失败：" + String(e));
  }
  busy.value = false;
}

function clearAll() {
  if (!text.value.trim()) return;
  text.value = "";
  say("已清空输入框；点保存后才会从盘上删掉（旧版会备份）");
}

onMounted(load);
defineExpose({ load });
</script>

<template>
  <div class="card">
    <div class="card-head">
      <h3>作品写作约束</h3>
      <span class="meta">每本书一份 · 跟着这本书走 · 不调模型</span>
      <span class="spacer"></span>
      <span class="meta" v-if="dirty">未保存</span>
      <UiButton size="sm" variant="default" :disabled="busy" @click="load">
        {{ busy ? "读取中…" : "刷新" }}
      </UiButton>
      <UiButton size="sm" variant="primary" :disabled="busy || !dirty" @click="save">保存</UiButton>
    </div>
    <div class="meta">
      这本书的硬规矩：术语、禁忌、称呼、视角、每章必须出现的东西。有内容时会写进
      <b>写作与润色</b>的任务里（阶段 4 / 阶段 6）；
      <b>留空则不注入</b>，不占每章的位置。
    </div>

    <div v-if="err" class="empty" style="padding:12px 0;">{{ err }}</div>

    <div v-else class="mt-2.5">
      <div v-if="meta && !meta.exists && !text.trim()"
           class="mb-2 mt-2 rounded-[var(--r-sm)] border-l-[3px] border-[var(--warn)] bg-[var(--field-dim)] px-3 py-2.5 text-[length:var(--fs-ui)] leading-[1.7]">
        还没写这本书的写作约束。写几条硬规矩（比如“不许出现现代科技名词”“每章至少一次视角切换”），
        写作时会按它来。
      </div>
      <textarea v-model="text" spellcheck="false"
                class="min-h-[220px] w-full box-border resize-y rounded-[var(--r-sm)] border border-[var(--border)] bg-[var(--field)] px-3 py-2.5 text-[length:var(--fs-ui)] leading-[1.75] text-[color:var(--ink)] focus:border-[var(--accent)] focus:outline-none"
                placeholder="一条一行，写“必须”和“禁止”，不要写“尽量”：&#10;&#10;必须：全篇用第三人称限知，跟随主视角&#10;必须：每章至少出现一次具体物件（触感/声音）&#10;禁止：解释人物心理（“她感到……”），用动作与对白代替&#10;禁止：现代科技名词"></textarea>
      <div class="mt-2 flex items-baseline gap-3 text-[length:var(--fs-ui)] text-[color:var(--muted)]">
        <span :class="{ 'text-[color:var(--bad)]': over }">{{ chars }} / {{ maxChars }} 字</span>
        <span class="meta" v-if="meta && meta.exists && meta.mtime">
          盘上版本：{{ new Date(meta.mtime * 1000).toLocaleString() }}</span>
        <span class="spacer"></span>
        <UiButton size="sm" variant="ghost" :disabled="busy || !text.trim()" @click="clearAll">清空输入</UiButton>
      </div>
      <div v-if="over" class="meta text-[color:var(--bad)]">
        超过上限 {{ maxChars }} 字：保存会被拒收（这段每章都要发一遍，请压缩）。
      </div>
    </div>
  </div>
</template>

