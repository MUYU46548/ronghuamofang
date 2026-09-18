#!/usr/bin/env node
/**
 * 绒花墨坊 — 一键打包发布脚本
 * 
 * 用法：
 *   node scripts/make-release.js          # 打包 + 发布到 GitHub Releases
 *   node scripts/make-release.js --dry-run  # 只打包不发布
 * 
 * 前置条件：
 *   - GH_TOKEN 环境变量已设置（GitHub Personal Access Token）
 *   - 在 console/ 目录下运行
 * 
 * 流程：
 *   1. 读取 package.json 版本号
 *   2. 构建 renderer（vite build）
 *   3. 打包 Electron（electron-builder --win --publish never）
 *   4. 创建/更新 GitHub Release 并上传产物
 */

const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const https = require('https');

// ─── 配置 ───────────────────────────────────────────────
const REPO_OWNER = 'MUYU46548';
const REPO_NAME = 'ronghuamofang';
const DIST_DIR = path.join(__dirname, '..', 'dist');

// ─── 工具函数 ───────────────────────────────────────────
function log(msg) {
  console.log(`[make-release] ${msg}`);
}

function fail(msg) {
  console.error(`[make-release] ❌ ${msg}`);
  process.exit(1);
}

function run(cmd, opts = {}) {
  log(`$ ${cmd}`);
  return execSync(cmd, { stdio: 'inherit', cwd: path.join(__dirname, '..'), ...opts });
}

function ghApi(method, path, body = null) {
  return new Promise((resolve, reject) => {
    const token = process.env.GH_TOKEN;
    if (!token) return reject(new Error('GH_TOKEN 环境变量未设置'));

    const data = body ? JSON.stringify(body) : null;
    const req = https.request({
      hostname: 'api.github.com',
      path: path,
      method: method,
      headers: {
        'Authorization': `Bearer ${token}`,
        'User-Agent': 'mofang-release-script/1.0',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        ...(data ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) } : {}),
      },
    }, (res) => {
      let body = '';
      res.on('data', chunk => body += chunk);
      res.on('end', () => {
        let parsed;
        try { parsed = body ? JSON.parse(body) : {}; } catch { parsed = { raw: body }; }
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve({ status: res.statusCode, data: parsed });
        } else {
          reject(new Error(`GitHub API ${res.statusCode}: ${parsed.message || body}`));
        }
      });
    });
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

function uploadAsset(uploadUrl, filePath) {
  return new Promise((resolve, reject) => {
    const token = process.env.GH_TOKEN;
    const fileName = path.basename(filePath);
    const fileSize = fs.statSync(filePath).size;
    const fileData = fs.readFileSync(filePath);

    // GitHub upload_url 是 RFC 6570 模板: https://uploads.github.com/.../assets{?name,label}
    // new URL() 无法解析 {?name,label} 模板字面量，searchParams.set 会丢弃它 → 400
    // 正确做法：手动替换模板为查询字符串后再解析
    const urlStr = uploadUrl.replace('{?name,label}', '?name=' + encodeURIComponent(fileName));
    const url = new URL(urlStr);

    const req = https.request({
      hostname: url.hostname,
      path: url.pathname + url.search,
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'User-Agent': 'mofang-release-script/1.0',
        'Content-Type': 'application/octet-stream',
        'Content-Length': fileSize,
      },
    }, (res) => {
      let body = '';
      res.on('data', chunk => body += chunk);
      res.on('end', () => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          resolve(JSON.parse(body));
        } else {
          reject(new Error(`Upload ${res.statusCode}: ${body}`));
        }
      });
    });
    req.on('error', reject);
    req.write(fileData);
    req.end();
  });
}

// ─── 主流程 ─────────────────────────────────────────────
async function main() {
  const dryRun = process.argv.includes('--dry-run');
  
  // 1. 读取版本号
  const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'package.json'), 'utf-8'));
  const version = pkg.version;
  const tagName = `v${version}`;
  log(`版本: ${version} (tag: ${tagName})`);

  // 2. 构建 renderer
  log('Step 1/4: 构建 renderer...');
  run('npm run build');

  // 3. 打包 Electron
  log('Step 2/4: 打包 Electron...');
  run('npx electron-builder --win --x64 --publish never');

  // 4. 确认产物存在
  log('Step 3/4: 检查产物...');
  const artifacts = [
    `ronghuamofang-console-setup-${version}.exe`,
    `ronghuamofang-console-setup-${version}.exe.blockmap`,
    'latest.yml',
  ];
  const existing = artifacts.filter(f => fs.existsSync(path.join(DIST_DIR, f)));
  log(`产物: ${existing.join(', ')}`);
  if (existing.length === 0) fail('未找到任何打包产物');

  if (dryRun) {
    log('Step 4/4: --dry-run 模式，跳过发布');
    log('✅ 打包完成，未发布');
    return;
  }

  // 5. 检查 GH_TOKEN
  if (!process.env.GH_TOKEN) {
    fail('GH_TOKEN 环境变量未设置。请设置后重试。');
  }

  // 6. 创建/更新 Release
  log('Step 4/4: 发布到 GitHub Releases...');
  
  // 检查 tag 是否存在
  let release;
  try {
    const r = await ghApi('GET', `/repos/${REPO_OWNER}/${REPO_NAME}/releases/tags/${tagName}`);
    log(`Release 已存在 (id: ${r.data.id})，将更新`);
    release = r.data;
  } catch (e) {
    if (!e.message.includes('404')) throw e;
    // 创建 Release
    log(`创建新 Release: ${tagName}`);
    const r = await ghApi('POST', `/repos/${REPO_OWNER}/${REPO_NAME}/releases`, {
      tag_name: tagName,
      name: `绒花墨坊 ${version}`,
      body: `绒花墨坊桌面控制台 ${version}`,
      draft: false,
      prerelease: false,
    });
    release = r.data;
    log(`Release 已创建 (id: ${release.id})`);
  }

  // 7. 删除旧产物（如果有）
  if (release.assets && release.assets.length > 0) {
    for (const asset of release.assets) {
      log(`删除旧产物: ${asset.name}`);
      await ghApi('DELETE', `/repos/${REPO_OWNER}/${REPO_NAME}/releases/assets/${asset.id}`);
    }
  }

  // 8. 上传新产物
  for (const fileName of existing) {
    const filePath = path.join(DIST_DIR, fileName);
    log(`上传: ${fileName} (${(fs.statSync(filePath).size / 1024 / 1024).toFixed(1)} MB)`);
    await uploadAsset(release.upload_url, filePath);
    log(`  ✅ ${fileName}`);
  }

  log(`\n✅ 发布完成！`);
  log(`地址: https://github.com/${REPO_OWNER}/${REPO_NAME}/releases/tag/${tagName}`);
}

main().catch(e => {
  fail(e.message);
});
