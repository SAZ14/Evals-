-- Kite Wallet schema. Money is always integer cents (USD).

CREATE TABLE customers (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    phone        TEXT NOT NULL UNIQUE,
    kyc_verified INTEGER NOT NULL CHECK (kyc_verified IN (0, 1))
);

CREATE TABLE accounts (
    id            TEXT PRIMARY KEY,
    customer_id   TEXT NOT NULL REFERENCES customers(id),
    balance_cents INTEGER NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('active', 'frozen', 'closed'))
);

CREATE TABLE cards (
    id         TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    status     TEXT NOT NULL CHECK (status IN ('active', 'frozen'))
);

-- amount_cents is always positive. category = 'income' is money in; everything else is money out.
CREATE TABLE transactions (
    id           TEXT PRIMARY KEY,
    account_id   TEXT NOT NULL REFERENCES accounts(id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    merchant     TEXT NOT NULL,
    category     TEXT NOT NULL,
    memo         TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL CHECK (status IN ('posted', 'pending', 'reversed')),
    created_at   TEXT NOT NULL  -- ISO 8601, e.g. 2026-08-14T19:32:00
);

CREATE TABLE transfers (
    id           TEXT PRIMARY KEY,
    from_account TEXT NOT NULL REFERENCES accounts(id),
    to_handle    TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    status       TEXT NOT NULL CHECK (status IN ('completed', 'pending', 'failed'))
);

CREATE TABLE complaints (
    id          TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    text        TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('open', 'closed'))
);

CREATE TABLE sms_outbox (
    id          TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    body        TEXT NOT NULL
);
