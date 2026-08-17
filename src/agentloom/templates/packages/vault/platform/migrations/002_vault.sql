-- Credential vault (agentloom package: vault).
-- Secrets are stored AES-256-GCM encrypted; the master key comes from the
-- VAULT_KEY env var and is never persisted. Every decrypt is audit-logged.

CREATE TABLE IF NOT EXISTS vault_credentials (
    service TEXT PRIMARY KEY,
    encrypted BLOB NOT NULL,
    nonce BLOB NOT NULL,
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_decrypted_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS vault_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT,
    at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_vault_audit_service ON vault_audit(service, id DESC);
