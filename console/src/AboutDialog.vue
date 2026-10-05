<script setup>
// 「关于」弹窗（发布级必备）：版本 / 运行环境 / 数据位置 / 快速上手 / 许可与链接。
// 入口：左上角 LOGO 或应用名点击；命令面板「关于绒花墨坊」。
import { ref, watch } from "vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  api: { type: Function, required: true },   // App.vue 的 api(path, method, body)
  book: { type: String, default: "" },
});
const emit = defineEmits(["close", "goto", "disclaimer"]);

const info = ref(null);
const loading = ref(false);
const err = ref("");
const app = ref(null);   // Electron 侧信息（打包版才有，浏览器预览为 null）

// Electron/Chrome 版本从 UA 里取（Electron 的 UA 自带 Electron/x.y.z）
const ua = navigator.userAgent || "";
const uaMatch = (re) => (ua.match(re) || [])[1] || "-";
const electronVer = uaMatch(/Electron\/([\d.]+)/);
const chromeVer = uaMatch(/Chrome\/([\d.]+)/);
const isElectron = !!window.mofangAPI;

async function load() {
  loading.value = true;
  err.value = "";
  const r = await props.api("/about");
  loading.value = false;
  if (r.status === 200 && r.data?.ok) info.value = r.data;
  else err.value = r.data?.error || ("HTTP " + r.status);
  // Electron 侧信息（可选）：打包版能拿到真实 Electron 版本与用户数据目录
  if (window.mofangAPI?.appAbout) {
    try {
      app.value = await window.mofangAPI.appAbout();
    } catch (e) { app.value = null; }
  }
}

watch(() => props.open, (v) => { if (v) load(); });

function openDir(rel) {
  if (!window.mofangAPI?.openArtifact) return;
  window.mofangAPI.openArtifact(rel).then((r) => {
    if (!r.ok) alert("打不开：" + (r.error || ""));
  });
}

function openExternal(url) {
  if (window.mofangAPI?.openExternal) window.mofangAPI.openExternal(url);
}

// ---------- 版本更新（与设置页共用主进程 IPC，判定口径只有一处） ----------
// 以前「关于」里只有「下载新版本」外链 —— 用户找不到「检查更新」，
// 会以为这功能被砍了。这里补上，并把失败原因说清楚（不再只显示一行错误码）。
const updStatus = ref("");
const updChecking = ref(false);

// 每次重新打开都清掉上一次的检查结果（避免显示陈旧提示）
watch(() => props.open, (v) => { if (v) updStatus.value = ""; });

async function checkUpdate() {
  if (!window.mofangAPI?.updaterCheck) {
    updStatus.value = "当前为浏览器预览，不支持自动更新";
    return;
  }
  try {
    const st = await window.mofangAPI.updaterStatus();
    if (st?.dev) {
      updStatus.value = "开发模式不支持自动更新（仅安装版可用）";
      return;
    }
    if (st && st.initialized === false) {
      updStatus.value = "本机未启用自动更新，请用「下载新版本」";
      return;
    }
    updChecking.value = true;
    updStatus.value = "正在检查…";
    const r = await window.mofangAPI.updaterCheck();
    if (r?.ok) {
      const v = r.version || "", cur = r.currentVersion || "";
      updStatus.value = (v && cur && v !== cur)
        ? `发现新版本 ${v}（正在下载，完成后可重启安装）`
        : `已是最新版本（v${cur || "?"}）`;
    } else if (r?.noUpdate) {
      updStatus.value = r.error || "暂无更新（当前已是最新版本）";
    } else {
      updStatus.value = "检查失败：" + ((r && r.error) || "未知错误");
    }
  } catch (e) {
    updStatus.value = "检查失败：" + String((e && e.message) || e);
  } finally {
    updChecking.value = false;
  }
}

const STEPS = [
  { t: "放素材", d: "设定卡进 materials/raw/；随手写的碎片进 materials/original_scraps/" },
  { t: "建项目", d: "「项目」页签 →「＋ 新建项目」（书名/类型/章数，自动归档旧项目）" },
  { t: "跑流水线", d: "「流水线」→「执行下一步」（Space）；审批门在「收件箱」" },
  { t: "验收交付", d: "审稿逐条决策 → 校对体检 → 导出 Word 成品" },
];
</script>

<template>
  <div v-if="open" class="drawer-mask" @click.self="$emit('close')">
    <div class="dialog about-dlg">
      <div class="about-head">
        <span class="brand-mark">绒</span>
        <div>
          <div class="about-title">绒花墨坊<span class="about-ver">v{{ info?.version || "…" }}</span></div>
          <div class="meta">全自动长篇小说生成系统 · 内部代号 NovelForge</div>
        </div>
        <span class="spacer"></span>
      </div>

      <div class="about-scroll">
        <div v-if="loading" class="empty">读取中…</div>
        <div v-else-if="err" class="meta" style="color: var(--bad);">
          读取版本信息失败：{{ err }}（后端未连接时属正常）
        </div>

        <template v-else-if="info">
          <p class="about-lead">
            输入混沌素材，输出结构化长篇小说（Markdown → Word）：七阶段流水线自主执行，
            你只保留审批权（暂停 / 打回 / 重跑），成本逐笔记账并可熔断。
          </p>

          <h4 class="about-h">快速上手</h4>
          <ol class="about-steps">
            <li v-for="s in STEPS" :key="s.t"><b>{{ s.t }}</b><span>{{ s.d }}</span></li>
          </ol>

          <h4 class="about-h">运行环境</h4>
          <table class="cost-table">
            <tbody>
              <tr><td style="width:130px;">绒花墨坊</td><td>v{{ info.version }}</td>
                  <td style="width:110px;">Python</td><td>{{ info.python }}</td></tr>
              <tr><td>Electron</td><td>{{ app?.electron || (isElectron ? electronVer : "（浏览器预览）") }}</td>
                  <td>平台</td><td>{{ info.platform }}</td></tr>
              <tr><td>Chromium</td><td>{{ app?.chrome || chromeVer }}</td>
                  <td>当前项目</td><td>{{ book || "（未命名）" }}</td></tr>
              <tr><td>运行方式</td>
                  <td>{{ app ? (app.packaged ? "安装版（打包）" : "源码运行") : "浏览器预览" }}</td>
                  <td>API 端口</td><td>{{ app?.apiPort || 8765 }}</td></tr>
            </tbody>
          </table>

          <h4 class="about-h">数据位置（全部在本机，不上传）</h4>
          <div class="about-path">
            <div class="ap-row">
              <span class="ap-label">项目根</span>
              <code>{{ info.project_root }}</code>
              <button class="mini" @click="openDir('config/project.yaml')">打开</button>
            </div>
            <div class="ap-row">
              <span class="ap-label">运行数据</span>
              <code>{{ info.data_dir }}</code>
              <button class="mini" @click="openDir('data/books')">打开</button>
            </div>
            <div class="ap-row">
              <span class="ap-label">成品输出</span>
              <code>{{ info.output_dir }}</code>
              <button class="mini" @click="openDir('output')">打开</button>
            </div>
            <div v-if="app?.userDataDir" class="ap-row">
              <span class="ap-label">应用数据</span>
              <code>{{ app.userDataDir }}</code>
              <span style="width:52px;"></span>
            </div>
          </div>
          <div class="meta">
            · 数据全在本机：快照在 history/，章节备份在 data/chapters/history/；除调用你自己配置的模型 API 外不向外发送数据<br>
            · API Key 只存项目 `.env`（不进版本库，界面内从不显示明文）；Obsidian 设定库固定只读
          </div>

          <h4 class="about-h">版本更新</h4>
          <div class="about-links">
            <button class="mini" @click="checkUpdate" :disabled="updChecking">
              {{ updChecking ? "检查中…" : "检查更新" }}
            </button>
            <button class="mini" @click="openExternal(info.repo + '/releases')">下载新版本</button>
          </div>
          <div class="meta" style="margin-top: 6px;">
            {{ updStatus || `当前 v${info.version}；检查更新需要能访问 GitHub Releases` }}
          </div>

          <h4 class="about-h">许可与链接</h4>
          <div class="about-links">
            <button class="mini" @click="openExternal(info.repo)">GitHub 仓库</button>
            <button class="mini" @click="openExternal(info.repo + '/releases')">下载新版本</button>
            <button class="mini" @click="openExternal(info.repo + '/blob/main/LICENSE')">开源许可（MIT）</button>
            <button class="mini" @click="openExternal(info.repo + '/issues')">问题反馈</button>
            <button class="mini" @click="$emit('disclaimer')" title="AI 订阅合规 / 内容权属 / 风险自担 · 首次使用需确认">免责声明</button>
            <button class="mini" @click="$emit('close'); $emit('goto', 'settings')">设置与密钥</button>
          </div>
          <div class="meta" style="margin-top: 8px;">
            © 2026 暮雨（MUYU46548）。本程序以 MIT 许可发布；内置第三方组件：
            Electron（MIT）、Vue 3（MIT）、python-docx（MIT）、lxml（BSD-3）、PyYAML（MIT），
            完整清单见仓库 THIRD-PARTY.md。
          </div>
        </template>
      </div>

      <div class="dialog-actions">
        <span class="meta">绒花墨坊 v{{ info?.version || "…" }}</span>
        <span class="spacer"></span>
        <button class="mini" @click="$emit('close'); $emit('goto', 'pipeline')">前往流水线</button>
        <button class="mini primary" @click="$emit('close')">关闭</button>
      </div>
    </div>
  </div>
</template>
