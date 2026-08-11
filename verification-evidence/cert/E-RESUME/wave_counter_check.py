"""Does a refused wave inflate the restored wave counter?"""
import json, subprocess, sys, tempfile
from pathlib import Path
HERE = Path('/Users/sammyfammy/neyma-product-driver/verification-evidence/cert/E-RESUME')
for p in ('/Users/sammyfammy/neyma-product-driver', '/Users/sammyfammy/neyma-product-driver/tests', str(HERE)):
    sys.path.insert(0, p)
import resume_probe as rp, resume_probe2 as rp2
root = Path(tempfile.mkdtemp(prefix="wavecheck-"))
work = rp2.fresh(root, "w")
rp2.run(work, "generate", "boundary", "1")          # max_waves=1, wave 1 spent
rp2.run(work, "resume_generate", "1", "after.json")  # resume, ask again -> refused
after = json.loads((work/"after.json").read_text())
rp2.run(work, "resume", "restored2.json")            # resume a second time
again = json.loads((work/"restored2.json").read_text())
print(json.dumps({
  "waves_used_p1": 1,
  "wave_records_after_refusal": [(w["wave"], w["stage"], w["budget_notes"]) for w in after["plan"]["waves"]],
  "waves_used_on_second_resume": again["waves_used"],
  "budget_exhausted_on_second_resume": again["budget_exhausted"],
  "scenarios_intact": again["plan"]["scenario_ids"],
}, indent=2))
