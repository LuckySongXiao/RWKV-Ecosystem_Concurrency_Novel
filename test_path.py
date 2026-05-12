import requests, urllib.parse

base = 'http://localhost:5000'

d = requests.get(f'{base}/api/novel/dirs', timeout=5).json()
novel_dir = d['novels'][0]['path']
print(f"Novel dir: {novel_dir}")

d = requests.get(f'{base}/api/novel/tree?dir={novel_dir}', timeout=5).json()

def find_md_file(tree):
    for item in tree:
        if item['type'] == 'file' and item['name'].endswith('.md'):
            return item['path']
        if item['type'] == 'dir' and item.get('children'):
            r = find_md_file(item['children'])
            if r: return r
    return None

md_path = find_md_file(d['tree'])
print(f"Found md: {md_path}")

if md_path:
    print(f"\nTest 1 - encodeURIComponent:")
    url1 = f'{base}/api/novel/file?path={urllib.parse.quote(md_path, safe="")}'
    print(f"  URL: {url1[:100]}...")
    r1 = requests.get(url1, timeout=5)
    print(f"  Status: {r1.status_code}")
    print(f"  Response: {r1.text[:200]}")

    print(f"\nTest 2 - raw path:")
    url2 = f'{base}/api/novel/file?path={md_path}'
    print(f"  URL: {url2[:100]}...")
    r2 = requests.get(url2, timeout=5)
    print(f"  Status: {r2.status_code}")
    print(f"  Response: {r2.text[:200]}")

    print(f"\nTest 3 - double backslash:")
    path3 = md_path.replace('\\', '\\\\')
    url3 = f'{base}/api/novel/file?path={urllib.parse.quote(path3, safe="")}'
    print(f"  URL: {url3[:100]}...")
    r3 = requests.get(url3, timeout=5)
    print(f"  Status: {r3.status_code}")
    print(f"  Response: {r3.text[:200]}")
