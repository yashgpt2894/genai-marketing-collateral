"""
Request authentication — app-level Google ID-token verification.

  AUTH_MODE=none   (dev/tests): no token required; principal = 'anonymous@local'.
  AUTH_MODE=google (production): every call must carry
      Authorization: Bearer <google-id-token>
  which is verified against Google's public certs (signature, expiry, and the
  expected audience). The caller's email becomes the authenticated principal.
  Missing/invalid -> 401.

On Cloud Run this pairs with --no-allow-unauthenticated, so the platform IAM also
checks the token (defense in depth). Declared via HTTPBearer so the requirement
shows up in the OpenAPI spec for every protected route.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings

log = logging.getLogger("collateral.auth")

_bearer = HTTPBearer(auto_error=False, description="Google-issued ID token")


def require_identity(creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> str:
    """FastAPI dependency. Returns the authenticated principal (email).

    AUTH_MODE=none -> always 'anonymous@local'. AUTH_MODE=google -> verifies the
    bearer token as a Google ID token and returns its email, else 401.
    """
    s = get_settings()
    if s.auth_mode.lower() != "google":
        return "anonymous@local"  # auth disabled for local dev / tests

    if creds is None or not creds.credentials:
        raise HTTPException(status_code=401, detail="missing bearer token (Google ID token required)")
    try:
        from google.auth.transport import requests as ga_requests
        from google.oauth2 import id_token
        claims = id_token.verify_oauth2_token(
            creds.credentials, ga_requests.Request(), audience=s.auth_audience or None
        )
    except Exception as e:  # invalid signature / expired / wrong audience
        raise HTTPException(status_code=401, detail=f"invalid identity token: {e}")

    return claims.get("email") or claims.get("sub") or "unknown"
