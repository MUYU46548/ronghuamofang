/**
 * 草稿（先存草稿、显式发送才进对话）。
 *
 * 为什么要有这么一层（2026-11-15，抄 Papilot 第 ⑥ 条）：
 * 写在输入框里的东西，不该因为关掉这个框、切到另一处、或者刷新一下就没；
 * 也不该因为切到别的主题/章节就**串过去**发给不对的地方。
 * 而且“发没发出去”作者自己得看得见 —— 看着像说过了、其实只是草稿，是最误人的一种。
 *
 * 落点：localStorage（本机、不含正文、不进 Git）。键一律带前缀，按位置分开存。
 * 纪律：只有**发送成功**才清草稿；失败保留（不然重打一遍）。
 */
import { ref } from "vue";

const PREFIX = "mofang_draft:";

/** 全局版本号：任何一次存/清都 +1，界面据此重算“有没有未发送的草稿”。 */
export const draftsVersion = ref(0);

function nowStamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function draftKey(...parts) {
  return PREFIX + parts.map((x) => String(x == null ? "" : x)).join("|");
}

export function loadDraft(key) {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return { text: "", savedAt: "" };
    const d = JSON.parse(raw);
    return { text: String(d.text || ""), savedAt: String(d.savedAt || "") };
  } catch (_) {
    return { text: "", savedAt: "" };
  }
}

export function saveDraft(key, text) {
  if (!key) return;
  try {
    if (!String(text || "").trim()) {
      localStorage.removeItem(key);
    } else {
      localStorage.setItem(key, JSON.stringify({ text, savedAt: nowStamp() }));
    }
    draftsVersion.value++;
  } catch (_) { /* 存储满了/被禁用：功能降级，不炸界面 */ }
}

export function clearDraft(key) {
  if (!key) return;
  try {
    localStorage.removeItem(key);
    draftsVersion.value++;
  } catch (_) { /* 同上 */ }
}

export function hasDraft(key) {
  // 读一下版本号，让调用方的 computed 跟着变（否则界面不知道草稿被存/清了）
  void draftsVersion.value;
  return Boolean(loadDraft(key).text.trim());
}
