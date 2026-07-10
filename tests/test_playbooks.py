"""Playbook loading: the shipped YAMLs must always be valid."""

import pytest

from switchboard.agent.playbooks import load_playbook
from switchboard.config import PLAYBOOKS_DIR


def shipped_playbooks():
    return sorted(p.stem for p in PLAYBOOKS_DIR.glob("*.yaml"))


def test_playbooks_are_shipped():
    assert "cod_confirmation" in shipped_playbooks()
    assert "refund_status" in shipped_playbooks()


@pytest.mark.parametrize("name", shipped_playbooks())
def test_every_shipped_playbook_loads(name):
    pb = load_playbook(name)
    assert pb.name == name
    assert pb.greeting
    assert pb.system_prompt
    # .strip() applied — no leading/trailing whitespace surprises in prompts
    assert pb.greeting == pb.greeting.strip()
    assert pb.system_prompt == pb.system_prompt.strip()


def test_missing_playbook_lists_available_options():
    with pytest.raises(FileNotFoundError, match="cod_confirmation"):
        load_playbook("does_not_exist")
