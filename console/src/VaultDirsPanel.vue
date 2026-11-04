<template>
  <div class="vd-panel">
    <div class="vd-head">
      <strong>设定目录（你的库，你来指）</strong>
      <button class="mini" @click="load" :disabled="busy">刷新目录</button>
      <button class="mini primary" @click="save" :disabled="busy || !dirty">保存</button>
      <span class="muted" v-if="msg">{{ msg }}</span>
    </div>
    <p class="muted">{{ note }}</p>

    <div class="vd-grid">
      <label v-for="k in keys" :key="k" class="vd-row">
        <span class="vd-k">{{ labels[k] }}</span>
        <select v-model="picked[k]">
          <option value="">（这一类不读）</option>
          <option v-for="c in candidates" :key="c.path || '__root'" :value="c.path">
            {{ c.path || "（库根）" }}{{ c.md ? " · " + c.md + " 篇" : "" }}
          </option>
        </select>
      </label>
    </div>
    <p class="muted vd-hint">
      每一行 = 设定集里的一类条目，右边选它在你的库（Obsidian 等 markdown 库）里
      <b>对应哪个目录</b>；选“这一类不读”= 这类条目不从库里读，只用素材卡里的内容。
      留空的那几类不会报错，也不会凭空补字。
    </p>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";

const props = defineProps({ api: { type: Function, required: true } });
const keys = ["characters", "locations", "factions", "concepts", "timeline"];
const labels = { characters: "角色", locations: "地点", factions: "势力",
                 concepts: "概念", timeline: "年表" };
const candidates = ref([]);
const picked = ref({ characters: "", locations: "", factions: "", concepts: "", timeline: "" });
const orig = ref({ ...picked.value });
const busy = ref(false);
const msg = ref("");
const note = ref("");

const dirty = computed(() => keys.some((k) => (picked.value[k] || "") !== (orig.value[k] || "")));

function payload(r) {
  if (!r || r.status !== 200) {
    const e = (r && r.data && r.data.error) || (r && r.status) || "请求失败";
    throw new Error(String(e));
  }
  return r.data || {};
}

async function load() {
  busy.value = true;
  try {
    const d = payload(await props.api("/config/vault_dirs"));
    candidates.value = d.candidates || [];
    note.value = d.note || "";
    picked.value = { ...picked.value, ...(d.current || {}) };
    orig.value = { ...picked.value };
    msg.value = "";
  } catch (e) {
    msg.value = "读目录失败：" + e.message;
  } finally {
    busy.value = false;
  }
}

async function save() {
  busy.value = true;
  try {
    const d = payload(await props.api("/config/vault_dirs", "POST", picked.value));
    orig.value = { ...picked.value };
    msg.value = d.message || "已保存";
  } catch (e) {
    msg.value = "保存失败：" + e.message;
  } finally {
    busy.value = false;
  }
}

onMounted(load);
</script>

<style scoped>
.vd-panel { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; margin: 10px 0; }
.vd-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.vd-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 8px; margin-top: 8px; }
.vd-row { display: flex; align-items: center; gap: 8px; min-width: 0; }
.vd-k { width: 3.5em; font-size: 12px; color: var(--muted); flex: none; }
.vd-row select { flex: 1; min-width: 0; }
.muted { color: var(--muted); font-size: 12px; }
</style>
