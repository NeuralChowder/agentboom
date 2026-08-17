"""Vault mini-app — encrypted credential store (agentloom package: vault).

Design:
- AES-256-GCM with a per-secret random nonce; master key from VAULT_KEY
  env only (never persisted, never returned by any endpoint).
- Every decrypt is written to vault_audit — the trail IS the feature.
- Listing never includes secret material.

Endpoints (mounted at /api/vault/):
  GET    /credentials             list services (no secrets)
  PUT    /credentials/{service}   {secret, note?} store or update
  GET    /credentials/{service}   {service, secret} (audit-logged)
  DELETE /credentials/{service}
  POST   /credentials/{service}/rotate   same body as PUT
  GET    /audit?service=&limit=
"""
import logging
import os
import secrets as pysecrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agentloom_sdk import db

log = logging.getLogger("miniapps.vault")

router = APIRouter()

_KEY_LEN_BYTES = 32
_NONCE_LEN_BYTES = 12


def _key() -> bytes | None:
    raw = os.environ.get("VAULT_KEY", "").strip()
    if not raw:
        return None
    try:
        key = bytes.fromhex(raw)
    except ValueError:
        log.error("VAULT_KEY is not valid hex")
        return None
    if len(key) != _KEY_LEN_BYTES:
        log.error("VAULT_KEY must be %d bytes (%d hex chars)", _KEY_LEN_BYTES, _KEY_LEN_BYTES * 2)
        return None
    return key


def _no_key_response():
    return JSONResponse(
        {"error": "vault disabled: VAULT_KEY not set or invalid (expected `openssl rand -hex 32`)"},
        status_code=503,
    )


async def _audit(service: str, action: str, detail: str = "") -> None:
    await db.execute(
        "INSERT INTO vault_audit (service, action, detail) VALUES (?, ?, ?)",
        (service, action, detail),
    )


@router.get("/health")
async def health():
    return {"status": "ok", "app": "vault", "enabled": _key() is not None}


@router.get("/credentials")
async def list_credentials():
    rows = await db.fetchall(
        "SELECT service, note, created_at, updated_at, last_decrypted_at "
        "FROM vault_credentials ORDER BY service"
    )
    return {"credentials": rows}


@router.put("/credentials/{service}")
async def store_credential(service: str, payload: dict):
    key = _key()
    if key is None:
        return _no_key_response()
    secret = payload.get("secret")
    if not secret or not isinstance(secret, str):
        return JSONResponse({"error": "secret (string) is required"}, status_code=400)

    nonce = pysecrets.token_bytes(_NONCE_LEN_BYTES)
    encrypted = AESGCM(key).encrypt(nonce, secret.encode("utf-8"), service.encode("utf-8"))
    await db.execute(
        """
        INSERT INTO vault_credentials (service, encrypted, nonce, note)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(service) DO UPDATE SET
            encrypted = excluded.encrypted,
            nonce = excluded.nonce,
            note = COALESCE(excluded.note, vault_credentials.note),
            updated_at = CURRENT_TIMESTAMP
        """,
        (service, encrypted, nonce, payload.get("note")),
    )
    await _audit(service, "store")
    log.info("vault: stored credential for %s", service)
    return {"ok": True, "service": service}


@router.get("/credentials/{service}")
async def read_credential(service: str):
    key = _key()
    if key is None:
        return _no_key_response()
    row = await db.fetchone(
        "SELECT encrypted, nonce FROM vault_credentials WHERE service = ?",
        (service,),
    )
    if row is None:
        return JSONResponse({"error": f"no credential for '{service}'"}, status_code=404)
    try:
        secret = AESGCM(key).decrypt(bytes(row["nonce"]), bytes(row["encrypted"]),
                                     service.encode("utf-8")).decode("utf-8")
    except Exception:  # noqa: BLE001 — wrong key or corruption must not leak detail
        await _audit(service, "decrypt-failed")
        return JSONResponse({"error": "decryption failed (wrong VAULT_KEY?)"}, status_code=500)
    await db.execute(
        "UPDATE vault_credentials SET last_decrypted_at = CURRENT_TIMESTAMP WHERE service = ?",
        (service,),
    )
    await _audit(service, "decrypt")
    return {"service": service, "secret": secret}


@router.post("/credentials/{service}/rotate")
async def rotate_credential(service: str, payload: dict):
    result = await store_credential(service, payload)
    if result.status_code == 200:
        await _audit(service, "rotate")
    return result


@router.delete("/credentials/{service}")
async def delete_credential(service: str):
    removed = await db.execute("DELETE FROM vault_credentials WHERE service = ?", (service,))
    if removed:
        await _audit(service, "delete")
    return {"deleted": bool(removed)}


@router.get("/audit")
async def audit_trail(service: str = "", limit: int = 100):
    if service:
        rows = await db.fetchall(
            "SELECT service, action, detail, at FROM vault_audit "
            "WHERE service = ? ORDER BY id DESC LIMIT ?", (service, limit),
        )
    else:
        rows = await db.fetchall(
            "SELECT service, action, detail, at FROM vault_audit ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    return {"audit": rows}


def get_router() -> APIRouter:
    return router
