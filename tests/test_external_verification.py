"""The external gate: generic, commit-bound, and unable to invent a green.

Nothing here knows about any particular verifier. What is tested is the three
properties that make the gate safe: evidence is about one commit, a broken
verifier is not a broken product, and a probe Product Driver may not run is not
run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from neyma_product_driver.config import PhaseClosureConfig
from neyma_product_driver.external_verification import (
    ExternalEvidence,
    ExternalRequirement,
    ExternalStatus,
    check_probe_command,
    classify_conclusion,
    evidence_from_payload,
    requirement_from_criteria,
    run_probe,
)
from neyma_product_driver.phase_acceptance import AcceptanceCriterion

SHA = "e6b1753131f33016cf7beb77206b77eaf361b89e"


def requirement(sha: str = SHA) -> ExternalRequirement:
    return ExternalRequirement(required=True, gate_name="ci", expected_sha=sha)


class TestEvidenceIsAboutOneCommit:
    def test_green_on_the_expected_commit_satisfies(self) -> None:
        evidence = ExternalEvidence(sha=SHA, status=ExternalStatus.SUCCESS)
        ok, reason = evidence.satisfies(requirement())
        assert ok and SHA[:12] in reason

    def test_a_short_sha_that_prefixes_the_expected_one_is_the_same_commit(self) -> None:
        evidence = ExternalEvidence(sha=SHA[:12], status=ExternalStatus.SUCCESS)
        assert evidence.satisfies(requirement())[0]

    def test_green_on_another_commit_does_not(self) -> None:
        evidence = ExternalEvidence(sha="0" * 40, status=ExternalStatus.SUCCESS)
        ok, reason = evidence.satisfies(requirement())
        assert not ok and "a different tree" in reason

    def test_evidence_naming_no_commit_does_not(self) -> None:
        evidence = ExternalEvidence(status=ExternalStatus.SUCCESS)
        ok, reason = evidence.satisfies(requirement())
        assert not ok and "names no commit" in reason

    def test_a_requirement_with_no_expected_commit_can_never_be_satisfied(self) -> None:
        ok, reason = ExternalEvidence(sha=SHA, status=ExternalStatus.SUCCESS).satisfies(
            requirement(sha="")
        )
        assert not ok and "never captured" in reason

    def test_no_requirement_means_nothing_to_satisfy(self) -> None:
        assert ExternalEvidence().satisfies(ExternalRequirement(required=False))[0]

    @pytest.mark.parametrize(
        "status",
        [
            ExternalStatus.FAILURE,
            ExternalStatus.PENDING,
            ExternalStatus.INFRASTRUCTURE,
            ExternalStatus.AWAITING,
        ],
    )
    def test_only_success_satisfies(self, status: ExternalStatus) -> None:
        assert not ExternalEvidence(sha=SHA, status=status).satisfies(requirement())[0]


class TestABrokenVerifierIsNotABrokenProduct:
    @pytest.mark.parametrize(
        "conclusion", ["cancelled", "timed_out", "startup_failure", "action_required", "stale"]
    )
    def test_an_infrastructure_word_is_not_a_failure(self, conclusion: str) -> None:
        assert classify_conclusion("completed", conclusion) is ExternalStatus.INFRASTRUCTURE

    def test_an_infrastructure_word_beats_a_completed_status(self) -> None:
        """A cancelled run is a broken verifier reported as a finished one."""
        assert classify_conclusion("completed", "cancelled") is not ExternalStatus.SUCCESS

    @pytest.mark.parametrize("conclusion", ["success", "passed", "green"])
    def test_green_words_are_green(self, conclusion: str) -> None:
        assert classify_conclusion("completed", conclusion) is ExternalStatus.SUCCESS

    @pytest.mark.parametrize("conclusion", ["failure", "failed", "neutral"])
    def test_red_words_are_red(self, conclusion: str) -> None:
        assert classify_conclusion("completed", conclusion) is ExternalStatus.FAILURE

    def test_saying_nothing_is_not_saying_green(self) -> None:
        assert classify_conclusion("", "") is ExternalStatus.AWAITING
        assert classify_conclusion("something we have never seen", "") is ExternalStatus.AWAITING


class TestReadingAPayload:
    def test_common_commit_field_names_are_all_read(self) -> None:
        for key in ("sha", "head_sha", "headSha", "commit", "revision"):
            assert evidence_from_payload({key: SHA, "conclusion": "success"}).sha == SHA

    def test_a_list_is_read_as_the_most_recent_run(self) -> None:
        evidence = evidence_from_payload(
            [{"sha": SHA, "conclusion": "success"}, {"sha": "0" * 40, "conclusion": "failure"}]
        )
        assert evidence.sha == SHA and evidence.status is ExternalStatus.SUCCESS

    def test_a_json_string_is_parsed(self) -> None:
        evidence = evidence_from_payload(f'{{"sha": "{SHA}", "conclusion": "success"}}')
        assert evidence.status is ExternalStatus.SUCCESS

    def test_unreadable_output_is_infrastructure_not_absence(self) -> None:
        evidence = evidence_from_payload("<html>502 Bad Gateway</html>")
        assert evidence.status is ExternalStatus.INFRASTRUCTURE
        assert "not readable JSON" in evidence.detail

    def test_an_explicit_status_word_is_honoured(self) -> None:
        assert (
            evidence_from_payload({"sha": SHA, "status": "SUCCESS"}).status
            is ExternalStatus.SUCCESS
        )

    def test_an_empty_payload_is_awaiting(self) -> None:
        assert evidence_from_payload({}).status is ExternalStatus.AWAITING


class TestTheProbeIsNarrow:
    @pytest.mark.parametrize(
        "command",
        [
            "git push origin main",
            "gh pr merge 1",
            "kubectl apply -f x.yaml",
            "aws s3 cp x y",
            "cat .env",
        ],
    )
    def test_the_command_guard_refuses_what_it_always_refuses(self, command: str) -> None:
        assert "command guard refuses it" in check_probe_command(command)

    @pytest.mark.parametrize(
        "command",
        ["curl -s https://ci/status | jq .", "curl -s https://ci/x && rm -rf y", "echo `id`"],
    )
    def test_a_chained_command_is_refused(self, command: str) -> None:
        """The guard's verdict is about the command it was shown."""
        assert check_probe_command(command)

    def test_a_program_outside_the_short_list_is_refused(self) -> None:
        assert "not one of the programs" in check_probe_command("ncat ci 80")

    def test_an_empty_probe_is_not_a_probe(self) -> None:
        assert check_probe_command("") == "no probe command is configured"

    @pytest.mark.parametrize(
        "command",
        [
            "curl -s https://ci.example.com/status/{sha}",
            "git notes --ref=ci show {sha}",
            "python3 tools/ci_status.py {sha}",
        ],
    )
    def test_a_read_only_probe_is_allowed(self, command: str) -> None:
        assert check_probe_command(command) == ""

    def test_a_refused_probe_produces_infrastructure_evidence_not_silence(
        self, tmp_path: Path
    ) -> None:
        evidence = run_probe("git push origin main", expected_sha=SHA, cwd=tmp_path)
        assert evidence.status is ExternalStatus.INFRASTRUCTURE
        assert "was not run" in evidence.detail
        assert not evidence.satisfies(requirement())[0]

    def test_the_sha_token_is_substituted(self, tmp_path: Path) -> None:
        script = tmp_path / "probe.py"
        script.write_text(
            "import sys, json\n"
            'print(json.dumps({"sha": sys.argv[1], "conclusion": "success"}))\n',
            encoding="utf-8",
        )
        evidence = run_probe(
            f"python3 {script} {{sha}}", expected_sha=SHA, cwd=tmp_path, gate_name="ci"
        )
        assert evidence.sha == SHA
        assert evidence.satisfies(requirement())[0]

    def test_a_probe_that_exits_nonzero_is_infrastructure(self, tmp_path: Path) -> None:
        script = tmp_path / "bad.py"
        script.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
        evidence = run_probe(f"python3 {script}", expected_sha=SHA, cwd=tmp_path)
        assert evidence.status is ExternalStatus.INFRASTRUCTURE
        assert "exited 3" in evidence.detail

    def test_a_probe_reporting_no_commit_cannot_be_this_trees_evidence(
        self, tmp_path: Path
    ) -> None:
        script = tmp_path / "vague.py"
        script.write_text('print(\'{"conclusion": "success"}\')\n', encoding="utf-8")
        evidence = run_probe(f"python3 {script}", expected_sha=SHA, cwd=tmp_path)
        assert evidence.status is ExternalStatus.INFRASTRUCTURE
        assert "named no commit" in evidence.detail


class TestTheRequirementComesFromTheCriteria:
    def test_no_external_criterion_means_no_requirement(self) -> None:
        assert not requirement_from_criteria([], expected_sha=SHA).required

    def test_the_requirement_names_the_criteria_it_settles(self) -> None:
        criteria = [
            AcceptanceCriterion(criterion_id="AC-3", name="ci_green_on_the_accepted_tree")
        ]
        built = requirement_from_criteria(criteria, expected_sha=SHA)
        assert built.required
        assert built.criterion_ids == ["AC-3"]
        assert built.expected_sha == SHA

    def test_the_waiting_block_names_the_commit_and_the_schema(self) -> None:
        built = requirement_from_criteria(
            [AcceptanceCriterion(criterion_id="AC-3", name="ci_green")], expected_sha=SHA
        )
        block = built.waiting_block()
        assert SHA in block
        assert "sha" in block and "status" in block
        assert "different tree" in block


class TestConfigurationRefusesEarly:
    def test_a_mutating_probe_is_refused_when_the_config_loads(self) -> None:
        """Finding this at config-read time beats finding it mid-closure."""
        with pytest.raises(ValueError, match="read-only verification probe"):
            PhaseClosureConfig(external_probe_command="git push origin main")

    def test_a_read_only_probe_is_accepted(self) -> None:
        config = PhaseClosureConfig(external_probe_command="curl -s https://ci/{sha}")
        assert config.external_probe_command

    def test_the_probe_timeout_is_bounded(self) -> None:
        with pytest.raises(ValueError):
            PhaseClosureConfig(external_probe_timeout_s=100_000)

    def test_the_conservative_defaults_hold(self) -> None:
        config = PhaseClosureConfig()
        assert config.enabled
        assert not config.stale_verification_blocks
        assert config.external_probe_command == ""

    def test_there_is_no_switch_that_lets_the_driver_commit(self) -> None:
        """``allow_auto_commit`` is refused outright, so a second knob here
        would read like a capability this does not have."""
        assert "prepare_acceptance_commit" not in PhaseClosureConfig.model_fields
