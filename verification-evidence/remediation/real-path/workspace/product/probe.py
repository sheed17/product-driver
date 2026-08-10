import json, os, sys

state = os.environ["APPROVAL_STATE"]
payments = []
if os.path.exists(state):
    with open(state) as fh:
        payments = json.load(fh).get("payments", [])
print("payments=%d" % len(payments))
print("invoices=%s" % ",".join(payments))
