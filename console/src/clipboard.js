/* 复制到剪贴板的**唯一**实现（2026-10-18 抽出）。
 *
 * 为什么抽出来：Electron 渲染进程在 contextIsolation 下 `navigator.clipboard`
 * 可能不可用（无焦点 / 未授权 / 平台差异），裸调 writeText 会抛异常 →
 * 「点了复制没反应」。所以必须有回退路径。
 *
 * 历史坑：App.vue 的 copyText 早就做了这个回退，但组件各自实现时（如
 * ChapterBlueprint 的「讨论此章」）又裸调了一次 navigator.clipboard ——
 * 同一个坑踩第二遍。现在收敛到这里，新组件一律 import 本模块。
 *
 * 用法：
 *   import { copyToClipboard } from "./clipboard.js";
 *   const ok = await copyToClipboard(text);   // 失败返回 false，不抛
 */
export async function copyToClipboard(text) {
  // 首选：异步 Clipboard API
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) {
    /* 落到回退路径 */
  }
  // 回退：隐藏 textarea + execCommand（Electron 里稳定可用）
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch (e) {
    return false;
  }
}
