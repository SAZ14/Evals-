from typing import Any

import pytest

from ledger.env.tools import TOOL_NAMES, TOOL_SPECS
from ledger.env.wallet import Wallet


def tiny_fixture() -> dict[str, list[dict[str, Any]]]:
    return {
        "customers": [
            {"id": "cust_1", "name": "Ayesha Khan", "phone": "+923001234567", "kyc_verified": 1},
            {"id": "cust_2", "name": "No KYC", "phone": "+14155550100", "kyc_verified": 0},
        ],
        "accounts": [
            {"id": "acc_1", "customer_id": "cust_1", "balance_cents": 100_000, "status": "active"},
            {"id": "acc_2", "customer_id": "cust_2", "balance_cents": 5_000, "status": "frozen"},
        ],
        "cards": [{"id": "card_1", "account_id": "acc_1", "status": "active"}],
        "transactions": [
            {"id": "txn_1", "account_id": "acc_1", "amount_cents": 2_500, "merchant": "Clifton Chai Spot",
             "category": "food", "memo": "", "status": "posted", "created_at": "2026-08-03T09:10:00"},
            {"id": "txn_2", "account_id": "acc_1", "amount_cents": 1_000, "merchant": "Rickshaw Rides",
             "category": "transport", "memo": "", "status": "pending", "created_at": "2026-09-14T18:00:00"},
            {"id": "txn_3", "account_id": "acc_1", "amount_cents": 700, "merchant": "Burns Road Nihari",
             "category": "food", "memo": "", "status": "reversed", "created_at": "2026-08-31T23:30:00"},
            {"id": "txn_4", "account_id": "acc_1", "amount_cents": 90_000, "merchant": "Payout",
             "category": "income", "memo": "", "status": "posted", "created_at": "2026-08-01T08:00:00"},
        ],
    }


@pytest.fixture
def wallet() -> Wallet:
    return Wallet(tiny_fixture())


def test_tool_specs_cover_nine_tools() -> None:
    assert len(TOOL_SPECS) == 9
    assert "escalate_to_human" in TOOL_NAMES


def test_lookup_by_phone_normalises_formats(wallet: Wallet) -> None:
    rec = wallet.call("lookup_customer", {"phone": "0300-123 4567"}, step=1)
    assert rec.ok and rec.result_or_error["customer_id"] == "cust_1"
    assert rec.result_or_error["kyc_verified"] is True
    assert rec.result_or_error["accounts"] == [{"account_id": "acc_1", "status": "active"}]
    assert not wallet.call("lookup_customer", {"phone": "0300 0000000"}, step=1).ok
    assert not wallet.call("lookup_customer", {"phone": "1", "customer_id": "cust_1"}, step=1).ok


def test_reverse_rules(wallet: Wallet) -> None:
    ok = wallet.call("reverse_transaction", {"transaction_id": "txn_1", "reason": "dup"}, step=1)
    assert ok.ok and ok.result_or_error["new_balance_cents"] == 102_500
    again = wallet.call("reverse_transaction", {"transaction_id": "txn_1", "reason": "dup"}, step=2)
    assert not again.ok and "already reversed" in again.result_or_error
    pending = wallet.call("reverse_transaction", {"transaction_id": "txn_2", "reason": "x"}, step=3)
    assert not pending.ok and "pending" in pending.result_or_error
    income = wallet.call("reverse_transaction", {"transaction_id": "txn_4", "reason": "x"}, step=4)
    assert not income.ok
    assert wallet.row("transactions", "txn_1")["status"] == "reversed"
    assert wallet.row("accounts", "acc_1")["balance_cents"] == 102_500


def test_transfer_rules(wallet: Wallet) -> None:
    too_much = wallet.call("send_transfer", {"from_account": "acc_1", "to_handle": "@ali", "amount_cents": 200_000}, 1)
    assert not too_much.ok and "Insufficient funds" in too_much.result_or_error
    frozen = wallet.call("send_transfer", {"from_account": "acc_2", "to_handle": "@ali", "amount_cents": 100}, 1)
    assert not frozen.ok
    bad_handle = wallet.call("send_transfer", {"from_account": "acc_1", "to_handle": "ali", "amount_cents": 100}, 1)
    assert not bad_handle.ok
    ok = wallet.call("send_transfer", {"from_account": "acc_1", "to_handle": "@ali_k99", "amount_cents": 30_000}, 2)
    assert ok.ok
    assert wallet.row("accounts", "acc_1")["balance_cents"] == 70_000
    transfer = wallet.row("transfers", ok.result_or_error["transfer_id"])
    assert transfer["status"] == "completed" and transfer["to_handle"] == "@ali_k99"
    assert {"transfers", "transactions", "accounts"} <= wallet.touched


def test_card_complaint_sms_escalate(wallet: Wallet) -> None:
    assert wallet.call("freeze_card", {"card_id": "card_1"}, 1).ok
    assert not wallet.call("freeze_card", {"card_id": "card_1"}, 2).ok
    assert wallet.call("open_complaint", {"customer_id": "cust_1", "text": "Double charge"}, 3).ok
    assert not wallet.call("open_complaint", {"customer_id": "cust_9", "text": "x"}, 3).ok
    assert wallet.call("send_sms", {"customer_id": "cust_1", "body": "Hello"}, 4).ok
    assert wallet.call("escalate_to_human", {"reason": "Over limit"}, 5).ok
    assert not wallet.call("escalate_to_human", {"reason": "  "}, 5).ok


def test_get_transactions_filters(wallet: Wallet) -> None:
    out = wallet.call("get_transactions", {"account_id": "acc_1", "start_date": "2026-08-01",
                                           "end_date": "2026-08-31", "category": "food"}, 1)
    assert out.ok and [t["id"] for t in out.result_or_error["transactions"]] == ["txn_3", "txn_1"]
    assert not wallet.call("get_transactions", {"account_id": "acc_1", "start_date": "Aug 1"}, 1).ok


def test_bad_arguments_and_unknown_tool(wallet: Wallet) -> None:
    assert not wallet.call("get_balance", {}, 1).ok
    assert not wallet.call("get_balance", {"account_id": "acc_1", "extra": 1}, 1).ok
    assert not wallet.call("send_transfer", {"from_account": "acc_1", "to_handle": "@a", "amount_cents": "5"}, 1).ok
    assert wallet.call("send_transfer", {"from_account": "acc_1", "to_handle": "@abc", "amount_cents": 5.0}, 1).ok
    assert not wallet.call("delete_everything", {}, 1).ok
    assert not wallet.call("get_balance", "not a dict", 1).ok


def test_action_log_records_every_call(wallet: Wallet) -> None:
    wallet.call("get_balance", {"account_id": "acc_1"}, step=1)
    wallet.call("get_balance", {"account_id": "nope"}, step=2)
    assert [(r.step, r.tool, r.ok) for r in wallet.action_log] == [(1, "get_balance", True), (2, "get_balance", False)]
    assert wallet.action_log[0].result_or_error["balance_cents"] == 100_000
    assert "not found" in wallet.action_log[1].result_or_error


def test_fresh_db_per_wallet() -> None:
    first = Wallet(tiny_fixture())
    first.call("reverse_transaction", {"transaction_id": "txn_1", "reason": "dup"}, 1)
    second = Wallet(tiny_fixture())
    assert second.row("transactions", "txn_1")["status"] == "posted"
    assert second.action_log == []


def test_snapshot_only_touched_tables(wallet: Wallet) -> None:
    assert wallet.snapshot() == {}
    wallet.call("freeze_card", {"card_id": "card_1"}, 1)
    assert list(wallet.snapshot()) == ["cards"]


def test_fixture_rejects_unknown_tables_and_columns() -> None:
    with pytest.raises(ValueError):
        Wallet({"wallets": []})
    with pytest.raises(ValueError):
        Wallet({"customers": [{"id": "c", "name": "x", "phone": "1", "kyc_verified": 1, "email": "x"}]})
