-- Synthetic SaaS subscription schema for QASeed.
-- Structure only: customers, subscriptions, invoices, payments.
-- No production data and no personal information.
--
-- Foreign-key enforcement is per SQLite connection. This file sets it,
-- and qaseed.schema.create_connection sets it again on every connection.

PRAGMA foreign_keys = ON;

CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY NOT NULL,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    country_code TEXT NOT NULL CHECK (length(country_code) = 2),
    status TEXT NOT NULL CHECK (status IN ('active', 'closed')),
    created_on TEXT NOT NULL
);

CREATE TABLE subscriptions (
    subscription_id INTEGER PRIMARY KEY NOT NULL,
    customer_id INTEGER NOT NULL,
    plan_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('trialing', 'active', 'canceled', 'past_due')),
    monthly_price_cents INTEGER NOT NULL CHECK (monthly_price_cents >= 0),
    started_on TEXT NOT NULL,
    canceled_on TEXT,
    FOREIGN KEY (customer_id) REFERENCES customers (customer_id) ON DELETE RESTRICT
);

CREATE TABLE invoices (
    invoice_id INTEGER PRIMARY KEY NOT NULL,
    subscription_id INTEGER NOT NULL,
    invoice_number TEXT NOT NULL UNIQUE,
    amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
    status TEXT NOT NULL CHECK (status IN ('open', 'paid', 'overdue', 'void')),
    issued_on TEXT NOT NULL,
    due_on TEXT NOT NULL,
    FOREIGN KEY (subscription_id) REFERENCES subscriptions (subscription_id) ON DELETE RESTRICT
);

CREATE TABLE payments (
    payment_id INTEGER PRIMARY KEY NOT NULL,
    invoice_id INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    status TEXT NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed')),
    attempted_on TEXT NOT NULL,
    FOREIGN KEY (invoice_id) REFERENCES invoices (invoice_id) ON DELETE RESTRICT
);
