<template>
  <div data-panel="vault-dirs" class="mb-2.5 mt-2.5 rounded-[var(--r-sm)] border border-[var(--border)] px-3 py-2.5">
    <div class="row-wrap mb-2">
      <strong class="text-[color:var(--ink)]">设定目录（你的库，你来指）</strong>
      <UiButton :disabled="busy" @click="load">刷新目录</UiButton>
      <UiButton variant="primary" :disabled="busy || !dirty" @click="save">保存</UiButton>
      <span class="meta" v-if="msg">{{ msg }}</span>
    </div>
    <p class="meta">{{ note }}</p>

    <div class="mt-2 grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(240px,1fr))]">
      <div v-for="k in keys" :key="k" class="flex items-center gap-2 min-w-0">
        <span class="w-[3.5em] flex-none text-[length:var(--fs-ui)] text-[color:var(--muted)]">{{ labels[k] }}</span>
        <select v-model="picked[k]" class="min-w-0 flex-1">
          <option value="">（这一类不读）</option>
          <option v-for="c in candidates" :key="c.path || '__root'" :value="c.path">
            {{ c.path || "（库根）" }}{{ c.md ? " · " + c.md + " 篇" : "" }}
          </option>
        </select>
      </div>
    </div>
    <!-- 其他库的目录（2026-11-08，0.9.1 验收第 2 条）：
         多库时“角色/地点/势力/概念/年表”五类目录**按库各一套**。
         别的库结构跟当前库不一样时，这里能分别指；没配的库要点名说清楚，
         否则它只是静默扫空，作者会以为多库坏了。 -->
    <div v-if="vaults.length" class="mt-3 border-t border-dashed border-[var(--border)] pt-2.5">
      <div class="mb-1 text-[length:var(--fs-ui)] font-semibold text-[color:var(--ink)]">其他库的目录（每个库各存一套）</div>
      <p class="meta">
        这些库的结构可能跟当前库不一样，所以目录要分别指。只写<b>库内相对目录</b>
        （例：设定/人物），不能填绝对路径。留空 = 这一类不从那个库读。
      </p>
      <div v-for="v in vaults" :key="v.path"
           class="my-2 rounded-[var(--r-sm)] border border-[var(--border)] px-2.5 py-2">
        <div class="row-wrap">
          <b class="text-[color:var(--ink)]">{{ v.name }}</b>
          <span class="meta max-w-[42em] truncate" :title="v.path">{{ v.path }}</span>
          <!-- 三种来源要说清楚（2026-11-08）：自己配过 / 沿用当前库 / 完全没配。
               只说“没配”会让人以为乙库也被忽略了 —— 沿用时它其实读得到，只是按当前库的目录找。 -->
          <UiBadge v-if="v.dirs_source === 'inherited'" variant="neutral"
                   title="这个库自己没配目录，暂时按当前库那套目录去找。结构一样就直接用；不一样就在这里改，保存后它就是自己一套。">
            沿用当前库的目录</UiBadge>
          <UiBadge v-else-if="!v.dirs_configured" variant="warn">这个库没配目录（扫不到条目）</UiBadge>
          <UiButton class="ml-auto" :disabled="v.busy" @click="saveVault(v)">保存这个库的目录</UiButton>
          <span class="meta" v-if="v.msg">{{ v.msg }}</span>
        </div>
        <div class="mt-2 grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(240px,1fr))]">
          <UiField v-for="k in keys" :key="v.path + k" :label="labels[k]" inline>
            <UiInput v-model="v.dirs[k]" :placeholder="'例：' + samples[k]" />
          </UiField>
        </div>
        <p v-if="!v.enabled" class="meta">这个库现在是“未启用”（不参与扫描）—— 配了目录也不会读。</p>
      </div>
    </div>

    <p class="meta mt-2">
      每一行 = 设定集里的一类条目，右边选它在你的库（Obsidian 等 markdown 库）里
      <b>对应哪个目录</b>；选“这一类不读”= 这类条目不从库里读，只用素材卡里的内容。
      留空的那几类不会报错，也不会凭空补字。
    </p>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";

import UiBadge from "./components/ui/UiBadge.vue";
import UiButton from "./components/ui/UiButton.vue";
import UiField from "./components/ui/UiField.vue";
import UiInput from "./components/ui/UiInput.vue";

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
// 其他库（多库时每个库一套目录，2026-11-08）
const vaults = ref([]);
const samples = { characters: "设定/人物", locations: "设定/地点",
                  factions: "势力", concepts: "概念", timeline: "年表" };
function emptyDirs() {
  return { characters: "", locations: "", factions: "", concepts: "", timeline: "" };
}

const dirty = computed(() => keys.some((k) => (picked.value[k] || "") !== (orig.value[k] || "")));

function payload(r) {
  if (!r || r.status !== 200) {
    const e = (r && r.data && r.data.error) || (r && r.status) || "请求失败";
    throw new Error(String(e));
  }
  return r.data || {};
}

async function loadVaults() {
  try {
    const d = payload(await props.api("/config/vaults"));
    vaults.value = (d.items || []).filter((x) => !x.current).map((x) => ({
      ...x,
      dirs: { ...emptyDirs(), ...(x.dirs || {}) },
      busy: false,
      msg: "",
    }));
  } catch (e) {
    vaults.value = [];
    msg.value = "读库列表失败：" + e.message;
  }
}

async function saveVault(v) {
  v.busy = true; v.msg = "";
  try {
    const d = payload(await props.api("/config/vaults/dirs", "POST",
                                      { path: v.path, dirs: v.dirs }));
    const hit = (d.items || []).find((x) => x.path === v.path);
    if (hit) {
      v.dirs = { ...emptyDirs(), ...(hit.dirs || {}) };
      v.dirs_configured = !!hit.dirs_configured;
    }
    v.msg = d.message || "已保存";
  } catch (e) {
    v.msg = "保存失败：" + e.message;
  } finally {
    v.busy = false;
  }
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
    // 保存后按盘上重读（问题总账第 7 条）：后端会把值归一化（去空格/反斜杠/首尾斜杠），
    // 且只认可真实存在的目录。拿本地 picked 当"已保存的样子"，界面与 system.local.yaml
    // 里的真实映射会一直差一个规范化的距离（dirty 判定也跟着错）。
    await load();
    msg.value = d.message || "已保存";
  } catch (e) {
    msg.value = "保存失败：" + e.message;
  } finally {
    busy.value = false;
  }
}

onMounted(() => { load(); loadVaults(); });
</script>

