"""
Step 9: Quality gate

Run basic sanity checks on rendered markdown.
"""


def run_quality_gate(markdown: str) -> list:
    """
    Run quality checks on briefing markdown.

    Returns:
        List of issue strings (empty = all good)
    """
    issues = []

    # Check 1: No NaN or null strings (word-boundary to avoid matching "Financial" etc.)
    import re as _re
    if _re.search(r"\b(nan|null)\b", markdown, _re.IGNORECASE):
        issues.append("Found NaN or null strings in output")

    # Check 2: Required sections present
    required_sections = [
        "# Daily Briefing",
        "## Market Context",
        "## Health",
        "## Risk Alerts",
        "## Today's Action List",
        "## Watch / Portfolio Review",
    ]
    for section in required_sections:
        if section not in markdown:
            issues.append(f"Missing required section: {section}")

    # Check 3: No empty critical panels
    if "Portfolio NLV: $0" in markdown:
        issues.append("Portfolio NLV is $0 — data error")

    return issues
