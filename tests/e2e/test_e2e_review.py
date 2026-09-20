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
