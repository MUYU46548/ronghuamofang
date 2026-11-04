<script setup>
/**
 * 设定引用核对（M10，2026-11-04）—— 改完章之后，哪些设定条目该复查。
 *
 * 要治的病：设定条目写得再细，也没有任何东西记录它依据哪一章。作者改完第 3 章，
 * 引用第 3 章的设定条目光看界面完全看不出“可能已经不对了”，只能凭记忆重读全书。
 *
 * 数据源：GET  /setting/stale        待核清单（零 token，读时现算，不写文件）
 *         POST /setting/stale/link   扫描章节，把现有引用登记进条目（只增不改）
 *         POST /setting/stale/review 作者逐条核对：confirm（还成立）/ drop（去掉来源）
 *
 * 三条语义坚持（与后端一致）：
 *   1. checked=false 必须显式说出来：没查成 != 没有待核。
 *   2. 从未登记 != 已核对 —— 没进过正文的条目不冒充“检查过没问题”。
 *   3. 核对是作者的判断：面板只提供“我看过了，还成立”，不自动确认。
 */
import { ref, computed, onMounted } from "vue";

const props = defineProps({ api: { type: Function, required: true } });
const emit = defineEmits(["say"]);
function say(m) { emit("say", m); }

const data = ref(null);
const busy = ref(false);
const err = ref("");
const acting = ref("");
const preview = ref(null);          // link 的预演结果（确认前不写盘）

async function load() {
  busy.value = true;
  err.value = "";
  try {
    const r = await props.api("/setting/stale");
    if (r.status === 200 && r.data?.ok) {
      data.value = r.data;
    } else {
      data.value = null;
      err.value = r.data?.error || ("读取失败：HTTP " + r.status);
    }
  } catch (e) {
    err.value = "读取失败：" + String(e);
  }
  busy.value = false;
}

const counts = computed(() => data.value?.counts || {});
const items = computed(() => data.value?.items || []);
const staleItems = computed(() => items.value.filter((x) => x.state === "stale"));
const newItems = computed(() => items.value.filter((x) => x.state === "new"));

function shortTime(ts) {
  if (!ts) return "";
  const d = new Date(Number(ts) * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return p(d.getMonth() + 1) + "-" + p(d.getDate()) + " " + p(d.getHours()) + ":" + p(d.getMinutes());
}

function sourceText(s) {
  // 后端已经把人话原因写全了（含章号与层名），这里不再重复拼一遍
  if (s.state === "missing") return s.reason || ("第 " + s.chapter + " 章已不存在");
  if (s.state === "changed") return s.reason || ("第 " + s.chapter + " 章的正文改过");
  return "第 " + s.chapter + " 章：对得上";
}

async function runLinkPreview() {
  err.value = "";
  preview.value = null;
  acting.value = "link";
  try {
    const r = await props.api("/setting/stale/link", "POST", { dry_run: true });
    if (r.status === 200 && r.data?.ok) {
      preview.value = r.data;
      if (!r.data.added_pairs) say("没有新的引用要登记");
    } else {
      err.value = r.data?.error || ("预演失败：HTTP " + r.status);
    }
  } catch (e) {
    err.value = "预演失败：" + String(e);
  }
  acting.value = "";
}

async function runLinkCommit() {
  acting.value = "link";
  try {
    const r = await props.api("/setting/stale/link", "POST", {});
    if (r.status === 200 && r.data?.ok) {
      say(r.data.message || "已登记");
      preview.value = null;
      await load();
    } else {
      err.value = r.data?.error || ("登记失败：HTTP " + r.status);
    }
  } catch (e) {
    err.value = "登记失败：" + String(e);
  }
  acting.value = "";
}

async function review(item, chapters, action, key) {
  acting.value = key;
  try {
    const body = { name: item.name, kind: item.kind, action };
    if (chapters) body.chapters = chapters;
    const r = await props.api("/setting/stale/review", "POST", body);
    if (r.status === 200 && r.data?.ok) {
      say(r.data.message || "已记录");
      await load();
    } else {
      say("没成功：" + (r.data?.error || ("HTTP " + r.status)));
    }
  } catch (e) {
    say("没成功：" + String(e));
  }
  acting.value = "";
}

onMounted(load);
defineExpose({ load });
</script>

<template>
  <div class="card">
    <div class="card-head">
      <h3>设定引用核对</h3>
      <span class="spacer"></span>
      <span class="meta ss-mtime" v-if="data && data.setting_mtime">
        设定集改于 {{ shortTime(data.setting_mtime) }}</span>
      <button class="mini" :disabled="busy" @click="load">{{ busy ? "读取中…" : "刷新" }}</button>
      <button class="mini" :disabled="!!acting" @click="runLinkPreview">
        {{ acting === "link" ? "扫描中…" : "扫描章节并记来源" }}</button>
    </div>
    <div class="meta">
      每条设定会记下“依据哪一章、那一章当时是什么版本”。你改完正文，引用它的条目会自己
      出现在待核清单里 —— <b>点“已核对”只代表你看过、还成立</b>；不成立就去改设定本身。
      零 token，不调模型。
    </div>

    <div v-if="err" class="empty" style="padding:14px 0;">{{ err }}</div>
    <div v-else-if="!data" class="meta" style="padding:10px 0;">读取中…</div>

    <!-- checked=false 必须显式说出来：空清单 != 没有待核 -->
    <div v-else-if="!data.checked" class="ss-unchecked">
      没查成：{{ data.reason || "原因未知" }}
    </div>

    <template v-else>
      <div class="ss-summary">
        <span class="ss-stat"><b>{{ counts.entries ?? 0 }}</b> 条设定</span>
        <span class="ss-stat"><b>{{ counts.quoted ?? 0 }}</b> 条登记过来源</span>
        <span class="ss-stat" :class="{ 'ss-bad': counts.stale }">
          <b>{{ counts.stale ?? 0 }}</b> 条待核</span>
        <span class="ss-stat" :class="{ 'ss-warn': counts.new }">
          <b>{{ counts.new ?? 0 }}</b> 条新引用</span>
        <span class="ss-stat"><b>{{ data.chapters ?? 0 }}</b> 章已扫</span>
        <span class="ss-stat" v-if="counts.absent"><b>{{ counts.absent }}</b> 条未进正文</span>
        <span class="ss-stat" v-if="counts.unlocatable"><b>{{ counts.unlocatable }}</b> 条名字太短无法定位</span>
      </div>

      <div v-if="data.hint" class="meta ss-hint">{{ data.hint }}</div>

      <!-- 预演：先说清会写什么，再让作者点确认 -->
      <div v-if="preview" class="ss-preview">
        <div class="meta">
          预演：会新增 <b>{{ preview.added_pairs }}</b> 条引用登记（涉及
          {{ preview.added.length }} 个条目），写入前会自动备份设定集。
        </div>
        <div class="ss-preview-actions">
          <button class="mini" @click="preview = null">取消</button>
          <button class="mini primary" :disabled="!!acting" @click="runLinkCommit">确认写入</button>
        </div>
      </div>

      <div v-if="!staleItems.length && !newItems.length" class="meta ss-ok">
        没有待核，也没有未登记的新引用。
      </div>

      <div v-for="item in staleItems" :key="'s-' + item.kind + '-' + item.name" class="ss-item">
        <div class="ss-head">
          <span class="ss-tag bad">待核</span>
          <b>{{ item.name }}</b>
          <span class="ss-kind">{{ item.label }}</span>
          <span class="ss-excerpt" v-if="item.excerpt">{{ item.excerpt }}</span>
          <span class="spacer"></span>
          <button class="mini primary" :disabled="!!acting"
                  :title="'把这条条目的全部来源都标成已核对'"
                  @click="review(item, null, 'confirm', 'c-all-' + item.name)">
            已核对</button>
        </div>
        <div v-for="s in item.sources" :key="'s-' + item.name + '-' + s.chapter" class="ss-src">
          <span class="ss-ch" :class="{ muted: s.state === 'ok' }">{{ sourceText(s) }}</span>
          <span class="ss-time" v-if="s.mtime">{{ shortTime(s.mtime) }}</span>
          <span class="spacer"></span>
          <template v-if="s.state !== 'ok'">
            <button class="mini" :disabled="!!acting"
                    v-if="s.state === 'changed'"
                    @click="review(item, [s.chapter], 'confirm', 'c-' + item.name + '-' + s.chapter)">
              这一章也核对</button>
            <button class="mini danger" :disabled="!!acting"
                    :title="s.state === 'missing' ? '这一章已经不存在，去掉这条来源' : '这个引用是误匹配，去掉它'"
                    @click="review(item, [s.chapter], 'drop', 'd-' + item.name + '-' + s.chapter)">
              移除来源</button>
          </template>
        </div>
      </div>

      <div v-for="item in newItems" :key="'n-' + item.kind + '-' + item.name" class="ss-item ss-new">
        <div class="ss-head">
          <span class="ss-tag warn">新引用</span>
          <b>{{ item.name }}</b>
          <span class="ss-kind">{{ item.label }}</span>
          <span class="ss-excerpt" v-if="item.excerpt">{{ item.excerpt }}</span>
        </div>
        <div class="ss-src">
          <span class="ss-ch">第 {{ item.new_chapters.join("、") }} 章里有引用，尚未登记来源</span>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.ss-summary {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  align-items: baseline;
  margin: 10px 0 6px;
}
.ss-stat + .ss-stat::before {
  content: "·";
  margin-right: 10px;
  color: var(--muted);
}
.ss-stat b { font-size: 16px; }
.ss-mtime { margin-right: 8px; }
.ss-bad { color: var(--bad); }
.ss-warn { color: var(--warn); }
.ss-ok { color: var(--ok); }
.ss-hint { margin-bottom: 8px; }
.ss-unchecked {
  margin: 10px 0;
  padding: 10px 12px;
  border-radius: 8px;
  border-left: 3px solid var(--warn);
  background: var(--field-dim);
}
.ss-preview {
  margin: 8px 0 10px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: 8px;
  background: var(--field-dim);
}
.ss-preview-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 8px; }
.ss-item {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  margin: 8px 0;
  background: var(--field-inset);
}
.ss-item.ss-new { border-left: 3px solid var(--warn); }
.ss-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.ss-tag {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  border: 1px solid var(--border);
  background: var(--field);
  flex: none;
}
.ss-tag.bad { color: var(--bad); border-color: var(--bad); }
.ss-tag.warn { color: var(--warn); border-color: var(--warn); }
.ss-kind {
  font-size: 11.5px;
  padding: 1px 7px;
  border-radius: 4px;
  border: 1px solid var(--border);
  background: var(--field);
  color: var(--muted);
}
.ss-excerpt {
  font-size: 12.5px;
  color: var(--muted);
  max-width: 360px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ss-src {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
  margin-top: 6px;
  font-size: 12.5px;
  min-width: 0;
}
.ss-ch { word-break: break-word; }
.ss-ch.muted { color: var(--muted); }
.ss-time { color: var(--muted); font-size: 11.5px; }
</style>
