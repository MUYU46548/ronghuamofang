// 绒花墨坊 — 提示词升级判据（纯函数，node 可直测，不需要 electron）
//
// 为什么单独成文件（2026-11-28）：`SEED_KEEP_USER_EDITS = ["prompts"]` 的本意是
// “用户改过的提示词不能被升级覆盖”。但判据用的是 `factory_manifest.json` 里记的
// sha256 —— 而那份清单**只记 `prompts/factory/*`（18 条），顶层 `prompts/*.md`
// 一条都没记**（播种时 factory/ 走 force 刷新分支，不进 seededRels）。
// 于是 `recorded` 恒为 undefined → 所有顶层提示词永远被判成“用户改过” → 永远不更新。
//
// 实测后果（0.9.15 工作区）：`stage4_expand` / `stage4_group` / `stage4_refine` /
// `stage6_polish` 四份停在 11-24 的旧版；把它们的字节拿去 git 历史里比对，
// 能逐字节匹配到旧提交 —— 说明不是用户改的，是升级没刷。
//
// 修法：判“用户改没改过”时补第二路参照 —— **出厂副本 `prompts/factory/<同名>`**。
// 盘上内容若与出厂副本完全相同，相等即“上一版的官方原文”，用户没动过，跟着新版走。
// 这正是 `prompts/factory/` 早就存在的意义（“还原出厂默认”就是读它）。
//
// 三路判据，缺一不可：
//   ① 清单记的 sha == 盘上        → 没动过 → 跟随新版
//   ② 出厂副本 sha == 盘上        → 盘上就是上一版官方原文 → 跟随新版
//   ③ 其余（含读不出 sha、老工作区无清单）→ 判为用户改过，一个字不动

const PROMPTS_PREFIX = "prompts/";
const FACTORY_PREFIX = "prompts/factory/";

/**
 * 这个文件该不该跟着新版更新？
 *
 * @param {object} p
 * @param {string} p.rel        相对工作区根的路径，正斜杠，例如 "prompts/stage4_writing.md"
 * @param {string|null} p.curSha      盘上当前文件的 sha256（读不出传 null）
 * @param {string|null} p.recordedSha 出厂清单里记的 sha256（没记录传 null/undefined）
 * @param {string|null} p.factorySha  出厂副本 prompts/factory/<同名> 的 sha256（不存在传 null）
 * @returns {{follow: boolean, reason: string}}
 */
function decideRefresh({ rel, curSha, recordedSha, factorySha }) {
  if (!curSha) return { follow: false, reason: "unreadable-current" };
  if (recordedSha && recordedSha === curSha) {
    return { follow: true, reason: "manifest-match" };
  }
  // 第二路只对顶层提示词生效：factory/ 自己随版本 force 刷新，不参与这条判据。
  if (rel && rel.startsWith(PROMPTS_PREFIX) && !rel.startsWith(FACTORY_PREFIX)
      && factorySha && factorySha === curSha) {
    return { follow: true, reason: "factory-copy-match" };
  }
  return { follow: false, reason: "user-edited" };
}

/** 该文件是否参与“用户可能改过”的保守更新（顶层提示词）。 */
function isUserEditablePrompt(rel) {
  return Boolean(rel) && rel.startsWith(PROMPTS_PREFIX) && !rel.startsWith(FACTORY_PREFIX);
}

module.exports = { decideRefresh, isUserEditablePrompt };
