"""A generated importer scan may not demand "no importers" where the repository authorizes one.

Run 20260925-043237 is the case this file exists for. Its coverage wave
admitted two scenarios that scanned the product package for importers of a
dark machine and expected an empty list (and forbade a non-empty one). The
repository's own guard had long since fixed that importer set to exactly one
authorized admission layer — "wired but dark" — so the scenarios could only
pass against a product that removed wiring its authority requires, and the
builder was asked for that regression.

Here a tiny real product in a real git repository carries its own import
guard. Nothing in this file names the product that exposed the defect:
Product Driver must read what "dark" means for a module from the repository
it is judging. Probes that pass validation are executed through the real
executor to show a genuine dark-shipping regression is still caught.
"""

from __future__ import annotations

import asyncio
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from neyma_product_driver.config import ScenarioGenerationConfig, ScenarioRunConfig
from neyma_product_driver.evidence import EvidenceStore
from neyma_product_driver.outcome_contract import RepositoryText, contract_problems
from neyma_product_driver.scenario_plan import REJECTED_INVOCATION, compile_to_scenario
from neyma_product_driver.scenario_planner import ScenarioPlanner
from neyma_product_driver.scenario_validation import ApprovedCommands, validate_scenario
from neyma_product_driver.scenarios import Scenario, ScenarioExecutor

from scenario_fixtures import FakeFounder, FakeUnit, ScriptedReasoner, raw_payload, raw_scenario, validation_context

PY = shlex.quote(sys.executable)

# --------------------------------------------------------------------------
# The product: one dark machine, one authorized admission layer, one channel
# --------------------------------------------------------------------------

PRODUCT = {
    "product/__init__.py": "",
    "product/ledger.py": "class Ledger:\n    pass\n",
    "product/ledger_admission.py": "from product.ledger import Ledger\n\nADMITTED = Ledger\n",
    "product/outbound.py": "import socket\n\nCHANNEL = socket.AF_INET\n",
    "product/adapters/__init__.py": "",
    "product/adapters/mail.py": "import socket\n",
}
#: A genuine dark-shipping regression: a second, unauthorized importer.
ROGUE = {"product/workflow.py": "from product.ledger import Ledger\n\nLIVE = Ledger()\n"}

#: Exact set equality, both ways: one authorized importer, and it must be present.
AUTHORIZED_GUARD = '''
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_ledger_has_exactly_its_authorized_importer():
    PERMITTED = {"ledger_admission.py"}
    observed = set()
    for py in (ROOT / "product").rglob("*.py"):
        if py.name == "ledger.py":
            continue
        for node in ast.walk(ast.parse(py.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[-1] == "ledger":
                observed.add(py.name)
    assert not (observed - PERMITTED), "a second importer is a second authority"
    assert not (PERMITTED - observed), "the authorized integration vanished"
'''

#: The same boundary, when the repository says nothing may import the machine.
ZERO_GUARD = '''
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_ledger_ships_dark_with_no_importer():
    observed = set()
    for py in (ROOT / "product").rglob("*.py"):
        if py.name == "ledger.py":
            continue
        for node in ast.walk(ast.parse(py.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[-1] == "ledger":
                observed.add(py.name)
    assert sorted(observed) == []
'''

#: What ONE file imports — the other direction. Never an importer boundary.
DIRECTION_GUARD = '''
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_outbound_does_not_import_the_ledger():
    tree = ast.parse((ROOT / "product" / "outbound.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[-1] != "ledger"
'''


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def product_repo(root: Path, *, guards=("authorized",), zero_wired: bool = False, rogue: bool = False) -> Path:
    name = "-".join(guards) or "unguarded"
    repo = root / f"product-{name}{'-zero' if zero_wired else ''}{'-rogue' if rogue else ''}"
    files = dict(PRODUCT)
    if zero_wired:
        files.pop("product/ledger_admission.py")
    if rogue:
        files.update(ROGUE)
    bodies = {"authorized": AUTHORIZED_GUARD, "zero": ZERO_GUARD, "direction": DIRECTION_GUARD}
    for guard in guards:
        files[f"tests/test_{guard}_boundary.py"] = bodies[guard]
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


# --------------------------------------------------------------------------
# Probes, written the way run 20260925-043237's generator wrote them
# --------------------------------------------------------------------------

PRELUDE = (
    "import ast,pathlib; open('probe-ran', 'a').write('x'); "
    "src=pathlib.Path({root!r}); "
    "trees=[(py, ast.parse(py.read_text())) for py in sorted(src.rglob('*.py'))]; "
    "own=lambda t: set([n.module.split('.')[-1] for n in ast.walk(t) if isinstance(n, ast.ImportFrom) and n.module] "
    "+ [a.name.split('.')[-1] for n in ast.walk(t) if isinstance(n, ast.Import) for a in n.names]); "
    "print('modules scanned:', len(trees)); "
)
SCAN = "sorted(p.name for p, t in trees if p.name != 'ledger.py' and 'ledger' in own(t))"


def probe(body: str, root: str = "product") -> str:
    return f"{PY} -c {shlex.quote(PRELUDE.format(root=root) + body)}"


#: The defect: every importer of the machine, expected to be none.
ZERO = probe(f"print('production importers of ledger:', {SCAN})")
#: The same scan bound to a name first, as the second defective scenario did.
BOUND = probe(f"importers={SCAN}; print('shipped importers of ledger:', importers)")
#: The same scan counted, and in an f-string. Double-quoted, because SCAN holds
#: single-quoted literals and Python 3.11 cannot reuse the enclosing quote.
COUNTED = probe(f'print(f"importer count: {{len({SCAN})}}")')
#: The same scan under a label that says nothing about importers.
NEUTRAL = probe(f"print('value:', {SCAN})")
#: A real dark-shipping guard: importers OUTSIDE the authorized set.
UNAUTHORIZED = probe(
    "print('unauthorized importers of ledger:', sorted(set("
    "p.name for p, t in trees if p.name != 'ledger.py' and 'ledger' in own(t)) - {'ledger_admission.py'}))"
)
#: Another: no channel-capable module reaches the machine (live outbound reach).
CHANNEL = probe(
    "chan=set(p.name for p, t in trees if 'socket' in own(t)); "
    "print('channel-capable importers of ledger:', sorted(p.name for p, t in trees if p.name in chan and 'ledger' in own(t)))"
)
#: A scan over a population the authorized importer is not in.
ADAPTERS = probe(f"print('adapter importers of ledger:', {SCAN})", root="product/adapters")

COMMANDS = [ZERO, BOUND, COUNTED, NEUTRAL, UNAUTHORIZED, CHANNEL, ADAPTERS]


def harness() -> Scenario:
    return Scenario(
        name="ledger_base",
        mode="backend",
        expect_state=[
            {"name": f"probe {i}", "command": command, "contains": []} for i, command in enumerate(COMMANDS)
        ],
    )


def scenario(scenario_id: str, command: str, contains, *, forbidden=(), **kw) -> dict:
    act = {
        "kind": "command",
        "name": "scan the dark machine's importers",
        "command": command,
        "expect_outcome": "permitted",
        "expect_exit_code": 0,
        "expect_contains": list(contains),
    }
    return raw_scenario(
        scenario_id,
        risk_category="authorization",
        actions=[act],
        state_checks=[],
        expected_observations=list(contains),
        forbidden_observations=list(forbidden),
        service_refs=[],
        cleanup=[],
        isolation_note="reads source only",
        generating_risk="the dark machine could acquire a live importer",
        **kw,
    )


def zero_shape(scenario_id: str = "ships-dark") -> dict:
    """What run 20260925-043237 stored: expects none, forbids any."""
    return scenario(
        scenario_id,
        ZERO,
        ["production importers of ledger: []"],
        forbidden=["production importers of ledger: ['"],
    )


def as_model(raw: dict):
    from neyma_product_driver.scenario_generator import parse_scenarios
    from neyma_product_driver.scenario_plan import ScenarioProvenance

    provenance = ScenarioProvenance(
        task_hash="t", stage="initial", wave=1, model="opus", session_id="s",
        generating_risk="the dark machine could acquire a live importer",
    )
    parsed, malformed = parse_scenarios(raw_payload(raw), provenance=provenance)
    assert not malformed, malformed
    return parsed[0]


def problems(repo: Path, raw: dict) -> list[str]:
    return contract_problems(as_model(raw), RepositoryText(repo))


def reasons(repo: Path, raw: dict) -> list[str]:
    context = validation_context(
        approved_commands=ApprovedCommands.from_sources(scenarios=[harness()]),
        established_observations={},
        declared_services=set(),
        repository_text=RepositoryText(repo),
    )
    return validate_scenario(as_model(raw), context)


def execute(repo: Path, raw: dict, artifacts: Path):
    model = as_model(raw)
    compiled = compile_to_scenario(model, approved_commands=set(model.command_strings()))
    executor = ScenarioExecutor(
        repo=repo, run_config=ScenarioRunConfig(command_timeout_s=60), artifact_dir=artifacts
    )
    return asyncio.run(executor.execute(compiled))


def _risks() -> list[dict]:
    return [
        {
            "id": "R1",
            "description": "the dark machine could acquire a live importer",
            "risk_category": "authorization",
            "severity": "P0",
        }
    ]


def _planner(tmp_path: Path, repo: Path, payloads, *, run_id: str = "r1"):
    return ScenarioPlanner(
        repo=repo,
        config=ScenarioGenerationConfig(enabled=True, max_waves=3),
        reasoner=ScriptedReasoner(list(payloads)),
        store=EvidenceStore(tmp_path / "runs", run_id),
        base_scenario=harness(),
        permanent_scenarios=[harness()],
        founder=FakeFounder(),
        contract_probe=lambda _c: pytest.fail("no contract probe is needed here"),
    )


def _boundary(found: list[str]) -> list[str]:
    return [p for p in found if "authorizes exactly" in p]


# ==========================================================================
# a — "zero importers" against an authorized, wired-but-dark boundary
# ==========================================================================


class TestAZeroImportersContradictsAnAuthorizedImporter:
    def test_the_run_shape_is_refused_before_it_runs(self, tmp_path):
        repo = product_repo(tmp_path)
        found = _boundary(problems(repo, zero_shape()))
        # One for the expected emptiness, one for the forbidden non-empty rendering.
        assert len(found) == 2, found
        assert all("['ledger_admission']" in p for p in found), found
        assert all("tests/test_authorized_boundary.py:" in p for p in found), found
        assert all("product/ledger_admission.py" in p for p in found), found
        assert not (repo / "probe-ran").exists(), "validation must not execute the product"

    def test_validation_reports_it_as_a_harness_defect(self, tmp_path):
        repo = product_repo(tmp_path)
        found = [r for r in reasons(repo, zero_shape()) if "authorizes exactly" in r]
        assert found and all("not a product failure" in r for r in found), found

    @pytest.mark.parametrize(
        ("command", "contains", "forbidden"),
        [
            (ZERO, ["production importers of ledger: []"], []),
            (ZERO, [], ["production importers of ledger: ['"]),
            (ZERO, [], ["production importers of ledger: ['ledger_admission.py']"]),
            (BOUND, ["shipped importers of ledger: []"], []),
            (COUNTED, ["importer count: 0"], []),
        ],
        ids=["expects-empty", "forbids-any", "forbids-the-authorized-one", "bound-to-a-name", "counted-f-string"],
    )
    def test_every_spelling_of_no_importer_is_refused(self, tmp_path, command, contains, forbidden):
        repo = product_repo(tmp_path)
        raw = scenario("S", command, contains, forbidden=forbidden)
        assert len(_boundary(problems(repo, raw))) == 1

    def test_the_planner_refuses_it_and_keeps_the_risk(self, tmp_path):
        repo = product_repo(tmp_path)
        planner = _planner(tmp_path, repo, [raw_payload(zero_shape(), risks=_risks())])
        planner.plan_initial(task="prove the ledger ships dark", unit=FakeUnit(), run_id="r1")
        assert planner.plan.scenarios == []
        assert planner.compiled == {}
        rejected = planner.plan.waves[-1].rejected
        assert rejected and rejected[0].kind == REJECTED_INVOCATION
        assert any("authorizes exactly" in r for r in rejected[0].reasons), rejected[0].reasons
        assert any(r.risk_category.value == "authorization" for r in planner.plan.risks)
        assert not (repo / "probe-ran").exists()

    def test_an_admitted_one_is_held_on_resume_never_re_run(self, tmp_path):
        """Admitted while the repository stated no boundary; the boundary is then committed."""
        repo = product_repo(tmp_path, guards=())
        planner = _planner(tmp_path, repo, [raw_payload(zero_shape(), risks=_risks())])
        planner.plan_initial(task="prove the ledger ships dark", unit=FakeUnit(), run_id="r1")
        assert "ships-dark" in planner.compiled
        (repo / "tests").mkdir()
        (repo / "tests" / "test_authorized_boundary.py").write_text(AUTHORIZED_GUARD)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "the admission layer is authorized")
        resumed = _planner(tmp_path, repo, [])
        resumed.restore_from_store()
        assert resumed.held_scenario_ids == ["ships-dark"]
        assert "ships-dark" not in resumed.compiled
        hold = resumed.plan.by_id("ships-dark").contract_hold
        assert "not a product failure" in hold and "authorizes exactly" in hold
        assert not (repo / "probe-ran").exists()

    def test_the_old_expectation_really_does_fail_the_correct_product(self, tmp_path):
        """Why this must be refused: executed, it blames the product for its authorized wiring."""
        repo = product_repo(tmp_path)
        result = execute(repo, zero_shape(), tmp_path / "a")
        assert not result.passed
        assert "production importers of ledger: ['ledger_admission.py']" in result.commands[0].stdout


# ==========================================================================
# b — absence of an UNAUTHORIZED importer, or of live outbound reach, stays valid
# ==========================================================================


class TestBRealDarkShippingGuardsStayValid:
    def test_no_unauthorized_importer_is_lawful(self, tmp_path):
        repo = product_repo(tmp_path)
        raw = scenario("S-unauth", UNAUTHORIZED, ["unauthorized importers of ledger: []"])
        assert reasons(repo, raw) == []

    def test_no_channel_capable_importer_is_lawful(self, tmp_path):
        repo = product_repo(tmp_path)
        raw = scenario("S-chan", CHANNEL, ["channel-capable importers of ledger: []"])
        assert reasons(repo, raw) == []

    def test_exactly_the_authorized_importer_is_lawful(self, tmp_path):
        repo = product_repo(tmp_path)
        raw = scenario("S-exact", ZERO, ["production importers of ledger: ['ledger_admission.py']"])
        assert reasons(repo, raw) == []

    def test_a_population_the_authorized_importer_is_not_in_is_lawful(self, tmp_path):
        repo = product_repo(tmp_path)
        raw = scenario("S-adapters", ADAPTERS, ["adapter importers of ledger: []"])
        assert reasons(repo, raw) == []

    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            (UNAUTHORIZED, "unauthorized importers of ledger: []"),
            (ZERO, "production importers of ledger: ['ledger_admission.py']"),
        ],
        ids=["unauthorized-absent", "exactly-authorized"],
    )
    def test_they_pass_the_correct_product_and_catch_a_second_importer(self, tmp_path, command, expected):
        raw = scenario("S", command, [expected])
        assert execute(product_repo(tmp_path), raw, tmp_path / "ok").passed
        rogue = execute(product_repo(tmp_path, rogue=True), raw, tmp_path / "rogue")
        assert not rogue.passed
        assert "workflow.py" in rogue.commands[0].stdout

    def test_a_channel_capable_importer_is_caught(self, tmp_path):
        repo = product_repo(tmp_path)
        (repo / "product" / "outbound.py").write_text("import socket\nfrom product.ledger import Ledger\n")
        _git(repo, "commit", "-qam", "a live channel reaches the dark machine")
        raw = scenario("S-chan", CHANNEL, ["channel-capable importers of ledger: []"])
        result = execute(repo, raw, tmp_path / "a")
        assert not result.passed
        assert "outbound.py" in result.commands[0].stdout


# ==========================================================================
# c — where the repository genuinely requires zero importers, zero stays valid
# ==========================================================================


class TestCAuthorityThatRequiresZero:
    def test_zero_importers_is_lawful_and_discriminates(self, tmp_path):
        repo = product_repo(tmp_path, guards=("zero",), zero_wired=True)
        assert reasons(repo, zero_shape()) == []
        assert execute(repo, zero_shape(), tmp_path / "ok").passed
        wired = product_repo(tmp_path, guards=("zero",))
        assert not execute(wired, zero_shape(), tmp_path / "wired").passed

    def test_without_any_boundary_the_expectation_stands_as_written(self, tmp_path):
        """Fail closed: an unknown boundary is never reinterpreted as a permitted importer."""
        repo = product_repo(tmp_path, guards=())
        assert reasons(repo, zero_shape()) == []

    def test_guards_that_disagree_establish_nothing(self, tmp_path):
        repo = product_repo(tmp_path, guards=("authorized", "zero"))
        assert _boundary(problems(repo, zero_shape())) == []

    def test_a_guard_on_what_one_file_imports_is_not_an_importer_boundary(self, tmp_path):
        repo = product_repo(tmp_path, guards=("direction",))
        assert RepositoryText(repo).import_boundaries().boundaries == {}
        assert _boundary(problems(repo, zero_shape())) == []


# ==========================================================================
# d — words neither broaden nor weaken the rule
# ==========================================================================


class TestDWordsDoNotDecide:
    def test_a_label_that_never_says_importers_is_still_read_by_what_it_computes(self, tmp_path):
        repo = product_repo(tmp_path)
        assert len(_boundary(problems(repo, scenario("S", NEUTRAL, ["value: []"])))) == 1

    def test_a_label_that_says_importers_over_a_narrower_scan_is_not_refused(self, tmp_path):
        repo = product_repo(tmp_path)
        assert reasons(repo, scenario("S", CHANNEL, ["channel-capable importers of ledger: []"])) == []

    def test_requirement_prose_demanding_zero_does_not_make_a_valid_guard_invalid(self, tmp_path):
        repo = product_repo(tmp_path)
        raw = scenario(
            "S",
            UNAUTHORIZED,
            ["unauthorized importers of ledger: []"],
            rationale="ships dark means ZERO production importers of the ledger",
            purpose="no production importers of ledger may exist",
        )
        assert reasons(repo, raw) == []

    def test_prose_claiming_authorization_does_not_excuse_zero_without_a_guard(self, tmp_path):
        repo = product_repo(tmp_path, guards=())
        raw = zero_shape()
        raw["rationale"] = "the admission layer is the authorized importer; wired but dark"
        assert _boundary(problems(repo, raw)) == []

    def test_an_expectation_on_another_line_of_the_same_probe_is_untouched(self, tmp_path):
        repo = product_repo(tmp_path)
        raw = scenario("S", ZERO, ["modules scanned: 7", "production importers of ledger: ['ledger_admission.py']"])
        assert _boundary(problems(repo, raw)) == []


def test_a_boundary_committed_later_is_read_by_the_same_repository_reader(tmp_path):
    repo = product_repo(tmp_path, guards=())
    text = RepositoryText(repo)
    assert _boundary(contract_problems(as_model(zero_shape()), text)) == []
    (repo / "tests").mkdir()
    (repo / "tests" / "test_authorized_boundary.py").write_text(AUTHORIZED_GUARD)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "authorize the admission layer")
    text.refresh()
    assert len(_boundary(contract_problems(as_model(zero_shape()), text))) == 2
    index = text.import_boundaries()
    text.refresh()
    assert text.import_boundaries() is index
