"""Clear old pipeline data and characters for fresh test"""
import os
import json

output_dir = r"e:\RWKV_生态_并发式小说\output"

# Files to clear
files_to_clear = [
    "characters.json",
    "storyline.json",
    "outline.json",
    "chapters.jsonl",
    "volumes.jsonl",
]

# Backup the old files first
backup_dir = r"e:\RWKV_生态_并发式小说\output\.backup"
os.makedirs(backup_dir, exist_ok=True)

for fname in files_to_clear:
    fpath = os.path.join(output_dir, fname)
    if os.path.exists(fpath):
        # Backup
        backup_path = os.path.join(backup_dir, fname + ".bak")
        with open(fpath, 'r', encoding='utf-8') as f:
            data = f.read()
        with open(backup_path, 'w', encoding='utf-8') as f:
            f.write(data)
        print(f"Backed up: {fname}")

# Clear drafts
draft_dir = os.path.join(output_dir, "draft")
if os.path.exists(draft_dir):
    for fname in os.listdir(draft_dir):
        fpath = os.path.join(draft_dir, fname)
        if os.path.isfile(fpath):
            backup_path = os.path.join(backup_dir, "draft_" + fname)
            with open(fpath, 'r', encoding='utf-8') as f:
                data = f.read()
            with open(backup_path, 'w', encoding='utf-8') as f:
                f.write(data)
            os.remove(fpath)
            print(f"Cleared: draft/{fname}")

# Clear pipeline cache
cache_dir = os.path.join(output_dir, ".cache")
if os.path.exists(cache_dir):
    for root, dirs, files in os.walk(cache_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            backup_path = os.path.join(backup_dir, "cache_" + fname)
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    data = f.read()
                with open(backup_path, 'w', encoding='utf-8') as f:
                    f.write(data)
            except Exception:
                pass
            os.remove(fpath)
    print("Cleared: .cache/*")

# Clear checkpoint
checkpoint = os.path.join(output_dir, "..", ".checkpoint.json")
if os.path.exists(checkpoint):
    backup_path = os.path.join(backup_dir, "checkpoint.json.bak")
    with open(checkpoint, 'r', encoding='utf-8') as f:
        data = f.read()
    with open(backup_path, 'w', encoding='utf-8') as f:
        f.write(data)
    os.remove(checkpoint)
    print("Cleared: .checkpoint.json")

print("\n=== Done. Old data backed up to", backup_dir, "===")
