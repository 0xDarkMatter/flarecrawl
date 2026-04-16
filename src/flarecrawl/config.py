"""Flarecrawl configuration and credential storage."""

import json
import os
import platform
import tempfile
from pathlib import Path

APP_NAME = "flarecrawl"


def get_env_int(key: str, default: int) -> int:
    """Get integer from environment variable with fallback."""
    val = os.environ.get(key, "").strip()
    if val:
        try:
            return int(val)
        except ValueError:
            pass
    return default


# Configurable via environment variables
DEFAULT_CACHE_TTL = get_env_int("FLARECRAWL_CACHE_TTL", 3600)
DEFAULT_MAX_RETRIES = get_env_int("FLARECRAWL_MAX_RETRIES", 3)
DEFAULT_MAX_WORKERS = get_env_int("FLARECRAWL_MAX_WORKERS", 50)
DEFAULT_TIMEOUT = get_env_int("FLARECRAWL_TIMEOUT", 120)


def get_proxy() -> str | None:
    """Get proxy URL from env var or config file.

    Checks FLARECRAWL_PROXY env var first, then config.json.
    Supports http://, https://, socks5:// URLs.
    """
    env_val = os.environ.get("FLARECRAWL_PROXY", "").strip()
    if env_val:
        return env_val
    config = load_config()
    return config.get("proxy") or None


def get_config_dir() -> Path:
    """Get platform-appropriate config directory."""
    system = platform.system()

    if system == "Windows":
        base = Path.home() / "AppData" / "Roaming"
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".config"

    config_dir = base / APP_NAME
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_config_file() -> Path:
    """Get config file path."""
    return get_config_dir() / "config.json"


def load_config() -> dict:
    """Load configuration."""
    config_file = get_config_file()
    if config_file.exists():
        try:
            return json.loads(config_file.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_config(config: dict) -> None:
    """Save configuration atomically (write to temp, then rename)."""
    config_file = get_config_file()
    config_dir = config_file.parent
    try:
        fd, tmp_path = tempfile.mkstemp(dir=config_dir, suffix=".tmp", prefix=".config_")
        with os.fdopen(fd, "w") as f:
            json.dump(config, f, indent=2)
        # Atomic rename (same filesystem)
        Path(tmp_path).replace(config_file)
    except OSError:
        # Fallback to direct write if temp file fails
        config_file.write_text(json.dumps(config, indent=2))


def get_account_id() -> str | None:
    """Get Cloudflare account ID.

    Checks: FLARECRAWL_ACCOUNT_ID env var → config file.
    """
    env_val = os.environ.get("FLARECRAWL_ACCOUNT_ID", "").strip()
    if env_val:
        return env_val

    config = load_config()
    stored = config.get("account_id", "")
    return stored.strip() if stored else None


def get_api_token() -> str | None:
    """Get Cloudflare API token.

    Checks: FLARECRAWL_API_TOKEN env var → config file.
    """
    env_val = os.environ.get("FLARECRAWL_API_TOKEN", "").strip()
    if env_val:
        return env_val

    config = load_config()
    stored = config.get("api_token", "")
    return stored.strip() if stored else None


def save_credentials(account_id: str, api_token: str) -> None:
    """Save both credentials to config."""
    config = load_config()
    config["account_id"] = account_id
    config["api_token"] = api_token
    save_config(config)


def clear_credentials() -> None:
    """Clear stored credentials."""
    config = load_config()
    config.pop("account_id", None)
    config.pop("api_token", None)
    save_config(config)


def get_usage() -> dict:
    """Get tracked browser time usage."""
    config = load_config()
    usage = config.get("usage", {})
    return usage


def track_usage(ms: int) -> None:
    """Add browser time to today's usage counter."""
    from datetime import date
    today = date.today().isoformat()
    config = load_config()
    usage = config.get("usage", {})
    usage[today] = usage.get(today, 0) + ms
    config["usage"] = usage
    # Keep only last 30 days
    keys = sorted(usage.keys())
    if len(keys) > 30:
        for old_key in keys[:-30]:
            del usage[old_key]
    save_config(config)


def get_auth_status() -> dict:
    """Get authentication status."""
    account_id = get_account_id()
    api_token = get_api_token()

    if account_id and api_token:
        # Determine source
        if os.environ.get("FLARECRAWL_API_TOKEN"):
            source = "environment"
        else:
            source = "config"

        return {
            "authenticated": True,
            "source": source,
            "account_id": account_id[:8] + "..." if len(account_id) > 8 else account_id,
        }

    missing = []
    if not account_id:
        missing.append("account_id")
    if not api_token:
        missing.append("api_token")

    return {
        "authenticated": False,
        "source": "none",
        "missing": missing,
    }


# ------------------------------------------------------------------
# Session persistence
# ------------------------------------------------------------------


def get_sessions_dir() -> Path:
    """Get sessions directory, creating it if needed."""
    sessions_dir = get_config_dir() / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return sessions_dir


def save_session(name: str, cookies: list[dict]) -> Path:
    """Save cookies to a named session file."""
    path = get_sessions_dir() / f"{name}.json"
    path.write_text(json.dumps(cookies, indent=2))
    return path


def load_session(name: str) -> list[dict]:
    """Load cookies from a named session file."""
    path = get_sessions_dir() / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Session not found: {name}")
    return json.loads(path.read_text())


def list_sessions() -> list[str]:
    """List saved session names."""
    sessions_dir = get_sessions_dir()
    return sorted(p.stem for p in sessions_dir.glob("*.json"))


def delete_session(name: str) -> bool:
    """Delete a saved session. Returns True if deleted."""
    path = get_sessions_dir() / f"{name}.json"
    if path.exists():
        path.unlink()
        return True
    return False


# ------------------------------------------------------------------
# CDP session persistence
# ------------------------------------------------------------------

_CDP_SESSIONS_FILE = "cdp_sessions.json"


def _get_cdp_sessions_path() -> Path:
    """Get the CDP sessions store file path."""
    return get_config_dir() / _CDP_SESSIONS_FILE


def _load_cdp_sessions_raw() -> list[dict]:
    """Load all CDP sessions from disk (including expired)."""
    path = _get_cdp_sessions_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_cdp_sessions_raw(sessions: list[dict]) -> None:
    """Write CDP sessions list to disk."""
    path = _get_cdp_sessions_path()
    path.write_text(json.dumps(sessions, indent=2))


def save_cdp_session(session_id: str, ws_url: str, expiry: float) -> None:
    """Persist a CDP session for reuse across invocations."""
    import time
    sessions = [s for s in _load_cdp_sessions_raw() if s.get("expiry", 0) > time.time()]
    sessions = [s for s in sessions if s.get("session_id") != session_id]
    sessions.append({"session_id": session_id, "ws_url": ws_url, "expiry": expiry})
    _save_cdp_sessions_raw(sessions)


def load_cdp_session() -> dict | None:
    """Return the newest non-expired CDP session, or None."""
    import time
    now = time.time()
    sessions = _load_cdp_sessions_raw()
    active = [s for s in sessions if s.get("expiry", 0) > now]
    if not active:
        return None
    active.sort(key=lambda s: s["expiry"], reverse=True)
    return active[0]


def list_cdp_sessions() -> list[dict]:
    """Return all non-expired CDP sessions."""
    import time
    now = time.time()
    return [s for s in _load_cdp_sessions_raw() if s.get("expiry", 0) > now]


def clear_cdp_session(session_id: str | None = None) -> bool:
    """Remove a CDP session (or all if session_id is None). Returns True if any removed."""
    if session_id is None:
        path = _get_cdp_sessions_path()
        if path.exists():
            path.unlink()
            return True
        return False
    sessions = _load_cdp_sessions_raw()
    filtered = [s for s in sessions if s.get("session_id") != session_id]
    if len(filtered) == len(sessions):
        return False
    _save_cdp_sessions_raw(filtered)
    return True
