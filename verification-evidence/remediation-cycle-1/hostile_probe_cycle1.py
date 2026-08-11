"""Replay the exact exploits the reviewers proved, against the remediated code.

Harmless and local only: nothing here dials a real external host, and the
"payload" fixtures are inert marker files. What is being tested is whether the
boundary REFUSES, not whether the payload works.
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/Users/sammyfammy/neyma-product-driver")

from neyma_product_driver.scenario_validation import (  # noqa: E402
    FIXTURE_DATA_EXTENSIONS,
    identity_key,
    resolve_browser_target,
)

FAILURES: list[str] = []


def check(label: str, refused: bool, detail: str = "") -> None:
    status = "REFUSED " if refused else "*** ADMITTED ***"
    print(f"  {status} {label}" + (f"  [{detail[:70]}]" if detail and refused else ""))
    if not refused:
        FAILURES.append(label)


print("=" * 78)
print("D-SAFETY-02 / F-4 — goto scheme mismatch escapes loopback")
print("=" * 78)
# The exact shapes the reviewer proved the validator never inspected while the
# executor treated them as absolute.
ESCAPES = [
    "http:/evil.invalid/x",
    "http:evil.invalid/x",
    "http:\\\\evil.invalid\\x",
    "httpx://evil.invalid/x",
    "//evil.invalid/x",
    "\\\\evil.invalid\\x",
    "https:/evil.invalid/x",
    "HTTP://evil.invalid/x",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "data:text/html,<h1>x",
    "http:/\x00evil.invalid/x",
]
for goto in ESCAPES:
    target, problem = resolve_browser_target(app_url="http://127.0.0.1:8000", goto=goto)
    # Refused outright, or resolved to something still on loopback.
    escaped = (not problem) and "evil.invalid" in target
    bad_scheme = (not problem) and target.split(":", 1)[0].lower() not in ("http", "https")
    check(repr(goto), bool(problem) or not (escaped or bad_scheme), problem)

print("\n  -- legitimate navigation must still be admitted (no false refusals) --")
for goto in ["/dashboard", "/", "/a/b?c=d#e", "http://127.0.0.1:8000/x", "http://localhost:8000/y"]:
    target, problem = resolve_browser_target(app_url="http://127.0.0.1:8000", goto=goto)
    ok = not problem
    print(f"  {'ADMITTED' if ok else '*** FALSE REFUSAL ***'} {goto!r} -> {target!r}")
    if not ok:
        FAILURES.append(f"false refusal: {goto}")

print("\n" + "=" * 78)
print("D-SAFETY-01 — fixture_content executes as code via {{fixture:}}")
print("=" * 78)
CODE_FIXTURES = [
    "conftest.py", "test_evil.py", "evil.py", "run.sh", "x.js", "y.rb",
    "setup.cfg", "pytest.ini", "pyproject.toml", "sitecustomize.pth",
    "evil.PY", "evil.Py", "a.json.py", "evil.so", "evil.dylib",
]
for name in CODE_FIXTURES:
    from pathlib import PurePosixPath
    suffix = PurePosixPath(name).suffix.casefold()
    refused = suffix not in FIXTURE_DATA_EXTENSIONS
    check(f"fixture {name!r}", refused, f"suffix {suffix!r} not in data allowlist")

print("\n  -- legitimate data fixtures must still be admitted --")
for name in ["invoice.json", "rows.csv", "cfg.yaml", "notes.txt", "feed.xml", "a.jsonl"]:
    from pathlib import PurePosixPath
    suffix = PurePosixPath(name).suffix.casefold()
    ok = suffix in FIXTURE_DATA_EXTENSIONS
    print(f"  {'ADMITTED' if ok else '*** FALSE REFUSAL ***'} {name!r}")
    if not ok:
        FAILURES.append(f"false refusal: {name}")

print("\n" + "=" * 78)
print("I1 / G-SCALE-02 — two identities, one evidence directory")
print("=" * 78)
from neyma_product_driver.evidence import sanitize_filename  # noqa: E402

COLLIDING = [
    ("gen-AUTH-01", "gen-auth-01"),
    ("approve twice", "approve-twice"),
    ("a b/c:d", "a_b/c:d"),
    ("///", "***"),
    ("x/y", "x-y"),
]
for a, b in COLLIDING:
    fa, fb = sanitize_filename(a), sanitize_filename(b)
    distinct = fa.casefold() != fb.casefold()
    check(f"{a!r} vs {b!r} -> {fa!r} vs {fb!r}", distinct)
    ka, kb = identity_key(a), identity_key(b)
    if a.casefold() == b.casefold() and ka != kb:
        FAILURES.append(f"identity_key failed to fold {a!r}/{b!r}")

print("\n  -- an already-safe label is unchanged (readability preserved) --")
for n in ["gen-auth-01", "backend_generic", "browser_generic"]:
    ok = sanitize_filename(n) == n
    print(f"  {'UNCHANGED' if ok else '*** MANGLED ***'} {n!r} -> {sanitize_filename(n)!r}")
    if not ok:
        FAILURES.append(f"mangled: {n}")

print("\n" + "=" * 78)
print("R1 — redact_obj corrupts the plan via 'authorization'")
print("=" * 78)
from neyma_product_driver.models import redact_obj  # noqa: E402

payload = {
    "by_risk_category": {"authorization": 3, "persistence": 2},
    "authorization": 7,
    "api_key": "sk-secret-value-here",
    "token": {"nested": "secret-string", "count": 4},
    "password": ["p1", 2, "p3"],
}
out = redact_obj(payload)
print(f"  {out}")
check("int under 'authorization' survives", out["authorization"] == 7)
check("dict[str,int] by_risk_category survives", out["by_risk_category"]["authorization"] == 3)
check("string api_key IS masked", out["api_key"] == "[REDACTED]")
check("string nested under 'token' IS masked", out["token"]["nested"] == "[REDACTED]")
check("int nested under 'token' survives", out["token"]["count"] == 4)
check("strings in list under 'password' ARE masked", out["password"][0] == "[REDACTED]")
check("int in list under 'password' survives", out["password"][1] == 2)

print("\n" + "=" * 78)
if FAILURES:
    print(f"HOSTILE PROBE RESULT: {len(FAILURES)} FAILURE(S)")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("HOSTILE PROBE RESULT: every replayed exploit refused; zero false refusals")
sys.exit(0)
