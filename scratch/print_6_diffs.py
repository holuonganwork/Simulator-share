import difflib

files = [
    "configs/pilot_hanoi.yaml",
    "src/gsm_sim/demand.py",
    "src/gsm_sim/environment.py",
    "src/gsm_sim/multiday.py",
    "src/gsm_sim/runner.py",
    "src/gsm_sim/world.py",
]

with open("scratch/6_diffs.txt", "w", encoding="utf-8") as out:
    for f in files:
        f_rl = f"e:/THUCTAP/crawldata/GSM_SIMULATOR-rl/{f}"
        f_chk = f"e:/THUCTAP/crawldata/GSM_SIMULATOR-check/{f}"
        with open(f_rl, "r", encoding="utf-8") as f1, open(f_chk, "r", encoding="utf-8") as f2:
            t1 = f1.readlines()
            t2 = f2.readlines()
        diff = list(difflib.unified_diff(t1, t2, fromfile=f"rl/{f}", tofile=f"check/{f}", n=3))
        out.write(f"\n==================== {f} ({len(diff)} diff lines) ====================\n")
        for line in diff:
            out.write(line)

print("Saved to scratch/6_diffs.txt")
