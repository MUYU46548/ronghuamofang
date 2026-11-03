#!/usr/bin/env node
/**
 * NovelForge dist 前 fail-fast 断言
 * 缺失关键 extraResources 则中止打包，防止产出空壳
 */

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const checks = [
  { path: 'runtime/python/python.exe', name: 'embeddable Python' },
  { path: 'runtime/python/Scripts/pip.exe', name: 'pip' },
  { path: '../scripts/nf_api.py', name: 'nf_api.py' },
  { path: '../prompts/stage1_materials.md', name: 'prompts/' },
  { path: '../templates', name: 'templates/' },
];

let ok = true;
for (const c of checks) {
  const abs = path.join(ROOT, c.path);
  const exists = fs.existsSync(abs);
  console.log('[assert-runtime] ' + (exists ? '✅' : '❌') + ' ' + c.name);
  if (!exists) ok = false;
}

if (!ok) {
  console.error('\n[assert-runtime] ❌ 关键资源缺失，中止打包。请运行: npm run prepare');
  process.exit(1);
}

// 前端新鲜度（2026-11-03）。`npm run dist` 原来**不跑 vite build** —— 改完 src 直接
// dist，asar 里装的是上一次的 renderer/dist：后端是新的、前端是旧的，界面上的修复
// 一个字都不会出现（与"工作区代码不刷新"是同一类事故，只是方向相反）。
// 判据：src/ 下最新改动时间 vs renderer/dist/assets 的最新时间。
function newestMtime(dir, skip = []) {
  let newest = 0;
  const walk = (d) => {
    for (const ent of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, ent.name);
      if (skip.some(s => p.includes(s))) continue;
      if (ent.isDirectory()) walk(p);
      else if (ent.isFile()) {
        const t = fs.statSync(p).mtimeMs;
        if (t > newest) newest = t;
      }
    }
  };
  if (!fs.existsSync(dir)) return 0;
  walk(dir);
  return newest;
}

const srcNewest = Math.max(
  newestMtime(path.join(ROOT, 'src')),
  (() => { try { return fs.statSync(path.join(ROOT, 'index.html')).mtimeMs; } catch (e) { return 0; } })(),
  (() => { try { return fs.statSync(path.join(ROOT, 'vite.config.js')).mtimeMs; } catch (e) { return 0; } })(),
);
const distNewest = newestMtime(path.join(ROOT, 'renderer', 'dist', 'assets'));
if (!distNewest) {
  console.error('[assert-runtime] ❌ renderer/dist 不存在，先 npm run build');
  process.exit(1);
}
if (srcNewest > distNewest + 1000) {
  console.error('[assert-runtime] ❌ 前端产物比源码旧（src 有新改动但没重新构建）');
  console.error('     src  最新: ' + new Date(srcNewest).toISOString());
  console.error('     dist 最新: ' + new Date(distNewest).toISOString());
  console.error('     → 先 npm run build，再打包');
  process.exit(1);
}
console.log('[assert-runtime] ✅ 前端产物不旧于源码');

console.log('[assert-runtime] ✅ 全部就绪\n');
