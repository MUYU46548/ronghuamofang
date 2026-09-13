<template>
  <!-- 章节蓝图编辑器：结构化编辑单章大纲 -->
  <div class="bp-layout">
    <!-- 左侧章节列表 -->
    <div class="bp-sidebar">
      <div class="bp-list-head">
        <span>章节列表</span>
        <button class="mini" @click="loadList">刷新</button>
      </div>
      <div class="bp-chapter-list">
        <div v-if="!chapters.length" class="bp-empty">暂无章节大纲</div>
        <div
          v-for="c in chapters"
          :key="c.n"
          class="bp-chapter-item"
          :class="{ active: currentN === c.n, dirty: dirty[c.n] }"
          @click="selectChapter(c.n)"
        >
          <span class="bp-no">{{ String(c.n).padStart(2, '0') }}</span>
          <span class="bp-ch-title">{{ c.title || '未命名' }}</span>
          <span v-if="dirty[c.n]" class="bp-dirty-mark">●</span>
        </div>
      </div>
    </div>

    <!-- 右侧编辑器 -->
    <div class="bp-main">
      <div v-if="!current" class="bp-empty-large">点击左侧章节开始编辑</div>

      <div v-else class="bp-editor">
        <!-- 工具栏 -->
        <div class="bp-toolbar">
          <h3>第 {{ currentN }} 章</h3>
          <div class="bp-actions">
            <label class="bp-toggle">
              <input type="checkbox" v-model="rawMode" />
              <span>原始 Markdown</span>
            </label>
            <button class="mini" @click="loadCurrent">重置</button>
            <button
              class="mini primary"
              :disabled="!isDirty || saving"
              @click="save"
            >
              {{ saving ? '保存中...' : '保存' }}
            </button>
          </div>
        </div>

        <!-- 原始模式 -->
        <div v-if="rawMode" class="bp-raw-editor">
          <textarea
            v-model="rawContent"
            class="bp-raw-area"
            spellcheck="false"
            @input="markDirty"
          ></textarea>
        </div>

        <!-- 结构化编辑器 -->
        <div v-else class="bp-form">
          <!-- 章节名 -->
          <div class="bp-field">
            <label>章节名</label>
            <input
              v-model="form.title"
              class="bp-input"
              placeholder="如：观测站的星光"
              @input="markDirty"
            />
          </div>

          <!-- 核心事件 -->
          <div class="bp-field">
            <label>核心事件 <span class="bp-hint">（至少 1 条）</span></label>
            <div class="bp-events">
              <div v-for="(ev, i) in form.events" :key="i" class="bp-event-row">
                <span class="bp-event-no">{{ i + 1 }}.</span>
                <input
                  v-model="form.events[i]"
                  class="bp-input"
                  placeholder="本章关键事件..."
                  @input="markDirty"
                />
                <button class="mini danger" @click="removeEvent(i)">×</button>
              </div>
            </div>
            <button class="mini" @click="addEvent">+ 添加事件</button>
          </div>

          <!-- 涉及角色 -->
          <div class="bp-field">
            <label>涉及角色 <span class="bp-hint">（至少 1 个）</span></label>
            <div class="bp-roles">
              <span v-for="(r, i) in form.roles" :key="i" class="bp-role-tag">
                {{ r }}
                <button class="bp-tag-remove" @click="removeRole(i)">×</button>
              </span>
              <input
                v-model="roleInput"
                class="bp-role-input"
                placeholder="输入角色名，回车添加..."
                @keydown.enter.prevent="addRole"
                @keydown.,.prevent="addRole"
              />
            </div>
            <div v-if="charSuggestions.length" class="bp-suggestions">
              <span class="bp-sugg-label">设定集角色：</span>
              <button
                v-for="c in charSuggestions"
                :key="c"
                class="mini"
                @click="addSuggestedRole(c)"
              >{{ c }}</button>
            </div>
          </div>

          <!-- 功能 -->
          <div class="bp-field">
            <label>功能</label>
            <select v-model="form.function" class="bp-select" @change="markDirty">
              <option value="">（选择本章功能）</option>
              <option v-for="f in FUNC_CHOICES" :key="f" :value="f">{{ f }}</option>
            </select>
          </div>

          <!-- 衔接 -->
          <div class="bp-field">
            <label>承接</label>
            <input
              v-model="form.carryover"
              class="bp-input"
              placeholder="承接上一章的什么结尾..."
              @input="markDirty"
            />
          </div>

          <div class="bp-field">
            <label>钩子</label>
            <input
              v-model="form.hook"
              class="bp-input"
              placeholder="为下一章埋下什么钩子..."
              @input="markDirty"
            />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue';

const API = 'http://127.0.0.1:8765';
const FUNC_CHOICES = ['铺垫', '推进', '转折', '高潮', '收束', 'transition'];

// ---- 状态 ----
const chapters = ref([]);
const currentN = ref(null);
const current = ref(null);        // 当前加载的原始 markdown
const rawContent = ref('');
const rawMode = ref(false);
const isDirty = ref(false);
const saving = ref(false);
const dirty = ref({});           // {n: true} 各章节脏状态

// 表单
const form = ref({
  title: '',
  events: [],
  roles: [],
  function: '',
  carryover: '',
  hook: '',
});
const roleInput = ref('');
const charSuggestions = ref([]); // 来自 setting.json 的角色建议

// ---- API ----
async function api(path, method = 'GET', body = null) {
  const opt = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opt.body = JSON.stringify(body);
  const r = await fetch(API + path, opt);
  let data = {};
  try { data = await r.json(); } catch (e) { /* empty */ }
  return { status: r.status, data };
}

// ---- 加载 ----
async function loadList() {
  const r = await api('/outline/chapters/list');
  if (r.status === 200) chapters.value = r.data.chapters || [];
}

async function loadSettingChars() {
  const r = await api('/setting/current');
  if (r.status === 200 && r.data.ok) {
    const chars = r.data.setting?.characters || [];
    charSuggestions.value = chars
      .map(c => c.name || c.id)
      .filter(Boolean)
      .slice(0, 30);
  }
}

async function selectChapter(n) {
  if (isDirty.value && !window.confirm('当前章节有未保存的修改，切换将丢失。继续？')) {
    return;
  }
  currentN.value = n;
  await loadCurrent();
}

async function loadCurrent() {
  const n = currentN.value;
  if (!n) return;
  const r = await api(`/outline/chapters/get?n=${n}`);
  if (r.status === 200 && r.data.ok) {
    current.value = r.data.content;
    rawContent.value = r.data.content;
    parseContent(r.data.content);
    isDirty.value = false;
  }
}

// ---- 解析 ----
function parseContent(md) {
  const f = { title: '', events: [], roles: [], function: '', carryover: '', hook: '' };

  // 标题
  const tm = md.match(/^#{1,2}\s*第?\s*\d*\s*章?\s*(.+?)\s*$/m);
  if (tm) f.title = tm[1].trim();

  // 核心事件
  const em = md.match(/-\s*核心事件[：:]\s*\n((?:\s*\d+[.、]\s*.+\n?)*)/);
  if (em) {
    const items = [...em[1].matchAll(/^\s*\d+[.、]\s*(.+?)\s*$/gm)];
    f.events = items.map(m => m[1].trim());
  }

  // 涉及角色
  const rm = md.match(/-\s*涉及角色[：:]\s*(.+)/);
  if (rm) {
    f.roles = rm[1].split(/[；;、,，]\s*/).map(s => s.trim()).filter(Boolean);
  }

  // 功能
  const fm = md.match(/-\s*功能[：:]\s*(.+)/);
  if (fm) f.function = fm[1].trim();

  // 衔接
  const cm = md.match(/-\s*衔接[：:]\s*([\s\S]*?)(?=\n## |\n- |\Z)/);
  if (cm) {
    let raw = cm[1].trim();
    if (raw.includes('钩子')) {
      const parts = raw.split(/钩子[：:]?\s*/);
      f.carryover = parts[0].trim().replace(/[；;。]+$/, '');
      f.hook = parts[1]?.trim() || '';
    } else {
      f.carryover = raw.replace(/[；;。]+$/, '');
    }
  }

  form.value = f;
}

// ---- 渲染 ----
function renderContent() {
  const f = form.value;
  if (!f.title) return '';

  const lines = [`## ${f.title}`, ''];

  if (f.events.length) {
    lines.push('- 核心事件：');
    f.events.forEach((ev, i) => lines.push(`  ${i + 1}. ${ev}`));
    lines.push('');
  }

  if (f.roles.length) {
    lines.push(`- 涉及角色：${f.roles.join('；')}`);
    lines.push('');
  }

  if (f.function) {
    lines.push(`- 功能：${f.function}`);
    lines.push('');
  }

  if (f.carryover || f.hook) {
    let carry = f.carryover.replace(/[；;。]+$/, '');
    let hook = f.hook ? `钩子——${f.hook}` : '';
    lines.push(`- 衔接：${[carry, hook].filter(Boolean).join('；')}`);
    lines.push('');
  }

  return lines.join('\n');
}

// ---- 操作 ----
function addEvent() {
  form.value.events.push('');
  markDirty();
}

function removeEvent(i) {
  form.value.events.splice(i, 1);
  markDirty();
}

function addRole() {
  const v = roleInput.value.trim();
  if (v && !form.value.roles.includes(v)) {
    form.value.roles.push(v);
    markDirty();
  }
  roleInput.value = '';
}

function removeRole(i) {
  form.value.roles.splice(i, 1);
  markDirty();
}

function addSuggestedRole(name) {
  if (!form.value.roles.includes(name)) {
    form.value.roles.push(name);
    markDirty();
  }
}

function markDirty() {
  isDirty.value = true;
  if (currentN.value) dirty.value[currentN.value] = true;
}

// ---- 保存 ----
async function save() {
  if (!currentN.value) return;
  let content;
  if (rawMode.value) {
    content = rawContent.value;
    if (!content.includes('核心事件')) return say('缺少「核心事件」字段');
    if (!content.includes('涉及角色')) return say('缺少「涉及角色」字段');
  } else {
    if (!form.value.title.trim()) return say('章节名不能为空');
    if (!form.value.events.length) return say('至少一条核心事件');
    if (!form.value.roles.length) return say('至少一个涉及角色');
    content = renderContent();
  }

  saving.value = true;
  try {
    const r = await api('/outline/chapters/save', 'POST', { n: currentN.value, content });
    if (r.status === 200 && r.data.ok) {
      say('已保存');
      isDirty.value = false;
      dirty.value[currentN.value] = false;
      // 刷新列表（标题可能变了）
      await loadList();
    } else {
      say('保存失败: ' + (r.data.error || r.status));
    }
  } catch (e) {
    say('保存失败: ' + e.message);
  } finally {
    saving.value = false;
  }
}

function say(msg) {
  // 简单 toast
  if (window._bpToast) window._bpToast(msg);
}

// ---- 初始化 ----
onMounted(async () => {
  await loadList();
  await loadSettingChars();
});

// 监听 raw 模式切换时同步
watch(rawMode, (v) => {
  if (v && current.value) {
    // 切换到 raw：用当前 rawContent（可能是用户刚编辑的）
  } else if (!v && currentN.value) {
    // 切回结构化：从 rawContent 解析
    parseContent(rawContent.value);
  }
});
</script>

<style scoped>
.bp-layout {
  display: flex;
  gap: 14px;
  min-height: 60vh;
}

/* ---- 左侧 ---- */
.bp-sidebar {
  width: 220px;
  flex-shrink: 0;
  border: 1px solid var(--border, #3a3a4a);
  border-radius: 6px;
  background: rgba(255,255,255,0.02);
  display: flex;
  flex-direction: column;
}
.bp-list-head {
  padding: 8px 10px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid var(--border, #3a3a4a);
  font-size: 12px;
  color: var(--muted-foreground, #aaa);
}
.bp-chapter-list {
  flex: 1;
  overflow-y: auto;
  max-height: 65vh;
  padding: 4px;
}
.bp-chapter-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  transition: background 0.15s;
}
.bp-chapter-item:hover {
  background: rgba(255,255,255,0.05);
}
.bp-chapter-item.active {
  background: rgba(124, 92, 200, 0.2);
  border-left: 2px solid #7c5cc8;
}
.bp-no {
  font-family: monospace;
  font-weight: 600;
  color: var(--muted-foreground, #999);
  min-width: 22px;
}
.bp-ch-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bp-dirty-mark {
  color: #f0a020;
  font-size: 8px;
}

/* ---- 右侧 ---- */
.bp-main {
  flex: 1;
  min-width: 0;
}
.bp-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border, #3a3a4a);
}
.bp-toolbar h3 {
  margin: 0;
  font-size: 15px;
}
.bp-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.bp-toggle {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  cursor: pointer;
  color: var(--muted-foreground, #aaa);
}
.bp-toggle input { margin: 0; }

/* ---- 表单 ---- */
.bp-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.bp-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.bp-field label {
  font-size: 12px;
  font-weight: 500;
  color: var(--muted-foreground, #bbb);
}
.bp-hint {
  font-weight: normal;
  opacity: 0.6;
  font-size: 11px;
}
.bp-input {
  background: rgba(255,255,255,0.04);
  border: 1px solid var(--border, #444);
  border-radius: 4px;
  padding: 6px 8px;
  color: var(--foreground, #eee);
  font-size: 13px;
  width: 100%;
  box-sizing: border-box;
}
.bp-input:focus {
  outline: none;
  border-color: #7c5cc8;
  box-shadow: 0 0 0 1px rgba(124, 92, 200, 0.3);
}
.bp-select {
  background: rgba(255,255,255,0.04);
  border: 1px solid var(--border, #444);
  border-radius: 4px;
  padding: 6px 8px;
  color: var(--foreground, #eee);
  font-size: 13px;
}

/* 事件列表 */
.bp-events {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.bp-event-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.bp-event-no {
  min-width: 18px;
  font-size: 12px;
  color: var(--muted-foreground, #999);
}
.bp-event-row .bp-input {
  flex: 1;
}

/* 角色标签 */
.bp-roles {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 6px;
  background: rgba(255,255,255,0.03);
  border: 1px solid var(--border, #444);
  border-radius: 4px;
  min-height: 34px;
}
.bp-role-tag {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 8px;
  background: rgba(124, 92, 200, 0.2);
  border: 1px solid rgba(124, 92, 200, 0.5);
  border-radius: 12px;
  font-size: 12px;
}
.bp-tag-remove {
  background: none;
  border: none;
  color: var(--muted-foreground, #aaa);
  cursor: pointer;
  padding: 0;
  font-size: 14px;
  line-height: 1;
}
.bp-tag-remove:hover { color: #f06; }
.bp-role-input {
  flex: 1;
  min-width: 80px;
  background: transparent;
  border: none;
  color: var(--foreground, #eee);
  font-size: 12px;
  padding: 2px 4px;
  outline: none;
}

.bp-suggestions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}
.bp-sugg-label {
  font-size: 11px;
  color: var(--muted-foreground, #999);
}

/* 原始编辑器 */
.bp-raw-editor {
  height: 60vh;
}
.bp-raw-area {
  width: 100%;
  height: 100%;
  background: rgba(0,0,0,0.2);
  border: 1px solid var(--border, #444);
  border-radius: 4px;
  padding: 10px;
  color: var(--foreground, #eee);
  font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
  font-size: 13px;
  resize: none;
  box-sizing: border-box;
  line-height: 1.6;
}

/* 空状态 */
.bp-empty, .bp-empty-large {
  text-align: center;
  color: var(--muted-foreground, #888);
  font-size: 12px;
  padding: 20px 8px;
}
.bp-empty-large {
  font-size: 14px;
  padding: 60px 20px;
}
</style>
