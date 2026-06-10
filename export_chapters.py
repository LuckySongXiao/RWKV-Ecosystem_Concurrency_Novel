# -*- coding: utf-8 -*-
import json
import sys

# Set stdout to UTF-8
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

with open(r'e:\RWKV_生态_并发式小说\pipeline_result.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

print('Status:', data.get('status'))
print('Stage:', data.get('current_stage'))
print()
for ch in data.get('chapter_matrix', []):
    ch_id = ch.get('chapter_id')
    ch_status = ch.get('status')
    slices = ch.get('slices', [])
    completed = sum(1 for s in slices if s.get('status') == 'completed')
    total_content = sum(len(s.get('content', '')) for s in slices)
    print(f'Ch {ch_id}: {ch_status} ({completed}/{len(slices)} slices, {total_content} chars)')

# Save the slices' content to a file with proper encoding
with open(r'e:\RWKV_生态_并发式小说\pipeline_chapters.txt', 'w', encoding='utf-8') as out:
    for ch in data.get('chapter_matrix', []):
        ch_id = ch.get('chapter_id')
        out.write(f'\n=== Chapter {ch_id} ===\n')
        for i, s in enumerate(ch.get('slices', [])):
            content = s.get('content', '')
            out.write(f'\n--- Slice {i} ({len(content)} chars) ---\n')
            out.write(content)
            out.write('\n')

print('\nChapters saved to pipeline_chapters.txt')
