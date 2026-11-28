<template>
  <div data-panel="setting-stale" class="card">
    <div class="card-head">
      <h3>设定引用核对</h3>
      <span class="spacer"></span>
      <span class="meta mr-2" v-if="data && data.setting_mtime">
        设定集改于 {{ shortTime(data.setting_mtime) }}</span>
      <UiButton :disabled="busy" @click="load">{{ busy ? "读取中…" : "刷新" }}</UiButton>
      <UiButton :disabled="!!acting" @click="runLinkPreview">
        {{ acting === "link" ? "扫描中…" : "扫描章节并记来源" }}</UiButton>
    </div>
    <div class="meta">
      每条设定会记下“依据哪一章、那一章当时是什么版本”。你改完正文，引用它的条目会自己
      出现在待核清单里 —— <b>点“已核对”只代表你看过、还成立</b>；不成立就去改设定本身。
      零 token，不调模型。
    </div>

    <div v-if="err" class="empty py-3.5">{{ err }}</div>
    <div v-else-if="!data" class="meta py-2.5">读取中…</div>

    <!-- checked=false 必须显式说出来：空清单 != 没有待核 -->
    <div v-else-if="!data.checked"
         class="my-2.5 rounded-[var(--r-sm)] border-l-[3px] border-[var(--warn)] bg-[var(--field-dim)] px-3 py-2.5">
      没查成：{{ data.reason || "原因未知" }}
    </div>

    <template v-else>
      <!-- 汇总：数字 + 颜色。0 条待核时不该显示红色（否则每天都是"一片红"）。 -->
      <div class="my-2.5 flex flex-wrap items-baseline gap-3">
        <span class="meta"><b class="text-[18px] text-[color:var(--ink)]">{{ counts.entries ?? 0 }}</b> 条设定</span>
        <span class="meta"><b class="text-[18px] text-[color:var(--ink)]">{{ counts.quoted ?? 0 }}</b> 条登记过来源</span>
        <span class="meta"><b class="text-[18px]" :class="counts.stale ? 'text-[color:var(--bad)]' : 'text-[color:var(--ink)]'">{{ counts.stale ?? 0 }}</b> 条待核</span>
        <span class="meta"><b class="text-[18px]" :class="counts.new ? 'text-[color:var(--warn)]' : 'text-[color:var(--ink)]'">{{ counts.new ?? 0 }}</b> 条新引用</span>
        <span class="meta"><b class="text-[18px] text-[color:var(--ink)]">{{ data.chapters ?? 0 }}</b> 章已扫</span>
        <span class="meta" v-if="counts.absent"><b class="text-[18px] text-[color:var(--ink)]">{{ counts.absent }}</b> 条未进正文</span>
        <span class="meta" v-if="counts.unlocatable"><b class="text-[18px] text-[color:var(--ink)]">{{ counts.unlocatable }}</b> 条名字太短无法定位</span>
      </div>

      <div v-if="data.hint" class="meta mb-2">{{ data.hint }}</div>

      <!-- 预演：先说清会写什么，再让作者点确认 -->
      <div v-if="preview"
           class="my-2 rounded-[var(--r-sm)] border border-l-[3px] border-[var(--border)] border-l-[var(--accent)] bg-[var(--field-dim)] px-3 py-2.5">
        <div class="meta">
          预演：会新增 <b>{{ preview.added_pairs }}</b> 条引用登记（涉及
          {{ preview.added.length }} 个条目），写入前会自动备份设定集。
        </div>
        <div class="mt-2 flex justify-end gap-2">
          <UiButton @click="preview = null">取消</UiButton>
          <UiButton variant="primary" :disabled="!!acting" @click="runLinkCommit">确认写入</UiButton>
        </div>
      </div>

      <div v-if="!staleItems.length && !newItems.length" class="meta text-[color:var(--ok)]">
        没有待核，也没有未登记的新引用。
      </div>

      <div v-for="item in staleItems" :key="'s-' + item.kind + '-' + item.name"
           class="my-2 rounded-[var(--r-sm)] border border-[var(--border)] bg-[var(--field-inset)] px-3 py-2.5">
        <div class="flex flex-wrap items-baseline gap-2.5">
          <UiBadge variant="bad">待核</UiBadge>
          <b class="text-[color:var(--ink)]">{{ item.name }}</b>
          <UiBadge variant="neutral">{{ item.label }}</UiBadge>
          <span v-if="item.excerpt"
                class="max-w-[360px] truncate text-[length:var(--fs-ui)] text-[color:var(--muted)]"
                :title="item.excerpt">{{ item.excerpt }}</span>
          <span class="spacer"></span>
          <UiButton variant="primary" :disabled="!!acting"
                    title="把这条条目的全部来源都标成已核对"
                    @click="review(item, null, 'confirm', 'c-all-' + item.name)">已核对</UiButton>
        </div>
        <div v-for="s in item.sources" :key="'s-' + item.name + '-' + s.chapter"
             class="mt-1.5 flex min-w-0 flex-wrap items-center gap-2.5 text-[length:var(--fs-ui)]">
          <span class="break-words" :class="s.state === 'ok' ? 'text-[color:var(--muted)]' : 'text-[color:var(--ink)]'">{{ sourceText(s) }}</span>
          <span v-if="s.mtime" class="text-[12px] text-[color:var(--muted)]">{{ shortTime(s.mtime) }}</span>
          <span class="spacer"></span>
          <template v-if="s.state !== 'ok'">
            <UiButton v-if="s.state === 'changed'" :disabled="!!acting"
                      @click="review(item, [s.chapter], 'confirm', 'c-' + item.name + '-' + s.chapter)">
              这一章也核对</UiButton>
            <UiButton variant="danger" :disabled="!!acting"
                      :title="s.state === 'missing' ? '这一章已经不存在，去掉这条来源' : '这个引用是误匹配，去掉它'"
                      @click="review(item, [s.chapter], 'drop', 'd-' + item.name + '-' + s.chapter)">
              移除来源</UiButton>
          </template>
        </div>
      </div>

      <!-- 四种引用状态在图例里说清，别让作者从颜色猜 -->
      <div class="meta mt-2 opacity-80">
        来源状态：<b>未改</b>（灰字）· <b>这一章改过</b>（可“这一章也核对”）·
        <b>这一章没了</b> 或 <b>误匹配</b>（可“移除来源”）
      </div>
    </template>
  </div>
</template>

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

import UiBadge from "./components/ui/UiBadge.vue";
import UiButton from "./components/ui/UiButton.vue";

const props = defineProps({ api: { type: Function, required: true } });
const emit = defineEmits(["say"]);
function say(m) { emit("say", m); }

const data = ref(null);
const busy = ref(false);
const err = ref("");
const acting = ref("");
const preview = ref(null);          // link 的预演结果（确认前不写盘）
const impact = ref(null);           // 反方向：设定改过之后，哪几章已检查稿要重看

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
  // 反方向（P1-5）：设定条目改过之后，哪些已检查的章要重看。
  // 单独一个请求：它挂了不该把上面那份待核清单也变成读失败。
  try {
    const ri = await props.api("/setting/impact");
    impact.value = (ri.status === 200 && ri.data && ri.data.checked !== undefined)
      ? ri.data : null;
  } catch (e) {
    impact.value = null;
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
