"""Playbooks: the use case as data, not code.

The pipeline is use-case-agnostic; a playbook YAML supplies the greeting and
system prompt. Redeploying the same platform for a new customer flow means
writing a new YAML — which is the actual forward-deployed-engineer workflow
this project demonstrates.
"""

from dataclasses import dataclass

import yaml

from switchboard.config import PLAYBOOKS_DIR


@dataclass
class Playbook:
    name: str
    description: str
    greeting: str
    system_prompt: str


def load_playbook(name: str) -> Playbook:
    path = PLAYBOOKS_DIR / f"{name}.yaml"
    if not path.exists():
        options = ", ".join(p.stem for p in PLAYBOOKS_DIR.glob("*.yaml"))
        raise FileNotFoundError(f"no playbook '{name}' — available: {options}")
    data = yaml.safe_load(path.read_text())
    return Playbook(
        name=data["name"],
        description=data["description"],
        greeting=data["greeting"].strip(),
        system_prompt=data["system_prompt"].strip(),
    )
