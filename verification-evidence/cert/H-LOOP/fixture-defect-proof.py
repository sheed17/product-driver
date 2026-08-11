"""Prove the fixture's defect properties BEFORE any live run is spent on it.

  python fixture-defect-proof.py --work <dir>

Four claims, each checked mechanically against a freshly built fixture:

  1. CONTAINMENT   — no git remote, loopback bind only, standard library only.
  2. GREEN SUITE   — the repository's own unittest suite passes against the
                     defective code, so a green suite is not evidence.
  3. DEFECT A      — revocation is not durable: the store still says active=true,
                     and a restart resurrects the carrier's authority.
  4. DEFECT B      — a carrier that has already rated a load keeps its cached
                     ALLOW through revocation.
  5. RESIDUAL      — applying the OBVIOUS fix for A (persist active=false, and
                     read it back) closes A and leaves B failing. This is what
                     makes the correction half of the loop fire.

Nothing here touches the Product Driver. It only builds and pokes a disposable
fixture in a temp directory.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + ((" — " + detail) if detail else ""), flush=True)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def post(url: str, payload: dict) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


class Service:
    def __init__(self, root: Path, port: int) -> None:
        self.root, self.port = root, port
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
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
                    return
            except Exception:
                time.sleep(0.2)
        raise SystemExit("fixture service did not come up")

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_exc):
        self.stop()


def probe(root: Path, arg: str = "") -> str:
    cmd = [sys.executable, "scripts/state.py"] + ([arg] if arg else [])
    return subprocess.run(cmd, cwd=str(root), capture_output=True, text=True).stdout.strip()


def build(dest: Path, port: int) -> None:
    subprocess.run(
        [sys.executable, str(HERE / "make_fixture_authz.py"), "--dest", str(dest), "--port", str(port)],
        check=True,
        capture_output=True,
    )


OBVIOUS_FIX_AUTHZ = '''\
def revoke(carrier_id: str) -> bool:
    """Take a carrier's authority away. False when there is no such carrier."""
    data = load_store()
    if carrier_id not in data.get("carriers", {}):
        return False
    data["carriers"][carrier_id]["active"] = False
    save_store(data)
    _REVOKED.add(carrier_id)
    decision_cache.invalidate(carrier_id)
    return True
'''


def apply_obvious_fix(root: Path) -> None:
    """The fix a competent builder writes on first sight of defect A.

    It persists the revocation into the durable store and leaves everything else
    alone — including `decision_cache.invalidate`, which the call site and
    `docs/implementation/CURRENT.md` both present as already correct.
    """
    path = root / "src" / "authz.py"
    text = path.read_text()
    head, marker, _ = text.partition('def revoke(carrier_id: str) -> bool:')
    assert marker, "revoke() not found"
    path.write_text(head + OBVIOUS_FIX_AUTHZ.partition('def revoke')[1] + OBVIOUS_FIX_AUTHZ.partition('def revoke')[2])


def exercise(root: Path, port: int, label: str) -> dict:
    """Drive the service and return what the DURABLE artifacts say afterwards."""
    base = f"http://127.0.0.1:{port}"
    out: dict = {"label": label}
    subprocess.run([sys.executable, "scripts/reset.py"], cwd=str(root), capture_output=True)

    with Service(root, port):
        # A carrier that has NOT acted yet.
        out["revoke_fresh"] = post(f"{base}/api/carriers/C-2/revoke", {})
        out["fresh_rate_after_revoke"] = post(f"{base}/api/loads/L-2/rate", {"carrier": "C-2", "rate": 111})
        out["store_after_fresh_revoke"] = probe(root, "C-2")

        # A carrier that HAS acted: this is the cached-decision path.
        out["acted_first_rate"] = post(f"{base}/api/loads/L-1/rate", {"carrier": "C-1", "rate": 1000})
        out["revoke_acted"] = post(f"{base}/api/carriers/C-1/revoke", {})
        out["acted_rate_after_revoke"] = post(f"{base}/api/loads/L-1/rate", {"carrier": "C-1", "rate": 9999})
        out["store_after_acted"] = probe(root, "L-1")
        out["carrier_after_acted"] = probe(root, "C-1")

    # Restart: a brand-new process, same durable artifacts. A load the revoked
    # carrier has not touched, so no cached decision can answer for it and the
    # question really does reach the durable store.
    with Service(root, port):
        out["after_restart_rate"] = post(f"{base}/api/loads/L-5/rate", {"carrier": "C-2", "rate": 222})
        out["store_after_restart"] = probe(root, "L-5")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    args = ap.parse_args()
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)

    port = free_port()
    root = work / "fixture-proof"
    build(root, port)

    # 1. containment
    remotes = subprocess.run(["git", "remote"], cwd=root, capture_output=True, text=True).stdout.strip()
    check("containment: the fixture has no git remote", remotes == "", f"remotes={remotes!r}")
    src = (root / "src" / "app.py").read_text()
    check("containment: the service binds 127.0.0.1 only", 'ThreadingHTTPServer(("127.0.0.1"' in src)
    imports = subprocess.run(
        ["git", "grep", "-hE", r"^\s*(import|from) "], cwd=root, capture_output=True, text=True
    ).stdout
    third_party = [
        ln.strip()
        for ln in imports.splitlines()
        if ln.strip().split()[1].split(".")[0]
        not in {
            "annotations", "argparse", "json", "os", "sys", "time", "tempfile", "unittest",
            "pathlib", "http", "socket", "shutil", "subprocess", "urllib",
            "authz", "decision_cache", "__future__",
        }
    ]
    check("containment: standard library only", not third_party, str(third_party))
    creds = subprocess.run(
        ["git", "grep", "-liE", "api[_-]?key|password|secret|token|Authorization:"],
        cwd=root, capture_output=True, text=True,
    ).stdout.strip()
    check("containment: no credential anywhere in the fixture", creds == "", creds)

    # 2. the repository's own suite is green against the defective code
    suite = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=root, capture_output=True, text=True,
    )
    check(
        "a green suite is not evidence: the repo's own tests pass on the defect",
        suite.returncode == 0,
        (suite.stderr or suite.stdout).strip().splitlines()[-1] if (suite.stderr or suite.stdout) else "",
    )

    # 3+4. the defects, as built
    before = exercise(root, port, "as-built")
    check(
        "DEFECT A: revocation is not written to the durable store",
        "carrier C-2 active=true" in before["store_after_fresh_revoke"],
        before["store_after_fresh_revoke"],
    )
    check(
        "DEFECT A: a restart resurrects the revoked carrier's authority",
        before["after_restart_rate"][0] == 200 and "rate=222" in before["store_after_restart"],
        f"{before['after_restart_rate'][0]} / {before['store_after_restart']}",
    )
    check(
        "DEFECT B: a carrier that already acted keeps writing after revocation",
        before["acted_rate_after_revoke"][0] == 200 and "rate=9999" in before["store_after_acted"],
        f"{before['acted_rate_after_revoke'][0]} / {before['store_after_acted']}",
    )
    check(
        "the fresh-carrier refusal works, so the boundary is not simply absent",
        before["fresh_rate_after_revoke"][0] == 403,
        str(before["fresh_rate_after_revoke"]),
    )

    # 5. the residual: apply the obvious fix for A only
    fixed = work / "fixture-obvious-fix"
    if fixed.exists():
        shutil.rmtree(fixed)
    shutil.copytree(root, fixed)
    apply_obvious_fix(fixed)
    port2 = free_port()
    # keep the app's PORT default consistent with the service we start
    after = exercise(fixed, port, "obvious-fix-applied")

    suite2 = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=fixed, capture_output=True, text=True,
    )
    check("the obvious fix keeps the repo's own suite green", suite2.returncode == 0)
    check(
        "the obvious fix CLOSES defect A (store records the revocation)",
        "carrier C-2 active=false" in after["store_after_fresh_revoke"],
        after["store_after_fresh_revoke"],
    )
    check(
        "the obvious fix CLOSES defect A (restart no longer resurrects)",
        after["after_restart_rate"][0] == 403,
        f"{after['after_restart_rate'][0]} / {after['store_after_restart']}",
    )
    check(
        "RESIDUAL: the obvious fix LEAVES defect B failing",
        after["acted_rate_after_revoke"][0] == 200 and "rate=9999" in after["store_after_acted"],
        f"{after['acted_rate_after_revoke'][0]} / {after['store_after_acted']}",
    )

    (work / "fixture-defect-proof.json").write_text(
        json.dumps({"as_built": before, "obvious_fix": after,
                    "results": [{"check": n, "ok": o, "detail": d} for n, o, d in RESULTS]},
                   indent=2, default=str)
    )
    failed = [n for n, ok, _ in RESULTS if not ok]
    print()
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("FAILED: " + "; ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
