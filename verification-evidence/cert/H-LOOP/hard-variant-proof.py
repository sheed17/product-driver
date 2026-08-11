"""Prove the HARD variant's third defect, and prove why it survives a builder.

  python hard-variant-proof.py --work <dir>

Claims:

  1. the repository's own suite still passes;
  2. `authz.authorize()` — the module-level check, which is the ONLY kind of
     verification the driver's PreToolUse hook leaves the builder (it denies an
     outbound mutating HTTP request even to loopback) — gives the CORRECT answer
     for a revoked carrier once revocation is durable;
  3. the RUNNING SERVER nevertheless lets that carrier write, because
     `app._WARM` was warmed at boot and is never refreshed;
  4. the durable effect of a rate write lands BEFORE the caller is
     acknowledged, so a sub-second client deadline observes a
     `timeout_after_effect` situation — which is what makes a fractional
     `timeout_s` the natural thing for a generated scenario to ask for.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from importlib import import_module  # noqa: E402

proof = import_module("fixture-defect-proof".replace("-", "_")) if False else None

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + ((" — " + detail) if detail else ""), flush=True)


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def post(url: str, payload: dict, timeout: float = 10.0):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def get(url: str, timeout: float = 10.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class Service:
    def __init__(self, root: Path, port: int) -> None:
        self.root, self.port, self.proc = root, port, None

    def __enter__(self):
        self.proc = subprocess.Popen(
            [sys.executable, "src/app.py"],
            cwd=str(self.root),
            env={**os.environ, "PORT": str(self.port), "PYTHONUNBUFFERED": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                if get(f"http://127.0.0.1:{self.port}/health")[0] == 200:
                    return self
            except Exception:
                time.sleep(0.2)
        raise SystemExit("hard fixture service did not come up")

    def __exit__(self, *_e):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def probe(root: Path, arg: str = "") -> str:
    return subprocess.run(
        [sys.executable, "scripts/state.py"] + ([arg] if arg else []),
        cwd=str(root), capture_output=True, text=True,
    ).stdout.strip()


DURABLE_REVOKE = '''\
def revoke(carrier_id: str) -> bool:
    """Take a carrier's authority away. False when there is no such carrier."""
    data = load_store()
    if carrier_id not in data.get("carriers", {}):
        return False
    data["carriers"][carrier_id]["active"] = False
    save_store(data)
    _REVOKED.add(carrier_id)
    decision_cache.invalidate_all(carrier_id)
    return True
'''

DURABLE_INVALIDATE = '''\
def invalidate(carrier_id: str) -> None:
    """Drop what is remembered about a carrier."""
    invalidate_all(carrier_id)


def invalidate_all(carrier_id: str) -> None:
    cache = load()
    for key in [k for k in cache if k.split("|")[0] == carrier_id]:
        cache.pop(key)
    save(cache)
'''


def apply_module_level_fix(root: Path) -> None:
    """Fix defects A and B properly, leaving the HTTP-layer defect C alone.

    This is what a builder that can only verify at module level arrives at: it
    makes `authz.authorize()` correct in every module-level sense.
    """
    authz = root / "src" / "authz.py"
    text = authz.read_text()
    head, marker, _ = text.partition("def revoke(carrier_id: str) -> bool:")
    assert marker
    authz.write_text(head + DURABLE_REVOKE)

    cache = root / "src" / "decision_cache.py"
    text = cache.read_text()
    head, marker, _ = text.partition("def invalidate(carrier_id: str) -> None:")
    assert marker
    cache.write_text(head + DURABLE_INVALIDATE + '''

def entries() -> dict:
    return load()
''')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    args = ap.parse_args()
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    root = work / "fixture-hard"
    port = free_port()

    subprocess.run(
        [sys.executable, str(HERE / "make_fixture_authz.py"), "--dest", str(root),
         "--port", str(port), "--variant", "hard"],
        check=True, capture_output=True,
    )
    apply_module_level_fix(root)

    suite = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=root, capture_output=True, text=True,
    )
    check("the repository's own suite is green after the module-level fix",
          suite.returncode == 0, (suite.stderr or "").strip().splitlines()[-1:][0] if suite.stderr else "")

    # 2. module-level verification — everything the builder can actually run
    module_probe = subprocess.run(
        [sys.executable, "-c",
         "import sys;sys.path.insert(0,'src');import authz;"
         "authz.revoke('C-1');"
         "print('MODULE authorize ->', authz.authorize('C-1','L-1'));"
         "print('MODULE status ->', authz.status('C-1'))"],
        cwd=root, capture_output=True, text=True,
        env={**os.environ, "DATA": str(root / "data")},
    )
    module_out = (module_probe.stdout + module_probe.stderr).strip()
    check("module-level verification says the revoked carrier is REFUSED",
          "MODULE authorize -> False" in module_out, module_out.replace("\n", " | "))

    subprocess.run([sys.executable, "scripts/reset.py"], cwd=root, capture_output=True)

    # 3. the running server, same code
    base = f"http://127.0.0.1:{port}"
    with Service(root, port):
        revoked = post(f"{base}/api/carriers/C-1/revoke", {})
        store_after_revoke = probe(root, "C-1")
        leaked = post(f"{base}/api/loads/L-1/rate", {"carrier": "C-1", "rate": 4242}, timeout=20)
        store_after_leak = probe(root, "L-1")

        # 4. sub-second deadline: the effect lands before the acknowledgement
        started = time.time()
        timed_out = False
        try:
            post(f"{base}/api/loads/L-2/rate", {"carrier": "C-2", "rate": 777}, timeout=0.5)
        except Exception:
            timed_out = True
        elapsed = time.time() - started
        time.sleep(2.5)
        store_after_timeout = probe(root, "L-2")

    check("the durable store DOES record the revocation (A and B are closed)",
          "carrier C-1 active=false" in store_after_revoke, store_after_revoke.replace("\n", " | "))
    check("DEFECT C: the RUNNING SERVER still lets the revoked carrier write",
          leaked[0] == 200 and "rate=4242" in store_after_leak,
          f"{leaked} / {store_after_leak}")
    check("a sub-second client deadline times out on the rate write",
          timed_out and elapsed < 1.2, f"timed_out={timed_out} elapsed={elapsed:.2f}s")
    check("timeout_after_effect: the effect landed anyway",
          "rate=777" in store_after_timeout, store_after_timeout.replace("\n", " | "))

    (work / "hard-variant-proof.json").write_text(json.dumps(
        {"results": [{"check": n, "ok": o, "detail": d} for n, o, d in RESULTS],
         "revoke": revoked, "leak": leaked}, indent=2, default=str))
    failed = [n for n, ok, _ in RESULTS if not ok]
    print()
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("FAILED: " + "; ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
