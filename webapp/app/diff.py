"""Structured diff between two briefing snapshots.

Compares two dated briefings on five axes:

  1. **Positions** — added / removed / qty-changed (by symbol)
  2. **Actions** — new / completed / still-pending (by key)
  3. **Parkev ratings** — rating tier change or conviction change per ticker
  4. **NLV / cash / coverage** — scalar deltas
  5. **Red flags** — added / removed (parsed from MD heading sections)

The diff is pure: it takes two Briefing dicts + recs maps and returns
a dict the template can render. No I/O here (the route loads inputs
via ingest helpers).

Design choice: we intentionally do NOT re-use the pipeline's
``briefing_diff.render_diff_panel`` here. That module is a
markdown-tail panel generator keyed on parsed action lines from MD;
the web app wants a structured Python dict per axis so the template
can render side-by-side tables and counts. They solve different
problems; sharing the parser would cost more than it saves.
"""

from __future__ import annotations

from typing import Any


def diff_briefings(
    a: dict[str, Any],
    b: dict[str, Any],
    recs_a: dict[str, dict] | None = None,
    recs_b: dict[str, dict] | None = None,
    positions_a: list[dict] | None = None,
    positions_b: list[dict] | None = None,
) -> dict[str, Any]:
    """Compute the structured diff. ``a`` is the older briefing, ``b`` newer.

    Returns:
        {
            "dates": {"a": "...", "b": "..."},
            "scalars": {"nlv": {"a", "b", "delta"}, "cash": ..., "coverage": ...},
            "positions": {"added": [...], "removed": [...], "changed": [...]},
            "actions":   {"new": [...], "completed": [...], "still_pending": [...]},
            "parkev":    {"changed": [...]},  # list of {ticker, prior, current}
            "red_flags": {"added": [...], "removed": [...]},  # from MD if available
        }

    Empty lists are returned for every axis even if no data is available,
    so the template can iterate without guards.
    """
    return {
        "dates": {"a": str(a.get("date") or ""), "b": str(b.get("date") or "")},
        "scalars": _diff_scalars(a, b),
        "positions": _diff_positions(positions_a or [], positions_b or []),
        "actions": _diff_actions(
            a.get("actions") or [], b.get("actions") or []
        ),
        "parkev": _diff_parkev(recs_a or {}, recs_b or {}),
    }


# ─── Scalar deltas ──────────────────────────────────────────────────


def _diff_scalars(a: dict, b: dict) -> dict[str, dict[str, Any]]:
    """NLV, cash, cash_pct, regime change."""
    def _f(x) -> float | None:
        if x is None:
            return None
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    nlv_a, nlv_b = _f(a.get("nlv")), _f(b.get("nlv"))
    cash_a, cash_b = _f(a.get("cash")), _f(b.get("cash"))

    cash_pct_a = (cash_a / nlv_a) if nlv_a and cash_a is not None else None
    cash_pct_b = (cash_b / nlv_b) if nlv_b and cash_b is not None else None

    return {
        "nlv":  _scalar(nlv_a, nlv_b),
        "cash": _scalar(cash_a, cash_b),
        "cash_pct": _scalar(cash_pct_a, cash_pct_b),
        "regime": {
            "a": a.get("regime"),
            "b": b.get("regime"),
            "changed": (a.get("regime") != b.get("regime")),
        },
    }


def _scalar(va: float | None, vb: float | None) -> dict[str, Any]:
    delta = None
    pct = None
    if va is not None and vb is not None:
        delta = vb - va
        pct = (delta / va) if va else None
    return {"a": va, "b": vb, "delta": delta, "pct": pct}


# ─── Position diff ──────────────────────────────────────────────────


def _diff_positions(
    a: list[dict], b: list[dict]
) -> dict[str, list[dict[str, Any]]]:
    """Compare position arrays by ``symbol``.

    Returns added / removed / changed (qty different by ≥ 0.001).
    """
    map_a = {(p.get("symbol") or "").upper(): p for p in a if p.get("symbol")}
    map_b = {(p.get("symbol") or "").upper(): p for p in b if p.get("symbol")}

    sa, sb = set(map_a), set(map_b)
    added = sorted(sb - sa)
    removed = sorted(sa - sb)
    common = sa & sb

    def _row(sym: str, p: dict) -> dict[str, Any]:
        return {
            "symbol": sym,
            "assetType": p.get("assetType"),
            "qty": p.get("qty"),
            "price": p.get("price"),
            "market_value": p.get("marketValue"),
            "underlying": p.get("underlying") or sym,
        }

    added_rows = [_row(s, map_b[s]) for s in added]
    removed_rows = [_row(s, map_a[s]) for s in removed]

    changed: list[dict[str, Any]] = []
    for sym in sorted(common):
        pa, pb = map_a[sym], map_b[sym]
        try:
            qa = float(pa.get("qty") or 0)
            qb = float(pb.get("qty") or 0)
        except (TypeError, ValueError):
            continue
        if abs(qa - qb) >= 1e-3:
            changed.append({
                "symbol": sym,
                "assetType": pa.get("assetType") or pb.get("assetType"),
                "qty_a": qa,
                "qty_b": qb,
                "qty_delta": qb - qa,
                "underlying": pb.get("underlying") or pa.get("underlying") or sym,
            })

    return {"added": added_rows, "removed": removed_rows, "changed": changed}


# ─── Action diff ────────────────────────────────────────────────────


def _diff_actions(
    a: list[dict], b: list[dict]
) -> dict[str, list[dict[str, Any]]]:
    """Compare action arrays by ``key`` (e.g. ``CLOSE:NVDA_PUT_180_20260918``).

    Returns:
      - ``new``           — in b, not in a
      - ``completed``     — in a, not in b (and `recon_status` from b is missing)
      - ``still_pending`` — in both
    """
    def _key(x: dict) -> str:
        return x.get("key") or f"{x.get('kind') or ''}:{x.get('ident') or ''}"

    map_a = {_key(x): x for x in a}
    map_b = {_key(x): x for x in b}

    sa, sb = set(map_a), set(map_b)
    new_keys = sorted(sb - sa)
    completed_keys = sorted(sa - sb)
    common = sorted(sa & sb)

    def _row(x: dict) -> dict[str, Any]:
        return {
            "key": _key(x),
            "kind": x.get("kind"),
            "ident": x.get("ident"),
            "summary": x.get("summary"),
            "days_flagged": x.get("days_flagged"),
        }

    return {
        "new":          [_row(map_b[k]) for k in new_keys],
        "completed":    [_row(map_a[k]) for k in completed_keys],
        "still_pending":[_row(map_b[k]) for k in common],
    }


# ─── Parkev rating diff ─────────────────────────────────────────────


def _diff_parkev(
    recs_a: dict[str, dict], recs_b: dict[str, dict]
) -> dict[str, list[dict[str, Any]]]:
    """Compare per-ticker Parkev rating + conviction.

    Returns:
      - ``changed`` — list of {ticker, prior, current} where either
        rating_tier or conviction differs (raw_recommendation is shown
        for context but not used in the change-detection key).

    Excludes tickers that exist in only one snapshot to keep noise low —
    those are typically just universe-additions, not "Parkev changed
    their mind" events.
    """
    # Normalize ticker keys to upper, accept either input shape
    ra = {k.upper(): v for k, v in recs_a.items() if k}
    rb = {k.upper(): v for k, v in recs_b.items() if k}
    changed: list[dict[str, Any]] = []
    for tk in sorted(set(ra) & set(rb)):
        x, y = ra[tk], rb[tk]
        tier_a, tier_b = x.get("rating_tier"), y.get("rating_tier")
        conv_a, conv_b = x.get("conviction"), y.get("conviction")
        if tier_a != tier_b or conv_a != conv_b:
            changed.append({
                "ticker": tk,
                "prior": {
                    "tier": tier_a,
                    "raw": x.get("raw_recommendation"),
                    "conviction": conv_a,
                },
                "current": {
                    "tier": tier_b,
                    "raw": y.get("raw_recommendation"),
                    "conviction": conv_b,
                },
            })

    # Also surface NEW coverage (ticker appeared in b but not a) and
    # DROPPED coverage as informational lists — empty when symmetric.
    new_coverage = sorted(set(rb) - set(ra))
    dropped_coverage = sorted(set(ra) - set(rb))
    return {
        "changed": changed,
        "new_coverage": [{"ticker": t, **{k: rb[t].get(k) for k in
                          ("rating_tier", "raw_recommendation", "conviction")}}
                          for t in new_coverage],
        "dropped_coverage": [{"ticker": t, **{k: ra[t].get(k) for k in
                              ("rating_tier", "raw_recommendation", "conviction")}}
                              for t in dropped_coverage],
    }
