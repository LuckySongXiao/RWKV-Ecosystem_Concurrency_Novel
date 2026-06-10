"""Monitor optimized pipeline progress - long wait version"""
import json
import urllib.request
import time

url = "http://localhost:5000/api/pipeline/progress"

last_total = -1
last_active = -1
for i in range(720):  # max 60 minutes
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        status = data.get("status", "unknown")
        stage = data.get("current_stage", "")
        completed = data.get("completed_tasks", 0)
        total = data.get("total_tasks", 0)
        matrix = data.get("chapter_matrix", [])
        active = [c for c in matrix if c.get("status") == "writing"]
        completed_ch = [c for c in matrix if c.get("status") == "completed"]

        # only print on state change
        if total != last_total or len(active) != last_active or status in ("completed", "failed", "error"):
            print(f"[{i*5:4d}s] status={status}, stage={stage}, completed={completed}/{total}, active={len(active)}, done_ch={len(completed_ch)}/{len(matrix)}")
            last_total = total
            last_active = len(active)

        if status in ("completed", "failed", "error", "stopped"):
            print("\n=== Final result ===")
            # write to file
            with open(r"e:\RWKV_生态_并发式小说\pipeline_result.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"Wrote result to pipeline_result.json ({len(json.dumps(data, ensure_ascii=False))} chars)")
            break
    except Exception as e:
        print(f"[{i*5:4d}s] Error: {e}")
    time.sleep(5)
