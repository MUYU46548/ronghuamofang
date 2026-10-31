/**
 * 极简 Markdown 渲染（零依赖，2026-10-31）。
 *
 * ## 为什么自己写，不装 marked
 *
 * 本项目前端只有 7 个依赖（d3 + vue + updater），刻意克制。手册要的东西很少：
 * 标题 / 段落 / 列表 / 表格 / 代码块 / 引用 / 粗体 / 行内码 / 链接 —— 别的语法不写就是了。
 * 装一个通用解析器反而多一条供应链。
 *
 * ## 安全
 *
 * 手册是我们自己随包发布的文件，不含用户输入。但**仍然先转义再解析**：
 * 万一将来手册里贴了用户来稿的片段（带 `<script>`），也不会变成执行路径。
 * 只识别下面的白名单语法，其余原样当文本。
 */

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** 行内：`code` / **粗** / [文字](链接)。先转义，再按顺序替换。 */
export function inline(text) {
  let s = esc(text);
  // 行内码优先（里面的 ** 不该被当粗体）
  const codes = [];
  s = s.replace(/`([^`]+)`/g, (_, c) => {
    codes.push(c);
    return "\u0000C" + (codes.length - 1) + "\u0000";
  });
  s = s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g,
    (_, t, u) => '<a href="' + u + '" target="_blank" rel="noreferrer">' + t + "</a>");
  s = s.replace(/\u0000C(\d+)\u0000/g, (_, i) => "<code>" + esc(codes[Number(i)]) + "</code>");
  return s;
}

/**
 * 块级渲染。返回 HTML 字符串。
 * 支持：`#/##/###` 标题、`- ` 列表、`1. ` 有序列表、`| 表格 |`、``` 代码块、`> ` 引用、段落。
 */
export function renderMarkdown(src) {
  const lines = String(src || "").replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let i = 0;

  const flushParagraph = (buf) => {
    if (!buf.length) return;
    out.push("<p>" + inline(buf.join(" ")) + "</p>");
    buf.length = 0;
  };
  const para = [];

  while (i < lines.length) {
    const ln = lines[i];

    // 代码块
    if (/^```/.test(ln)) {
      flushParagraph(para);
      const lang = ln.replace(/^```/, "").trim();
      const body = [];
      i += 1;
      while (i < lines.length && !/^```/.test(lines[i])) {
        body.push(lines[i]);
        i += 1;
      }
      i += 1;   // 跳过收尾 ```
      out.push('<pre class="md-code"' + (lang ? ' data-lang="' + esc(lang) + '"' : "")
        + "><code>" + esc(body.join("\n")) + "</code></pre>");
      continue;
    }

    // 标题
    const h = /^(#{1,4})\s+(.*)$/.exec(ln);
    if (h) {
      flushParagraph(para);
      const lv = h[1].length;
      out.push("<h" + (lv + 1) + ">" + inline(h[2]) + "</h" + (lv + 1) + ">");
      i += 1;
      continue;
    }

    // 表格：当前行是 |...| 且下一行是分隔行
    if (/^\s*\|.*\|\s*$/.test(ln) && i + 1 < lines.length
        && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      flushParagraph(para);
      const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const head = cells(ln);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        rows.push(cells(lines[i]));
        i += 1;
      }
      out.push('<table class="md-table"><thead><tr>'
        + head.map((c) => "<th>" + inline(c) + "</th>").join("")
        + "</tr></thead><tbody>"
        + rows.map((r) => "<tr>" + r.map((c) => "<td>" + inline(c) + "</td>").join("") + "</tr>").join("")
        + "</tbody></table>");
      continue;
    }

    // 引用
    if (/^>\s?/.test(ln)) {
      flushParagraph(para);
      const buf = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        buf.push(lines[i].replace(/^>\s?/, ""));
        i += 1;
      }
      out.push('<blockquote class="md-quote">' + inline(buf.join(" ")) + "</blockquote>");
      continue;
    }

    // 无序列表
    if (/^\s*[-*]\s+/.test(ln)) {
      flushParagraph(para);
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push("<li>" + inline(lines[i].replace(/^\s*[-*]\s+/, "")) + "</li>");
        i += 1;
      }
      out.push('<ul class="md-ul">' + items.join("") + "</ul>");
      continue;
    }

    // 有序列表
    if (/^\s*\d+[.)]\s+/.test(ln)) {
      flushParagraph(para);
      const items = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push("<li>" + inline(lines[i].replace(/^\s*\d+[.)]\s+/, "")) + "</li>");
        i += 1;
      }
      out.push('<ol class="md-ol">' + items.join("") + "</ol>");
      continue;
    }

    // 空行 = 段落分隔
    if (!ln.trim()) {
      flushParagraph(para);
      i += 1;
      continue;
    }

    para.push(ln.trim());
    i += 1;
  }
  flushParagraph(para);
  return out.join("\n");
}

/** 从 md 里取第一个 `# 标题`（手册目录用）。 */
export function mdTitle(src) {
  const m = /^#\s+(.+)$/m.exec(String(src || ""));
  return m ? m[1].trim() : "";
}
