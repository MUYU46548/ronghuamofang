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
    <!-- 其他库的目录（2026-11-08，0.9.1 验收第 2 条）：
         多库时“角色/地点/势力/概念/年表”五类目录**按库各一套**。
         别的库结构跟当前库不一样时，这里能分别指；没配的库要点名说清楚，
         否则它只是静默扫空，作者会以为多库坏了。 -->
    <div v-if="vaults.length" class="vd-others">
      <div class="vd-sub">其他库的目录（每个库各存一套）</div>
      <p class="muted">
        这些库的结构可能跟当前库不一样，所以目录要分别指。只写<b>库内相对目录</b>
        （例：设定/人物），不能填绝对路径。留空 = 这一类不从那个库读。
      </p>
      <div v-for="v in vaults" :key="v.path" class="vd-vault">
        <div class="vd-vault-head">
          <b>{{ v.name }}</b>
          <span class="muted vd-path">{{ v.path }}</span>
          <!-- 三种来源要说清楚（2026-11-08）：自己配过 / 沿用当前库 / 完全没配。
               只说“没配”会让人以为乙库也被忽略了 —— 沿用时它其实读得到，只是按当前库的目录找。 -->
          <span v-if="v.dirs_source === 'inherited'" class="vd-badge info"
                title="这个库自己没配目录，暂时按当前库那套目录去找。结构一样就直接用；不一样就在这里改，保存后它就是自己一套。">
            沿用当前库的目录</span>
          <span v-else-if="!v.dirs_configured" class="vd-badge">这个库没配目录（扫不到条目）</span>
          <button class="mini vd-save" :disabled="v.busy" @click="saveVault(v)">保存这个库的目录</button>
          <span class="muted" v-if="v.msg">{{ v.msg }}</span>
        </div>
        <div class="vd-grid">
          <label v-for="k in keys" :key="v.path + k" class="vd-row">
            <span class="vd-k">{{ labels[k] }}</span>
            <input v-model="v.dirs[k]" :placeholder="'例：' + samples[k]" />
          </label>
        </div>
        <p v-if="!v.enabled" class="muted">这个库现在是“未启用”（不参与扫描）—— 配了目录也不会读。</p>
      </div>
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

<style scoped>
.vd-panel { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; margin: 10px 0; }
.vd-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.vd-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 8px; margin-top: 8px; }
.vd-row { display: flex; align-items: center; gap: 8px; min-width: 0; }
.vd-k { width: 3.5em; font-size: 12px; color: var(--muted); flex: none; }
.vd-row select { flex: 1; min-width: 0; }
.muted { color: var(--muted); font-size: 12px; }
/* 其他库：一库一块，目录用输入框（每个库结构不同，没有统一的候选清单可下拉） */
.vd-others { margin-top: 12px; padding-top: 10px; border-top: 1px dashed var(--border); }
.vd-sub { font-size: 12.5px; font-weight: 600; margin-bottom: 4px; }
.vd-vault { border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; margin: 8px 0; }
.vd-vault-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.vd-path { max-width: 42em; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.vd-badge {
  font-size: 11px; padding: 1px 8px; border-radius: 9px;
  border: 1px solid var(--warn); color: var(--warn);
}
.vd-badge.info { border-color: var(--border); color: var(--muted); }
.vd-save { margin-left: auto; }
.vd-row input { flex: 1; min-width: 0; padding: 4px 8px; border: 1px solid var(--border);
  border-radius: 6px; background: var(--field); color: var(--ink); font-size: 12.5px; }
</style>
