"""Official Sign in with ChatGPT for a single-user loopback installation."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from urllib.parse import urlencode
from uuid import uuid4

import requests
from filelock import FileLock

from .archive import _atomic_write
from .profile import profile_path

ISSUER = "https://auth.openai.com"
TOKEN = f"{ISSUER}/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
GRANT = "chatgpt.tokens.use.direct"
DYNAMIC = "dynamic_agent_client"
CALLBACK = "/auth/callback"
APP_NAME = "Morning Deal Brief"
_TERMINAL = {"invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
             "refresh_token_invalidated", "refresh_token_reused"}


class ChatGPTError(RuntimeError):
    def __init__(self, code="unavailable"):
        self.code = code
        if code in _TERMINAL or code == "subscription_sharing_invalid_user":
            self.brief_status = "reconnect"
        elif code in {"subscription_sharing_usage_limit_exceeded", "rate_limit_exceeded"}:
            self.brief_status = "limit"
        elif code in {"subscription_sharing_user_not_eligible", "chatpass_v2_scope_not_authorized"}:
            self.brief_status = "ineligible"
        else:
            self.brief_status = "unavailable"
        super().__init__(self.brief_status)


def _error_code(payload):
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    return error.get("code") if isinstance(error, dict) else error if isinstance(error, str) else None


def _post(url, **data):
    try:
        with requests.post(url, data=data, timeout=(4, 8), allow_redirects=False) as response:
            if not response.ok or response.status_code != 200:
                try:
                    code = _error_code(response.json())
                except ValueError:
                    code = None
                raise ChatGPTError(code)
            return response.json() if response.content else {}
    except (ValueError, requests.RequestException):
        raise ChatGPTError() from None


def _model_list(token):
    try:
        with requests.get(f"{RESOURCE}/models", headers={"Authorization": f"Bearer {token}"},
                          timeout=(4, 6), allow_redirects=False) as response:
            payload = response.json()
            if response.status_code != 200:
                raise ChatGPTError(_error_code(payload))
    except (ValueError, requests.RequestException):
        raise ChatGPTError() from None
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        raise ChatGPTError()
    return [{"slug": row["slug"], "name": row.get("display_name") or row["slug"]}
            for row in models if isinstance(row, dict) and isinstance(row.get("slug"), str)
            and row.get("visibility") == "list"]


def _verify(token, client, nonce):
    import jwt

    try:
        key = jwt.PyJWKClient(f"{ISSUER}/.well-known/jwks.json", timeout=6).get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=client, issuer=ISSUER,
                            leeway=20, options={"require": ["exp", "sub", "nonce", "iat"]})
        if not secrets.compare_digest(str(claims["nonce"]).encode(), nonce.encode()):
            raise ChatGPTError()
        aud = claims["aud"]
        if (isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != client) or (
                "azp" in claims and claims["azp"] != client):
            raise ChatGPTError()
        return claims
    except (jwt.PyJWTError, OSError, TypeError, ValueError):
        raise ChatGPTError() from None


def _tokens(result, previous=None):
    previous = previous or {}
    if not isinstance(result, dict):
        raise ChatGPTError()
    scopes = result.get("scope", previous.get("scope", ""))
    if not isinstance(scopes, str) or GRANT not in scopes.split():
        raise ChatGPTError("chatpass_v2_scope_not_authorized")
    refresh = result.get("refresh_token") or previous.get("refresh_token")
    access = result.get("access_token")
    expires = result.get("expires_in")
    if (not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh
            or type(expires) is not int or expires <= 0
            or str(result.get("token_type", "")).lower() != "bearer"):
        raise ChatGPTError()
    # A refreshed ID token is unnecessary for an active session. Keep the verified
    # original for reauthorization rather than trusting an unverified identity hint.
    return {"access_token": access, "refresh_token": refresh, "scope": scopes, "expires_at": time.time() + expires}


class Connection:
    def __init__(self, settings):
        path = profile_path(settings).resolve()
        self.service = f"Morning Deal Brief {hashlib.sha256(str(path).encode()).hexdigest()[:24]}"
        self.path = path.with_name(path.name + ".chatgpt.enc")
        self.lock = FileLock(str(self.path) + ".lock", timeout=12)

    def _vault(self):
        import keyring

        vault = keyring.get_keyring()
        # Keep tokens in a platform credential vault. Some keyring plugins store them in plaintext.
        if vault.__class__.__module__ not in {
            "keyring.backends.Windows", "keyring.backends.macOS", "keyring.backends.SecretService",
            "keyring.backends.kwallet", "keyring.backends.libsecret",
        }:
            raise ChatGPTError("vault")
        return vault

    def _load(self):
        # An OS vault protects the encryption key. The file can hold rotating JWTs
        # and several accounts without exceeding Windows Credential Manager limits.
        from cryptography.fernet import Fernet

        vault = self._vault()
        if not self.path.exists():
            return {"active": None, "accounts": {}}
        key = vault.get_password(self.service, "encryption")
        if not key:
            raise ChatGPTError("vault")
        return json.loads(Fernet(key.encode()).decrypt(self.path.read_bytes()))

    def _save(self, state):
        from cryptography.fernet import Fernet

        vault = self._vault()
        key = vault.get_password(self.service, "encryption")
        if not key:
            key = Fernet.generate_key().decode()
            vault.set_password(self.service, "encryption", key)
        serialized = json.dumps(state, ensure_ascii=False).encode()
        _atomic_write(self.path, Fernet(key.encode()).encrypt(serialized).decode())

    def public_state(self):
        try:
            state = self._load()
            return {
                "available": True, "active": state["active"],
                "accounts": [{"id": client, "label": row["label"], "connected": bool(row.get("refresh_token"))}
                             for client, row in state["accounts"].items()],
            }
        except Exception:
            return {"available": False, "active": None, "accounts": []}

    def start(self, callback, client_id=None):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            state = self._load()
            if "host" not in state:
                state["host"] = f"urn:uuid:{uuid4()}"
                self._save(state)
            account = state["accounts"].get(client_id)
            if client_id and account is None:
                raise ChatGPTError()
        verifier = secrets.token_urlsafe(50)
        pending = {"state": secrets.token_urlsafe(32), "nonce": secrets.token_urlsafe(32),
                   "verifier": verifier, "client": client_id, "redirect_uri": callback, "started": time.monotonic()}
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        query = {
            "client_id": client_id or DYNAMIC, "ext_agent_host_id": state["host"], "response_type": "code",
            "redirect_uri": callback, "scope": f"openid profile email offline_access resource.invoke {GRANT}",
            "resource": RESOURCE, "state": pending["state"], "nonce": pending["nonce"],
            "code_challenge_method": "S256", "code_challenge": challenge,
        }
        if not client_id:
            query["agent_name_hint"] = APP_NAME
        elif account.get("id_token"):
            query["id_token_hint"] = account["id_token"]
        return f"{ISSUER}/api/accounts/authorize?{urlencode(query)}", pending

    def finish(self, pending, query):
        if query.get("error"):
            raise ChatGPTError("denied")
        issued = query.get("client_id") or pending["client"]
        if not issued or not re.fullmatch(r"oaiapp_[A-Za-z0-9_-]{1,150}", issued):
            raise ChatGPTError()
        if pending["client"] and issued != pending["client"]:
            raise ChatGPTError()
        code = query.get("code")
        if not code or len(code) > 4096:
            raise ChatGPTError()
        result = _post(TOKEN, grant_type="authorization_code", client_id=issued, code=code,
                       code_verifier=pending["verifier"], redirect_uri=pending["redirect_uri"], resource=RESOURCE)
        tokens = _tokens(result)
        id_token = result.get("id_token")
        if not isinstance(id_token, str):
            raise ChatGPTError()
        claims = _verify(id_token, issued, pending["nonce"])
        with self.lock:
            state = self._load()
            old = state["accounts"].get(issued)
            if old and old["subject"] != claims["sub"]:
                raise ChatGPTError()
            first = not old
            state["accounts"][issued] = {
                **tokens, "id_token": id_token, "subject": claims["sub"],
                "label": str(claims.get("email") or claims.get("name") or "ChatGPT account")[:140],
            }
            state["active"] = issued
            self._save(state)
        return first

    def sign_out(self):
        with self.lock:
            state = self._load()
            client = state["active"]
            if not client:
                return True
            account = state["accounts"][client]
            confirmed = True
            if token := account.get("refresh_token"):
                for delay in (0, 0.4):
                    time.sleep(delay)
                    try:
                        _post(f"{ISSUER}/api/accounts/oauth/revoke", token=token,
                              token_type_hint="refresh_token", client_id=client)
                        confirmed = True
                        break
                    except ChatGPTError:
                        confirmed = False
            state["active"] = None
            state["accounts"][client] = {"subject": account["subject"], "label": account["label"]}
            self._save(state)
            return confirmed

    def _access(self, *, force=False, stale_token=None):
        with self.lock:
            state = self._load()
            client = state["active"]
            if not client:
                return None
            account = state["accounts"][client]
            if not account.get("refresh_token"):
                raise ChatGPTError("invalid_grant")
            if account["expires_at"] > time.time() + 90 and (
                    not force or account["access_token"] != stale_token):
                return account["access_token"]
            try:
                result = _post(TOKEN, grant_type="refresh_token", client_id=client,
                               refresh_token=account["refresh_token"], resource=RESOURCE)
                account.update(_tokens(result, account))
            except ChatGPTError as exc:
                if exc.code in _TERMINAL:
                    state["accounts"][client] = {"subject": account["subject"], "label": account["label"]}
                    self._save(state)
                raise
            self._save(state)
            return account["access_token"]

    def generator(self):
        if not self._load()["active"]:
            return None
        return self.generate

    def generate(self, instructions, evidence):
        token = self._access()
        if not token:
            raise ChatGPTError("invalid_grant")
        try:
            return self._generate(token, instructions, evidence)
        except ChatGPTError as exc:
            if exc.code != "subscription_sharing_invalid_user":
                raise
        token = self._access(force=True, stale_token=token)
        if not token:
            raise ChatGPTError("invalid_grant")
        return self._generate(token, instructions, evidence)

    @staticmethod
    def _generate(token, instructions, evidence):
        models = _model_list(token)
        preferred = ("gpt-6.2-sol", "gpt-6.1-sol")
        available = [m["slug"] for m in models]
        model = next((m for m in preferred if m in available), available[0] if available else None)
        if not model:
            raise ChatGPTError("subscription_sharing_user_not_eligible")
        started = time.monotonic()
        segments, total = [], 0
        payload = {"model": model, "input": [{"role": "user", "content": evidence}],
                   "instructions": instructions, "store": False, "stream": True}
        try:
            with requests.post(f"{RESOURCE}/responses", headers={"Authorization": f"Bearer {token}"},
                               json=payload, stream=True, timeout=(5, 18), allow_redirects=False) as response:
                if response.status_code != 200:
                    raise ChatGPTError(_error_code(response.json()))
                for line in response.iter_lines(chunk_size=64):
                    if time.monotonic() - started > 75:  # a full brief (note + 13 panel reads) needs ~40-60s of output
                        raise ChatGPTError()
                    if not line.startswith(b"data:"):
                        continue
                    if line[5:].strip() == b"[DONE]":
                        break
                    event = json.loads(line[5:])
                    kind = event.get("type")
                    if kind == "response.output_text.delta":
                        piece = event.get("delta", "")
                        total += len(piece)
                        if total > 16_384:
                            raise ChatGPTError()
                        segments.append(piece)
                    elif kind == "response.completed":
                        result = json.loads("".join(segments))
                        if not isinstance(result, dict):
                            raise ChatGPTError()
                        return result
                    elif kind in {"error", "response.failed", "response.incomplete"}:
                        raise ChatGPTError(event.get("code") or _error_code(event.get("response") or event))
        except (ValueError, requests.RequestException):
            raise ChatGPTError() from None
        raise ChatGPTError()
