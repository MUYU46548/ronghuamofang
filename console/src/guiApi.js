// GUI 写请求的统一入口（2026-11-04）。
//
// 为什么需要：`agent_mode: true` 时，写端点要求**双因子**（来源头 + 主进程签发的
// token）。各面板自己写 fetch 只会带来源头，于是被判成"未受信来源" → 403，
// 而且报错只写 HTTP 403，看着像后端坏了。
//
// 判据唯一来源：App.vue 里的 api() 用的是同一套（getGuiToken + 两个头）。
// 新面板一律用这里的 postJSON，不要自己拼 fetch。
import { API } from "./apiBase.js";

let _tok = "";

async function guiToken() {
  if (_tok) return _tok;
  try {
    _tok = (await window.mofangAPI?.getGuiToken?.()) || "";
  } catch {
    _tok = "";
  }
  return _tok;
}

export async function postJSON(path, body) {
  const headers = { "Content-Type": "application/json", "X-Mofang-Source": "gui" };
  const tok = await guiToken();
  if (tok) headers["X-Mofang-Token"] = tok;
  const r = await fetch(API + path, {
    method: "POST",
    headers,
    body: JSON.stringify(body || {}),
  });
  const data = await r.json().catch(() => ({}));
  // 2xx 都算提交成功：有些写端点是异步的（202 + job_id，例：/outline/sample/candidates），
  // 把 202 当失败会让"点了生成"直接弹一句 HTTP 202 —— 看着像坏了。
  if (r.status < 200 || r.status >= 300 || data.ok === false) {
    throw new Error(data.error || "HTTP " + r.status);
  }
  return data;
}
