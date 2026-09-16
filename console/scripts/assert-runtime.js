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
console.log('[assert-runtime] ✅ 全部就绪\n');
