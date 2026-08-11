"""E-RESUME Part 2 — scenario identity attacks.

Reviewer-authored. Drives the real product path:
  scenario_plan.GeneratedScenario._identity   (limit 64)
  evidence.shorten_preserving_identity
  evidence.sanitize_filename                  (limit 80)

Run:  .venv/bin/python verification-evidence/cert/E-RESUME/identity_probe.py
Emits identity_probe.json beside itself.
"""
from __future__ import annotations

import json
import os
import random
import string
import subprocess
import sys
import unicodedata
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from neyma_product_driver.evidence import (  # noqa: E402
    FILENAME_LIMIT,
    sanitize_filename,
    shorten_preserving_identity,
)
from neyma_product_driver.scenario_plan import (  # noqa: E402
    SCENARIO_ID_LIMIT,
    GeneratedScenario,
    GeneratedScenarioPlan,
)

from scenario_fixtures import make_scenario  # noqa: E402

OUT: dict[str, object] = {}


def ident(raw: str) -> tuple[str, str] | str:
    """Real path: build a GeneratedScenario and read back (id, proposed_id)."""
    try:
        s = make_scenario(raw)
    except Exception as exc:  # noqa: BLE001
        return f"REFUSED: {type(exc).__name__}"
    return (s.id, s.proposed_id)


# --------------------------------------------------------------------------
# 1. shared-prefix families
# --------------------------------------------------------------------------
prefix_cases = {}
for n in (60, 64, 68, 80, 200):
    a = "gen-" + "x" * (n - 4) + "-alpha"
    b = "gen-" + "x" * (n - 4) + "-beta"
    ra, rb = ident(a), ident(b)
    prefix_cases[f"prefix_{n}"] = {
        "a_len": len(a),
        "a": ra,
        "b": rb,
        "collides": ra == rb,
        "fs_a": sanitize_filename(ra[0]) if isinstance(ra, tuple) else ra,
        "fs_b": sanitize_filename(rb[0]) if isinstance(rb, tuple) else rb,
        "fs_collides": (
            sanitize_filename(ra[0]) == sanitize_filename(rb[0])
            if isinstance(ra, tuple) and isinstance(rb, tuple)
            else None
        ),
    }
# differing only AFTER the 64-char truncation point
a = "g" * 63 + "A" + "tail-one"
b = "g" * 63 + "A" + "tail-two"
ra, rb = ident(a), ident(b)
prefix_cases["differ_after_truncation"] = {"a": ra, "b": rb, "collides": ra == rb}
OUT["prefix_families"] = prefix_cases

# --------------------------------------------------------------------------
# 2. sanitisation collisions — ids that differ only in characters the
#    sanitiser maps to the same replacement
# --------------------------------------------------------------------------
sanitise_groups = {
    "separator_family": ["a/b", "a\\b", "a:b", "a b", "a|b", "a*b", "a-b"],
    "dotdot_family": ["a..b", "a/../b"],
    "case_family": ["Gen-Approve-Twice", "gen-approve-twice", "GEN-APPROVE-TWICE"],
    "unicode_family": ["café-test", "cafè-test", "caf中-test"],
    "nfc_nfd_family": [
        unicodedata.normalize("NFC", "café"),
        unicodedata.normalize("NFD", "café"),
    ],
    "pure_separators": ["///", "---", "   ", "", "..."],
    "path_escape": ["..", ".", "../..", "/etc/passwd", "~/.ssh/id_rsa", "a\x00b", "x" * 300],
}
sanitise_out = {}
for group, members in sanitise_groups.items():
    rows = []
    for m in members:
        r = ident(m)
        rows.append(
            {
                "raw": m,
                "identity": r,
                "sanitize_filename": sanitize_filename(m),
            }
        )
    ids = [r["identity"][0] for r in rows if isinstance(r["identity"], tuple)]
    sanitise_out[group] = {
        "rows": rows,
        "distinct_ids": len(set(ids)),
        "members_admitted": len(ids),
        "collapsed": len(set(ids)) < len(ids),
    }
OUT["sanitisation"] = sanitise_out

# --------------------------------------------------------------------------
# 3. injectivity at scale — a few thousand adversarial ids
# --------------------------------------------------------------------------
rng = random.Random(20260810)
adversarial: list[str] = []
base = "gen-" + "z" * 70
for i in range(1200):  # long, shared 74-char prefix, differ only in the tail
    adversarial.append(f"{base}-{i:06d}")
for i in range(800):  # differ only past char 64
    adversarial.append("q" * 64 + f"-{i:06d}")
for i in range(600):  # random long
    adversarial.append(
        "".join(rng.choice(string.ascii_letters + string.digits + "-._") for _ in range(rng.randint(65, 300)))
    )
for i in range(600):  # short-ish, near limit
    adversarial.append("".join(rng.choice("abcXYZ0._-") for _ in range(rng.randint(55, 70))))
for i in range(400):  # separator soup (sanitiser-collision bait)
    adversarial.append("".join(rng.choice("ab/\\: |*") for _ in range(rng.randint(20, 90))))
adversarial = list(dict.fromkeys(adversarial))

id_map: dict[str, list[str]] = {}
fs_map: dict[str, list[str]] = {}
refused = 0
for raw in adversarial:
    r = ident(raw)
    if not isinstance(r, tuple):
        refused += 1
        continue
    sid, _pid = r
    id_map.setdefault(sid, []).append(raw)
    fs_map.setdefault(sanitize_filename(sid).lower(), []).append(raw)

id_collisions = {k: v for k, v in id_map.items() if len(v) > 1}
fs_collisions = {k: v for k, v in fs_map.items() if len(set(sanitize_filename(x) for x in v)) >= 1 and len(v) > 1}
OUT["scale"] = {
    "inputs": len(adversarial),
    "refused_at_model_validation": refused,
    "distinct_ids": len(id_map),
    "id_collision_groups": len(id_collisions),
    "id_collision_examples": {k: v[:4] for k, v in list(id_collisions.items())[:10]},
    "case_folded_fs_collision_groups": len(fs_collisions),
    "case_folded_fs_collision_examples": {k: v[:4] for k, v in list(fs_collisions.items())[:10]},
    "max_id_len": max((len(k) for k in id_map), default=0),
    "max_fs_len": max((len(sanitize_filename(k)) for k in id_map), default=0),
    "id_limit": SCENARIO_ID_LIMIT,
    "fs_limit": FILENAME_LIMIT,
}

# --------------------------------------------------------------------------
# 4. determinism across processes / hash seeds
# --------------------------------------------------------------------------
SAMPLE = [
    "gen-" + "x" * 90 + "-alpha",
    "gen-" + "x" * 90 + "-beta",
    "café-" + "y" * 80,
    "a/b" * 40,
]
CHILD = r"""
import json, sys
sys.path.insert(0, %r); sys.path.insert(0, %r)
from neyma_product_driver.evidence import sanitize_filename, shorten_preserving_identity
from scenario_fixtures import make_scenario
out = []
for raw in json.loads(sys.argv[1]):
    s = make_scenario(raw)
    out.append([s.id, s.proposed_id, sanitize_filename(s.id),
                shorten_preserving_identity(raw, 64)])
print(json.dumps(out))
""" % (str(REPO), str(REPO / "tests"))

runs = {}
for seed in ("0", "1", "12345", "random"):
    env = dict(os.environ, PYTHONHASHSEED=seed)
    proc = subprocess.run(
        [sys.executable, "-c", CHILD, json.dumps(SAMPLE)],
        env=env, capture_output=True, text=True, cwd=str(REPO), check=True,
    )
    runs[seed] = json.loads(proc.stdout)
first = runs["0"]
OUT["determinism"] = {
    "sample": SAMPLE,
    "per_seed_equal_to_seed0": {k: (v == first) for k, v in runs.items()},
    "seed0_result": first,
}

# --------------------------------------------------------------------------
# 5. proposed_id survives the plan JSON round trip
# --------------------------------------------------------------------------
long_a = "gen-" + "w" * 90 + "-alpha"
long_b = "gen-" + "w" * 90 + "-beta"
plan = GeneratedScenarioPlan(scenarios=[make_scenario(long_a), make_scenario(long_b)])
blob = json.dumps(plan.model_dump(mode="json"))
restored = GeneratedScenarioPlan.model_validate_json(blob)
OUT["proposed_id_roundtrip"] = {
    "originals": [long_a, long_b],
    "ids": [s.id for s in restored.scenarios],
    "proposed_ids": [s.proposed_id for s in restored.scenarios],
    "preserved": {s.proposed_id for s in restored.scenarios} == {long_a, long_b},
    "ids_distinct": len({s.id for s in restored.scenarios}) == 2,
}

# --------------------------------------------------------------------------
# 6. does the id survive a second validation pass (idempotence)?
# --------------------------------------------------------------------------
once = make_scenario(long_a)
twice = GeneratedScenario.model_validate(once.model_dump(mode="json"))
thrice = GeneratedScenario.model_validate(twice.model_dump(mode="json"))
OUT["idempotence"] = {
    "id_stable": once.id == twice.id == thrice.id,
    "proposed_stable": once.proposed_id == twice.proposed_id == thrice.proposed_id,
    "ids": [once.id, twice.id, thrice.id],
}

path = Path(__file__).with_name("identity_probe.json")
path.write_text(json.dumps(OUT, indent=2, ensure_ascii=False), encoding="utf-8")
print(json.dumps(OUT, indent=2, ensure_ascii=False)[:12000])
print(f"\nwrote {path}")
