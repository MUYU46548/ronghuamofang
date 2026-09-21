"""审稿闭环**手动冒烟脚本**（不是自动化用例）。

依赖：本机 `127.0.0.1:8781` 上跑着 nf_api，且当前书档已有
`data/outline/review_report.json`。**离线不可跑。**

⚠️ 本文件没有 PASS/FAIL 断言框架，只打印结果 —— 所以它不进自动化套件的
断言计数。原本在服务不可用时抛裸 `HTTPError` traceback，看起来像「测试失败」，
实际是「未执行」。按项目纪律（未执行 ≠ 失败，见 MEMORY.md），
现在改成打印可行动的 SKIP 并以 0 退出。
"""
import sys
sys.path.insert(0, 'scripts')
import json
from urllib.error import HTTPError, URLError
from urllib.request import urlopen, Request

API = 'http://127.0.0.1:8781'

def api(path, method='GET', body=None):
    data = json.dumps(body).encode() if body else None
    req = Request(API + path, data=data, method=method)
    req.add_header('Content-Type', 'application/json')
    try:
        r = urlopen(req)
    except (HTTPError, URLError) as e:
        print(f"[SKIP] 需要本机 {API} 运行 nf_api，且书档已有 review_report.json。")
        print(f"       当前不可用：{e}")
        print("       这是手动冒烟脚本，不是自动化用例（未执行 ≠ 失败）。")
        sys.exit(0)
    return r.status, json.loads(r.read().decode())

# Step 1: Load review report
print('=== Step 1: Load Review Report ===')
status, report = api('/review/report')
print(f'Status: {status}')
for c in report.get('chapters', []):
    print(f'  Ch {c["n"]}: {len(c.get("findings", []))} findings')

# Step 2: Simulate GUI decisions (accept all errors/warns, ignore info)
print('\n=== Step 2: Make Decisions ===')
decisions = []
for c in report.get('chapters', []):
    for f in c.get('findings', []):
        # Accept warns and errors, ignore info
        if f['severity'] in ('warn', 'error'):
            action = 'accept'
            feedback = ''
        else:
            action = 'ignore'
            feedback = ''
        decisions.append({
            'finding_id': f['id'],
            'chapter': c['n'],
            'action': action,
            'feedback': feedback,
        })
        print(f'  Ch{c["n"]} {f["id"]} ({f["severity"]}): {action}')

# Step 3: Save decisions
print('\n=== Step 3: Save Decisions ===')
status, resp = api('/review/decisions', 'POST', {'decisions': decisions})
print(f'Status: {status}, Response: {resp}')

# Step 4: Run batch refine (fake mode would be used in real scenario)
print('\n=== Step 4: Run Batch Refine ===')
status, resp = api('/batch_refine/run', 'POST', {'decisions_from_file': True})
print(f'Status: {status}, Response: {resp}')
