#!/usr/bin/env python3
"""Mark completed tasks as [x] in artha-tasks.md by finding task headings."""
import sys

filepath = "/Users/ved/Library/CloudStorage/OneDrive-Personal/Artha/specs/artha-tasks.md"

with open(filepath, 'r') as f:
    lines = f.readlines()

# Task IDs to mark complete. Use trailing space where needed to avoid
# partial matches (e.g. "T-2.2.1 " won't match "T-2.2.1b").
tasks_to_complete = [
    "T-2A.17.1", "T-2A.17.2", "T-2A.17.3",
    "T-2A.18.1", "T-2A.18.2",
    "T-2A.19.1", "T-2A.19.2", "T-2A.19.3",
    "T-2A.20.1", "T-2A.20.2",
    "T-2A.21.1", "T-2A.21.2", "T-2A.21.3", "T-2A.21.4",
    "T-2A.22.1", "T-2A.22.2", "T-2A.22.3", "T-2A.22.4", "T-2A.22.5",
    "T-2.2.1 ", "T-2.2.1b", "T-2.2.2 ", "T-2.2.3 ", "T-2.2.3b",
    "T-2.2.5 ", "T-2.2.8 ", "T-2.2.9 ",
    "T-2.3.1 ", "T-2.3.2 ", "T-2.3.3 ",
    "T-2.4.1 ",
    "T-3.1.5 ", "T-3.1.6 ",
    "T-3.3.1 ",
]

changed = 0
skipped = 0
errors = []

for task_id in tasks_to_complete:
    tid = task_id.strip()
    heading_idx = None
    for i, line in enumerate(lines):
        if line.startswith("#### ") and task_id in line:
            heading_idx = i
            break
    if heading_idx is None:
        errors.append("NOT FOUND: " + tid)
        continue
    # Priority line is usually heading+2 (blank line in between), try 1-3
    marked = False
    for offset in (2, 1, 3):
        pi = heading_idx + offset
        if pi < len(lines) and "- [ ] **Priority**" in lines[pi]:
            lines[pi] = lines[pi].replace("- [ ] **Priority**", "- [x] **Priority**", 1)
            changed += 1
            print("  MARKED  " + tid + " (line " + str(pi + 1) + ")")
            marked = True
            break
        elif pi < len(lines) and "- [x] **Priority**" in lines[pi]:
            skipped += 1
            print("  SKIP    " + tid + " (already [x])")
            marked = True
            break
    if not marked:
        errors.append("MISMATCH " + tid + " heading=L" + str(heading_idx + 1))

if errors:
    print("\nERRORS:")
    for e in errors:
        print("  " + e)

with open(filepath, 'w') as f:
    f.writelines(lines)

print("\nResult: " + str(changed) + " marked, " + str(skipped) + " already done, " + str(len(errors)) + " errors")
if errors:
    sys.exit(1)
