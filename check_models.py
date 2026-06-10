"""Check RWKV service status"""
import json
import urllib.request

try:
    resp = urllib.request.urlopen('http://localhost:5000/api/models', timeout=10)
    data = json.loads(resp.read().decode('utf-8'))
    print(json.dumps(data, ensure_ascii=False, indent=2))
except Exception as e:
    print(f"Error: {e}")
