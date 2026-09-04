"""What a generated command is allowed to be: the string a human actually wrote.

Run ``20260830-034455`` verified P6/M9 against a strong body of evidence and had
exactly one failure, and the failure was Product Driver's. The generated scenario
``m9-w2-01`` exited 1 without reaching Neyma at all::

    IndentationError: expected an indented block after 'try' statement on line 4

The command it ran embedded a Python body through ``exec()``. The body a human
wrote into ``scenarios/p6_m9_exception.yaml`` indents the ``try`` block by two
spaces; the command Product Driver put in front of the generator indented it by
one. Nothing repaired the string and nothing corrupted it deliberately — the
generator was shown a *key* where it needed *text*.

:class:`ApprovedCommands` collapses whitespace to compare invocations, which is
right: two spellings of one command are one command. It then kept only the
collapsed form, and :class:`~neyma_product_driver.scenario_planner.ScenarioPlanner`
rendered those collapsed forms into the generation brief as the literal list of
"APPROVED COMMANDS" a proposal must copy. Collapsing cannot see quoting, so a run
of spaces that is *program syntax* inside a quoted argument was flattened along
with the spaces that merely separate arguments. The model copied faithfully. The
copy did not parse.

The requirement pinned here is general and says nothing about M9, about Python,
or about ``exec``: **whatever Product Driver shows to something that will copy a
command must be byte-identical to what a human wrote.** Matching may normalize;
presentation may not.

Every test here is offline. Nothing consumes Claude usage.
"""

from __future__ import annotations

import ast
import shlex
from pathlib import Path

import pytest

from neyma_product_driver.config import ScenarioGenerationConfig
from neyma_product_driver.scenario_generator import GenerationBasis, GenerationBrief
from neyma_product_driver.scenario_planner import ScenarioPlanner
from neyma_product_driver.scenario_validation import ApprovedCommands
from neyma_product_driver.scenarios import Scenario, load_scenario

from scenario_fixtures import FakeFounder, ScriptedReasoner, base_scenario

DRIVER_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------
# Commands whose meaning lives in their whitespace
# --------------------------------------------------------------------------


def _cmd(body: str) -> str:
    """A ``python -c`` invocation that hands ``body`` to ``exec`` at runtime.

    This is the shape the M9 scenario uses, and the shape the defect is
    invisible in: the outer payload parses no matter how the inner body is
    indented, because to the outer parser the body is just a string literal.
    Nothing notices until the process runs.
    """
    return f'.venv/bin/python -c "import sys; exec({body!r})"'


#: One case per kind of block whose indentation is load-bearing. Each body is
#: valid Python as written and becomes invalid the moment a nesting level is
#: flattened, so "it still parses" is a real assertion and not a tautology.
EMBEDDED_BODIES = {
    "nested-if": "def f(x):\n if x:\n  return 1\n return 0\n",
    "try-except": (
        "def f():\n try:\n  return 1 / 0\n"
        " except ZeroDivisionError as exc:\n  return exc\n"
    ),
    "try-finally": (
        "def f(log):\n try:\n  log.append(1)\n finally:\n  log.append(2)\n"
    ),
    "for-in-nested": (
        "def f(rows, out):\n for row in rows:\n  if row:\n   out.append(row)\n"
        " return out\n"
    ),
    "class-method": "class C:\n def m(self):\n  return 2\n",
    "four-space-suite": (
        "def f():\n    try:\n        return 1\n    except Exception:\n        return 0\n"
    ),
}


def _scenario_running(command: str) -> Scenario:
    return Scenario(name="carrier", mode="backend", commands=[{"name": "c", "run": command}])


def _embedded_bodies(command: str) -> list[str]:
    """Every literal this command hands to ``exec``/``compile``, as it would arrive.

    Parses the command the way a shell and then Python would: split the shell
    words, take the ``-c`` payload, and read the constant argument of each
    ``exec``/``compile`` call out of its AST. Returns what the interpreter would
    actually be asked to run.
    """
    tokens = shlex.split(command)
    if "-c" not in tokens:
        return []
    tree = ast.parse(tokens[tokens.index("-c") + 1])
    return [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"exec", "compile"}
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ]


# ==========================================================================
# 1 — the key and the text are two different strings, and both survive
# ==========================================================================


class TestKeyAndTextAreSeparate:
    """Normalizing to compare is fine. Normalizing and then forgetting is not."""

    def test_the_text_a_human_wrote_is_kept(self):
        authored = _cmd(EMBEDDED_BODIES["try-except"])
        approved = ApprovedCommands([authored])
        assert approved.verbatim == (authored,), (
            "the approved set no longer holds the command as written. Whatever is "
            "shown to a reader or a model is copied character for character, so a "
            "lossily-normalized form is not a substitute for it."
        )

    def test_the_key_still_collapses_so_matching_is_unchanged(self):
        """The split is deliberate: the key must stay lossy, or spelling variants stop matching."""
        approved = ApprovedCommands(["./probe.sh   --case  x"])
        assert approved.entries == ("./probe.sh --case x",)
        assert approved.verbatim == ("./probe.sh   --case  x",)

    def test_the_two_tuples_index_the_same_command(self):
        commands = [_cmd(body) for body in EMBEDDED_BODIES.values()] + ["./a.sh", "./b.sh  x"]
        approved = ApprovedCommands(commands)
        assert len(approved.entries) == len(approved.verbatim) == len(set(commands))
        for key, text in zip(approved.entries, approved.verbatim):
            assert ApprovedCommands([text]).entries == (key,), (
                f"entry {key!r} and text {text!r} are not the same command; the two "
                "tuples must stay aligned or a caller reading both will pair them wrong"
            )

    def test_a_duplicate_spelling_stays_one_command(self):
        approved = ApprovedCommands(["./probe.sh --case x", "./probe.sh  --case  x"])
        assert len(approved.entries) == 1
        assert approved.verbatim == ("./probe.sh --case x",)

    def test_the_verbatim_form_is_still_approved(self):
        """Presenting the text must not present something that is then refused."""
        authored = _cmd(EMBEDDED_BODIES["try-finally"])
        approved = ApprovedCommands([authored])
        ok, why = approved.approves(approved.verbatim[0])
        assert ok, f"the command the brief shows would itself be refused: {why}"

    def test_a_control_character_never_reaches_the_rendered_text(self):
        """A raw newline in the text would split one rendered command across two lines.

        Such a command is refused by `approves` anyway, so there is nothing to
        preserve — but what is shown must still be one line, whatever a scenario
        file happens to contain.
        """
        approved = ApprovedCommands(["./probe.sh --case\nx"])
        assert all("\n" not in text for text in approved.verbatim)


# ==========================================================================
# 2 — nested blocks survive generation and rendering
# ==========================================================================


class TestNestedBlocksSurvive:
    """The defect, stated as the property it broke, one case per block kind."""

    @pytest.mark.parametrize("kind", sorted(EMBEDDED_BODIES))
    def test_the_embedded_body_still_parses(self, kind):
        authored = _cmd(EMBEDDED_BODIES[kind])
        # The premise: the body is valid Python before Product Driver touches it.
        ast.parse(EMBEDDED_BODIES[kind])

        shown = ApprovedCommands([authored]).verbatim[0]
        bodies = _embedded_bodies(shown)
        assert bodies, "the presented command no longer carries an embedded body at all"
        for body in bodies:
            try:
                ast.parse(body)
            except SyntaxError as exc:
                pytest.fail(
                    f"the {kind} body Product Driver presents is not valid Python: "
                    f"{type(exc).__name__}: {exc.msg} (line {exc.lineno}). "
                    f"authored {EMBEDDED_BODIES[kind]!r}, presented {body!r}. This is "
                    "run 20260830-034455's m9-w2-01 failure: an approved command whose "
                    "indentation was flattened before the generator ever saw it."
                )

    @pytest.mark.parametrize("kind", sorted(EMBEDDED_BODIES))
    def test_the_body_is_unchanged_not_merely_parseable(self, kind):
        """Weaker forms of this test pass on a body that was repaired into something else."""
        authored = _cmd(EMBEDDED_BODIES[kind])
        shown = ApprovedCommands([authored]).verbatim[0]
        assert _embedded_bodies(shown) == [EMBEDDED_BODIES[kind]]

    @pytest.mark.parametrize("kind", sorted(EMBEDDED_BODIES))
    def test_the_whole_command_parses_before_anything_executes(self, kind):
        """Outer payload and every inner body, checked the way the interpreter would."""
        shown = ApprovedCommands([_cmd(EMBEDDED_BODIES[kind])]).verbatim[0]
        tokens = shlex.split(shown)
        ast.parse(tokens[tokens.index("-c") + 1])
        for body in _embedded_bodies(shown):
            ast.parse(body)


# ==========================================================================
# 3 — the path that actually failed: what the generator is shown
# ==========================================================================


class TestWhatTheGeneratorIsShown:
    """The brief is the artifact a proposal copies from. It is the thing under test."""

    def test_the_brief_renders_the_command_as_authored(self):
        authored = _cmd(EMBEDDED_BODIES["try-except"])
        approved = ApprovedCommands([authored])
        rendered = GenerationBrief(
            stage="initial",
            wave=1,
            basis=GenerationBasis(task="t"),
            max_scenarios=1,
            available_commands=list(approved.verbatim),
            available_services=[],
            app_url="",
            browser_enabled=False,
        ).render()
        assert authored in rendered, (
            "the generation brief does not contain the approved command as a human "
            "wrote it, so a proposal that copies it faithfully still copies something "
            "else"
        )

    def test_the_planner_hands_the_generator_the_authored_command(self, tmp_path):
        """End to end through the real planner: nothing in between may rewrite it."""
        authored = _cmd(EMBEDDED_BODIES["try-except"])
        planner = ScenarioPlanner(
            repo=tmp_path,
            config=ScenarioGenerationConfig(enabled=True),
            reasoner=ScriptedReasoner([{"risks": [], "scenarios": []}]),
            base_scenario=base_scenario(),
            permanent_scenarios=[base_scenario(), _scenario_running(authored)],
            founder=FakeFounder(),
        )
        planner.plan_initial(task="exercise the approved commands")
        briefs = planner.reasoner.briefs
        assert briefs, "the planner never briefed the generator"
        shown = list(briefs[0].available_commands)
        assert authored in shown, (
            "the planner briefed the generator with a rewritten form of an approved "
            f"command. Closest offered: "
            f"{[c for c in shown if c.startswith('.venv/bin/python')][:1]}"
        )
        for command in shown:
            for body in _embedded_bodies(command) if "-c" in shlex.split(command) else []:
                ast.parse(body)


# ==========================================================================
# 4 — the shipped corpus, which is where run 20260830-034455 broke
# ==========================================================================


class TestTheShippedCorpus:
    """Stated over the repository's own scenario files, so it keeps holding as they grow."""

    def _corpus(self):
        scenarios = [load_scenario(p) for p in sorted((DRIVER_ROOT / "scenarios").glob("*.y*ml"))]
        authored: list[str] = []
        for scenario in scenarios:
            authored += list(scenario.setup) + list(scenario.teardown)
            authored += [spec.run for spec in scenario.commands]
            authored += [check.command for check in scenario.expect_state]
            for step in scenario.steps:
                if step.command is not None:
                    authored.append(step.command.run)
                if step.state_check is not None:
                    authored.append(step.state_check.command)
        approved = ApprovedCommands.from_sources(scenarios=scenarios, configured=[])
        return sorted({c.strip() for c in authored if c.strip()}), approved

    def test_every_authored_command_is_offered_as_written(self):
        authored, approved = self._corpus()
        assert authored, "the shipped corpus declares no commands"
        missing = [c for c in authored if c not in set(approved.verbatim)]
        assert not missing, (
            f"{len(missing)} of {len(authored)} shipped command(s) are offered to the "
            f"generator in a rewritten form, e.g. {missing[0][:120]!r}"
        )

    def test_every_python_dash_c_payload_in_the_corpus_parses_after_shell_splitting(self):
        """The command a shell would actually run has to BE a program.

        ### THIS IS THE HOLE RUN 20260903-065810 FELL THROUGH, AND IT IS ONE LEVEL BELOW THE
        GUARD BELOW. `test_every_embedded_body_in_the_corpus_still_parses` reads the literals a
        command hands to `exec`, and to find them it must first shell-split the command and parse
        the `-c` payload — so when THAT parse fails it `continue`s, on the reasonable-sounding
        grounds that the command is "not a python -c invocation, or not ours to judge". A command
        whose payload does not parse is not un-judgeable. It is broken.

        M11's Policy-Owner-singularity oracle carried BARE double quotes inside a double-quoted
        shell command. `/bin/sh` closed the string at the first one, the command exited 2 having
        executed no Python at all, and the run reported a P0 safety-invariant failure against a
        product whose constraint was present and correct. Three iterations of a build session were
        spent on a defect that lived in the measurement instrument, and this file was one
        `continue` away from naming it before the run started.

        So the payload is parsed HERE, where an unparseable one is a failure rather than a skip.
        """
        _authored, approved = self._corpus()
        checked, broken = 0, []
        for command in approved.verbatim:
            try:
                tokens = shlex.split(command)
            except ValueError as exc:
                broken.append((f"the shell cannot split it ({exc})", command))
                continue
            if not tokens or "-c" not in tokens:
                continue
            if not Path(tokens[0]).name.startswith("python"):
                continue
            index = tokens.index("-c")
            if index + 1 >= len(tokens):
                broken.append(("-c is the last token, so there is no program", command))
                continue
            checked += 1
            try:
                ast.parse(tokens[index + 1])
            except SyntaxError as exc:
                broken.append((f"{type(exc).__name__}: {exc.msg} (line {exc.lineno})", command))
        assert not broken, (
            f"{len(broken)} shipped command(s) would reach the interpreter as something that is "
            f"not a program, so they can only ever fail — and they fail as a PRODUCT defect. "
            f"First: {broken[0][0]} — {broken[0][1][:160]!r}"
        )
        assert checked, (
            "no shipped command is a `python -c` invocation any more, so this guard is no longer "
            "measuring the thing it was written for — keep one, or retire the test deliberately "
            "rather than letting it pass vacuously"
        )

    def test_the_payload_guard_catches_the_bare_quote_that_broke_run_20260903(self):
        """The mutation control for the guard above, in the exact shape the defect had.

        A `python -c "..."` whose exec payload carries an unescaped double quote: the shell ends
        the argument at that quote, so what the interpreter receives is a truncated program. The
        escaped form of the same command is required to parse, so this is a statement about the
        quoting rather than about the program. If the first half stops raising, the guard above has
        been loosened into something that passes either way.
        """
        quote, backslash = chr(34), chr(92)
        body = "import sys; exec(chr(10).join(['def go():',{q} print(1){q}]), {{}})"
        bare = ".venv/bin/python -c " + quote + body.format(q=quote) + quote
        escaped = ".venv/bin/python -c " + quote + body.format(q=backslash + quote) + quote

        tokens = shlex.split(bare)
        with pytest.raises(SyntaxError):
            ast.parse(tokens[tokens.index("-c") + 1])

        tokens = shlex.split(escaped)
        ast.parse(tokens[tokens.index("-c") + 1])

    def test_every_embedded_body_in_the_corpus_still_parses(self):
        _authored, approved = self._corpus()
        checked = 0
        for command in approved.verbatim:
            try:
                bodies = _embedded_bodies(command)
            except (ValueError, SyntaxError):
                continue  # not a python -c invocation, or not ours to judge
            for body in bodies:
                checked += 1
                try:
                    ast.parse(body)
                except SyntaxError as exc:
                    pytest.fail(
                        f"a shipped approved command is offered to the generator with a "
                        f"body that does not parse: {type(exc).__name__}: {exc.msg} "
                        f"(line {exc.lineno}). Command: {command[:120]!r}"
                    )
        assert checked, (
            "no shipped command embeds a code block any more, so this guard is no longer "
            "measuring the thing it was written for — keep one, or retire the test "
            "deliberately rather than letting it pass vacuously"
        )
