import os
import filecmp

dir_rl = r"e:/THUCTAP/crawldata/GSM_SIMULATOR-rl"
dir_check = r"e:/THUCTAP/crawldata/GSM_SIMULATOR-check"

subdirs = ["src", "configs", "scripts", "tests"]

diff_files = []
only_in_rl = []
only_in_check = []

for sd in subdirs:
    p_rl = os.path.join(dir_rl, sd)
    p_check = os.path.join(dir_check, sd)
    for root, dirs, files in os.walk(p_rl):
        if "__pycache__" in root:
            continue
        rel = os.path.relpath(root, dir_rl)
        target_dir = os.path.join(dir_check, rel)
        for f in files:
            if f.endswith(".pyc"):
                continue
            f_rl = os.path.join(root, f)
            f_chk = os.path.join(target_dir, f)
            rel_file = os.path.join(rel, f).replace("\\", "/")
            if not os.path.exists(f_chk):
                only_in_rl.append(rel_file)
            else:
                if not filecmp.cmp(f_rl, f_chk, shallow=False):
                    diff_files.append(rel_file)

    for root, dirs, files in os.walk(p_check):
        if "__pycache__" in root:
            continue
        rel = os.path.relpath(root, dir_check)
        target_dir = os.path.join(dir_rl, rel)
        for f in files:
            if f.endswith(".pyc"):
                continue
            f_chk = os.path.join(root, f)
            f_rl = os.path.join(target_dir, f)
            rel_file = os.path.join(rel, f).replace("\\", "/")
            if not os.path.exists(f_rl):
                only_in_check.append(rel_file)

with open("scratch/diff_result.txt", "w", encoding="utf-8") as out:
    out.write("=== DIFF FILES ===\n")
    for f in sorted(diff_files):
        out.write(f + "\n")
    out.write("\n=== ONLY IN RL ===\n")
    for f in sorted(only_in_rl):
        out.write(f + "\n")
    out.write("\n=== ONLY IN CHECK ===\n")
    for f in sorted(only_in_check):
        out.write(f + "\n")

print("Done. Written to scratch/diff_result.txt")
