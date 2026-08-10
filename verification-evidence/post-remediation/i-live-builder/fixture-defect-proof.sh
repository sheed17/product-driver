#!/bin/zsh
# Prove what the fixture's defects are, before the driver ever sees it.
#
#   ./fixture-defect-proof.sh <driver-root> <work-dir> <port>
#
# Three claims, each checked against the running service:
#
#   1. the repository's own test suite is GREEN against the defective code
#      — so a builder that only runs the tests learns nothing;
#   2. approving the same invoice twice pays twice
#      — the defect a first-pass fix is expected to close;
#   3. after the obvious first-pass fix, two SIMULTANEOUS approvals still pay
#      twice — the defect that survives it, and the one the driver's generated
#      concurrency coverage has to find.
#
# Claim 3 is why the fixture is shaped this way. A defect a competent builder
# closes on sight never exercises the failure -> correction -> remediation half
# of the loop, which is the half this proof exists to demonstrate.
set -e

DRIVER="${1:?driver root}"
WORK="${2:?work dir}"
PORT="${3:-50141}"
PY="${PYBIN:-$DRIVER/.venv/bin/python}"
HERE="$(cd "$(dirname "$0")" && pwd)"

rm -rf "$WORK"
"$PY" "$HERE/make_fixture.py" --dest "$WORK" --port "$PORT" >/dev/null
cd "$WORK"

echo "=== 1. the repository's own tests, against the DEFECTIVE code ==="
python3 -m unittest discover -s tests 2>&1 | tail -3

echo
echo "=== 2. sequential repeat approval (the visible defect) ==="
(PORT=$PORT python3 src/app.py &) ; sleep 1.5
curl -s -XPOST -d '{"actor":"a"}' "http://127.0.0.1:$PORT/api/invoices/INV-1/approve" >/dev/null
curl -s -XPOST -d '{"actor":"a"}' "http://127.0.0.1:$PORT/api/invoices/INV-1/approve" >/dev/null
python3 scripts/state.py INV-1
pkill -f "$WORK/src/app.py" || true
# Wait for the port to actually free. Reusing it before the old process is gone
# means phase 3 silently reads the UNFIXED server, which reads as "the fix did
# not work" — a false negative in the one place this script exists to be right.
for _ in {1..40}; do
  lsof -ti ":$PORT" >/dev/null 2>&1 || break
  sleep 0.25
done
PORT=$((PORT + 1))

echo
echo "=== 3. after the obvious first-pass fix (port $PORT) ==="
python3 - "$WORK/src/app.py" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); s = p.read_text()
s = s.replace(
    '    record = data[invoice_id]\n    record["status"] = "approved"',
    '    record = data[invoice_id]\n    if record["status"] == "approved":\n        return\n    record["status"] = "approved"',
)
p.write_text(s)
print("applied: guard on record['status'] inside _approve")
PY
rm -f data/store.json data/payments.log
(PORT=$PORT python3 src/app.py &) ; sleep 1.5
echo "  sequential repeat, expect payments=1:"
curl -s -XPOST -d '{"actor":"a"}' "http://127.0.0.1:$PORT/api/invoices/INV-1/approve" >/dev/null
curl -s -XPOST -d '{"actor":"a"}' "http://127.0.0.1:$PORT/api/invoices/INV-1/approve" >/dev/null
echo -n "    " ; python3 scripts/state.py INV-1
echo "  CONCURRENT, expect payments=1 and the defect gives 2:"
curl -s -XPOST -d '{"actor":"x"}' "http://127.0.0.1:$PORT/api/invoices/INV-2/approve" >/dev/null &
curl -s -XPOST -d '{"actor":"y"}' "http://127.0.0.1:$PORT/api/invoices/INV-2/approve" >/dev/null &
wait
echo -n "    " ; python3 scripts/state.py INV-2
pkill -f "$WORK/src/app.py" || true
