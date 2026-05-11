"""Four expert persona validators for briefing quality checks."""

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CheckResult:
    """Result of a single quality check."""
    severity: str  # critical, major, minor
    text: str


@dataclass
class PersonaResult:
    """Final result for one persona."""
    name: str
    score: int
    issues: list[CheckResult] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "issues": [{"severity": i.severity, "text": i.text} for i in self.issues],
            "strengths": self.strengths,
        }


def financial_advisor_check(md: str) -> PersonaResult:
    """Verify every action has Why/Gain/Yield, impact card present, header formatted."""
    result = PersonaResult(name="financial_advisor", score=100)

    # Check for action-like words (EXECUTE, CLOSE, HOLD, etc) - may have words after like "CSP", "ROLL", etc
    # Action header patterns — match either:
    #  • Inline bold: "**EXECUTE ROLL** XYZ" or "**CLOSE** ABC"
    #  • Section header: "### 💰 1. CLOSE WINNER · `XYZ`" or "### 🔄 2. REVIEW ROLL · `ABC`"
    action_pattern = (
        r'\*\*(?:EXECUTE|CLOSE|HOLD|TRIM|ROLL|COLLAR|CONSIDER|REVIEW|HEDGE|URGENT)(?:\s+\w+)*\*\*'
        r'|^#{2,4}[^\n]*\b(?:EXECUTE|CLOSE|HOLD|TRIM|ROLL|COLLAR|CONSIDER|REVIEW|HEDGE|URGENT|WINNER)\b'
    )
    if not re.search(action_pattern, md, re.IGNORECASE | re.MULTILINE):
        result.issues.append(CheckResult("critical", "No actionable recommendations surfaced"))
        result.score = 0
        return result

    # Count action patterns
    action_count = len(re.findall(action_pattern, md, re.IGNORECASE | re.MULTILINE))

    if action_count == 0:
        result.issues.append(CheckResult("critical", "No actionable recommendations surfaced"))
        result.score = 0
        return result

    # Locate the action list section — accept multiple heading variants.
    section_start = -1
    for needle in ("## Today's Action List", "## Action List", "## Actions", "## EXECUTE"):
        idx = md.find(needle)
        if idx >= 0:
            section_start = idx
            break
    if section_start < 0:
        # Fall back to scanning whole doc — Why/Gain/Yield may live anywhere
        action_section = md
    else:
        # Cut from start of action list to the next H2 heading (or doc end)
        rest = md[section_start + 5:]  # skip past the "## " of this header
        next_h2 = rest.find("\n## ")
        action_section = (md[section_start:section_start + 5 + next_h2]
                          if next_h2 >= 0 else md[section_start:])

    why_count = len(re.findall(r'\*\*Why:\*\*', action_section))
    gain_count = len(re.findall(r'\*\*Gain:\*\*', action_section))
    yield_count = len(re.findall(r'(Yield:|no yield \(one-time\))', action_section))

    if why_count == 0:
        result.issues.append(CheckResult("major", "Missing **Why:** bullets in action items"))
        result.score -= 15

    if gain_count == 0:
        result.issues.append(CheckResult("major", "Missing **Gain:** bullets in action items"))
        result.score -= 15

    if yield_count == 0:
        result.issues.append(CheckResult("major", "Missing Yield: lines or 'no yield (one-time)' in actions"))
        result.score -= 15

    # Check for impact summary card (accept either H2 or H3 + emoji prefix)
    if not re.search(r'(?:^|\n)#{2,3}\s+[^\n]*(?:Total Impact|Impact Summary|Total Potential)',
                     md, re.IGNORECASE):
        result.issues.append(CheckResult("major", "Missing Total Impact summary card"))
        result.score -= 15
    else:
        result.strengths.append("Total Impact card present")

    # Check header format: must contain BOTH long-form date AND ISO date.
    # Accept any combination of H1/H2 "Daily Briefing" plus an ISO date anywhere in the
    # first ~500 chars of the document.
    head_text = md[:500]
    has_long_form = bool(re.search(
        r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|'
        r'January|February|March|April|May|June|July|August|September|October|November|December)',
        head_text))
    has_iso = bool(re.search(r'\b\d{4}-\d{2}-\d{2}\b', head_text))
    if has_iso and has_long_form:
        result.strengths.append("Header has both long-form and ISO dates")
    elif not has_iso:
        result.issues.append(CheckResult("minor", "Header missing ISO date format (YYYY-MM-DD)"))
        result.score -= 5
    elif not has_long_form:
        result.issues.append(CheckResult("minor", "Header missing long-form date"))
        result.score -= 5

    if result.score < 0:
        result.score = 0

    return result


def options_trader_check(md: str) -> PersonaResult:
    """Verify expirations are weekdays, rolls have both legs + strikes + limits, deltas shown."""
    result = PersonaResult(name="options_trader", score=100)

    # Check for invalid weekday expirations (Sat/Sun) on actual option contracts.
    # Pattern: "Sat May 29 '26" or "Sunday June 21" — but ONLY if the date
    # appears inside a line that references an option (strike + put/call, or
    # an OCC-style symbol like ABCD_PUT_135_20260529). The briefing header
    # also contains "generated Sat May 9 '26, …" which is a document
    # timestamp, not an expiration — exclude header/timestamp lines.
    invalid_weekday_pattern = re.compile(
        r'(Saturday|Sunday|Sun|Sat)\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)',
        re.IGNORECASE,
    )
    # Exclude header timestamp / trading-session lines and pure provenance
    # blocks. We only flag lines that look like option-trade content.
    EXCLUDE = re.compile(
        r'(generated\s+\w+\s+\w+\s+\d|trading session|For trading|asOf|fetched_at)',
        re.IGNORECASE,
    )
    OPTION_CONTEXT = re.compile(
        r'(\b\$?\d+(?:\.\d+)?\s*[CP]\b'         # "$300C" / "300P" strike+type
        r'|\bcall\b|\bput\b'
        r'|_PUT_|_CALL_'
        r'|\bBTC\b|\bSTO\b|\bBTO\b|\bSTC\b'    # option order verbs
        r'|buy-to-close|sell-to-open|buy-to-open|sell-to-close'
        r'|\bDTE\b|\bdelta\b|\bpremium\b|\bexpir(?:ation|y|es)\b'
        r'|\bROLL\b|\bROLLED\b)',
        re.IGNORECASE,
    )

    invalid_expirations: list[str] = []
    for line in md.splitlines():
        if not invalid_weekday_pattern.search(line):
            continue
        if EXCLUDE.search(line):
            continue
        if not OPTION_CONTEXT.search(line):
            continue
        invalid_expirations.extend(invalid_weekday_pattern.findall(line))

    if invalid_expirations:
        result.issues.append(
            CheckResult("critical", f"Invalid weekend expiration dates found: {invalid_expirations}")
        )
        result.score = 0
        return result

    # Check for VIX options (must be Wednesday only)
    if 'VIX' in md or 'VX' in md:
        vix_date_pattern = r'(Monday|Tuesday|Wednesday|Thursday|Friday)\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+\'?\d{2}.*VIX'
        non_wed_vix = re.findall(r'(Monday|Tuesday|Thursday|Friday)\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec).*VIX', md, re.IGNORECASE)
        if non_wed_vix:
            result.issues.append(
                CheckResult("critical", f"VIX options must expire Wednesday only, found on: {non_wed_vix}")
            )
            result.score = 0

    # Check for ROLL actions with both legs.
    # Match top-level action items: optional list-numbering and bold markers, then the
    # literal action label. Captures up to 10 follow-up lines as the "section" to validate.
    # This matches both the real briefing ("1. **EXECUTE ROLL** ...") and bare test inputs
    # ("EXECUTE ROLL AAPL\n- STO ...").
    roll_pattern = (
        r'^(?:\s*\d+\.\s+)?\*?\*?'
        r'(?:EXECUTE ROLL|DEFENSIVE ROLL|DEFENSIVE COLLAR)\b'
        r'[^\n]*(?:\n[^\n]*){0,10}'
    )
    roll_sections = re.findall(roll_pattern, md, re.MULTILINE)

    for section in roll_sections:
        # Match either token form (BTC/STO) or human form ("Buy-to-Close", "Sell-to-Open")
        has_btc = bool(re.search(r'\bBTC\b|Buy[-\s]?to[-\s]?Close', section, re.IGNORECASE))
        has_sto = bool(re.search(r'\bSTO\b|Sell[-\s]?to[-\s]?Open', section, re.IGNORECASE))
        has_strike = bool(re.search(r'\d{2,3}(?:\.\d{2})?', section))
        has_limit = bool(re.search(r'(?:limit|at|@).*\$\d+\.?\d*|\$\d+\.?\d*\s*(?:limit|at|@)?', section, re.IGNORECASE))
        has_delta = bool(re.search(r'\bDelta\b', section, re.IGNORECASE))

        if not has_btc:
            result.issues.append(CheckResult("critical", "ROLL missing BTC (buy to close) leg"))
        if not has_sto:
            result.issues.append(CheckResult("critical", "ROLL missing STO (sell to open) leg"))
        if not has_strike:
            result.issues.append(CheckResult("major", "ROLL missing strike prices"))
        if not has_limit:
            result.issues.append(CheckResult("major", "ROLL missing limit prices"))
        if not has_delta:
            result.issues.append(CheckResult("major", "ROLL missing Delta line"))
        elif has_btc and has_sto and has_delta:
            result.strengths.append("ROLL actions properly specify both legs and Delta")

    # Check for valid SPY/QQQ weekday expirations
    valid_dates = re.findall(r'(Monday|Tuesday|Wednesday|Thursday|Friday)\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)', md)
    if valid_dates:
        result.strengths.append("All option expirations use valid weekdays (Mon-Fri)")

    if result.score < 0:
        result.score = 0

    return result


def tax_cpa_check(md: str) -> PersonaResult:
    """Verify wash-sale checks, earnings checks, LTCG costs, account routing, tax savings in impact."""
    result = PersonaResult(name="tax_cpa", score=100)

    # Look for PULLBACK CSP / NEW CSP action items (these MUST have wash-sale
    # + earnings check lines). The old regex used .*sell.*put with re.DOTALL
    # which matched too greedily across unrelated sections. Now we match the
    # explicit action headline AND verify the check lines appear within the
    # same action block (≈ next 12 lines).
    action_line_re = re.compile(
        r"^\s*\d+\.\s+\*\*(PULLBACK CSP|NEW CSP)\*\*", re.MULTILINE
    )
    lines_of_md = md.splitlines()
    for m in action_line_re.finditer(md):
        # Compute which markdown line index this is at
        head_line_idx = md[:m.start()].count("\n")
        block = "\n".join(lines_of_md[head_line_idx: head_line_idx + 12])
        if not re.search(r"Wash-sale check:", block):
            result.issues.append(CheckResult(
                "critical",
                f"{m.group(1)} action found but no 'Wash-sale check:' line in its block"
            ))
            result.score -= 30
            break  # one report is enough
    for m in action_line_re.finditer(md):
        head_line_idx = md[:m.start()].count("\n")
        block = "\n".join(lines_of_md[head_line_idx: head_line_idx + 12])
        if not re.search(r"Earnings check:", block):
            result.issues.append(CheckResult(
                "critical",
                f"{m.group(1)} action found but no 'Earnings check:' line in its block"
            ))
            result.score -= 30
            break

    # Look for ROLL actions and verify earnings check
    if re.search(r'(EXECUTE ROLL|DEFENSIVE ROLL)', md, re.IGNORECASE):
        if not re.search(r'Earnings check:', md):
            result.issues.append(
                CheckResult("major", "ROLL action found but no 'Earnings check:' line")
            )
            result.score -= 15

    # Look for TRIM actions and check for LTCG mention
    trim_sections = re.findall(r'TRIM[^\n]*(?:\n[^\n]*){0,5}', md, re.IGNORECASE | re.DOTALL)
    if trim_sections:
        for section in trim_sections:
            if not re.search(r'(LTCG|tax cost|tax impact|capital gains)', section, re.IGNORECASE):
                result.issues.append(
                    CheckResult("major", "TRIM action missing LTCG tax cost mention")
                )
                result.score -= 15
                break

    # Check for account routing on new entries
    if re.search(r'(NEW|EXECUTE|BUY|SELL).*(?:\$\d+|contract)', md, re.IGNORECASE):
        if not re.search(r'(Roth|Taxable|Traditional|IRA)', md):
            result.issues.append(
                CheckResult("major", "New entries found but no account routing (Roth/Taxable/IRA)")
            )
            result.score -= 15
        else:
            result.strengths.append("Account routing clearly specified")

    # Check for tax savings in impact card
    if re.search(r'(## Total Impact|Impact Summary)', md, re.IGNORECASE):
        if re.search(r'(tax-avoided|tax saved|tax benefit|\$\d+.*tax)', md, re.IGNORECASE):
            result.strengths.append("Tax impact quantified in Total Impact card")
        else:
            result.issues.append(
                CheckResult("minor", "Total Impact card missing tax-avoided dollar amount")
            )
            result.score -= 5

    if result.score < 0:
        result.score = 0

    return result


def risk_manager_check(md: str) -> PersonaResult:
    """Verify stress test + positions named, net Greeks non-zero theta, hedge book, concentration."""
    result = PersonaResult(name="risk_manager", score=100)

    # Check for Stress Test section
    has_stress = re.search(r'(## Stress Test|Stress Coverage|Scenario Analysis)', md, re.IGNORECASE)
    if not has_stress:
        result.issues.append(
            CheckResult("critical", "Missing Stress Test panel")
        )

    # Check for Hedge Book section (allow emoji prefixes like "## 🎯 Hedge Book")
    has_hedge = re.search(r'#{2,3}[^\n]*Hedge Book|Current Hedges|Active hedges', md, re.IGNORECASE)
    if not has_hedge:
        result.issues.append(
            CheckResult("critical", "Missing Hedge Book panel")
        )

    # Check for Net Greeks / theta
    if re.search(r'Net Greeks|Greeks Summary', md, re.IGNORECASE):
        result.strengths.append("Net Greeks section present")

        # If there are short options, theta should be non-zero
        if re.search(r'(short|STO|sell|premium)', md, re.IGNORECASE):
            if re.search(r'Theta:.*\$0(?![1-9])|Theta:.*0\.0+(?![1-9])', md):
                result.issues.append(
                    CheckResult("critical", "Short options present but Net Theta is $0 (should be positive)")
                )
                result.score -= 30
            else:
                result.strengths.append("Net Theta correctly non-zero with short positions")
    else:
        result.issues.append(
            CheckResult("major", "Missing Net Greeks summary line")
        )
        result.score -= 15

    # Check stress test names positions
    if has_stress:
        stress_section = re.search(r'## Stress Test.*?(?=##|$)', md, re.IGNORECASE | re.DOTALL)
        if stress_section:
            stress_text = stress_section.group(0)
            # Look for position names (symbols, stock names, etc.)
            if re.search(r'(if SPY|if market|if \w+ -\d+%|[A-Z]{1,4}\s+(?:at|down|up|risk))', stress_text, re.IGNORECASE):
                result.strengths.append("Stress Test scenarios name specific positions")
            else:
                result.issues.append(
                    CheckResult("major", "Stress Test doesn't name specific positions in scenarios")
                )

    # Check for concentration breaches and required actions
    breach_pattern = r'(?:>10%|>0\.10|breach|exceeds.*10%|concentration.*high)'
    if re.search(breach_pattern, md, re.IGNORECASE):
        # Find what actions are recommended
        if not re.search(r'(TRIM|COLLAR)', md):
            result.issues.append(
                CheckResult("major", "Concentration breach detected but no TRIM or COLLAR action recommended")
            )
            result.score -= 15
        else:
            result.strengths.append("Concentration breaches paired with TRIM/COLLAR actions")

    if result.score < 0:
        result.score = 0

    return result
