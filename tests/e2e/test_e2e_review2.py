import sys
sys.path.insert(0, 'scripts')
import json
from urllib.request import urlopen, Request

API = 'http://127.0.0.1:8781'

def api(path, method='GET', body=None):
    data = json.dumps(body).encode() if body else None
    req = Request(API + path, data=data, method=method)
    req.add_header('Content-Type', 'application/json')
    r = urlopen(req)
    return r.status, json.loads(r.read().decode())

# Step 1: Load review report
print('=== Step 1: Load Review Report ===')
status, report = api('/review/report')
print(f'Status: {status}')
for c in report.get('chapters', []):
    print(f'  Ch {c["n"]}: {len(c.get("findings", []))} findings')
    for f in c.get('findings', []):
        print(f'    - {f["id"]} ({f["severity"]}): {f["detail"][:60]}...')

# Step 2: Accept ALL findings (test full chain)
print('\n=== Step 2: Accept All Findings ===')
decisions = []
for c in report.get('chapters', []):
    for f in c.get('findings', []):
        decisions.append({
            'finding_id': f['id'],
            'chapter': c['n'],
            'action': 'accept',
            'feedback': '',
        })
        print(f'  Ch{c["n"]} {f["id"]}: accept')

# Step 3: Save decisions
print('\n=== Step 3: Save Decisions ===')
status, resp = api('/review/decisions', 'POST', {'decisions': decisions})
print(f'Status: {status}')

# Step 4: Run batch refine (will use real LLM if not in fake mode)
print('\n=== Step 4: Run Batch Refine ===')
status, resp = api('/batch_refine/run', 'POST', {'decisions_from_file': True})
print(f'Status: {status}, Job ID: {resp.get("job_id")}')
