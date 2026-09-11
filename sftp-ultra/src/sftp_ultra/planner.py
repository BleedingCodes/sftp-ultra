from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .model import Config, OverwritePolicy, PlanItem, RemoteFile
from .paths import local_for_remote, unique_destination


def plan_transfers(
    files: Sequence[RemoteFile],
    config: Config,
) -> list[PlanItem]:
    plan: list[PlanItem] = []

    for remote in files:
        requested = local_for_remote(
            remote.path,
            config.remote_root,
            config.destination,
        )

        if not requested.exists():
            plan.append(
                PlanItem(
                    remote=remote,
                    local_path=requested,
                    action="transfer",
                )
            )
            continue

        if config.overwrite is OverwritePolicy.SKIP:
            plan.append(
                PlanItem(
                    remote=remote,
                    local_path=requested,
                    action="skip",
                    reason="destination exists",
                )
            )
        elif config.overwrite is OverwritePolicy.REPLACE:
            plan.append(
                PlanItem(
                    remote=remote,
                    local_path=requested,
                    action="transfer",
                    reason="replace destination",
                )
            )
        elif config.overwrite is OverwritePolicy.RENAME:
            plan.append(
                PlanItem(
                    remote=remote,
                    local_path=unique_destination(requested),
                    action="transfer",
                    reason="renamed destination",
                )
            )
        else:
            plan.append(
                PlanItem(
                    remote=remote,
                    local_path=requested,
                    action="ask",
                    reason="destination exists",
                )
            )

    return plan
