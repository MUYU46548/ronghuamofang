// Paragraph-level diff: split by blank lines, diff at paragraph level
export function diffParagraphs(raw, refined) {  if (!raw || !refined) return [];
  
  const splitParagraphs = (text) => {
    const normalized = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    let paras = normalized.split(/\n[ \t]*\n/).map(p => p.trim()).filter(p => p.length > 0);
    if (paras.length <= 1 && normalized.trim().length > 0) {
      paras = normalized.split('\n').map(p => p.trim()).filter(p => p.length > 0);
    }
    return paras;
  };
  
  const rawParas = splitParagraphs(raw);
  const refinedParas = splitParagraphs(refined);
  
  if (rawParas.length === 0 || refinedParas.length === 0) return [];
  
  const m = rawParas.length;
  const n = refinedParas.length;
  const dp = Array.from({ length: m + 1 }, () => Array(n + 1).fill(0));
  
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (rawParas[i - 1] === refinedParas[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }
  
  const result = [];
  let i = m, j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && rawParas[i - 1] === refinedParas[j - 1]) {
      result.unshift({ type: 'same', content: rawParas[i - 1] });
      i--; j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      result.unshift({ type: 'add', content: refinedParas[j - 1] });
      j--;
    } else {
      result.unshift({ type: 'del', content: rawParas[i - 1] });
      i--;
    }
  }
  
  return result;
}

/* =========================================================================
 * 结构化大纲 diff（任务4，独立于上面的 diffParagraphs：章节对比仍用旧函数）
 * =========================================================================
 * 后端已提供节点级 diff（scripts/utils/outline_panel.py → diff_outlines），
 * 前端此函数用于「上传/粘贴两份 global.md」的离线对比场景，算法与后端一致：
 *   解析 → 按类型内顺序对齐 → 标题相同做字符级 diff → 未匹配标记 add/del
 * 中文按字符切分（不按空格）。
 */

const ACT_NAMES = ["起", "承", "转", "合"];
const NODE_LINE = /^[-*]\s*(节点\s*\d+)\s*[：:，,]?\s*(.*)$/;
const PLAN_LINE = /^[-*]\s*(第\s*[\d一二三四五六七八九十百零两]+\s*章)\s*[：:，,]?\s*(.*)$/;
const SECTION_LINE = /^##\s*(起|承|转|合|关键节点|预计章节数|章节规划)\s*$/;

// 解析 global.md 为 { title, acts, nodes, plan, expected }
export function parseOutline(text) {
  const src = (text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const out = { title: "", acts: {}, nodes: [], plan: [], expected: null };
  const tm = src.match(/^#\s+(.*)$/m);
  if (tm) out.title = tm[1].trim();

  // 分节
  const lines = src.split("\n");
  let cur = null;
  const buf = {};
  for (const line of lines) {
    const sm = line.match(SECTION_LINE);
    if (sm) { cur = sm[1]; if (!(cur in buf)) buf[cur] = []; continue; }
    if (cur && buf[cur]) buf[cur].push(line);
  }
  for (const a of ACT_NAMES) out.acts[a] = (buf[a] || []).join("\n").trim();

  const em = src.match(/^##\s*预计章节数\s*\n\s*(\d+)/m);
  out.expected = em ? parseInt(em[1], 10) : null;

  for (const line of lines) {
    const s = line.trim();
    const nm = s.match(NODE_LINE);
    if (nm) {
      out.nodes.push({ title: nm[1].replace(/\s+/g, ""), tail: nm[2].trim(), text: s.replace(/^[-*]\s*/, "") });
      continue;
    }
    const pm = s.match(PLAN_LINE);
    if (pm) {
      out.plan.push({ title: pm[1].replace(/\s+/g, " ").trim(), tail: pm[2].trim(), text: s.replace(/^[-*]\s*/, "") });
    }
  }
  return out;
}

// 中文按字符切分的字符级 diff：[{op:'='|'add'|'del', text}]
export function charDiff(a, b) {
  const s1 = a || "", s2 = b || "";
  const m = s1.length, n = s2.length;
  if (m * n > 4000000) return [{ op: "=", text: s1 }, { op: "add", text: s2 }]; // 防爆
  const dp = Array.from({ length: m + 1 }, () => new Uint32Array(n + 1));
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = s1[i - 1] === s2[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }
  const ops = [];
  let i = m, j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && s1[i - 1] === s2[j - 1]) { ops.unshift({ op: "=", text: s1[i - 1] }); i--; j--; }
    else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) { ops.unshift({ op: "add", text: s2[j - 1] }); j--; }
    else { ops.unshift({ op: "del", text: s1[i - 1] }); i--; }
  }
  return ops;
}

// 结构化大纲 diff → [{type, scope, title, content, content_v2?, char_diff?}]
export function diffOutlines(v1Text, v2Text) {
  const s1 = parseOutline(v1Text);
  const s2 = parseOutline(v2Text);
  const segments = [];

  for (const name of ACT_NAMES) {
    const a1 = (s1.acts[name] || "").trim();
    const a2 = (s2.acts[name] || "").trim();
    if (a1 === a2) continue;
    if (a1) segments.push({ type: "del", scope: "act", title: "## " + name, content: a1 });
    if (a2) segments.push({ type: "add", scope: "act", title: "## " + name, content: a2 });
  }
  if (s1.expected !== s2.expected) {
    if (s1.expected !== null) segments.push({ type: "del", scope: "meta", title: "预计章节数", content: String(s1.expected) });
    if (s2.expected !== null) segments.push({ type: "add", scope: "meta", title: "预计章节数", content: String(s2.expected) });
  }

  segments.push(...diffSeq(s1.nodes, s2.nodes, "node"));
  segments.push(...diffSeq(s1.plan, s2.plan, "plan"));
  return segments;
}

function diffSeq(seq1, seq2, scope) {
  const k1 = seq1.map((x) => x.title.replace(/\s+/g, ""));
  const k2 = seq2.map((x) => x.title.replace(/\s+/g, ""));
  const ops = lcsOps(k1, k2);
  const out = [];
  for (const op of ops) {
    if (op.type === "same") {
      const a = seq1[op.i], b = seq2[op.j];
      if (a.text === b.text) {
        out.push({ type: "same", scope, title: a.title, content: a.text });
      } else {
        out.push({ type: "change", scope, title: a.title, content: a.text, content_v2: b.text, char_diff: charDiff(a.text, b.text) });
      }
    } else if (op.type === "del") {
      out.push({ type: "del", scope, title: seq1[op.i].title, content: seq1[op.i].text });
    } else {
      out.push({ type: "add", scope, title: seq2[op.j].title, content: seq2[op.j].text });
    }
  }
  return out;
}

// 简易 LCS 对齐（返回 same/del/add 序列）
function lcsOps(a, b) {
  const m = a.length, n = b.length;
  const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = a[i - 1] === b[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }
  const res = [];
  let i = m, j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && a[i - 1] === b[j - 1]) { res.unshift({ type: "same", i: i - 1, j: j - 1 }); i--; j--; }
    else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) { res.unshift({ type: "add", j: j - 1 }); j--; }
    else { res.unshift({ type: "del", i: i - 1 }); i--; }
  }
  return res;
}
