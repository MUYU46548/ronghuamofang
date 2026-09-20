import sys; sys.path.insert(0, 'scripts')
from chapter_review import _extract_json_from_output

# Test 1: bare JSON
t1 = '{"chapters":[{"n":1,"findings":[]}]}'
print('bare JSON:', _extract_json_from_output(t1) is not None)

# Test 2: FILE protocol
t2 = '===FILE: data/outline/review_report.json\n{"chapters":[{"n":1,"findings":[]}]}\n===END==='
print('FILE protocol:', _extract_json_from_output(t2) is not None)

# Test 3: noise before JSON
t4 = '以下是审查结果：\n{"chapters":[{"n":1,"findings":[]}]}'
print('noise before:', _extract_json_from_output(t4) is not None)

# Test 4: invalid
print('invalid:', _extract_json_from_output('not json') is None)
