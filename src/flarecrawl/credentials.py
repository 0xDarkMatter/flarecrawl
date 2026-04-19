"""
Standalone credential storage for flarecrawl CLI.

No external dependencies - works with:
- Environment variables (FLARECRAWL_*)
- OS Keyring (if 'keyring' package installed)
- .env file fallback
- Legacy config.json (read-only, auto-migrated)

Compatible with Forma workspaces but doesn't require forma-cli.
"""

import os
import platform
import sys
from pathlib import Path
from typing import Optional

# Keyring is optional
try:
    import keyring
    from keyring.errors import KeyringError

    KEYRING_AVAILABLE = True
except ImportError:
    KEYRING_AVAILABLE = False
    KeyringError = Exception  # type: ignore


def _legacy_config_path() -> Path:
    """Platform-aware path to legacy config.json (mirrors config.get_config_dir)."""
    system = platform.system()
    if system == "Windows":
        base = Path.home() / "AppData" / "Roaming"
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".config"
    return base / "flarecrawl" / "config.json"


class CredentialStore:
    """Credential storage with env -> keyring -> .env -> legacy config.json priority."""

    SERVICE = "flarecrawl"
    KEYRING_SERVICE = f"forma-{SERVICE}"  # Compatible with forma_auth

    def __init__(self, env_file: Optional[Path] = None):
        self.env_file = env_file or Path.cwd() / ".env"

    def _env_var(self, key: str) -> str:
        """Convert key to env var name: api_token -> FLARECRAWL_API_TOKEN"""
        return f"{self.SERVICE.upper()}_{key.upper()}"

    def get(self, key: str) -> Optional[str]:
        """Get credential: env -> keyring -> .env -> legacy config.json"""
        # 1. Environment variable
        if value := os.environ.get(self._env_var(key)):
            return value

        # 2. OS Keyring
        if KEYRING_AVAILABLE:
            try:
                if value := keyring.get_password(self.KEYRING_SERVICE, key):
                    return value
            except KeyringError:
                pass

        # 3. .env file
        if value := self._get_from_dotenv(key):
            return value

        # 4. Legacy config.json (read-only — triggers migration on hit)
        if value := self._get_from_legacy_config(key):
            self._migrate_legacy(key, value)
            return value

        return None

    def set(self, key: str, value: str) -> None:
        """Store credential in keyring, or .env if unavailable."""
        if KEYRING_AVAILABLE:
            try:
                keyring.set_password(self.KEYRING_SERVICE, key, value)
                return
            except KeyringError:
                pass

        # Fallback to .env
        self._set_in_dotenv(key, value)

    def delete(self, key: str) -> bool:
        """Delete credential from keyring and .env."""
        deleted = False

        if KEYRING_AVAILABLE:
            try:
                keyring.delete_password(self.KEYRING_SERVICE, key)
                deleted = True
            except KeyringError:
                pass

        if self._delete_from_dotenv(key):
            deleted = True

        return deleted

    def get_source(self, key: str) -> str:
        """Identify where credential is stored: environment|keyring|dotenv|config-legacy|none"""
        if os.environ.get(self._env_var(key)):
            return "environment"

        if KEYRING_AVAILABLE:
            try:
                if keyring.get_password(self.KEYRING_SERVICE, key):
                    return "keyring"
            except KeyringError:
                pass

        if self._get_from_dotenv(key):
            return "dotenv"

        if self._get_from_legacy_config(key):
            return "config-legacy"

        return "none"

    def status(self) -> dict:
        """Auth status — checks both account_id and api_token together."""
        account_source = self.get_source("account_id")
        token_source = self.get_source("api_token")
        if account_source != "none" and token_source != "none":
            # Use the highest-priority source for display
            priority = ["environment", "keyring", "dotenv", "config-legacy"]
            source = next((s for s in priority if account_source == s or token_source == s), "none")
            return {"authenticated": True, "source": source, "keyring_available": KEYRING_AVAILABLE}
        missing = []
        if account_source == "none":
            missing.append("account_id")
        if token_source == "none":
            missing.append("api_token")
        return {"authenticated": False, "source": "none", "missing": missing, "keyring_available": KEYRING_AVAILABLE}

    # --- .env file operations ---

    def _parse_dotenv(self) -> dict[str, str]:
        """Parse .env file into dict."""
        if not self.env_file.exists():
            return {}

        result = {}
        try:
            for line in self.env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    value = value.strip().strip("\"'")
                    result[key.strip()] = value
        except OSError:
            pass

        return result

    def _write_dotenv(self, data: dict[str, str]) -> None:
        """Write dict to .env file."""
        lines = [f"{k}={v}" for k, v in sorted(data.items())]
        try:
            self.env_file.parent.mkdir(parents=True, exist_ok=True)
            self.env_file.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
            if os.name != "nt":
                os.chmod(self.env_file, 0o600)
        except OSError:
            pass

    def _get_from_dotenv(self, key: str) -> Optional[str]:
        """Get value from .env file."""
        return self._parse_dotenv().get(self._env_var(key))

    def _set_in_dotenv(self, key: str, value: str) -> None:
        """Set value in .env file."""
        data = self._parse_dotenv()
        data[self._env_var(key)] = value
        self._write_dotenv(data)

    def _delete_from_dotenv(self, key: str) -> bool:
        """Delete value from .env file."""
        data = self._parse_dotenv()
        env_key = self._env_var(key)
        if env_key in data:
            del data[env_key]
            self._write_dotenv(data)
            return True
        return False

    # --- Legacy config.json operations ---

    def _get_from_legacy_config(self, key: str) -> Optional[str]:
        """Read from legacy config.json (plaintext store)."""
        try:
            import json as _json

            legacy = _legacy_config_path()
            if not legacy.exists():
                return None
            data = _json.loads(legacy.read_text(encoding="utf-8"))
            val = data.get(key, "")
            return val.strip() if val else None
        except (OSError, _json.JSONDecodeError, AttributeError):
            return None

    def _migrate_legacy(self, key: str, value: str) -> None:
        """Move credential from legacy JSON -> keyring (one-shot, idempotent)."""
        try:
            # Write to keyring (or .env if keyring unavailable)
            self.set(key, value)
            # Remove from legacy JSON (preserve other keys: usage, sessions, etc.)
            import json as _json

            legacy = _legacy_config_path()
            if legacy.exists():
                data = _json.loads(legacy.read_text(encoding="utf-8"))
                if key in data:
                    del data[key]
                    legacy.write_text(_json.dumps(data, indent=2), encoding="utf-8")
            sys.stderr.write(f"[flarecrawl] Migrated {key} from legacy config.json -> keyring\n")
        except OSError:
            pass  # migration failed — value still works, will retry next call


# Module-level singleton
_store: Optional[CredentialStore] = None


def get_credential_store() -> CredentialStore:
    """Get or create the credential store singleton."""
    global _store
    if _store is None:
        _store = CredentialStore()
    return _store
