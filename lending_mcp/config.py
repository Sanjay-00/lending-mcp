"""
Settings, read once per call from `<home>/config.toml` and the environment.

home is LENDING_MCP_HOME, else ~/.lending-mcp. It holds config.toml, the
parsed report cache (reports/) and the generated workbooks (workbooks/).

config.toml:

    allowed_dirs = ["C:/Users/me/Documents/bureau reports"]
    autocam_dir  = "C:/Users/me/Projects/CIBIL_EXCEL"

The environment overrides the file: LENDING_MCP_ALLOWED_DIRS (folders joined
by the OS path separator, ";" on Windows), LENDING_MCP_AUTOCAM_DIR, and
LENDING_MCP_UNMASKED=1 to show PAN, date of birth and phone in full.

With no allowed folder configured nothing can be loaded: the server never
reads a file the user did not explicitly put in reach.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass(frozen=True)
class Settings:
    home: Path
    allowed_dirs: List[Path]
    autocam_dir: Optional[Path]
    unmasked: bool

    @property
    def reports_dir(self) -> Path:
        return self.home / "reports"

    @property
    def workbooks_dir(self) -> Path:
        return self.home / "workbooks"


def home_dir() -> Path:
    return Path(os.environ.get("LENDING_MCP_HOME") or Path.home() / ".lending-mcp")


def load_settings() -> Settings:
    home = home_dir()
    data: dict = {}
    config_file = home / "config.toml"
    if config_file.is_file():
        with config_file.open("rb") as fh:
            data = tomllib.load(fh)

    env_dirs = os.environ.get("LENDING_MCP_ALLOWED_DIRS")
    raw_dirs = env_dirs.split(os.pathsep) if env_dirs else data.get("allowed_dirs", [])
    allowed = [Path(d).expanduser().resolve() for d in raw_dirs if str(d).strip()]

    autocam = os.environ.get("LENDING_MCP_AUTOCAM_DIR") or data.get("autocam_dir")
    return Settings(
        home=home,
        allowed_dirs=allowed,
        autocam_dir=Path(autocam).expanduser().resolve() if autocam else None,
        unmasked=os.environ.get("LENDING_MCP_UNMASKED") == "1",
    )
