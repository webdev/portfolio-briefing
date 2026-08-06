"""Shared JSON serialization helpers for the briefing pipeline.

Every ``json.dump`` in the pipeline passes ``default=json_default`` so a
stray non-JSON-native type degrades to a sensible JSON value instead of
killing a live run.

Regression (2026-08-04): the NLV reconciliation fix passed the E*TRADE
adapter's ``totalAccountValue`` — a ``Decimal`` — through
``_compose_balance`` into ``briefing_json``, and the Step 10
``json.dump(briefing_json, f)`` died with ``TypeError: Object of type
Decimal is not JSON serializable``, failing the whole live run. The
boundary now coerces Decimals to float at snapshot time AND every dump
carries this default as belt-and-suspenders.

Conversions:
- ``Decimal``            → ``float``
- ``datetime``/``date``  → ISO-8601 string (``isoformat()``)
- ``Path`` (any PurePath)→ ``str``
- ``set``/``frozenset``  → sorted list
Anything else still raises ``TypeError`` (we WANT to hear about brand-new
unserializable types in tests — just not by killing a live briefing run,
which is why every production dump routes through this hook).
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from pathlib import PurePath
from typing import Any


def json_default(o: Any):
    """``default=`` hook for ``json.dump``/``json.dumps``.

    Decimal → float, date/datetime → isoformat, Path → str,
    set → sorted list. Raises TypeError for anything else (same contract
    as the stdlib default, so behavior is unchanged for supported types).
    """
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (_dt.datetime, _dt.date)):
        return o.isoformat()
    if isinstance(o, PurePath):
        return str(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o, key=str)
    raise TypeError(
        f"Object of type {o.__class__.__name__} is not JSON serializable"
    )
