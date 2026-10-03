"""scripts/nightly.py: which stages run, in which order, and that every stage's module exists."""
import importlib.util
import sys

from trustissues.common import config

sys.path.insert(0, str(config.REPO_ROOT / "scripts"))
import nightly  # noqa: E402


def _names(**kw):
    return [n for n, _ in nightly.plan(**kw)]


def test_order_resume_and_skip():
    assert _names() == ["pipeline", "policy_index", "text_features", "queue", "fairness", "monitoring"]
    assert _names(start="queue") == ["queue", "fairness", "monitoring"]
    assert _names(skip_pipeline=True) == ["policy_index", "text_features", "queue", "fairness", "monitoring"]
    assert _names(only="fairness") == ["fairness"]


def test_every_required_stage_module_exists_and_the_queue_writes_gold():
    for name, cmd in nightly.STAGES:
        if name not in nightly.OPTIONAL:
            assert importlib.util.find_spec(cmd[0]), cmd[0]
    assert dict(nightly.STAGES)["queue"] == ["trustissues.nba.queue", "--write-gold"]
    assert dict(nightly.STAGES)["text_features"][1:3] == ["--scope", "open_cases"]  # not the labelled eval set
