"""Render expiration cluster warnings."""

from analysis.expiration_ladder import ExpirationCluster


def render_expiration_clusters(clusters: list[ExpirationCluster]) -> list[str]:
    """Render warnings for concentrated expiration dates.

    Format:
      📅 EXPIRATION CLUSTER: X contracts on DAY MON DD '26 (Y% NLV) — ladder risk
    """
    lines = []

    if not clusters:
        return lines

    lines.append("## Expiration Ladder")
    lines.append("")

    for cluster in clusters:
        day_name = cluster.expiration.strftime("%a")
        date_str = cluster.expiration.strftime("%b %d '%y")
        pct_str = f"{cluster.pct_of_nlv:.1%}"

        lines.append(
            f"📅 **{cluster.contract_count} contracts expire {day_name} {date_str}** "
            f"({pct_str} NLV notional) — consider laddering to reduce cluster risk"
        )

    lines.append("")
    return lines
