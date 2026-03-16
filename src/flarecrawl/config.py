"""Flarecrawl configuration and credential storage."""

import json
import os
import platform
from pathlib import Path

APP_NAME = "flarecrawl"


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
    """Save configuration."""
    config_file = get_config_file()
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
