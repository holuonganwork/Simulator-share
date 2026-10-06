import os

dir_rl = r"e:/THUCTAP/crawldata/GSM_SIMULATOR-rl"
dir_check = r"e:/THUCTAP/crawldata/GSM_SIMULATOR-check"

with open("scratch/diff_result.txt", "r", encoding="utf-8") as f:
    lines = [line.strip() for line in f if line.strip() and not line.startswith("===")]

real_diffs = []
crlf_only = []

for rel in lines:
    f_rl = os.path.join(dir_rl, rel)
    f_chk = os.path.join(dir_check, rel)
    if not (os.path.exists(f_rl) and os.path.exists(f_chk)):
        continue
    try:
        with open(f_rl, "r", encoding="utf-8") as f1, open(f_chk, "r", encoding="utf-8") as f2:
            t1 = f1.read().replace("\r\n", "\n")
            t2 = f2.read().replace("\r\n", "\n")
            if t1 != t2:
                real_diffs.append(rel)
            else:
                crlf_only.append(rel)
    except Exception as e:
        real_diffs.append(f"{rel} (read error: {e})")

print(f"Total diffs: {len(lines)}")
print(f"CRLF only diffs: {len(crlf_only)}")
print(f"Real content diffs: {len(real_diffs)}")
print("\nREAL CONTENT DIFFS:")
for d in real_diffs:
    print(d)
