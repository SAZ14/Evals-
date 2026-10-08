"""Tool definitions (JSON schema, provider-neutral) and argument-checked dispatch."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ledger.env.wallet import Wallet


class ToolError(Exception):
    """A hard rule was violated. The message is returned to the agent as the tool error."""


def _spec(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


_STR = {"type": "string"}
_INT = {"type": "integer"}

TOOL_SPECS: list[dict[str, Any]] = [
    _spec(
        "lookup_customer",
        "Find a customer by phone number or customer id (give exactly one). Returns the customer's "
        "name, kyc_verified flag, accounts and cards.",
        {"phone": {**_STR, "description": "Phone number in any format."}, "customer_id": _STR},
        [],
    ),
    _spec(
        "get_balance",
        "Get the current balance (integer cents) and status of an account.",
        {"account_id": _STR},
        ["account_id"],
    ),
    _spec(
        "get_transactions",
        "List an account's transactions, newest first. Dates are inclusive, format YYYY-MM-DD. "
        "amount_cents is always positive: category 'income' is money received, every other row is money spent.",
        {
            "account_id": _STR,
            "start_date": {**_STR, "description": "YYYY-MM-DD, inclusive."},
            "end_date": {**_STR, "description": "YYYY-MM-DD, inclusive."},
            "category": {**_STR, "description": "Exact category, e.g. food, groceries, transport."},
        },
        ["account_id"],
    ),
    _spec(
        "reverse_transaction",
        "Reverse a posted card transaction and credit the amount back to the account.",
        {"transaction_id": _STR, "reason": _STR},
        ["transaction_id", "reason"],
    ),
    _spec("freeze_card", "Freeze a card so it can no longer be used.", {"card_id": _STR}, ["card_id"]),
    _spec(
        "send_transfer",
        "Send money from an account to a wallet handle such as @ali_k99.",
        {"from_account": _STR, "to_handle": _STR, "amount_cents": {**_INT, "description": "Integer cents, > 0."}},
        ["from_account", "to_handle", "amount_cents"],
    ),
    _spec(
        "open_complaint",
        "Open a complaint or dispute case for a customer.",
        {"customer_id": _STR, "text": _STR},
        ["customer_id", "text"],
    ),
    _spec("send_sms", "Send an SMS to the customer's registered phone.", {"customer_id": _STR, "body": _STR},
          ["customer_id", "body"]),
    _spec(
        "escalate_to_human",
        "Hand the case to a human support agent, with the reason.",
        {"reason": _STR},
        ["reason"],
    ),
]

TOOL_NAMES: tuple[str, ...] = tuple(spec["name"] for spec in TOOL_SPECS)
_BY_NAME = {spec["name"]: spec for spec in TOOL_SPECS}

# Tools that change state (or hand the case off). Used by graders for `action_done` claims.
ACTION_TOOLS: frozenset[str] = frozenset(
    {"reverse_transaction", "freeze_card", "send_transfer", "open_complaint", "send_sms", "escalate_to_human"}
)


def validate_args(name: str, args: Any) -> dict[str, Any]:
    """Check args against the tool's JSON schema (strings and integers only). Raise ToolError if bad."""
    if not isinstance(args, dict):
        raise ToolError("Arguments must be a JSON object.")
    params = _BY_NAME[name]["parameters"]
    props: dict[str, Any] = params["properties"]
    unknown = sorted(set(args) - set(props))
    if unknown:
        raise ToolError(f"Unknown argument(s) for {name}: {', '.join(unknown)}.")
    missing = [key for key in params["required"] if args.get(key) is None]
    if missing:
        raise ToolError(f"Missing required argument(s) for {name}: {', '.join(missing)}.")
    clean: dict[str, Any] = {}
    for key, value in args.items():
        if value is None:
            continue
        expected = props[key]["type"]
        if expected == "string":
            if not isinstance(value, str):
                raise ToolError(f"Argument '{key}' must be a string.")
        elif expected == "integer":
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ToolError(f"Argument '{key}' must be an integer.")
        clean[key] = value
    return clean


def dispatch(wallet: Wallet, name: str, args: Any) -> Any:
    """Run one tool against the wallet. Raises ToolError for unknown tools, bad args or rule violations."""
    if name not in _BY_NAME:
        raise ToolError(f"Unknown tool: {name}.")
    return getattr(wallet, name)(**validate_args(name, args))
