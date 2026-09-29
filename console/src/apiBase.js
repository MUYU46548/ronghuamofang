// 后端基址（唯一判据，别再各写一份）。
//
// Electron 生产环境：后端固定在本机 8765。
// 自动化 UI 验收 / 端口覆盖：用 window.__NF_API_BASE__ 指向别的实例。
//
// 2026-09-29 收敛：此前 App.vue 认 __NF_API_BASE__，而另外 6 个面板
// （StylePanel / ScrapsPanel / ReviewConsole / ProofreadPanel /
//   ParagraphRefinePanel / ChapterBlueprint）各自硬编码
// `const API = "http://127.0.0.1:8765";` —— 绕过覆盖点。
// 后果：端口一改、或跑浏览器视觉验收（后端在 8798/8799）时，
// 这些面板打向**没人监听的 8765**，console 里成片
// `net::ERR_CONNECTION_REFUSED`，而错误信息不带 URL，极难定位。
// 「同一判据写 7 遍、只改一处」是这类 bug 的温床，故收敛到这里。
export const API = (window.__NF_API_BASE__ || "http://127.0.0.1:8765").replace(/\/+$/, "");
