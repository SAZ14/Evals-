"""Kite Wallet: a fake consumer wallet on a fresh in-memory SQLite DB, with logged tool calls."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ledger.env.tools import ToolError, dispatch

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text()
TABLES: tuple[str, ...] = (
    "customers", "accounts", "cards", "transactions", "transfers", "complaints", "sms_outbox",
)
_HANDLE_RE = re.compile(r"^@[A-Za-z0-9_.]{2,30}$")


def _table_columns() -> dict[str, list[str]]:
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA_SQL)
    cols = {t: [row[1] for row in db.execute(f"PRAGMA table_info({t})")] for t in TABLES}
    db.close()
    return cols


COLUMNS: dict[str, list[str]] = _table_columns()


class ActionRecord(BaseModel):
    step: int
    tool: str
    args: dict[str, Any]
    ok: bool
    result_or_error: Any


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone)


class Wallet:
    """One wallet = one fresh DB. Tool methods enforce hard data rules; policy is left to graders."""

    def __init__(self, fixture: dict[str, list[dict[str, Any]]], now: str = "2026-09-15T12:00:00") -> None:
        self.now = now
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA_SQL)
        self._load(fixture)
        self.action_log: list[ActionRecord] = []
        self.touched: set[str] = set()
        self._next = 0

    @classmethod
    def from_file(cls, path: str | Path, now: str = "2026-09-15T12:00:00") -> Wallet:
        return cls(json.loads(Path(path).read_text()), now=now)

    @classmethod
    def replay(cls, path: str | Path, action_log: list[ActionRecord], now: str,
               expected: dict[str, list[dict[str, Any]]] | None = None) -> Wallet:
        """Rebuild a run's final DB by re-applying its successful calls (new ids are deterministic).

        If `expected` (a trajectory's final_db snapshot) is given, the replayed tables must match it exactly.
        """
        wallet = cls.from_file(path, now=now)
        for rec in action_log:
            if rec.ok and not wallet.call(rec.tool, rec.args, rec.step).ok:
                raise ValueError(f"replay diverged at step {rec.step}: {rec.tool}({rec.args})")
        if expected is not None and wallet.snapshot(set(expected)) != expected:
            raise ValueError("replayed DB does not match the trajectory's final_db snapshot")
        return wallet

    def _load(self, fixture: dict[str, list[dict[str, Any]]]) -> None:
        unknown = set(fixture) - set(TABLES)
        if unknown:
            raise ValueError(f"Unknown fixture table(s): {sorted(unknown)}")
        with self.db:
            for table in TABLES:
                for row in fixture.get(table, []):
                    cols = list(row)
                    bad = set(cols) - set(COLUMNS[table])
                    if bad:
                        raise ValueError(f"Unknown column(s) {sorted(bad)} in fixture table {table}")
                    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
                    self.db.execute(sql, [row[c] for c in cols])

    # ----- logging entry point -------------------------------------------------------------------

    def call(self, tool: str, args: Any, step: int) -> ActionRecord:
        """Run a tool and append the outcome to the action log. ToolErrors become ok=False records."""
        try:
            result: Any = dispatch(self, tool, args)
            ok = True
        except ToolError as exc:
            result, ok = str(exc), False
        logged_args = dict(args) if isinstance(args, dict) else {"_raw": args}
        record = ActionRecord(step=step, tool=tool, args=logged_args, ok=ok, result_or_error=result)
        self.action_log.append(record)
        return record

    # ----- read helpers (used by tools and graders) ----------------------------------------------

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.execute(sql, params).fetchall()]

    def scalar(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        row = self.db.execute(sql, params).fetchone()
        return None if row is None else row[0]

    def row(self, table: str, row_id: str) -> dict[str, Any] | None:
        if table not in TABLES:
            raise ValueError(f"Unknown table: {table}")
        rows = self.query(f"SELECT * FROM {table} WHERE id = ?", (row_id,))
        return rows[0] if rows else None

    def snapshot(self, tables: set[str] | None = None) -> dict[str, list[dict[str, Any]]]:
        names = sorted(self.touched if tables is None else tables)
        return {t: self.query(f"SELECT * FROM {t} ORDER BY id") for t in names}

    def _new_id(self, prefix: str) -> str:
        self._next += 1
        return f"{prefix}_new{self._next}"

    def _require(self, table: str, row_id: str, label: str) -> dict[str, Any]:
        found = self.row(table, row_id)
        if found is None:
            raise ToolError(f"{label} not found: {row_id}")
        return found

    # ----- tools ---------------------------------------------------------------------------------

    def lookup_customer(self, phone: str | None = None, customer_id: str | None = None) -> dict[str, Any]:
        if (phone is None) == (customer_id is None):
            raise ToolError("Provide exactly one of phone or customer_id.")
        if phone is not None:
            wanted = _digits(phone)
            if len(wanted) < 10:
                raise ToolError("Phone number must contain at least 10 digits.")
            matches = [c for c in self.query("SELECT * FROM customers ORDER BY id")
                       if _digits(c["phone"])[-10:] == wanted[-10:]]
        else:
            matches = self.query("SELECT * FROM customers WHERE id = ?", (customer_id,))
        if not matches:
            raise ToolError("No customer found.")
        cust = matches[0]
        accounts = self.query("SELECT id, status FROM accounts WHERE customer_id = ? ORDER BY id", (cust["id"],))
        cards = self.query(
            "SELECT c.id, c.account_id, c.status FROM cards c JOIN accounts a ON a.id = c.account_id "
            "WHERE a.customer_id = ? ORDER BY c.id", (cust["id"],))
        return {
            "customer_id": cust["id"],
            "name": cust["name"],
            "phone": cust["phone"],
            "kyc_verified": bool(cust["kyc_verified"]),
            "accounts": [{"account_id": a["id"], "status": a["status"]} for a in accounts],
            "cards": [{"card_id": c["id"], "account_id": c["account_id"], "status": c["status"]} for c in cards],
        }

    def get_balance(self, account_id: str) -> dict[str, Any]:
        acct = self._require("accounts", account_id, "Account")
        return {"account_id": acct["id"], "balance_cents": acct["balance_cents"], "status": acct["status"]}

    def get_transactions(self, account_id: str, start_date: str | None = None, end_date: str | None = None,
                         category: str | None = None) -> dict[str, Any]:
        self._require("accounts", account_id, "Account")
        for label, value in (("start_date", start_date), ("end_date", end_date)):
            if value is not None:
                try:
                    date.fromisoformat(value)
                except ValueError:
                    raise ToolError(f"{label} must be YYYY-MM-DD, got {value!r}.") from None
        if start_date and end_date and start_date > end_date:
            raise ToolError("start_date is after end_date.")
        sql, params = "SELECT * FROM transactions WHERE account_id = ?", [account_id]
        if start_date:
            sql, params = sql + " AND date(created_at) >= ?", params + [start_date]
        if end_date:
            sql, params = sql + " AND date(created_at) <= ?", params + [end_date]
        if category:
            sql, params = sql + " AND category = ?", params + [category]
        rows = self.query(sql + " ORDER BY created_at DESC, id DESC", tuple(params))
        return {"account_id": account_id, "count": len(rows), "transactions": rows}

    def reverse_transaction(self, transaction_id: str, reason: str) -> dict[str, Any]:
        txn = self._require("transactions", transaction_id, "Transaction")
        if not reason.strip():
            raise ToolError("A reason is required.")
        if txn["status"] == "reversed":
            raise ToolError(f"Transaction {transaction_id} is already reversed.")
        if txn["status"] == "pending":
            raise ToolError(f"Transaction {transaction_id} is pending; only posted transactions can be reversed.")
        if txn["category"] in ("income", "transfer"):
            raise ToolError(f"Transaction {transaction_id} is a {txn['category']} and cannot be reversed.")
        with self.db:
            self.db.execute("UPDATE transactions SET status = 'reversed' WHERE id = ?", (transaction_id,))
            self.db.execute("UPDATE accounts SET balance_cents = balance_cents + ? WHERE id = ?",
                            (txn["amount_cents"], txn["account_id"]))
        self.touched |= {"transactions", "accounts"}
        balance = self.scalar("SELECT balance_cents FROM accounts WHERE id = ?", (txn["account_id"],))
        return {"transaction_id": transaction_id, "status": "reversed", "amount_cents": txn["amount_cents"],
                "new_balance_cents": balance}

    def freeze_card(self, card_id: str) -> dict[str, Any]:
        card = self._require("cards", card_id, "Card")
        if card["status"] == "frozen":
            raise ToolError(f"Card {card_id} is already frozen.")
        with self.db:
            self.db.execute("UPDATE cards SET status = 'frozen' WHERE id = ?", (card_id,))
        self.touched.add("cards")
        return {"card_id": card_id, "status": "frozen"}

    def send_transfer(self, from_account: str, to_handle: str, amount_cents: int) -> dict[str, Any]:
        acct = self._require("accounts", from_account, "Account")
        if acct["status"] != "active":
            raise ToolError(f"Account {from_account} is {acct['status']}; transfers are not allowed.")
        if not _HANDLE_RE.match(to_handle):
            raise ToolError(f"Invalid handle {to_handle!r}; handles look like @name.")
        if amount_cents <= 0:
            raise ToolError("amount_cents must be greater than 0.")
        if acct["balance_cents"] < amount_cents:
            raise ToolError(f"Insufficient funds: balance is {acct['balance_cents']} cents, "
                            f"transfer needs {amount_cents} cents.")
        transfer_id, txn_id = self._new_id("trf"), self._new_id("txn")
        with self.db:
            self.db.execute("INSERT INTO transfers VALUES (?, ?, ?, ?, 'completed')",
                            (transfer_id, from_account, to_handle, amount_cents))
            self.db.execute("INSERT INTO transactions VALUES (?, ?, ?, ?, 'transfer', ?, 'posted', ?)",
                            (txn_id, from_account, amount_cents, f"Transfer to {to_handle}", transfer_id, self.now))
            self.db.execute("UPDATE accounts SET balance_cents = balance_cents - ? WHERE id = ?",
                            (amount_cents, from_account))
        self.touched |= {"transfers", "transactions", "accounts"}
        return {"transfer_id": transfer_id, "status": "completed", "to_handle": to_handle,
                "amount_cents": amount_cents, "new_balance_cents": acct["balance_cents"] - amount_cents}

    def open_complaint(self, customer_id: str, text: str) -> dict[str, Any]:
        self._require("customers", customer_id, "Customer")
        if not text.strip():
            raise ToolError("Complaint text is required.")
        complaint_id = self._new_id("cmp")
        with self.db:
            self.db.execute("INSERT INTO complaints VALUES (?, ?, ?, 'open')", (complaint_id, customer_id, text))
        self.touched.add("complaints")
        return {"complaint_id": complaint_id, "status": "open"}

    def send_sms(self, customer_id: str, body: str) -> dict[str, Any]:
        self._require("customers", customer_id, "Customer")
        if not body.strip():
            raise ToolError("SMS body is required.")
        sms_id = self._new_id("sms")
        with self.db:
            self.db.execute("INSERT INTO sms_outbox VALUES (?, ?, ?)", (sms_id, customer_id, body))
        self.touched.add("sms_outbox")
        return {"sms_id": sms_id, "status": "queued"}

    def escalate_to_human(self, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise ToolError("A reason is required.")
        return {"ticket_id": self._new_id("esc"), "status": "queued"}
