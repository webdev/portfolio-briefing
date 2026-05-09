"""
Step 11: Delivery

Once the briefing has been written to reports/daily/, copy it to a stable
delivery location so the user has one path that always points at the latest
briefing. Default destination is ~/Documents/briefings/.

Two files are written each day:
  - briefing_YYYY-MM-DD.md   (dated copy — never overwritten)
  - latest.md                (rolling pointer to the most recent briefing)

Both files include the same body. The dated copy is also kept in
reports/daily/ inside the repo, but ~/Documents/briefings/ is more user-
discoverable for a daily 8am file-write workflow.

Set PORTFOLIO_BRIEFING_DELIVERY_DIR to override the destination.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


_DEFAULT_DELIVERY_DIR = Path.home() / "Documents" / "briefings"


def _resolve_delivery_dir(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env = os.getenv("PORTFOLIO_BRIEFING_DELIVERY_DIR")
    if env:
        return Path(env).expanduser()
    return _DEFAULT_DELIVERY_DIR


def deliver_briefing(
    briefing_path: Path | str,
    json_path: Path | str | None = None,
    delivery_dir: str | None = None,
) -> dict:
    """Copy the released briefing to the delivery location.

    Returns {"delivery_dir": str, "dated": str, "latest": str, "json": str|None}.
    Failures are caught and reported in the dict's "error" key — delivery is
    best-effort, never load-bearing on the briefing itself.
    """
    briefing_path = Path(briefing_path)
    if not briefing_path.exists():
        return {"error": f"Briefing not found: {briefing_path}"}

    dest_dir = _resolve_delivery_dir(delivery_dir)
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return {"error": f"Could not create delivery dir {dest_dir}: {e}"}

    result = {"delivery_dir": str(dest_dir)}

    # Dated copy (e.g. briefing_2026-05-08.md). Strip any DRAFT suffix.
    dated_name = briefing_path.name.replace(".DRAFT", "")
    dated_path = dest_dir / dated_name
    try:
        shutil.copy2(briefing_path, dated_path)
        result["dated"] = str(dated_path)
    except Exception as e:
        result["error"] = f"Dated copy failed: {e}"
        return result

    # Rolling latest.md pointer
    latest_path = dest_dir / "latest.md"
    try:
        shutil.copy2(briefing_path, latest_path)
        result["latest"] = str(latest_path)
    except Exception as e:
        # Dated copy already succeeded — surface the latest.md error but
        # don't tear down the whole delivery.
        result["latest_error"] = f"latest.md copy failed: {e}"

    # Optional JSON sidecar
    if json_path is not None:
        json_path = Path(json_path)
        if json_path.exists():
            json_dest = dest_dir / json_path.name.replace(".DRAFT", "")
            try:
                shutil.copy2(json_path, json_dest)
                result["json"] = str(json_dest)
            except Exception as e:
                result["json_error"] = f"JSON copy failed: {e}"

    return result
