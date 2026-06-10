# -*- coding: utf-8 -*-
import urllib.request, json, sys

url = 'http://localhost:5000/api/pipeline/progress'
with urllib.request.urlopen(url, timeout=10) as resp:
    data = json.loads(resp.read().decode('utf-8'))

# Save result
with open(r'e:\RWKV_生态_并发式小说\pipeline_result.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print('Saved pipeline_result.json')

print('Status:', data.get('status'))
for ch in data.get('chapter_matrix', []):
    ch_id = ch.get('chapter_id')
    ch_status = ch.get('status')
    slices = ch.get('slices', [])
    completed = sum(1 for s in slices if s.get('status') == 'completed')
    total_content = sum(len(s.get('content', '')) for s in slices)
    print(f'Ch {ch_id}: {ch_status} ({completed}/{len(slices)} slices, {total_content} chars)')

# Force UTF-8 output
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Check each chapter content for issues
for ch_idx in range(len(data['chapter_matrix'])):
    ch = data['chapter_matrix'][ch_idx]
    cid = ch['chapter_id']
    print(f'\n=== Chapter {cid} ===')
    for i, s in enumerate(ch['slices']):
        content = s.get('content', '')
        print(f'--- Slice {i} ({len(content)} chars) ---')
        print(content[:1500])
        print('...')
