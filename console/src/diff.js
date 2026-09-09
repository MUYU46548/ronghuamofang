// Paragraph-level diff: split by blank lines, diff at paragraph level
export function diffParagraphs(raw, refined) {
  if (!raw || !refined) return [];
  
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
