<script setup>
// 设定版本：列出 / 两版对照 / 一键恢复（2026-11-02，视频③对标）。
//
// 为什么要有：`data/setting/history/setting_vN.json` 一直在写，但人看不见也拿不回来 ——
// 改错了只能去目录里翻 json 手工比对。这里把它接到界面上。
//
// 数据源：GET /setting/versions · GET /setting/diff?v=N · POST /setting/restore
import { ref, onMounted } from "vue";

const props = defineProps({ api: { type: Function, required: true } });
const emit = defineEmits(["say"]);

const versions = ref([]);
const live = ref({ exists: false, counts: {} });
const loading = ref(false);
const diffData = ref(null);
const diffFor = ref(null);
const busy = ref(false);

function say(msg) { emit("say", msg); }

async function load() {
  loading.value = true;
  try {
    const r = await props.api("/setting/versions");
    if (r.status === 200) {
      versions.value = r.data.items || [];
      live.value = r.data.live || { exists: false, counts: {} };
    } else {
      say("读版本列表失败：" + (r.data?.error || r.status));
    }
  } finally {
    loading.value = false;
  }
}

async function showDiff(v) {
  diffData.value = null;
  diffFor.value = v;
  const r = await props.api(`/setting/diff?v=${v}`);
  if (r.status === 200) diffData.value = r.data;
  else say("看差异失败：" + (r.data?.error || r.status));
}

async function restore(v) {
  const yes = window.confirm(
    `恢复到 v${v}？\n\n· 当前设定会先被存成新的一版（恢复本身也能回退）\n`
    + "· 设定会被整体替换，不是合并\n· 改完的段落/大纲不受影响");
  if (!yes) return;
  busy.value = true;
  try {
    const r = await props.api("/setting/restore", "POST", { v });
    if (r.status === 200) {
      say(r.data.message || `已恢复到 v${v}`);
      diffData.value = null;
      await load();
    } else {
      say("恢复失败：" + (r.data?.error || r.message || r.status));
    }
  } finally {
    busy.value = false;
  }
}

function countsText(c) {
  if (!c) return "";
  const parts = [];
  if (c.characters) parts.push("角色 " + c.characters);
  if (c.world) parts.push("世界 " + c.world);
  return parts.join(" · ") || "空";
}

function fieldText(v) {
  if (Array.isArray(v)) return v.join("、");
  if (v === true) return "是";
  if (v === false) return "否";
  return String(v ?? "（空）");
}

onMounted(load);
</script>

<template>
  <div class="card" style="margin-top: 12px;">
    <div class="card-head">
      <h3>设定版本</h3>
      <span class="meta">每次覆盖设定前自动备份；改错了能找回</span>
      <span class="spacer"></span>
      <span class="meta">当前：{{ countsText(live.counts) }}</span>
      <button class="mini" :disabled="loading" @click="load">{{ loading ? "读取中…" : "刷新" }}</button>
    </div>

    <div v-if="!versions.length" class="meta" style="padding: 10px 0;">
      还没有历史版本 —— 设定被覆盖或补全过一次之后，这里会出现可回退的版本。
    </div>

    <div v-for="v in versions" :key="v.v" style="padding: 8px 0; border-bottom: 1px solid var(--border);">
      <div style="display: flex; align-items: center; gap: 10px;">
        <b>v{{ v.v }}</b>
        <span class="meta">{{ v.at }}</span>
        <span class="meta">{{ countsText(v.counts) }}</span>
        <span v-if="!v.readable" class="pill st-rej">文件读不出来</span>
        <span class="spacer"></span>
        <button class="mini" :disabled="!v.readable" @click="showDiff(v.v)">看差异</button>
        <button class="mini" :disabled="!v.readable || busy" @click="restore(v.v)">恢复这一版</button>
      </div>
    </div>

    <!-- 差异：added / removed / changed（字段级） -->
    <div v-if="diffData && diffFor" style="margin-top: 12px; padding: 12px; border: 1px solid var(--border); border-radius: 10px;">
      <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px;">
        <b>{{ diffData.from }} → {{ diffData.to }}</b>
        <span class="meta">
          新增 {{ diffData.counts?.character?.added || 0 }} ·
          删除 {{ diffData.counts?.character?.removed || 0 }} ·
          改动 {{ diffData.counts?.character?.changed || 0 }}
        </span>
        <span class="spacer"></span>
        <button class="mini" @click="diffData = null">关闭</button>
      </div>

      <div v-if="diffData.added.length" style="margin-bottom: 6px;">
        <span class="pill st-done">新增</span>
        <span class="meta">{{ diffData.added.join("、") }}</span>
      </div>
      <div v-if="diffData.removed.length" style="margin-bottom: 6px;">
        <span class="pill st-rej">删除</span>
        <span class="meta">{{ diffData.removed.join("、") }}</span>
      </div>

      <div v-for="c in diffData.changed" :key="c.name" style="margin: 8px 0; font-size: 12.5px;">
        <b>{{ c.name }}</b>
        <div v-for="f in c.fields" :key="f.field" style="margin-left: 12px; color: var(--muted);">
          {{ f.label }}：<s>{{ fieldText(f.from) }}</s> → <b style="color: var(--ink);">{{ fieldText(f.to) }}</b>
        </div>
      </div>

      <div v-if="!diffData.added.length && !diffData.removed.length && !diffData.changed.length"
           class="meta">
        角色条目没有差别（世界条目的变化单独列在 /setting/diff 的 world 字段里）。
      </div>
    </div>
  </div>
</template>
