"""Shared integration data structures."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class IntegrationAction:
    host: str
    kind: str
    status: str
    summary: str
    path: Optional[Path] = None
    requested_scope: str = "auto"
    effective_scope: Optional[str] = None
    shadowed_by: Optional[str] = None
    backup_path: Optional[Path] = None
    use_host_cli: bool = False
    command_args: tuple[str, ...] = ()
