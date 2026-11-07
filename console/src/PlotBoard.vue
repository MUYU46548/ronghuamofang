<template>
  <div class="pboard">
    <div class="pboard-head">
      <strong>情节画布</strong>
      <span class="pill">{{ cards.length }} 张卡片 · {{ seqLinks }} 条连线</span>
      <button class="mini" @click="collapseAll = !collapseAll">
        {{ collapseAll ? "全部展开" : "按卷收起" }}
      </button>
      <button class="mini" @click="load" title="重新读大纲">刷新</button>
      <span class="pboard-hint" v-if="note">{{ note }}</span>
    </div>

    <div class="pboard-vols">
      <section v-for="v in volumes" :key="v.vol" class="pboard-vol">
        <header @click="toggle(v.vol)">
          <span class="pboard-vt">第 {{ v.vol }} 卷</span>
          <span class="pill">{{ v.cards.length }} 章</span>
          <span class="pboard-caret">{{ collapsed.has(v.vol) ? "▸" : "▾" }}</span>
        </header>
        <div v-if="!collapsed.has(v.vol)" class="pboard-row">
          <template v-for="(c, i) in v.cards" :key="c.n">
            <div class="pcard" :class="{ sel: picked === c.n, written: c.written }"
                 @click="pick(c)">
              <div class="pcard-kind">第 {{ c.n }} 章</div>
              <div class="pcard-title">{{ c.title || "（未命名）" }}</div>
              <div class="pcard-sum">{{ c.summary || "—" }}</div>
              <div class="pcard-foot">
                <span class="pcard-dot" :class="c.written ? 'ok' : (c.discussing ? 'talk' : '')"></span>
                <span class="pcard-st">{{ c.written ? "已写正文" : (c.discussing ? "讨论中" : "未写") }}</span>
                <span class="pcard-meta" v-if="c.roles">· {{ c.roles }} 角色</span>
              </div>
            </div>
            <div v-if="i < v.cards.length - 1" class="parrow">→</div>
          </template>
        </div>
      </section>
      <p v-if="!cards.length" class="muted">{{ empty || "还没有章大纲（data/outline/chapters/）。先跑阶段 3。" }}</p>
    </div>

    <div v-if="stickies.length" class="pboard-stickies">
      <span class="muted">讨论中：</span>
      <button v-for="s in stickies" :key="s.chapter + s.topic" class="sticky"
              @click="openTalk(s.chapter)" :title="s.topic">
        <b>第 {{ s.chapter }} 章</b>
        <span>{{ s.topic.replace(/^审稿-/, "审稿 · ") }}</span>
        <span class="muted">{{ s.rounds || 0 }} 轮</span>
      </button>
    </div>

    <div v-if="picked" class="pboard-talk">
      <DiscussThreadPanel :api="api" :chapters="[]" :default-chapter="picked" />
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import DiscussThreadPanel from "./DiscussThreadPanel.vue";

const props = defineProps({
  api: { type: Function, required: true },
});

const cards = ref([]);
const linkCount = ref(0);
const note = ref("");
const empty = ref("");
const collapsed = ref(new Set());
const collapseAll = ref(false);
const picked = ref(0);
const stickies = ref([]);

const volumes = computed(() => {
  const map = new Map();
  for (const c of cards.value) {
    if (!map.has(c.vol)) map.set(c.vol, { vol: c.vol, cards: [] });
    map.get(c.vol).cards.push(c);
  }
  return [...map.values()].sort((a, b) => a.vol - b.vol);
});
const seqLinks = computed(() => linkCount.value);

function payload(r) {
  if (!r || r.status !== 200) return {};
  return r.data || {};
}

async function load() {
  try {
    const d = payload(await props.api("/outline/graph"));
    note.value = d.note || "";
    empty.value = d.note || "";
    const threads = payload(await props.api("/discuss/list")).threads || [];
    const talking = new Set(threads.filter((t) => t.status !== "decided")
                                   .map((t) => Number(t.chapter)));
    stickies.value = threads.filter((t) => t.status !== "decided");
    cards.value = (d.nodes || []).map((x) => ({
      n: x.n,
      vol: x.vol || 1,
      title: x.title || String(x.label || "").replace(/^第\d+章\s*/, ""),
      summary: x.function || (x.roles && x.roles.length ? "涉及：" + x.roles.join("、") : ""),
      roles: (x.roles || []).length,
      written: !!x.written || !!x.has_raw,
      discussing: talking.has(x.n),
    }));
    linkCount.value = (d.links || []).filter((l) => l.kind !== "role").length;
  } catch (e) {
    note.value = "读情节图失败：" + e.message;
  }
}

function toggle(vol) {
  const s = new Set(collapsed.value);
  s.has(vol) ? s.delete(vol) : s.add(vol);
  collapsed.value = s;
}

function pick(c) {
  picked.value = picked.value === c.n ? 0 : c.n;
}

function openTalk(n) {
  picked.value = n;
  const el = document.querySelector(".pboard-talk");
  if (el) el.scrollIntoView({ block: "nearest" });
}

onMounted(load);
</script>

<style scoped>
.pboard { border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; margin: 10px 0;
          background-image: radial-gradient(var(--border) 1px, transparent 1px);
          background-size: 24px 24px; }
.pboard-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.pboard-hint { color: var(--muted); font-size: 12px; }
.pboard-vols { display: flex; flex-direction: column; gap: 16px; margin-top: 10px; }
.pboard-vol { border: 1px solid var(--viz-volume-edge);
  background: color-mix(in srgb, var(--viz-volume-edge) 8%, transparent);
              border-radius: 12px; padding: 10px 12px; }
.pboard-vol > header { display: flex; gap: 8px; align-items: center; cursor: pointer; }
.pboard-vt { font-weight: 600; font-size: 13px; }
.pboard-caret { margin-left: auto; color: var(--muted); }
.pboard-row { display: flex; align-items: stretch; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
.pcard { width: 220px; min-height: 116px; background: var(--card); border: 1px solid var(--border);
         border-radius: 8px; padding: 12px; display: flex; flex-direction: column; gap: 4px;
         cursor: pointer; box-shadow: 0 1px 2px rgba(0,0,0,.05); transition: border-color .15s; }
.pcard:hover { border-color: var(--accent); }
.pcard.sel { border-color: var(--accent); box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 30%, transparent); }
.pcard-kind { font-size: 11px; color: var(--muted); }
.pcard-title { font-size: 14px; font-weight: 600; line-height: 1.35; }
.pcard-sum { font-size: 12px; color: var(--muted); line-height: 1.5; flex: 1;
             display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.pcard-foot { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--muted); }
.pcard-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--border); }
.pcard-dot.ok { background: var(--viz-dot-ok); }
.pcard-dot.talk { background: var(--viz-gold); }
.parrow { align-self: center; color: var(--muted); }
.pboard-stickies { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 12px; }
.sticky { background: var(--viz-note-bg); border: 1px solid var(--viz-note-edge); border-radius: 8px; padding: 8px 10px;
          text-align: left; cursor: pointer; display: flex; flex-direction: column; gap: 2px;
          font-size: 12px; max-width: 240px; }
.pboard-talk { margin-top: 12px; }
.muted { color: var(--muted); font-size: 12px; }
</style>
