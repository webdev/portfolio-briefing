#!/usr/bin/env python3
"""CLI for briefing directives: capture, list, show, override, renew, evaluate."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from directive_store import create, get, list as list_directives, transition
from trigger_evaluator import evaluate_all_active


def _prompt(msg: str, default: str = None) -> str:
    """Simple input prompt."""
    if default:
        msg = f"{msg} [{default}]: "
    else:
        msg = f"{msg}: "
    response = input(msg).strip()
    return response or default


def _prompt_choices(msg: str, choices: list[str]) -> str:
    """Prompt user to select from a list."""
    print(f"\n{msg}")
    for i, choice in enumerate(choices, 1):
        print(f"  {i}. {choice}")
    while True:
        response = input("Choose (number): ").strip()
        try:
            idx = int(response) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        except ValueError:
            pass
        print("Invalid choice. Try again.")


def cmd_capture(args):
    """Interactive capture of a new directive."""
    state_dir = args.state_dir

    print("\n=== Briefing Directive Capture ===\n")

    # Step 1: Directive type
    directive_type = _prompt_choices(
        "What kind of directive?",
        ["DEFER", "MANUAL", "OVERRIDE", "WATCH_ONLY", "SUPPRESS"],
    )

    # Step 2: Target
    target_kind = _prompt_choices(
        "What's the target scope?",
        [
            "option_position (specific contract)",
            "position_scope (all positions in symbol/type)",
            "new_idea (screener idea)",
            "symbol (broad symbol-level rule)",
        ],
    )
    target_kind = target_kind.split(" ")[0]  # Extract just the kind

    target = {"kind": target_kind}

    if target_kind == "option_position":
        target["identifier"] = _prompt("Option identifier (e.g., AAPL  260619P00170000)")
    elif target_kind == "position_scope":
        target["symbol"] = _prompt("Symbol (e.g., AAPL)")
        pos_type = _prompt(
            "Position type (short_call/short_put/long_call/long_put, or blank for all)"
        )
        if pos_type:
            target["position_type"] = pos_type
    elif target_kind == "new_idea":
        target["symbol"] = _prompt("Symbol (e.g., NVDA)")
        target["source_screener"] = _prompt("Screener name (e.g., vcp-screener)")
    elif target_kind == "symbol":
        target["symbol"] = _prompt("Symbol (e.g., BABA)")
        scope = _prompt_choices(
            "Scope (long ideas only, or all)?", ["all", "long_only"]
        )
        if scope == "long_only":
            target["scope"] = "long_only"

    # Step 3: Reason
    reason = _prompt("Why this directive? (free text)")

    # Step 4: Expiry trigger
    trigger_type = _prompt_choices(
        "When does it expire?",
        [
            "time_elapsed (specific date)",
            "earnings_passed (after earnings)",
            "position_closed (when position closes)",
            "price_above (stock closes above level)",
            "price_below (stock closes below level)",
            "screener_drops (idea no longer surfaces)",
            "open_ended (manual renewal every 30 days)",
        ],
    )
    trigger_type = trigger_type.split(" ")[0]  # Extract just the type

    expires = {"trigger": trigger_type}

    if trigger_type == "time_elapsed":
        until_date = _prompt("Until date (YYYY-MM-DD)")
        expires["until_date"] = until_date
    elif trigger_type == "earnings_passed":
        expires["symbol"] = _prompt("Symbol")
    elif trigger_type == "position_closed":
        expires["position_identifier"] = _prompt("Position identifier")
    elif trigger_type == "price_above":
        expires["symbol"] = _prompt("Symbol")
        expires["level"] = float(_prompt("Price level"))
    elif trigger_type == "price_below":
        expires["symbol"] = _prompt("Symbol")
        expires["level"] = float(_prompt("Price level"))
    elif trigger_type == "screener_drops":
        expires["symbol"] = _prompt("Symbol")
        expires["screener_name"] = _prompt("Screener name")
    # open_ended has no additional params

    # Step 5: OVERRIDE special handling
    old_value = None
    new_value = None
    parameter = None
    if directive_type == "OVERRIDE":
        parameter = _prompt("Parameter name (e.g., take_profit_threshold)")
        old_value = _prompt("Current value")
        new_value = _prompt("New value")

    # Build directive dict
    directive_dict = {
        "type": directive_type,
        "target": target,
        "reason": reason,
        "expires": expires,
        "created_via": "cli",
    }

    if parameter:
        directive_dict["parameter"] = parameter
        directive_dict["old_value"] = old_value
        directive_dict["new_value"] = new_value

    # Step 6: Confirm
    print("\n=== Review Directive ===\n")
    print(f"Type:      {directive_dict['type']}")
    print(f"Target:    {target_kind} → {json.dumps(target, indent=12)}")
    print(f"Reason:    {directive_dict['reason']}")
    print(f"Expires:   {trigger_type} → {json.dumps(expires, indent=12)}")

    confirm = input("\nCreate this directive? (yes/no): ").strip().lower()
    if confirm != "yes":
        print("Cancelled.")
        return 1

    # Create
    try:
        directive = create(state_dir, directive_dict)
        print(f"\nCreated: {directive['directive_id']}")
        print(f"Status:  ACTIVE")
        print(f"File:    {state_dir}/active/{directive['directive_id']}.yaml")
        return 0
    except Exception as e:
        print(f"\nError creating directive: {e}")
        return 1


def cmd_list(args):
    """List directives, optionally filtered by status."""
    state_dir = args.state_dir
    status = args.status

    directives = list_directives(state_dir, status=status)

    if not directives:
        print("No directives found." if status is None else f"No {status} directives.")
        return 0

    print(f"\n=== Directives ({len(directives)} total) ===\n")
    for d in directives:
        directive_id = d.get("directive_id", "?")
        d_type = d.get("type", "?")
        d_status = d.get("status", "?")
        target = d.get("target", {})
        reason = d.get("reason", "")

        target_str = (
            target.get("identifier")
            or f"{target.get('symbol')} {target.get('position_type', 'any')}"
            or "?"
        )
        print(f"{directive_id}")
        print(f"  Type:    {d_type}")
        print(f"  Status:  {d_status}")
        print(f"  Target:  {target_str}")
        print(f"  Reason:  {reason[:60]}")
        expires = d.get("expires", {})
        print(f"  Expires: {expires.get('trigger', '?')}")
        print()

    return 0


def cmd_show(args):
    """Show a specific directive."""
    state_dir = args.state_dir
    directive_id = args.directive_id

    directive = get(state_dir, directive_id)
    if not directive:
        print(f"Directive not found: {directive_id}")
        return 1

    print(f"\n=== {directive_id} ===\n")
    print(json.dumps(directive, indent=2, default=str))
    return 0


def cmd_override(args):
    """Mark a directive as OVERRIDDEN (user changed their mind)."""
    state_dir = args.state_dir
    directive_id = args.directive_id
    reason = args.reason or "User override"

    try:
        directive = transition(state_dir, directive_id, "OVERRIDDEN", reason)
        print(f"Overridden: {directive_id}")
        print(f"Reason:     {reason}")
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


def cmd_renew(args):
    """Extend an open-ended directive expiry."""
    state_dir = args.state_dir
    directive_id = args.directive_id

    directive = get(state_dir, directive_id)
    if not directive:
        print(f"Directive not found: {directive_id}")
        return 1

    trigger = directive.get("expires", {}).get("trigger")
    if trigger != "open_ended":
        print(f"Cannot renew {directive_id}: trigger is {trigger}, not open_ended")
        return 1

    # For open_ended, renewal is implicit (30-day reset happens on next evaluation)
    # We just log that the user confirmed renewal
    print(f"Renewed: {directive_id}")
    print("Open-ended directive will be re-evaluated in 30 days.")
    return 0


def cmd_evaluate(args):
    """Run trigger evaluation on all ACTIVE directives."""
    state_dir = args.state_dir

    # Build minimal current_state from command line or defaults
    current_state = {
        "current_date": date.today(),
        "positions": args.positions or [],
        "last_close": args.prices or {},
        "earnings_calendar": args.earnings or {},
        "screener_outputs": args.screener or {},
    }

    print(f"\n=== Evaluating triggers ===\n")

    try:
        expired = evaluate_all_active(state_dir, current_state)
        print(f"Expired {len(expired)} directive(s):\n")
        for d in expired:
            print(f"  {d.get('directive_id')}")
            history = d.get("status_history", [])
            if history:
                last_transition = history[-1]
                print(f"    Reason: {last_transition.get('reason')}")
        return 0
    except Exception as e:
        print(f"Error during evaluation: {e}")
        return 1


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Briefing Directives CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 cli.py capture
  python3 cli.py list
  python3 cli.py list --status active
  python3 cli.py show dir_20260507_aapl_defer_a3f1
  python3 cli.py override dir_20260507_aapl_defer_a3f1 --reason "Changed my mind"
  python3 cli.py evaluate
        """,
    )

    parser.add_argument(
        "--state-dir",
        default="state/directives/",
        help="Path to state/directives/ directory",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # capture
    subparsers.add_parser("capture", help="Interactively capture a new directive")

    # list
    list_parser = subparsers.add_parser("list", help="List directives")
    list_parser.add_argument(
        "--status", choices=["active", "expired", "overridden", "resolved"]
    )

    # show
    show_parser = subparsers.add_parser("show", help="Show a specific directive")
    show_parser.add_argument("directive_id", help="Directive ID")

    # override
    override_parser = subparsers.add_parser("override", help="Override a directive")
    override_parser.add_argument("directive_id", help="Directive ID")
    override_parser.add_argument("--reason", help="Reason for override")

    # renew
    renew_parser = subparsers.add_parser("renew", help="Renew an open-ended directive")
    renew_parser.add_argument("directive_id", help="Directive ID")

    # evaluate
    eval_parser = subparsers.add_parser("evaluate", help="Run trigger evaluation")
    eval_parser.add_argument("--positions", type=json.loads, help="JSON positions list")
    eval_parser.add_argument("--prices", type=json.loads, help="JSON last_close dict")
    eval_parser.add_argument("--earnings", type=json.loads, help="JSON earnings_calendar dict")
    eval_parser.add_argument("--screener", type=json.loads, help="JSON screener_outputs dict")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "capture":
        return cmd_capture(args)
    elif args.command == "list":
        return cmd_list(args)
    elif args.command == "show":
        return cmd_show(args)
    elif args.command == "override":
        return cmd_override(args)
    elif args.command == "renew":
        return cmd_renew(args)
    elif args.command == "evaluate":
        return cmd_evaluate(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
