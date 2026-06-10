"""Start the RWKV model"""
import json
import urllib.request

# Try to start the recommended model
req_data = {"name": "rwkv7-g1g-7.2b-20260523-ctx8192.st"}
try:
    req = urllib.request.Request(
        "http://localhost:5000/api/models/start",
        data=json.dumps(req_data, ensure_ascii=False).encode('utf-8'),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        result = json.loads(resp.read().decode('utf-8'))
        print("Start result:", json.dumps(result, ensure_ascii=False, indent=2)[:500])
except Exception as e:
    print(f"Error: {e}")
