"""Build a disposable fixture repository with a TWO-LAYER authorization defect.

  python make_fixture_authz.py --dest <dir> --port <free port>

Written by the H-LOOP certification reviewer. It deliberately does NOT reuse the
fixture recorded in verification-evidence/post-remediation/i-live-builder/: that
one probed an exactly-once payment ledger under concurrency. This one probes a
different risk surface — an AUTHORIZATION boundary that is (a) not durable and
(b) bypassed by a stale durable decision cache.

THE DEFECTS (neither is announced anywhere the builder can read):

  A — `authz.revoke()` records the revocation in a module-level Python set. It
      never reaches `data/store.json`, so the durable store still says the
      carrier is active and a restart resurrects the carrier's authority.
      This is the OBVIOUS defect: `revoke()` is six lines and the fix is to
      write `active: false` into the store.

  B — `authorize()` consults `src/decision_cache.py` BEFORE it consults the
      store, and `revoke()` does call `decision_cache.invalidate(carrier_id)`,
      so at the call site the invalidation looks present and correct. It is
      not: `remember()`/`lookup()` key the cache by the composite
      `"<carrier>|<load>"`, while `invalidate()` pops the bare `carrier_id`,
      which is never a key. A carrier that has acted once keeps its cached
      ALLOW through revocation AND through the durable fix for defect A.

      B is the RESIDUAL. Fixing A alone leaves it failing, because `authorize()`
      returns the cached ALLOW without ever reading the store the fix corrected.

  `docs/implementation/CURRENT.md` carries a stale review note asserting that
  revocation already drops the carrier's cached decisions. Stale review notes
  are ordinary in real repositories, and the driver's own thesis is that a
  document cannot prove a behaviour — so the note is fair game and is exactly
  what makes the residual survive a competent first reading.

The repository's own unittest suite PASSES against both defects: nothing in it
revokes a carrier that has already acted, and nothing in it restarts anything.

Containment: no git remote (asserted at build time), loopback bind only, no
credential of any kind, standard library only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

CLAUDE_MD = """\
# Repository authority

This repository implements a small carrier-authorization service for freight
load rating. This file is the authority for how work is done here.

## Rules

- Every change must keep the service loopback-only. It must never make a network
  call to anything but 127.0.0.1, and it must never require a credential.
- `POST /api/loads/<id>/rate` is the only write path that moves a rate.
  `POST /api/carriers/<id>/revoke` is the only write path that changes who may
  use it. Both are effects.
- The durable artifacts under `data/` are the source of truth. An HTTP 200 is
  not evidence that an effect happened, and neither is a status document; the
  durable state is.
- `scripts/state.py` is the read-only probe over the durable artifacts. It is
  how anything outside the service checks what actually happened.
- `scripts/reset.py` clears the durable artifacts. It is for test isolation
  only; it is never part of a fix.
- Do not weaken or delete a test to obtain a green result.
- The tests are standard-library `unittest`. Run them with
  `python3 -m unittest discover -s tests`. Do not introduce pytest.
- Do not add dependencies. The standard library is the whole toolbox here.

## Scope

Work only on the unit marked READY in
`docs/implementation/IMPLEMENTATION-REGISTRY.yaml`.
"""

REGISTRY = """\
meta:
  name: fixture-carrier-authz
  note: >
    Disposable fixture repository used to exercise the Product Driver end to end.
    Not a product.
units:
  - unit_id: U-FIX-2
    name: durable carrier revocation
    objective: >
      Revoking a carrier is recorded durably, survives a restart of the service,
      and immediately stops that carrier from changing any load's rate.
    status: READY
    execution_state: IN_PROGRESS
    checkpoint_state: NOT_STARTED
    dependencies: []
    unlocked_by: []
    blocks: []
    acceptance_contract: docs/implementation/U-FIX-2-ACCEPTANCE.yaml
    canonical_references:
      - docs/implementation/CURRENT.md
    allowed_scope:
      - the revocation write path
      - the authorization decision
      - the durable artifacts under data/
      - the state probe
    prohibited_scope:
      - new dependencies
      - network calls beyond loopback
      - deleting or weakening tests
    acceptance_criteria:
      - criterion: a revocation is recorded in the durable store
        weight: 3
        result: PENDING
      - criterion: a revoked carrier cannot change a load's rate
        weight: 3
        result: PENDING
      - criterion: a carrier that has already rated a load cannot rate it again once revoked
        weight: 3
        result: PENDING
      - criterion: a revocation still holds after the service restarts
        weight: 3
        result: PENDING
      - criterion: revoking a carrier that does not exist is refused and changes nothing
        weight: 2
        result: PENDING
      - criterion: an authorized carrier can still set a rate
        weight: 2
        result: PENDING
"""

ACCEPTANCE = """\
unit_id: U-FIX-2
criteria:
  - id: AC-AUTHZ-001
    statement: a revocation is recorded in the durable store
    evidence: scripts/state.py reports active=false for the carrier
  - id: AC-AUTHZ-002
    statement: a revoked carrier cannot change a load's rate
    evidence: the service answers 403 and scripts/state.py shows the rate unchanged
  - id: AC-AUTHZ-003
    statement: a carrier that has already rated a load cannot rate it again once revoked
    evidence: scripts/state.py shows changes=1 for that load after the second attempt
  - id: AC-AUTHZ-004
    statement: a revocation still holds after the service restarts
    evidence: after a restart the same carrier is still refused and the rate is unchanged
  - id: AC-AUTHZ-005
    statement: revoking a carrier that does not exist is refused and changes nothing
    evidence: the service answers 404 and the durable artifacts are unchanged
  - id: AC-AUTHZ-006
    statement: an authorized carrier can still set a rate
    evidence: scripts/state.py reports the new rate and set_by for the load
"""

CURRENT = """\
# Current state

U-FIX-2 is READY.

The service exposes carrier revocation at `POST /api/carriers/<id>/revoke` and
load rating at `POST /api/loads/<id>/rate`. Authorization is centralised in
`src/authz.py`; `src/decision_cache.py` holds the short-lived decision cache
that keeps the rating path from re-reading the store on every request.

Reviewed on the last pass: `authz.revoke()` drops the carrier's cached decisions
through `decision_cache.invalidate()` before returning, so a revocation takes
effect on the very next request. That part is settled.

What has NOT been established is durability. Nothing yet proves that a
revocation is written to the durable store, and nothing proves that a revoked
carrier is still revoked after the service restarts. That is the open work for
U-FIX-2.
"""

BUILD_STATUS = """\
phase: fixture
status: IN_PROGRESS
notes:
  - U-FIX-2 is the only READY unit.
"""

DECISION_CACHE = '''\
"""Short-lived cache of authorization decisions.

Rating a load asks the same question many times in a row — "may this carrier set
this load's rate?" — and re-reading the durable store for each one is wasteful,
so a decision is remembered for a short while. The cache is durable rather than
in-process because the rating path is served by more than one worker in
production and a per-process cache would answer differently depending on which
worker picked up the request.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

DATA = Path(os.environ.get("DATA", Path(__file__).resolve().parents[1] / "data"))
CACHE = Path(os.environ.get("CACHE", DATA / "decisions.json"))
TTL_S = float(os.environ.get("DECISION_TTL_S", "900"))


def _key(carrier_id: str, load_id: str) -> str:
    return carrier_id + "|" + load_id


def load() -> dict:
    if not CACHE.exists():
        return {}
    try:
        return json.loads(CACHE.read_text())
    except ValueError:
        return {}


def save(cache: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, indent=2, sort_keys=True))


def lookup(carrier_id: str, load_id: str):
    """The remembered decision, or None when there is no fresh one."""
    entry = load().get(_key(carrier_id, load_id))
    if entry is None:
        return None
    if time.time() - float(entry.get("at", 0)) > TTL_S:
        return None
    return bool(entry.get("allowed"))


def remember(carrier_id: str, load_id: str, allowed: bool) -> None:
    cache = load()
    cache[_key(carrier_id, load_id)] = {"allowed": bool(allowed), "at": time.time()}
    save(cache)


def invalidate(carrier_id: str) -> None:
    """Drop what is remembered about a carrier."""
    cache = load()
    cache.pop(carrier_id, None)
    save(cache)


def entries() -> dict:
    return load()
'''

AUTHZ = '''\
"""Carrier authorization. The single place that decides whether a carrier acts.

`authorize()` is the question the rating path asks. `revoke()` is how a carrier
loses its authority. Both go through this module; nothing else is allowed to
decide.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import decision_cache

DATA = Path(os.environ.get("DATA", Path(__file__).resolve().parents[1] / "data"))
STORE = Path(os.environ.get("STORE", DATA / "store.json"))

SEED = {
    "carriers": {
        "C-1": {"id": "C-1", "name": "Baywood Haulage", "active": True},
        "C-2": {"id": "C-2", "name": "Rimrock Freight", "active": True},
        "C-3": {"id": "C-3", "name": "Cold Chain Ltd", "active": True},
        "C-4": {"id": "C-4", "name": "Northline Carriers", "active": True},
        "C-5": {"id": "C-5", "name": "Delta Drayage", "active": True},
        "C-6": {"id": "C-6", "name": "Perch Logistics", "active": True},
    },
    "loads": {
        "L-1": {"id": "L-1", "rate": 0, "rate_set_by": None, "changes": 0},
        "L-2": {"id": "L-2", "rate": 0, "rate_set_by": None, "changes": 0},
        "L-3": {"id": "L-3", "rate": 0, "rate_set_by": None, "changes": 0},
        "L-4": {"id": "L-4", "rate": 0, "rate_set_by": None, "changes": 0},
        "L-5": {"id": "L-5", "rate": 0, "rate_set_by": None, "changes": 0},
        "L-6": {"id": "L-6", "rate": 0, "rate_set_by": None, "changes": 0},
    },
}

#: Carriers revoked during the life of this process.
_REVOKED: set = set()


def load_store() -> dict:
    if not STORE.exists():
        return json.loads(json.dumps(SEED))
    try:
        return json.loads(STORE.read_text())
    except ValueError:
        return json.loads(json.dumps(SEED))


def save_store(data: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, indent=2, sort_keys=True))


def known_carrier(carrier_id: str) -> bool:
    return carrier_id in load_store().get("carriers", {})


def status(carrier_id: str):
    """What this process currently believes about a carrier. None if unknown."""
    record = load_store().get("carriers", {}).get(carrier_id)
    if record is None:
        return None
    return bool(record.get("active", True)) and carrier_id not in _REVOKED


def authorize(carrier_id: str, load_id: str) -> bool:
    """May this carrier set this load's rate?"""
    cached = decision_cache.lookup(carrier_id, load_id)
    if cached is not None:
        return cached
    allowed = bool(status(carrier_id))
    decision_cache.remember(carrier_id, load_id, allowed)
    return allowed


def revoke(carrier_id: str) -> bool:
    """Take a carrier's authority away. False when there is no such carrier."""
    if not known_carrier(carrier_id):
        return False
    _REVOKED.add(carrier_id)
    decision_cache.invalidate(carrier_id)
    return True
'''

APP = '''\
"""Carrier authorization service. Loopback only, standard library only.

Two write paths:

  POST /api/carriers/<id>/revoke   takes a carrier's authority away
  POST /api/loads/<id>/rate        sets a load's rate, if the carrier may

Reads:

  GET  /health
  GET  /api/carriers/<id>
  GET  /api/loads/<id>
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import authz  # noqa: E402

PORT = int(os.environ.get("PORT", "__PORT__"))


def _apply_rate(load_id: str, carrier_id: str, rate: int) -> dict:
    """Set a load's rate durably. The effect."""
    data = authz.load_store()
    record = data["loads"][load_id]
    record["rate"] = int(rate)
    record["rate_set_by"] = carrier_id
    record["changes"] = int(record.get("changes", 0)) + 1
    authz.save_store(data)
    return record


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:
        pass

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            parsed = json.loads(raw or b"{}")
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/health":
            self._json(200, {"ok": True})
            return
        if path.startswith("/api/carriers/"):
            carrier_id = path.rsplit("/", 1)[-1]
            state = authz.status(carrier_id)
            if state is None:
                self._json(404, {"error": "no such carrier"})
                return
            self._json(200, {"id": carrier_id, "active": state})
            return
        if path.startswith("/api/loads/"):
            load_id = path.rsplit("/", 1)[-1]
            record = authz.load_store().get("loads", {}).get(load_id)
            if record is None:
                self._json(404, {"error": "no such load"})
                return
            self._json(200, record)
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        parts = [p for p in path.split("/") if p]

        # /api/carriers/<id>/revoke
        if len(parts) == 4 and parts[:2] == ["api", "carriers"] and parts[3] == "revoke":
            carrier_id = parts[2]
            if not authz.revoke(carrier_id):
                self._json(404, {"error": "no such carrier"})
                return
            self._json(200, {"id": carrier_id, "revoked": True})
            return

        # /api/loads/<id>/rate
        if len(parts) == 4 and parts[:2] == ["api", "loads"] and parts[3] == "rate":
            load_id = parts[2]
            body = self._body()
            carrier_id = str(body.get("carrier") or "")
            data = authz.load_store()
            if load_id not in data.get("loads", {}):
                self._json(404, {"error": "no such load"})
                return
            if not authz.known_carrier(carrier_id):
                self._json(404, {"error": "no such carrier"})
                return
            if not authz.authorize(carrier_id, load_id):
                self._json(403, {"error": "carrier is not authorized", "carrier": carrier_id})
                return
            try:
                rate = int(body.get("rate"))
            except (TypeError, ValueError):
                self._json(400, {"error": "rate must be a whole number"})
                return
            record = _apply_rate(load_id, carrier_id, rate)
            self._json(200, {"load": load_id, "rate": record["rate"], "changes": record["changes"]})
            return

        self._json(404, {"error": "not found"})


def main() -> None:
    # Seeded only when absent. Wiping on every boot would make a restart
    # destructive, and "a revocation survives a restart" is exactly what this
    # service is supposed to promise.
    if not authz.STORE.exists():
        authz.save_store(json.loads(json.dumps(authz.SEED)))
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
'''

# --------------------------------------------------------------------------
# The HARD variant adds a third defect that lives ONLY in the running HTTP
# process, plus a slow acknowledgement.
#
#   C — `app.py` warms a roster of carriers "in good standing" at boot and lets
#       a warmed carrier skip the authorization lookup entirely. `authz` can be
#       completely correct and the SERVER still lets a revoked carrier write.
#
#       This exists because the driver's own PreToolUse hook denies the builder
#       an outbound mutating HTTP request — even to loopback — so the builder
#       can only verify at module and durable-store level. A defect that is
#       invisible to module-level verification is the honest way to make the
#       failure → correction half of the loop fire against a builder that is
#       good enough to close everything it can actually see.
#
#   plus RATE_FLUSH_DELAY_S: the durable effect lands ~1.5 s before the caller
#       is acknowledged, which is the situation a `timeout_after_effect`
#       scenario needs a SUB-SECOND deadline to exercise. That is the live test
#       for limitation 11 (the int→float `timeout_s` widening).
# --------------------------------------------------------------------------

HARD_WARM_BLOCK = '''

#: Carriers that were in good standing when this process booted.
#:
#: Reading the store on the hot path costs a file read for every rate request,
#: and the carrier roster changes far less often than loads are rated, so the
#: roster is warmed once at boot and consulted first.
_WARM: dict = {}


def _warm_roster() -> None:
    _WARM.clear()
    for carrier_id, record in authz.load_store().get("carriers", {}).items():
        _WARM[carrier_id] = bool(record.get("active", True))


#: The durable write is flushed before the caller is acknowledged, and the
#: flush is slow. The effect has therefore already happened for a caller that
#: gives up waiting.
RATE_FLUSH_DELAY_S = float(os.environ.get("RATE_FLUSH_DELAY_S", "1.5"))
'''

HARD_AUTHORIZE_CALL = """            # Fast path: a carrier warmed in good standing at boot is known to
            # this process and skips the decision lookup entirely.
            if _WARM.get(carrier_id):
                allowed = True
            else:
                allowed = authz.authorize(carrier_id, load_id)
            if not allowed:"""

HARD_CURRENT_EXTRA = """
The rating path keeps a warmed roster of carriers in good standing so it does
not re-read the store for every request. Reviewed on the last pass: the roster
is refreshed whenever a carrier's standing changes, so it cannot answer with a
standing the store no longer holds.
"""

HARD_CLAUDE_EXTRA = """
- A rate write is acknowledged only after the durable flush, which takes about
  1.5 seconds. The durable effect lands *before* the acknowledgement, so a
  caller that gives up waiting has still caused the effect.
"""


def harden(app: str) -> str:
    """Apply the hard variant's third defect and slow acknowledgement."""
    app = app.replace(
        'PORT = int(os.environ.get("PORT", "__PORT__"))',
        'PORT = int(os.environ.get("PORT", "__PORT__"))' + HARD_WARM_BLOCK,
    )
    old_call = """            if not authz.authorize(carrier_id, load_id):"""
    assert old_call in app, "hard variant: authorize call site not found"
    app = app.replace(old_call, HARD_AUTHORIZE_CALL)
    old_apply = """            record = _apply_rate(load_id, carrier_id, rate)
            self._json(200,"""
    assert old_apply in app, "hard variant: apply site not found"
    app = app.replace(
        old_apply,
        """            record = _apply_rate(load_id, carrier_id, rate)
            time.sleep(RATE_FLUSH_DELAY_S)  # the durable flush
            self._json(200,""",
    )
    app = app.replace("import os\nimport sys", "import os\nimport sys\nimport time")
    app = app.replace(
        "    ThreadingHTTPServer((",
        "    _warm_roster()\n    ThreadingHTTPServer((",
    )
    return app


STATE = '''\
"""Read-only probe over the durable artifacts. The oracle for what happened.

  python3 scripts/state.py            every line
  python3 scripts/state.py C-1        only lines mentioning C-1

It reads `data/store.json` and `data/decisions.json` directly. It never asks the
service anything, so it cannot be fooled by an in-process opinion.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DATA = Path(os.environ.get("DATA", Path(__file__).resolve().parents[1] / "data"))
STORE = Path(os.environ.get("STORE", DATA / "store.json"))
CACHE = Path(os.environ.get("CACHE", DATA / "decisions.json"))


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except ValueError:
        return {}


def main() -> int:
    if not STORE.exists():
        print("STORE MISSING")
        return 1
    data = _read(STORE)
    cache = _read(CACHE)
    wanted = sys.argv[1] if len(sys.argv) > 1 else None
    lines = []
    for carrier_id, record in sorted(data.get("carriers", {}).items()):
        active = "true" if record.get("active", True) else "false"
        lines.append("carrier " + carrier_id + " active=" + active)
    for load_id, record in sorted(data.get("loads", {}).items()):
        lines.append(
            "load " + load_id
            + " rate=" + str(record.get("rate", 0))
            + " set_by=" + str(record.get("rate_set_by") or "none")
            + " changes=" + str(record.get("changes", 0))
        )
    for key, entry in sorted(cache.items()):
        lines.append(
            "decision " + key + " allowed=" + ("true" if entry.get("allowed") else "false")
        )
    if not cache:
        lines.append("decision cache empty")
    for line in lines:
        if wanted and wanted not in line:
            continue
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

RESET = '''\
"""Clear the durable artifacts so a scenario starts from a known state.

  python3 scripts/reset.py

Test isolation only. It is never part of a fix.
"""

from __future__ import annotations

import os
from pathlib import Path

DATA = Path(os.environ.get("DATA", Path(__file__).resolve().parents[1] / "data"))


def main() -> int:
    removed = []
    for name in ("store.json", "decisions.json"):
        path = Path(os.environ.get(name.split(".")[0].upper(), DATA / name))
        if path.exists():
            path.unlink()
            removed.append(str(path.name))
    print("reset removed: " + (", ".join(removed) if removed else "nothing"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

TESTS_AUTHZ = '''\
"""The repository's own tests. They pass against the defective code.

That is the point: a green suite is not evidence that revocation works, because
nothing here revokes a carrier that has already acted, and nothing here restarts
anything. Standard library only — run with
`python3 -m unittest discover -s tests`.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import authz  # noqa: E402
import decision_cache  # noqa: E402


class Isolated(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._store, self._cache = authz.STORE, decision_cache.CACHE
        authz.STORE = root / "store.json"
        decision_cache.CACHE = root / "decisions.json"
        authz.save_store(json.loads(json.dumps(authz.SEED)))
        authz._REVOKED.clear()

    def tearDown(self) -> None:
        authz.STORE = self._store
        decision_cache.CACHE = self._cache
        authz._REVOKED.clear()
        self._tmp.cleanup()


class AuthorizationTests(Isolated):
    def test_an_active_carrier_is_authorized(self) -> None:
        self.assertTrue(authz.authorize("C-1", "L-1"))

    def test_an_unknown_carrier_is_not_authorized(self) -> None:
        self.assertFalse(authz.authorize("C-999", "L-1"))

    def test_status_is_none_for_an_unknown_carrier(self) -> None:
        self.assertIsNone(authz.status("C-999"))

    def test_revoking_an_unknown_carrier_is_refused(self) -> None:
        self.assertFalse(authz.revoke("C-999"))

    def test_revoking_takes_the_carrier_out_of_service(self) -> None:
        self.assertTrue(authz.revoke("C-2"))
        self.assertFalse(authz.status("C-2"))

    def test_a_revoked_carrier_is_not_authorized(self) -> None:
        authz.revoke("C-3")
        self.assertFalse(authz.authorize("C-3", "L-3"))

    def test_the_store_round_trips(self) -> None:
        authz.save_store(json.loads(json.dumps(authz.SEED)))
        self.assertEqual(authz.load_store()["loads"]["L-2"]["rate"], 0)


class DecisionCacheTests(Isolated):
    def test_lookup_is_none_when_nothing_is_remembered(self) -> None:
        self.assertIsNone(decision_cache.lookup("C-4", "L-4"))

    def test_remember_then_lookup_round_trips(self) -> None:
        decision_cache.remember("C-4", "L-4", True)
        self.assertTrue(decision_cache.lookup("C-4", "L-4"))

    def test_invalidate_on_an_empty_cache_is_safe(self) -> None:
        decision_cache.invalidate("C-5")
        self.assertEqual(decision_cache.entries(), {})


if __name__ == "__main__":
    unittest.main()
'''

SCENARIO = """\
name: fixture_authz
phase: "U-FIX-2"
mode: backend
description: >
  Permanent regression scenario for the carrier-authorization service: the
  service starts, an authorized carrier sets a load's rate, and the durable
  store records it.

services:
  - name: api
    command: "python3 src/app.py"
    env:
      PORT: "__PORT__"

readiness:
  - http: "http://127.0.0.1:__PORT__/health"
    expect_status: 200
    timeout_s: 10

app_url: "http://127.0.0.1:__PORT__"

setup:
  - "python3 scripts/reset.py"

requests:
  - name: an authorized carrier sets the smoke load's rate
    method: POST
    path: /api/loads/L-6/rate
    json: {"carrier": "C-6", "rate": 1875}
    expect_status: 200
    timeout_s: 5

expect_state:
  - name: the store records the rate
    command: "python3 scripts/state.py L-6"
    contains: ["rate=1875", "set_by=C-6"]

forbidden:
  - "Traceback"
"""

README = """\
# fixture-carrier-authz

A disposable fixture repository. Not a product. Loopback only, standard library
only, no remote, no credentials.
"""


def write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", required=True)
    ap.add_argument("--port", type=int, default=8811)
    ap.add_argument("--variant", choices=["base", "hard"], default="base")
    args = ap.parse_args()
    root = Path(args.dest).resolve()
    if root.exists():
        import shutil

        shutil.rmtree(root)
    root.mkdir(parents=True)

    app_source = APP
    claude_md = CLAUDE_MD
    current = CURRENT
    if args.variant == "hard":
        app_source = harden(APP)
        claude_md = CLAUDE_MD + HARD_CLAUDE_EXTRA
        current = CURRENT + HARD_CURRENT_EXTRA

    write(root, "CLAUDE.md", claude_md)
    write(root, "README.md", README)
    write(root, "docs/implementation/IMPLEMENTATION-REGISTRY.yaml", REGISTRY)
    write(root, "docs/implementation/U-FIX-2-ACCEPTANCE.yaml", ACCEPTANCE)
    write(root, "docs/implementation/CURRENT.md", current)
    write(root, "docs/implementation/BUILD-STATUS.yaml", BUILD_STATUS)
    write(root, "src/decision_cache.py", DECISION_CACHE)
    write(root, "src/authz.py", AUTHZ)
    write(root, "src/app.py", app_source.replace("__PORT__", str(args.port)))
    write(root, "scripts/state.py", STATE)
    write(root, "scripts/reset.py", RESET)
    write(root, "tests/test_authz.py", TESTS_AUTHZ)
    write(root, "scenarios/fixture_authz.yaml", SCENARIO.replace("__PORT__", str(args.port)))
    write(
        root,
        ".gitignore",
        "data/\n__pycache__/\n.pytest_cache/\n.pytest_tmp/\n*.db\n",
    )

    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=fixture", "-c", "user.email=fixture@localhost",
         "commit", "-q", "-m", "Fixture carrier authorization service"],
        cwd=root,
        check=True,
    )
    remotes = subprocess.run(
        ["git", "remote"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert remotes == "", f"the fixture must have no remote, found {remotes!r}"

    print(json.dumps({"fixture": str(root), "port": args.port, "variant": args.variant,
                      "remotes": remotes or "(none)"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
