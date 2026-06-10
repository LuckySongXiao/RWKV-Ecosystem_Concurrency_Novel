"""Monitor pipeline progress"""
import json
import urllib.request
import time

url = "http://localhost:5000/api/pipeline/status"

for i in range(120):  # max 10 minutes
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        status = data.get("status", "unknown")
        stage = data.get("current_stage", "")
        completed = data.get("completed_tasks", 0)
        total = data.get("total_tasks", 0)
        print(f"[{i*5:3d}s] status={status}, stage={stage}, completed={completed}/{total}")
        if status in ("completed", "failed", "error", "stopped"):
            print("\n=== Final result ===")
            print(json.dumps(data, ensure_ascii=False, indent=2)[:2000])
            break
    except Exception as e:
        print(f"[{i*5:3d}s] Error: {e}")
    time.sleep(5)
