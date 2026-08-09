import datetime
import hashlib
import json
import pathlib
import subprocess

live = pathlib.Path("/Users/sammyfammy/neyma-product-driver/neyma_product_driver")
stage = pathlib.Path(
    "/Users/sammyfammy/neyma-product-driver/verification-evidence/r4-scale/stage/neyma_product_driver"
)
out = {}
for p in sorted(live.glob("*.py")):
    a = hashlib.sha256(p.read_bytes()).hexdigest()
    q = stage / p.name
    b = hashlib.sha256(q.read_bytes()).hexdigest() if q.exists() else None
    out[p.name] = {"live_sha256": a, "stage_sha256": b, "match": a == b}

d = subprocess.run(
    ["diff", "-r", "--brief", "--exclude=__pycache__", str(live), str(stage)],
    capture_output=True,
    text=True,
)
rec = {
    "verified_at": datetime.datetime.now().isoformat(timespec="seconds"),
    "executed_against": str(stage.parent),
    "source_of_truth": str(live.parent),
    "note": "the assigned git worktree is stale (commit bbe0fd2) and lacks the "
            "untracked feature modules; nothing was executed from it",
    "diff_r_brief_output": d.stdout.strip() or "(no differences)",
    "diff_returncode": d.returncode,
    "all_files_match": all(v["match"] for v in out.values()),
    "file_count": len(out),
    "per_file": out,
}
pathlib.Path(
    "/Users/sammyfammy/neyma-product-driver/verification-evidence/r4-scale/out/PROVENANCE.json"
).write_text(json.dumps(rec, indent=2))
print("all_match:", rec["all_files_match"], "| files:", rec["file_count"],
      "| diff:", rec["diff_r_brief_output"])
