<script setup>
/**
 * 旧段落池（2026-11-14，P1=A 作者拍板）。
 *
 * 数据源（零 token）：
 *   GET  /pool/stats    规模 / 建索引时间
 *   GET  /pool/facets   人名、地名下拉（取索引里真出现过的）
 *   GET  /pool/search   检索
 *   POST /pool/build    重建索引（只读归档书档）
 *
 * 拍板口径：**只在作者查的时候才用**。这个面板不往写作提示词里塞任何东西，
 * 也不做“自动推荐相似段落”——查到的是候选，用不用作者自己定。
 */
import { ref, onMounted } from "vue";

import { API } from "./apiBase.js";
import { postJSON } from "./guiApi.js";
import { copyToClipboard } from "./clipboard.js";

const emit = defineEmits(["say"]);
function say(m) { emit("say", m); }

const st = ref(null);
const facets = ref({ people: [], places: [] });
const q = ref("");
const person = ref("");
const place = ref("");
const res = ref(null);
const busy = ref(false);
const err = ref("");
const building = ref(false);
const copied = ref("");

async function get(path) {
  const r = await fetch(API + path);
  const d = await r.json().catch(() => ({}));
  if (r.status !== 200 || !d.ok) throw new Error(d.error || ("HTTP " + r.status));
  return d;
}

async function loadStats() {
  try {
    st.value = await get("/pool/stats");
  } catch (e) {
    err.value = "读不到索引状态：" + String(e.message || e);
  }
  try {
    facets.value = await get("/pool/facets");
  } catch (e) {
    facets.value = { people: [], places: [] };
  }
}

async function rebuild() {
  building.value = true;
  try {
    const d = await postJSON("/pool/build", {});
    const s = d.stats || {};
    say("索引已重建：" + (s.books || 0) + " 本书 / " + (s.chapters || 0) + " 章 / "
        + (s.paragraphs || 0) + " 段");
    res.value = null;
    await loadStats();
  } catch (e) {
    say("重建失败：" + String(e.message || e));
  }
  building.value = false;
}

async function search() {
  if (!q.value.trim() && !person.value && !place.value) {
    err.value = "给个关键词，或者选一个人物 / 地名";
    return;
  }
  busy.value = true;
  err.value = "";
  try {
    const p = new URLSearchParams();
    if (q.value.trim()) p.set("q", q.value.trim());
    if (person.value) p.set("person", person.value);
    if (place.value) p.set("place", place.value);
    p.set("limit", "20");
    res.value = await get("/pool/search?" + p.toString());
  } catch (e) {
    err.value = "搜索失败：" + String(e.message || e);
  }
  busy.value = false;
}

async function copy(it) {
  const ok = await copyToClipboard(it.text);
  copied.value = ok ? it.id : "";
  say(ok ? ("已复制这一段（" + it.chars + " 字）") : "复制失败：剪贴板不可用，可以手动选中复制");
}

function bookLabel(it) {
  const name = String(it.book || "");
  const cut = name.indexOf("__");
  return (cut > 0 ? name.slice(0, cut) : name) + " · 第" + it.chapter + "章";
}

onMounted(loadStats);
</script>

<template>
  <section class="card pool-card">
    <div class="card-head">
      <h3>旧段落池<span class="meta">（归档书里的合格段落，查了才用）</span></h3>
      <span class="meta" v-if="st && st.exists">
        {{ st.books }} 本书 · {{ st.chapters }} 章 · {{ st.paragraphs }} 段
        <span v-if="st.built_at">· 索引于 {{ String(st.built_at).replace("T", " ") }}</span>
      </span>
      <span class="spacer"></span>
      <button class="mini" :disabled="building" @click="rebuild">
        {{ building ? "重建中…" : "重建索引" }}
      </button>
    </div>

    <div class="meta pool-note">
      从归档书（`data/books/`）里筛出 30 字以上的段落，按出现的人物、地名打标签。
      <b>它不会自动进你的写作上下文</b>——你搜到、复制走，用不用自己定。
    </div>

    <div v-if="st && !st.exists" class="pool-empty">
      还没有索引。点“重建索引”扫一遍归档书（不调模型、不花钱）。
    </div>

    <div class="pool-form row-wrap">
      <input v-model="q" class="pool-input" placeholder="关键词，例：雨 / 盐砖 / 闭嘴"
             @keyup.enter="search" />
      <select v-model="person" class="pool-select">
        <option value="">人物（不限）</option>
        <option v-for="p in facets.people" :key="'p' + p[0]" :value="p[0]">{{ p[0] }}（{{ p[1] }}）</option>
      </select>
      <select v-model="place" class="pool-select">
        <option value="">地名（不限）</option>
        <option v-for="p in facets.places" :key="'l' + p[0]" :value="p[0]">{{ p[0] }}（{{ p[1] }}）</option>
      </select>
      <button class="mini primary" :disabled="busy" @click="search">{{ busy ? "搜索中…" : "搜索" }}</button>
    </div>

    <div v-if="err" class="meta pool-err">{{ err }}</div>

    <div v-if="res" class="pool-result">
      <div class="meta" v-if="res.total">
        命中 {{ res.total }} 段（显示前 {{ res.items.length }} 段）
      </div>
      <div class="meta" v-else>{{ res.hint || "没有符合条件的段落。" }}</div>
      <div v-for="it in res.items" :key="it.id" class="pool-item">
        <div class="pool-item-head">
          <b>{{ bookLabel(it) }}</b>
          <span class="meta">{{ it.chars }} 字 · {{ it.layer }}</span>
          <span v-if="it.tags && it.tags.people && it.tags.people.length" class="pill">
            {{ it.tags.people.join("、") }}
          </span>
          <span v-if="it.tags && it.tags.places && it.tags.places.length" class="pill">
            {{ it.tags.places.join("、") }}
          </span>
          <span class="spacer"></span>
          <button class="mini" @click="copy(it)">{{ copied === it.id ? "已复制" : "复制这段" }}</button>
        </div>
        <div class="pool-text">{{ it.text }}</div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.pool-card { margin-bottom: 14px; }
.pool-note { line-height: 1.7; margin: 6px 0 10px; }
.pool-empty {
  margin: 8px 0; padding: 10px 12px; border-radius: 8px; line-height: 1.7;
  border-left: 3px solid var(--warn); background: var(--field-dim);
}
.pool-input {
  flex: 1; min-width: 220px; padding: 7px 10px; border-radius: 8px;
  border: 1px solid var(--border); background: var(--field-dim); color: var(--ink);
}
.pool-select {
  padding: 7px 8px; border-radius: 8px; border: 1px solid var(--border);
  background: var(--field-dim); color: var(--ink); max-width: 200px;
}
.pool-err { color: var(--bad); margin-top: 8px; }
.pool-result { margin-top: 12px; }
.pool-item { border-top: 1px solid var(--border); padding: 8px 0; }
.pool-item-head { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.pool-item-head .spacer { flex: 1; }
.pool-text {
  margin-top: 4px; font-size: 13px; line-height: 1.75; white-space: pre-wrap;
  word-break: break-word; max-height: 200px; overflow: auto;
}
</style>
