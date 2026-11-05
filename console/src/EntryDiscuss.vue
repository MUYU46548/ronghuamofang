<template>
  <div v-if="open" class="drawer-mask" @click.self="close">
    <div class="drawer ed">
      <div class="drawer-head">
        <b>{{ kindLabel }} · {{ name }}</b>
        <span class="pill" :class="thread && thread.status === 'decided' ? 'st-done' : 'st-gate'">
          {{ thread ? (thread.status === "decided" ? "已定稿" : "讨论中") : "未开题" }}
        </span>
        <span class="spacer"></span>
        <button class="mini" @click="reload">刷新</button>
        <button class="mini" @click="close">关闭</button>
      </div>

      <div class="ed-body">
        <div class="meta ed-note">
          讨论的结论<b>不会自动进章节写作提示词</b>：它是“这条设定要不要改”的商量过程。
          定稿后你可以在这里直接<b>写回设定集</b>（改条目状态为已采用 / 已否决）。
        </div>

        <!-- 未开题 -->
        <div v-if="!thread" class="ed-open">
          <input v-model="topic" class="text-input" placeholder="这条要解决什么（例：他的年龄要不要改大两岁）" />
          <button class="mini primary" :disabled="busy || !topic.trim()" @click="openThread">开题</button>
        </div>

        <!-- 已有讨论 -->
        <template v-else>
          <div class="ed-topic"><b>{{ thread.topic }}</b>
            <span class="meta">· {{ (thread.rounds || []).length }} 轮</span>
          </div>
          <div class="ed-rounds">
            <div v-for="(r, i) in thread.rounds || []" :key="i" class="ed-round" :class="'r-' + r.role">
              <span class="pill">{{ r.role === "author" ? "作者" : "AI" }}</span>
              <span class="ed-text">{{ r.text }}</span>
              <span class="meta">{{ (r.ts || "").replace("T", " ") }}</span>
            </div>
            <div v-if="!(thread.rounds || []).length" class="meta">还没有发言。</div>
          </div>

          <textarea v-model="sayText" rows="3" class="ed-say"
                    placeholder="这一轮说什么（AI 的建议 / 作者的取舍）"></textarea>
          <div class="ed-actions">
            <button class="mini" :disabled="busy || !sayText.trim()" @click="say('ai')">以 AI 身份说</button>
            <button class="mini primary" :disabled="busy || !sayText.trim()" @click="say('author')">以作者身份说</button>
            <span class="meta">轮次只追加，不改写历史。</span>
          </div>

          <details class="ed-decide" :open="thread.status === 'decided'">
            <summary>定稿（作者拍板）</summary>
            <p class="meta">至少写一条；一条都没有不算结论。</p>
            <textarea v-model="dec.must" rows="2" placeholder="必须成立（每行一条）"></textarea>
            <textarea v-model="dec.must_not" rows="2" placeholder="必须不成立 / 禁止（每行一条）"></textarea>
            <textarea v-model="dec.summary" rows="2" placeholder="一句话结论"></textarea>
            <button class="mini primary" :disabled="busy" @click="decide">定稿</button>
            <div v-if="thread.decision && thread.decision.summary" class="ed-decision">
              已定稿：{{ thread.decision.summary }}
            </div>
          </details>

          <!-- 写回设定集：复用 /setting/status（adopt / reject），素材没有状态字段 -->
          <div v-if="isSetting" class="ed-writeback">
            <div class="ed-title">写回设定集</div>
            <div class="meta">把这条的状态改成已采用 / 已否决（与“设定 → 素材裁决”同一套规则）。</div>
            <input v-model="reason" class="text-input" placeholder="理由（否决必填）" />
            <button class="mini primary" :disabled="busy" @click="writeback('adopt')">标为已采用</button>
            <button class="mini danger" :disabled="busy || !reason.trim()" @click="writeback('reject')">标为已否决</button>
          </div>
          <div v-else class="meta ed-note">
            素材文件没有“状态”字段：讨论结论保存在 data/state/entry_discuss.json，
            改内容请直接编辑素材（改完触发重归并才会进设定集）。
          </div>
        </template>

        <div v-if="msg" class="ed-msg">{{ msg }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue";

const props = defineProps({
  api: { type: Function, required: true },
  kind: { type: String, required: true },
  name: { type: String, required: true },
  kindLabel: { type: String, default: "" },
});
const emit = defineEmits(["say", "close"]);

const open = ref(false);
const thread = ref(null);
const topic = ref("");
const sayText = ref("");
const dec = ref({ must: "", must_not: "", summary: "" });
const reason = ref("");
const busy = ref(false);
const msg = ref("");

const isSetting = computed(() => props.kind !== "materials");

function payload(r) {
  if (!r || r.status !== 200) {
    throw new Error((r && r.data && r.data.error) || (r && r.status) || "请求失败");
  }
  return r.data || {};
}

async function reload() {
  try {
    const d = payload(await props.api(
      "/discuss/entry?kind=" + encodeURIComponent(props.kind)
      + "&name=" + encodeURIComponent(props.name)));
    const hit = (d.items || []).find((t) => t.kind === props.kind && t.name === props.name);
    thread.value = hit || null;
    if (hit && hit.decision) dec.value = { ...dec.value, ...hit.decision };
  } catch (e) {
    msg.value = "读取失败：" + e.message;
  }
}

async function show() {
  open.value = true;
  msg.value = "";
  await reload();
}
async function close() {
  open.value = false;
  emit("close");
}

async function openThread() {
  busy.value = true;
  try {
    payload(await props.api("/discuss/entry/open", "POST",
                            { kind: props.kind, name: props.name, topic: topic.value.trim() }));
    topic.value = "";
    await reload();
    emit("say", "已开题（只写 data/state/entry_discuss.json，没碰设定集）");
  } catch (e) {
    msg.value = "开题失败：" + e.message;
  } finally {
    busy.value = false;
  }
}

async function say(role) {
  busy.value = true;
  try {
    payload(await props.api("/discuss/entry/say", "POST",
                            { kind: props.kind, name: props.name, role, text: sayText.value.trim() }));
    sayText.value = "";
    await reload();
  } catch (e) {
    msg.value = "发言失败：" + e.message;
  } finally {
    busy.value = false;
  }
}

async function decide() {
  busy.value = true;
  try {
    payload(await props.api("/discuss/entry/decide", "POST",
                            { kind: props.kind, name: props.name,
                              must: dec.value.must, must_not: dec.value.must_not,
                              summary: dec.value.summary }));
    await reload();
    emit("say", "已定稿（结论可用于写回设定集）");
  } catch (e) {
    msg.value = "定稿失败：" + e.message;
  } finally {
    busy.value = false;
  }
}

async function writeback(action) {
  busy.value = true;
  try {
    const d = payload(await props.api("/setting/status", "POST",
                                      { name: props.name, action, reason: reason.value.trim() }));
    msg.value = d.message || "已写回设定集";
    emit("say", "已" + (action === "adopt" ? "标记为已采用" : "标记为已否决"));
  } catch (e) {
    msg.value = "写回失败：" + e.message;
  } finally {
    busy.value = false;
  }
}

defineExpose({ show });
</script>

<style scoped>
.ed { width: min(760px, 94vw); height: min(82vh, 820px); display: flex; flex-direction: column; }
.ed-body { padding: 10px 14px 14px; overflow: auto; display: flex; flex-direction: column; gap: 10px; }
.ed-note { line-height: 1.7; padding: 8px 10px; border-left: 2px solid var(--border);
  background: rgba(155, 143, 196, 0.08); }
.ed-open { display: flex; gap: 8px; }
.ed-topic { font-size: 14px; }
.ed-rounds { display: flex; flex-direction: column; gap: 8px; }
.ed-round { padding: 8px 10px; border: 1px solid var(--border); border-radius: 8px; font-size: 13px; line-height: 1.7; }
.ed-round.r-author { background: rgba(155, 143, 196, 0.10); }
.ed-text { display: block; margin: 4px 0; white-space: pre-wrap; }
.ed-say, .ed-decide textarea { width: 100%; padding: 8px 10px; font-size: 13px; line-height: 1.7;
  border: 1px solid var(--border); border-radius: 6px; background: var(--field); color: var(--ink); }
.ed-actions, .ed-writeback { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.ed-decide { border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; }
.ed-decide summary { cursor: pointer; font-weight: 600; }
.ed-decision { margin-top: 6px; font-size: 12.5px; color: var(--ok); }
.ed-title { font-weight: 600; }
.ed-writeback input { flex: 1; min-width: 160px; }
.ed-msg { font-size: 12.5px; color: var(--bad); }
</style>
