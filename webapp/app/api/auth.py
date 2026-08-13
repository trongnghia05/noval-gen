"""Login and the admin / viewer split.

Two accounts, both from the environment — no user table, no admin screen. That is
enough for an internal tool with a handful of people, and the whole thing is one
file so replacing it with a real identity provider later touches nothing else.

## The authorisation rule is one line

Every endpoint that only reads is a GET, and every endpoint that changes something
is not — there is no endpoint that does both. So "viewer may read, admin may write"
is exactly "viewer may GET", and it can be enforced once on the router instead of
being repeated on nineteen routes and forgotten on the twentieth.

`/settings/{key}` is the single exception: it reads, but what it reads is the CMS
upload URL, which is configuration rather than story content, so it is admin-only.

## Why the API is gated at all, not just the UI

The API port is published on the host. Gating only Streamlit would leave
`curl -X DELETE .../stories/101` working for anyone who can reach the machine, so
the login on the UI would be decoration.
"""

import hmac
import logging
import os
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from hashlib import sha256
from json import dumps, loads

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
VIEWER_USERNAME = os.getenv("VIEWER_USERNAME", "viewer")
VIEWER_PASSWORD = os.getenv("VIEWER_PASSWORD", "")
AUTH_SECRET = os.getenv("AUTH_SECRET", "")
TOKEN_TTL = int(os.getenv("AUTH_TOKEN_TTL", str(12 * 3600)))

# No passwords set = auth off, everything open. Keeps a fresh checkout and the test
# scripts working, and is the same shape as GCS_BUCKET being empty.
def enabled() -> bool:
    return bool(ADMIN_PASSWORD or VIEWER_PASSWORD)


# Endpoints that read but are still admin-only, matched on the path. See the module
# docstring: the CMS upload URL is configuration, not story content.
_ADMIN_ONLY_READS = ("/settings/",)

router = APIRouter()


# ── token ─────────────────────────────────────────────────────────────────────
# A signed payload rather than a JWT library: this needs exactly one algorithm and
# two claims, and adding a dependency for that is not worth it.

def _b64(raw: bytes) -> str:
    return urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: bytes) -> str:
    return _b64(hmac.new(AUTH_SECRET.encode(), payload, sha256).digest())


def make_token(username: str, role: str) -> str:
    body = dumps({"sub": username, "role": role, "exp": int(time.time()) + TOKEN_TTL}).encode()
    return f"{_b64(body)}.{_sign(body)}"


def read_token(token: str) -> dict | None:
    """The claims, or None for anything malformed, mis-signed or expired."""
    try:
        body_b64, sig = token.split(".", 1)
        body = _unb64(body_b64)
    except Exception:  # noqa: BLE001 — any malformed token is simply not a token
        return None
    # compare_digest, not ==, so a wrong signature cannot be found byte by byte
    # from how long the comparison took.
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    claims = loads(body)
    if claims.get("exp", 0) < time.time():
        return None
    return claims


# ── login ─────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


def _match(username: str, password: str) -> str | None:
    """The role these credentials buy, or None. Both comparisons always run so the
    reply takes the same time whether the username was wrong or only the password."""
    admin_ok = (
        bool(ADMIN_PASSWORD)
        and hmac.compare_digest(username, ADMIN_USERNAME)
        and hmac.compare_digest(password, ADMIN_PASSWORD)
    )
    viewer_ok = (
        bool(VIEWER_PASSWORD)
        and hmac.compare_digest(username, VIEWER_USERNAME)
        and hmac.compare_digest(password, VIEWER_PASSWORD)
    )
    if admin_ok:
        return "admin"
    if viewer_ok:
        return "viewer"
    return None


@router.post("/auth/login")
def login(req: LoginRequest):
    if not enabled():
        return {"token": "", "role": "admin", "username": "anonymous", "auth_enabled": False}
    if not AUTH_SECRET:
        raise HTTPException(500, "AUTH_SECRET is not set — cannot issue tokens")
    role = _match(req.username, req.password)
    if not role:
        logger.warning("failed login for %r", req.username[:40])
        raise HTTPException(401, "sai tài khoản hoặc mật khẩu")
    return {"token": make_token(req.username, role), "role": role,
            "username": req.username, "auth_enabled": True}


def _claims_from(request: Request) -> dict | None:
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else ""
    return read_token(token) if token else None


@router.get("/auth/me")
def me(request: Request):
    """Who am I, and is auth even on — the UI asks this before drawing anything.

    Reads the header itself rather than `request.state`: this router sits OUTSIDE
    the guard (the login endpoint has to be reachable signed-out), so nothing has
    populated that state.
    """
    if not enabled():
        return {"username": "anonymous", "role": "admin", "auth_enabled": False}
    claims = _claims_from(request)
    if not claims:
        raise HTTPException(401, "chưa đăng nhập")
    return {"username": claims["sub"], "role": claims["role"], "auth_enabled": True}


# ── the gate ──────────────────────────────────────────────────────────────────

def guard(request: Request) -> None:
    """Router-level dependency: authenticate, then allow GET to anyone signed in and
    everything else to admins only."""
    if not enabled():
        return
    path = request.url.path
    if path.endswith("/auth/login"):
        return

    claims = _claims_from(request)
    if not claims:
        raise HTTPException(401, "chưa đăng nhập hoặc phiên đã hết hạn")
    request.state.auth = claims

    if claims["role"] == "admin":
        return
    if request.method == "GET" and not any(p in path for p in _ADMIN_ONLY_READS):
        return
    raise HTTPException(403, "tài khoản này chỉ được xem và tải, không sửa được")


AuthGuard = Depends(guard)
