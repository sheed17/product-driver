# Raw wave-2 populations (pre-validation), every run

## authz_retry-a  —  defect=authz_retry evidence_withheld=False wave1_failures=['gen-authz-refusal'] accepted=5 refused=0

  [P0 safety_invariant] adaptive-authz-no-downstream (ACCEPTED)
    title: Unpermitted approval must write no payment and no ledger entry
    generating_risk: R2 — an unauthorized approval propagates into payment and the downstream ledger
    source_failures: ['gen-authz-refusal'] source_clusters: ['C01']
    purpose: The failure showed mallory's refused approval still produced payments=1 and ledger=1. Prove that a non-permitted actor's approve attempt on a fresh invoice records no approval, no payment, and no ledger entry. Assumes an invoice id is addressable and defaults to pending; state.py is a read-only oracle.
    rationale: An authorization bypass that also moves money and writes the downstream ledger is the most dangerous form of the observed defect.
    - POST /api/invoices/AZN-DS1/approve body={"actor": "mallory"} expect_status=403 expect_contains=['actor is not permitted to approve']
    - STATE `python3 fixture/state.py AZN-DS1` contains=['approvals=0'] not_contains=['mallory', 'approvals=1', 'payments=1', 'ledger=1', 'status=approved']
    ORACLE `python3 fixture/state.py AZN-DS1` contains=['approvals=0'] not_contains=['mallory', 'payments=1', 'ledger=1', 'status=approved']
    EXPECT ['actor is not permitted to approve', 'approvals=0']

  [P0 repeated_request] adaptive-authz-determinism (ACCEPTED)
    title: Repeated identical unpermitted approvals are all refused (no 403-then-200 flip)
    generating_risk: R3 — the authorization decision is non-deterministic (403 on one call, 200 on another)
    source_failures: ['gen-authz-refusal'] source_clusters: ['C01']
    purpose: The evidence shows the same actor/invoice returning 403 once and 200 another time. Issue the same non-permitted approval three times against one fresh invoice and prove every response refuses and the persisted state never records an approval.
    rationale: A non-deterministic authorization decision means a single refusal proves nothing; the gate must refuse every time.
    - POST /api/invoices/AZN-DET1/approve body={"actor": "mallory"} expect_status=403 expect_contains=['actor is not permitted to approve']
    - POST /api/invoices/AZN-DET1/approve body={"actor": "mallory"} expect_status=403 expect_contains=['actor is not permitted to approve']
    - POST /api/invoices/AZN-DET1/approve body={"actor": "mallory"} expect_status=403 expect_contains=['actor is not permitted to approve']
    - STATE `python3 fixture/state.py AZN-DET1` contains=['approvals=0'] not_contains=['mallory', 'approvals=1', 'payments=1', 'status=approved']
    ORACLE `python3 fixture/state.py AZN-DET1` contains=['approvals=0'] not_contains=['mallory', 'payments=1', 'status=approved']
    EXPECT ['actor is not permitted to approve', 'approvals=0']

  [P0 concurrency] adaptive-authz-concurrency (ACCEPTED)
    title: Two simultaneous unpermitted approvals both refused, none recorded
    generating_risk: R1 — the permitted-approver check is not enforced before the effect
    source_failures: ['gen-authz-refusal'] source_clusters: ['C01']
    purpose: Race two identical non-permitted approval requests against one fresh invoice. Prove neither slips past the gate under concurrency and that no approval, payment, or ledger row is written.
    rationale: A gate evaluated after the effect (as the observed payments=1 suggests) is especially likely to leak under concurrent requests.
    - [parallel] POST /api/invoices/AZN-CON1/approve body={"actor": "mallory"} expect_status=403
    - [parallel] POST /api/invoices/AZN-CON1/approve body={"actor": "mallory"} expect_status=403
    - STATE `python3 fixture/state.py AZN-CON1` contains=['approvals=0'] not_contains=['mallory', 'approvals=1', 'payments=1', 'ledger=1', 'status=approved']
    ORACLE `python3 fixture/state.py AZN-CON1` contains=['approvals=0'] not_contains=['mallory', 'payments=1', 'ledger=1', 'status=approved']
    EXPECT ['actor is not permitted to approve']

  [P1 missing_data] adaptive-authz-missing-actor (ACCEPTED)
    title: Approval with missing or empty actor is not default-allowed
    generating_risk: R4 — the approver gate may default-allow when the actor is missing or empty
    source_failures: ['gen-authz-refusal'] source_clusters: ['C01']
    purpose: With the permitted-approver gate demonstrably broken, check the adjacent default-allow case: an approve request carrying no actor field, and one carrying an empty actor string, must both be refused and record nothing on a fresh invoice.
    rationale: A gate that fails open for a named non-permitted actor is likely to also fail open when the actor is absent, recording an approval attributable to no one.
    - POST /api/invoices/AZN-MISS1/approve body={} expect_status=403 expect_contains=['not permitted']
    - POST /api/invoices/AZN-MISS1/approve body={"actor": ""} expect_status=403 expect_contains=['not permitted']
    - STATE `python3 fixture/state.py AZN-MISS1` contains=['approvals=0'] not_contains=['approvals=1', 'payments=1', 'status=approved']
    ORACLE `python3 fixture/state.py AZN-MISS1` contains=['approvals=0'] not_contains=['approvals=1', 'payments=1', 'status=approved']
    EXPECT ['approvals=0']

  [P0 restart_recovery] adaptive-authz-restart (ACCEPTED)
    title: An unpermitted refusal leaves no durable approval after api restart
    generating_risk: R1 — an unauthorized approval may be durably persisted despite the endpoint being expected to refuse
    source_failures: ['gen-authz-refusal'] source_clusters: ['C01']
    purpose: Send a non-permitted approval to a fresh invoice, restart the api service, then re-read persisted state. Prove the refusal left no durable approval and that a bypass did not silently persist to storage across a restart.
    rationale: The observed state (approvers=mallory, ledger=1) was read from persisted storage; a restart re-read is the true test of whether a bypass durably contaminated the store.
    - POST /api/invoices/AZN-RST1/approve body={"actor": "mallory"} expect_status=403 expect_contains=['actor is not permitted to approve']
    - restart_service service=api wait_ms=None command=-
    - wait service=- wait_ms=500 command=-
    - STATE `python3 fixture/state.py AZN-RST1` contains=['approvals=0'] not_contains=['mallory', 'approvals=1', 'payments=1', 'ledger=1', 'status=approved']
    ORACLE `python3 fixture/state.py AZN-RST1` contains=['approvals=0'] not_contains=['mallory', 'approvals=1', 'ledger=1', 'status=approved']
    EXPECT ['approvals=0']

## nonidempotent-a  —  defect=nonidempotent evidence_withheld=False wave1_failures=['gen-idempotent-approve'] accepted=4 refused=0

  [P0 concurrency] gen-concurrent-double-approve (ACCEPTED)
    title: Two simultaneous approvals of one invoice pay exactly once
    generating_risk: A concurrent duplicate approval could double-pay a single invoice.
    source_failures: ['gen-idempotent-approve'] source_clusters: ['C01']
    purpose: Race two identical authorized approvals of a never-before-seen invoice and confirm the durable store records exactly one payment, one approval, one ledger entry. Assumes the api service is already running on 127.0.0.1:9511 and that invoice CON1 does not pre-exist (invoices are created on first touch).
    rationale: The sequential double-pay seen in gen-idempotent-approve almost certainly reproduces under a race, and a race is the realistic way a duplicate approval slips past any naive guard at 3am.
    - [parallel] POST /api/invoices/CON1/approve body={"actor": "alice"} expect_status=200
    - [parallel] POST /api/invoices/CON1/approve body={"actor": "alice"} expect_status=200
    - STATE `python3 fixture/state.py CON1` contains=['CON1 status=approved', 'payments=1', 'approvals=1', 'ledger=1'] not_contains=['payments=2', 'approvals=2', 'alice,alice']
    ORACLE `python3 fixture/state.py CON1` contains=['CON1 status=approved', 'payments=1', 'approvals=1', 'ledger=1'] not_contains=['payments=2', 'approvals=2', 'alice,alice']
    EXPECT ['payments=1']
    FORBID ['payments=2', 'alice,alice']

  [P0 repeated_request] gen-repeat-thrice-approve (ACCEPTED)
    title: Approving the same invoice three times still records one approval
    generating_risk: Repeated approvals grow payments and duplicate the recorded approver without bound.
    source_failures: ['gen-idempotent-approve'] source_clusters: ['C01']
    purpose: Approve a fresh invoice three times as the same authorized actor and confirm the durable store shows exactly one payment, one approval, and a single (non-duplicated) approver — proving the defect grows unbounded, not just to two. Assumes api is running and RPT1 does not pre-exist.
    rationale: gen-idempotent-approve proved two approvals double-pay; the neighbouring question is whether the count is bounded at all and whether the approver identity is duplicated in the durable approval history.
    - POST /api/invoices/RPT1/approve body={"actor": "alice"} expect_status=200 expect_contains=[]
    - POST /api/invoices/RPT1/approve body={"actor": "alice"} expect_status=200 expect_contains=[]
    - POST /api/invoices/RPT1/approve body={"actor": "alice"} expect_status=200 expect_contains=[]
    - STATE `python3 fixture/state.py RPT1` contains=['RPT1 status=approved', 'approvals=1', 'approvers=alice', 'payments=1'] not_contains=['approvals=2', 'approvals=3', 'payments=2', 'payments=3', 'alice,alice']
    ORACLE `python3 fixture/state.py RPT1` contains=['RPT1 status=approved', 'approvals=1', 'approvers=alice', 'payments=1'] not_contains=['approvals=3', 'payments=3', 'alice,alice']
    EXPECT ['approvals=1', 'payments=1']
    FORBID ['payments=3', 'alice,alice']

  [P1 dependency_failure] gen-ledger-no-duplicate (ACCEPTED)
    title: Repeated approval does not append a duplicate ledger entry
    generating_risk: Each repeated approval appends a duplicate downstream ledger entry.
    source_failures: ['gen-idempotent-approve'] source_clusters: ['C01']
    purpose: Approve a fresh invoice twice as an authorized actor and confirm the downstream ledger holds exactly one entry, not one per call. Assumes api is running and LDG1 does not pre-exist.
    rationale: gen-idempotent-approve observed ledger=2 alongside payments=2; the downstream ledger is a distinct acceptance criterion and double-counting there corrupts financial reconciliation independent of the payment count.
    - POST /api/invoices/LDG1/approve body={"actor": "alice"} expect_status=200 expect_contains=[]
    - POST /api/invoices/LDG1/approve body={"actor": "alice"} expect_status=200 expect_contains=[]
    - STATE `python3 fixture/state.py LDG1` contains=['LDG1 status=approved', 'ledger=1'] not_contains=['ledger=2']
    ORACLE `python3 fixture/state.py LDG1` contains=['LDG1 status=approved', 'ledger=1'] not_contains=['ledger=2']
    EXPECT ['ledger=1']
    FORBID ['ledger=2']

  [P0 conflicting_evidence] gen-second-approver-nodup (ACCEPTED)
    title: A second permitted approver on an approved invoice does not pay again
    generating_risk: A second, also-permitted approver re-triggers the payment effect on an already-approved invoice.
    source_failures: ['gen-idempotent-approve'] source_clusters: ['C01']
    purpose: Approve a fresh invoice as alice, then approve the same invoice as bob (also permitted), and confirm the durable store still shows exactly one payment and one recorded approver — an already-approved invoice must be a no-op regardless of who calls next. Assumes api is running and SEC1 does not pre-exist.
    rationale: The non-idempotent effect path has no already-approved guard, so a second legitimate approver is a realistic path to a second payment that the same-actor idempotency test would not catch.
    - POST /api/invoices/SEC1/approve body={"actor": "alice"} expect_status=200 expect_contains=[]
    - POST /api/invoices/SEC1/approve body={"actor": "bob"} expect_status=200 expect_contains=[]
    - STATE `python3 fixture/state.py SEC1` contains=['SEC1 status=approved', 'approvals=1', 'approvers=alice', 'payments=1', 'ledger=1'] not_contains=['payments=2', 'approvals=2', 'alice,bob']
    ORACLE `python3 fixture/state.py SEC1` contains=['SEC1 status=approved', 'approvals=1', 'approvers=alice', 'payments=1'] not_contains=['payments=2', 'alice,bob']
    EXPECT ['payments=1', 'approvers=alice']
    FORBID ['payments=2', 'alice,bob']

## partial_dep-a  —  defect=partial_dep evidence_withheld=False wave1_failures=['gen-ledger-recorded'] accepted=4 refused=0

  [P0 ui_backend_disagreement] adapt-ledger-claim-vs-store (ACCEPTED)
    title: 200 {"ledger":"recorded"} must match a real durable ledger entry
    generating_risk: A downstream ledger write silently fails yet the endpoint reports success, so the caller trusts a record that does not exist.
    source_failures: ['gen-ledger-recorded'] source_clusters: ['C01']
    purpose: Approve a fresh invoice as a permitted approver, confirm the endpoint claims the ledger was recorded, then read the durable store directly. A correct product wrote exactly one ledger entry; the observed defect answers the same success body while the store holds none. Assumes the api service is already running on 127.0.0.1:9501.
    rationale: The failure showed the endpoint reporting ledger=recorded while the durable store held ledger=0 — this binds the response claim to the persisted oracle so the lie is observable.
    - POST /api/invoices/LEDGA/approve body={"actor": "alice"} expect_status=200 expect_contains=['"ledger": "recorded"', 'approved']
    - STATE `python3 fixture/state.py LEDGA` contains=['status=approved', 'ledger=1'] not_contains=['ledger=0']
    ORACLE `python3 fixture/state.py LEDGA` contains=['ledger=1', 'status=approved'] not_contains=['ledger=0']
    EXPECT ['"ledger": "recorded"', 'ledger=1']
    FORBID ['ledger=0']

  [P0 partial_failure] adapt-payment-implies-ledger (ACCEPTED)
    title: A durably-paid invoice must carry a matching ledger entry
    generating_risk: The payment persists while the downstream ledger write is dropped, leaving a paid invoice with no ledger trail.
    source_failures: ['gen-ledger-recorded'] source_clusters: ['C01']
    purpose: Approve a fresh invoice as a permitted approver and read the durable store. The invariant: if a payment is recorded, a ledger entry must accompany it — no paid invoice may exist with an empty ledger. Assumes the api service is running on 127.0.0.1:9501.
    rationale: The failure produced payments=1 ledger=0 in the durable store — money moved with no downstream record; this asserts payment and ledger co-presence as a safety invariant.
    - POST /api/invoices/LEDGB/approve body={"actor": "bob"} expect_status=200 expect_contains=['approved']
    - STATE `python3 fixture/state.py LEDGB` contains=['payments=1', 'ledger=1'] not_contains=['ledger=0']
    ORACLE `python3 fixture/state.py LEDGB` contains=['payments=1', 'ledger=1'] not_contains=['ledger=0']
    EXPECT ['payments=1', 'ledger=1']
    FORBID ['ledger=0']

  [P1 idempotency] adapt-ledger-idempotent-once (ACCEPTED)
    title: Repeated approval records the ledger exactly once
    generating_risk: The downstream ledger write is unreliable, so repeated approval may drop the entry or duplicate it rather than land exactly once.
    source_failures: ['gen-ledger-recorded'] source_clusters: ['C01']
    purpose: Approve the same invoice twice as a permitted approver, then read the durable store. A correct product records one payment and exactly one ledger entry regardless of repeats — never zero, never two. Assumes the api service is running on 127.0.0.1:9501.
    rationale: The revealed unreliability of the ledger write means a repeat could leave the ledger at zero (dropped) or two (double-recorded); this pins it to exactly one alongside a single payment.
    - POST /api/invoices/LEDGC/approve body={"actor": "alice"} expect_status=200 expect_contains=['approved']
    - POST /api/invoices/LEDGC/approve body={"actor": "alice"} expect_status=200 expect_contains=['approved']
    - STATE `python3 fixture/state.py LEDGC` contains=['payments=1', 'ledger=1'] not_contains=['ledger=0', 'ledger=2', 'payments=2']
    ORACLE `python3 fixture/state.py LEDGC` contains=['ledger=1', 'payments=1'] not_contains=['ledger=0', 'ledger=2', 'payments=2']
    EXPECT ['ledger=1', 'payments=1']
    FORBID ['ledger=0', 'ledger=2', 'payments=2']

  [P1 persistence_failure] adapt-ledger-survives-reread (ACCEPTED)
    title: A recorded ledger entry survives a re-read of the durable store
    generating_risk: A ledger entry that looks written may not have been committed to the durable store, so a re-read reveals it absent.
    source_failures: ['gen-ledger-recorded'] source_clusters: ['C01']
    purpose: Approve a fresh invoice as a permitted approver, then read the durable store twice (with a pause between) to confirm the ledger entry is genuinely persisted and not a transient in-memory artifact. Assumes the api service is running on 127.0.0.1:9501.
    rationale: The failure read the durable store and found ledger=0; this checks that a ledger entry, once claimed recorded, is still present on an independent re-read of persisted state.
    - POST /api/invoices/LEDGD/approve body={"actor": "alice"} expect_status=200 expect_contains=['"ledger": "recorded"', 'approved']
    - STATE `python3 fixture/state.py LEDGD` contains=['ledger=1'] not_contains=['ledger=0']
    - wait service=- wait_ms=500 command=-
    - STATE `python3 fixture/state.py LEDGD` contains=['status=approved', 'ledger=1'] not_contains=['ledger=0']
    ORACLE `python3 fixture/state.py LEDGD` contains=['ledger=1', 'status=approved'] not_contains=['ledger=0']
    EXPECT ['ledger=1']
    FORBID ['ledger=0']

## ui_lies-a  —  defect=ui_lies evidence_withheld=False wave1_failures=['permanent:approval-smoke', 'gen-idempotent-approve', 'gen-persist-approve', 'gen-ledger-recorded'] accepted=5 refused=0

  [P0 restart_recovery] w2-restart-durable (ACCEPTED)
    title: Approval survives an api restart
    generating_risk: restart_recovery — the approval that claimed persisted:true does not survive a process restart
    source_failures: ['gen-persist-approve'] source_clusters: ['C03']
    purpose: After approving invoice W2R, restart the api service and re-read persisted state; a durable approval must still show status=approved and ledger=1, not revert to pending. Assumes the api service is running and W2R starts pending (state.py W2R shows this).
    rationale: Wave 1 showed 'persisted:true' was a lie; the sharpest proof of non-durability is that the state is gone after a restart, directly probing AC-APPROVAL-002.
    - STATE `python3 fixture/state.py W2R` contains=['status=pending'] not_contains=None
    - POST /api/invoices/W2R/approve body=null expect_status=200 expect_contains=[]
    - restart_service service=api wait_ms=None command=-
    - wait service=- wait_ms=1500 command=-
    - STATE `python3 fixture/state.py W2R` contains=['status=approved'] not_contains=['status=pending']
    ORACLE `python3 fixture/state.py W2R` contains=['status=approved', 'ledger=1'] not_contains=['status=pending', 'ledger=0']
    EXPECT ['status=approved']
    FORBID ['status=pending status']

  [P0 concurrency] w2-concurrent-approve (ACCEPTED)
    title: Two simultaneous approvals pay and ledger exactly once
    generating_risk: concurrency — two simultaneous approvals could double-write payments/ledger
    source_failures: ['gen-idempotent-approve'] source_clusters: ['C02']
    purpose: Fire two approve requests for W2C at the same instant; the durable store must record approvals=1, payments=1, ledger=1 — never doubled. Assumes W2C starts pending.
    rationale: Idempotency was tested sequentially and never even changed state; a true race is the untested neighbour now that we know the write path is suspect.
    - STATE `python3 fixture/state.py W2C` contains=['status=pending', 'payments=0'] not_contains=None
    - [parallel] POST /api/invoices/W2C/approve body=null expect_status=200
    - [parallel] POST /api/invoices/W2C/approve body=null expect_status=200
    ORACLE `python3 fixture/state.py W2C` contains=['status=approved', 'approvals=1', 'payments=1', 'ledger=1'] not_contains=['payments=2', 'approvals=2', 'ledger=2']
    EXPECT ['payments=1']
    FORBID ['payments=2']

  [P0 partial_failure] w2-atomic-write (ACCEPTED)
    title: Approval, payment and ledger commit together
    generating_risk: partial_failure — status may advance while the paired payment/ledger writes do not commit
    source_failures: ['gen-persist-approve', 'gen-ledger-recorded'] source_clusters: ['C03', 'C04']
    purpose: Approve W2F once; the response asserts persisted+recorded, so the durable store must show status=approved, approvals=1, payments=1 AND ledger=1 together — no torn write where status flips but payments/ledger stay 0. Assumes W2F starts pending.
    rationale: Wave 1 showed the response claims all three effects while state showed none; the neighbouring risk is a partial commit where some fields advance and others don't.
    - STATE `python3 fixture/state.py W2F` contains=['status=pending', 'ledger=0', 'payments=0'] not_contains=None
    - POST /api/invoices/W2F/approve body=null expect_status=200 expect_contains=['approved']
    ORACLE `python3 fixture/state.py W2F` contains=['status=approved', 'approvals=1', 'payments=1', 'ledger=1'] not_contains=['status=pending', 'ledger=0', 'payments=0']
    EXPECT ['ledger=1', 'payments=1']
    FORBID ['ledger=0']

  [P1 missing_data] w2-approver-identity (ACCEPTED)
    title: A successful approval records who approved
    generating_risk: missing_data — the approver's identity is not durably recorded on approval
    source_failures: ['permanent:approval-smoke'] source_clusters: ['C01']
    purpose: Approve W2P; a durable approval must record the approver identity (approvers must not remain '-'). Assumes W2P starts pending and the caller is a permitted approver as in the happy path.
    rationale: Smoke output showed approvers=- even after a 'successful' approve, so the permitted-approver identity that AC-APPROVAL-003 turns on is not being persisted.
    - STATE `python3 fixture/state.py W2P` contains=['approvers=-'] not_contains=None
    - POST /api/invoices/W2P/approve body=null expect_status=200 expect_contains=[]
    ORACLE `python3 fixture/state.py W2P` contains=['status=approved', 'approvals=1'] not_contains=['approvers=-']
    EXPECT ['status=approved']

  [P2 unexpected_state_transition] w2-missing-invoice (ACCEPTED)
    title: Approving an unknown invoice creates no phantom approval
    generating_risk: unexpected_state_transition — the endpoint may claim approval for a non-existent invoice
    source_failures: ['permanent:approval-smoke'] source_clusters: ['C01']
    purpose: POST approve for an invoice id that does not exist (W2NOPE); the durable store must not report it as approved and must not create a payment/ledger entry. Probes that state.py reflects reality rather than the response body.
    rationale: Since the endpoint answered 'approved/persisted' regardless of what was persisted in wave 1, it may answer the same for an invoice that was never a real approvable record.
    - POST /api/invoices/W2NOPE/approve body=null expect_status=None expect_contains=[]
    ORACLE `python3 fixture/state.py W2NOPE` contains=None not_contains=['status=approved', 'payments=1', 'ledger=1']
    FORBID ['status=approved payments=1']
