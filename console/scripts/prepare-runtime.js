#!/usr/bin/env node
/**
 * NovelForge 运行时准备脚本
 * 
 * 流程：
 *   1. 下载 python-3.11-embed-amd64.zip（仅当 runtime/python 不存在时）
 *   2. 解压到 runtime/python
 *   3. 安装 pip（get-pip.py）
 *   4. pip install -r requirements.txt
 *   5. 修复 python311._pth（加入 Lib\site-packages）
 *   6. 冒烟测试：python.exe -c "import yaml, docx, lxml; print('ok')"
 */

const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const https = require('https');

const ROOT = path.resolve(__dirname, '..');
const RUNTIME_DIR = path.join(ROOT, 'runtime');
const PYTHON_DIR = path.join(RUNTIME_DIR, 'python');
const REQUIREMENTS = path.join(ROOT, '..', 'requirements.txt');

function log(msg) { console.log('[prepare-runtime] ' + msg); }
function fail(msg) { console.error('[prepare-runtime] ERROR: ' + msg); process.exit(1); }
function win(p) { return p.replace(/\\/g, '/'); }
function run(cmd, opts) {
  log('$ ' + cmd);
  return execSync(cmd, Object.assign({ stdio: 'inherit', cwd: ROOT }, opts || {}));
}

function download(url, dest) {
  return new Promise((resolve, reject) => {
    if (fs.existsSync(dest)) { log('已存在，跳过: ' + path.basename(dest)); return resolve(); }
    log('下载: ' + url);
    const file = fs.createWriteStream(dest);
    https.get(url, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        return download(res.headers.location, dest).then(resolve).catch(reject);
      }
      if (res.statusCode !== 200) return reject(new Error('HTTP ' + res.statusCode));
      res.pipe(file);
      file.on('finish', function() { file.close(); resolve(); });
    }).on('error', reject);
  });
}

async function main() {
  const embedUrl = 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip';
  const embedZip = path.join(RUNTIME_DIR, 'python-3.11-embed.zip');

  // 1. 下载
  fs.mkdirSync(RUNTIME_DIR, { recursive: true });
  await download(embedUrl, embedZip);

  // 2. 解压
  if (!fs.existsSync(path.join(PYTHON_DIR, 'python.exe'))) {
    log('解压 embeddable Python...');
    run('powershell -Command "Expand-Archive -Path \'' + win(embedZip) + '\' -DestinationPath \'' + win(PYTHON_DIR) + '\' -Force"');
  } else {
    log('Python 已解压，跳过');
  }

  // 3. 安装 pip
  const getPip = path.join(PYTHON_DIR, 'get-pip.py');
  if (!fs.existsSync(path.join(PYTHON_DIR, 'Scripts', 'pip.exe'))) {
    await download('https://bootstrap.pypa.io/get-pip.py', getPip);
    log('安装 pip...');
    run('"' + win(path.join(PYTHON_DIR, 'python.exe')) + '" "' + win(getPip) + '" --no-warn-script-location');
  } else {
    log('pip 已安装，跳过');
  }

  // 4. 修 python311._pth
  const pthFile = path.join(PYTHON_DIR, 'python311._pth');
  const pth = fs.readFileSync(pthFile, 'utf8');
  if (!pth.includes('Lib\\site-packages')) {
    log('修复 python311._pth...');
    const newPth = pth.replace(
      '# Uncomment to run site.main() automatically',
      'Lib\\site-packages\n\n# Uncomment to run site.main() automatically'
    );
    fs.writeFileSync(pthFile, newPth);
  }

  // 5. pip install dependencies
  log('安装依赖...');
  run('"' + win(path.join(PYTHON_DIR, 'python.exe')) + '" -m pip install -r "' + win(REQUIREMENTS) + '" --no-warn-script-location');

  // 6. 冒烟测试
  log('冒烟测试...');
  run('"' + win(path.join(PYTHON_DIR, 'python.exe')) + '" -c "import yaml, docx, lxml; print(\'ok\')"');

  // 7. 完成
  var ver = execSync('"' + win(path.join(PYTHON_DIR, 'python.exe')) + '" --version').toString().trim();
  log('完成: ' + ver);

  // 8. 清理
  try { fs.unlinkSync(embedZip); } catch(e) {}
  try { fs.unlinkSync(getPip); } catch(e) {}
}

main().catch(function(e) { fail(e.message); });
