"""Tests for flarecrawl.credentials — CredentialStore priority chain and migration."""

import json
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path, monkeypatch):
    """Create an isolated CredentialStore (no env, no keyring, fresh singleton)."""
    monkeypatch.delenv("FLARECRAWL_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("FLARECRAWL_API_TOKEN", raising=False)
    monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", False)
    monkeypatch.setattr("flarecrawl.credentials._legacy_config_path", lambda: tmp_path / "nonexistent.json")
    import flarecrawl.credentials as _creds
    monkeypatch.setattr(_creds, "_store", None)
    from flarecrawl.credentials import CredentialStore
    return CredentialStore(env_file=tmp_path / ".env")


# ---------------------------------------------------------------------------
# Priority chain: env > keyring > dotenv > legacy
# ---------------------------------------------------------------------------


class TestPriorityChain:
    """Verify env -> keyring -> .env -> legacy config.json order."""

    def test_env_beats_keyring(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FLARECRAWL_API_TOKEN", "from-env")
        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", True)
        with patch("flarecrawl.credentials.keyring") as mock_kr:
            mock_kr.get_password.return_value = "from-keyring"
            from flarecrawl.credentials import CredentialStore
            store = CredentialStore(env_file=tmp_path / ".env")
            assert store.get("api_token") == "from-env"
            # Keyring should not even be queried
            mock_kr.get_password.assert_not_called()

    def test_keyring_beats_dotenv(self, monkeypatch, tmp_path):
        monkeypatch.delenv("FLARECRAWL_API_TOKEN", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("FLARECRAWL_API_TOKEN=from-dotenv\n")
        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", True)
        with patch("flarecrawl.credentials.keyring") as mock_kr:
            mock_kr.get_password.return_value = "from-keyring"
            from flarecrawl.credentials import CredentialStore
            store = CredentialStore(env_file=env_file)
            assert store.get("api_token") == "from-keyring"

    def test_dotenv_when_keyring_empty(self, monkeypatch, tmp_path):
        monkeypatch.delenv("FLARECRAWL_API_TOKEN", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("FLARECRAWL_API_TOKEN=from-dotenv\n")
        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", True)
        with patch("flarecrawl.credentials.keyring") as mock_kr:
            mock_kr.get_password.return_value = None
            from flarecrawl.credentials import CredentialStore
            store = CredentialStore(env_file=env_file)
            assert store.get("api_token") == "from-dotenv"

    def test_dotenv_when_no_keyring(self, monkeypatch, tmp_path):
        monkeypatch.delenv("FLARECRAWL_API_TOKEN", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("FLARECRAWL_API_TOKEN=from-dotenv\n")
        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", False)
        from flarecrawl.credentials import CredentialStore
        store = CredentialStore(env_file=env_file)
        assert store.get("api_token") == "from-dotenv"

    def test_legacy_config_when_nothing_else(self, monkeypatch, tmp_path):
        """Legacy config.json is the last resort."""
        monkeypatch.delenv("FLARECRAWL_API_TOKEN", raising=False)
        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", False)
        legacy = tmp_path / "config.json"
        legacy.write_text(json.dumps({"api_token": "from-legacy"}))
        monkeypatch.setattr("flarecrawl.credentials._legacy_config_path", lambda: legacy)
        from flarecrawl.credentials import CredentialStore
        store = CredentialStore(env_file=tmp_path / ".env")
        assert store.get("api_token") == "from-legacy"

    def test_none_when_nothing_available(self, monkeypatch, tmp_path):
        store = _make_store(tmp_path, monkeypatch)
        assert store.get("api_token") is None


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------


class TestLegacyMigration:
    """Legacy config.json -> keyring migration on first read."""

    def test_migration_moves_to_keyring(self, monkeypatch, tmp_path):
        """First read of legacy JSON triggers migration to keyring + deletion from JSON."""
        monkeypatch.delenv("FLARECRAWL_API_TOKEN", raising=False)
        monkeypatch.delenv("FLARECRAWL_ACCOUNT_ID", raising=False)
        legacy = tmp_path / "config.json"
        legacy.write_text(json.dumps({
            "api_token": "legacy-tok",
            "account_id": "legacy-acct",
            "usage": {"2026-01-01": 100},
        }))
        monkeypatch.setattr("flarecrawl.credentials._legacy_config_path", lambda: legacy)

        keyring_store = {}
        def fake_get(svc, key):
            return keyring_store.get((svc, key))
        def fake_set(svc, key, val):
            keyring_store[(svc, key)] = val

        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", True)
        with patch("flarecrawl.credentials.keyring") as mock_kr:
            mock_kr.get_password = fake_get
            mock_kr.set_password = fake_set
            from flarecrawl.credentials import CredentialStore
            store = CredentialStore(env_file=tmp_path / ".env")

            # First read returns legacy value
            assert store.get("api_token") == "legacy-tok"
            # Migration: keyring now has it
            assert keyring_store[("forma-flarecrawl", "api_token")] == "legacy-tok"
            # Migration: legacy JSON no longer has the credential
            remaining = json.loads(legacy.read_text())
            assert "api_token" not in remaining
            # Migration: usage data preserved
            assert remaining["usage"] == {"2026-01-01": 100}

    def test_migration_preserves_session_data(self, monkeypatch, tmp_path):
        """Migration only removes credential keys, not sessions/usage."""
        monkeypatch.delenv("FLARECRAWL_API_TOKEN", raising=False)
        legacy = tmp_path / "config.json"
        legacy.write_text(json.dumps({
            "api_token": "tok",
            "usage": {"2026-04-19": 500},
            "proxy": "socks5://localhost:9050",
        }))
        monkeypatch.setattr("flarecrawl.credentials._legacy_config_path", lambda: legacy)
        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", False)

        from flarecrawl.credentials import CredentialStore
        store = CredentialStore(env_file=tmp_path / ".env")
        store.get("api_token")

        remaining = json.loads(legacy.read_text())
        assert "api_token" not in remaining
        assert remaining["usage"] == {"2026-04-19": 500}
        assert remaining["proxy"] == "socks5://localhost:9050"

    def test_migration_idempotent(self, monkeypatch, tmp_path):
        """Second call after migration doesn't error (key already gone from JSON)."""
        monkeypatch.delenv("FLARECRAWL_API_TOKEN", raising=False)
        legacy = tmp_path / "config.json"
        legacy.write_text(json.dumps({"api_token": "tok"}))
        monkeypatch.setattr("flarecrawl.credentials._legacy_config_path", lambda: legacy)
        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", False)

        from flarecrawl.credentials import CredentialStore
        store = CredentialStore(env_file=tmp_path / ".env")
        # First read: migrates to .env
        val1 = store.get("api_token")
        # Second read: legacy gone, reads from .env now
        val2 = store.get("api_token")
        assert val1 == val2 == "tok"

    def test_migration_falls_to_dotenv_when_keyring_fails(self, monkeypatch, tmp_path):
        """If keyring.set_password raises, credential lands in .env instead."""
        monkeypatch.delenv("FLARECRAWL_API_TOKEN", raising=False)
        legacy = tmp_path / "config.json"
        legacy.write_text(json.dumps({"api_token": "tok"}))
        monkeypatch.setattr("flarecrawl.credentials._legacy_config_path", lambda: legacy)
        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", True)

        with patch("flarecrawl.credentials.keyring") as mock_kr:
            mock_kr.get_password.return_value = None
            mock_kr.set_password.side_effect = Exception("keyring locked")
            from flarecrawl.credentials import CredentialStore
            env_file = tmp_path / ".env"
            store = CredentialStore(env_file=env_file)
            assert store.get("api_token") == "tok"
            # Should have been written to .env since keyring failed
            assert "FLARECRAWL_API_TOKEN=tok" in env_file.read_text()


# ---------------------------------------------------------------------------
# get_source
# ---------------------------------------------------------------------------


class TestGetSource:
    """Verify source detection labels."""

    def test_source_environment(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FLARECRAWL_API_TOKEN", "x")
        from flarecrawl.credentials import CredentialStore
        assert CredentialStore(env_file=tmp_path / ".env").get_source("api_token") == "environment"

    def test_source_keyring(self, monkeypatch, tmp_path):
        monkeypatch.delenv("FLARECRAWL_API_TOKEN", raising=False)
        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", True)
        with patch("flarecrawl.credentials.keyring") as mock_kr:
            mock_kr.get_password.return_value = "x"
            from flarecrawl.credentials import CredentialStore
            assert CredentialStore(env_file=tmp_path / ".env").get_source("api_token") == "keyring"

    def test_source_dotenv(self, monkeypatch, tmp_path):
        monkeypatch.delenv("FLARECRAWL_API_TOKEN", raising=False)
        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", False)
        monkeypatch.setattr("flarecrawl.credentials._legacy_config_path", lambda: tmp_path / "nonexistent.json")
        env_file = tmp_path / ".env"
        env_file.write_text("FLARECRAWL_API_TOKEN=x\n")
        from flarecrawl.credentials import CredentialStore
        assert CredentialStore(env_file=env_file).get_source("api_token") == "dotenv"

    def test_source_config_legacy(self, monkeypatch, tmp_path):
        monkeypatch.delenv("FLARECRAWL_API_TOKEN", raising=False)
        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", False)
        legacy = tmp_path / "config.json"
        legacy.write_text(json.dumps({"api_token": "x"}))
        monkeypatch.setattr("flarecrawl.credentials._legacy_config_path", lambda: legacy)
        from flarecrawl.credentials import CredentialStore
        assert CredentialStore(env_file=tmp_path / ".env").get_source("api_token") == "config-legacy"

    def test_source_none(self, monkeypatch, tmp_path):
        store = _make_store(tmp_path, monkeypatch)
        assert store.get_source("api_token") == "none"


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------


class TestStatus:
    """Auth status aggregation."""

    def test_status_authenticated(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FLARECRAWL_ACCOUNT_ID", "acct")
        monkeypatch.setenv("FLARECRAWL_API_TOKEN", "tok")
        from flarecrawl.credentials import CredentialStore
        s = CredentialStore(env_file=tmp_path / ".env").status()
        assert s["authenticated"] is True
        assert s["source"] == "environment"
        assert "keyring_available" in s

    def test_status_missing_both(self, monkeypatch, tmp_path):
        store = _make_store(tmp_path, monkeypatch)
        s = store.status()
        assert s["authenticated"] is False
        assert s["source"] == "none"
        assert "account_id" in s["missing"]
        assert "api_token" in s["missing"]
        assert "keyring_available" in s

    def test_status_missing_one(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FLARECRAWL_ACCOUNT_ID", "acct")
        monkeypatch.delenv("FLARECRAWL_API_TOKEN", raising=False)
        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", False)
        monkeypatch.setattr("flarecrawl.credentials._legacy_config_path", lambda: tmp_path / "nonexistent.json")
        from flarecrawl.credentials import CredentialStore
        s = CredentialStore(env_file=tmp_path / ".env").status()
        assert s["authenticated"] is False
        assert "api_token" in s["missing"]
        assert "account_id" not in s.get("missing", [])

    def test_status_eagerly_migrates_both_keys(self, monkeypatch, tmp_path):
        """Regression: status() must trigger migration for BOTH credentials.

        Previously only account_id (fetched for masked display) migrated, leaving
        api_token in plaintext while status reported source=keyring.
        """
        monkeypatch.delenv("FLARECRAWL_ACCOUNT_ID", raising=False)
        monkeypatch.delenv("FLARECRAWL_API_TOKEN", raising=False)
        legacy = tmp_path / "config.json"
        legacy.write_text(
            json.dumps({"account_id": "legacy-acct", "api_token": "legacy-tok", "usage": {"d": 1}})
        )
        monkeypatch.setattr("flarecrawl.credentials._legacy_config_path", lambda: legacy)
        keyring_store: dict = {}
        monkeypatch.setattr(
            "flarecrawl.credentials.keyring.get_password",
            lambda svc, key: keyring_store.get((svc, key)),
        )
        monkeypatch.setattr(
            "flarecrawl.credentials.keyring.set_password",
            lambda svc, key, val: keyring_store.__setitem__((svc, key), val),
        )
        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", True)

        from flarecrawl.credentials import CredentialStore
        s = CredentialStore(env_file=tmp_path / ".env").status()

        assert s["authenticated"] is True
        assert s["source"] == "keyring"
        # Both creds must be in keyring after status()
        assert keyring_store[("forma-flarecrawl", "account_id")] == "legacy-acct"
        assert keyring_store[("forma-flarecrawl", "api_token")] == "legacy-tok"
        # Both removed from legacy JSON, usage preserved
        remaining = json.loads(legacy.read_text())
        assert "account_id" not in remaining
        assert "api_token" not in remaining
        assert remaining["usage"] == {"d": 1}


# ---------------------------------------------------------------------------
# set / delete
# ---------------------------------------------------------------------------


class TestSetDelete:
    """Write and delete operations."""

    def test_set_to_keyring(self, monkeypatch, tmp_path):
        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", True)
        with patch("flarecrawl.credentials.keyring") as mock_kr:
            mock_kr.set_password = MagicMock()
            from flarecrawl.credentials import CredentialStore
            store = CredentialStore(env_file=tmp_path / ".env")
            store.set("api_token", "secret")
            mock_kr.set_password.assert_called_once_with("forma-flarecrawl", "api_token", "secret")

    def test_set_falls_to_dotenv_when_keyring_fails(self, monkeypatch, tmp_path):
        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", True)
        with patch("flarecrawl.credentials.keyring") as mock_kr:
            mock_kr.set_password.side_effect = Exception("locked")
            from flarecrawl.credentials import CredentialStore
            env_file = tmp_path / ".env"
            store = CredentialStore(env_file=env_file)
            store.set("api_token", "secret")
            assert "FLARECRAWL_API_TOKEN=secret" in env_file.read_text()

    def test_set_to_dotenv_when_no_keyring(self, monkeypatch, tmp_path):
        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", False)
        from flarecrawl.credentials import CredentialStore
        env_file = tmp_path / ".env"
        store = CredentialStore(env_file=env_file)
        store.set("api_token", "secret")
        assert "FLARECRAWL_API_TOKEN=secret" in env_file.read_text()

    def test_delete_clears_keyring_and_dotenv(self, monkeypatch, tmp_path):
        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", True)
        env_file = tmp_path / ".env"
        env_file.write_text("FLARECRAWL_API_TOKEN=old\n")
        with patch("flarecrawl.credentials.keyring") as mock_kr:
            mock_kr.delete_password = MagicMock()
            from flarecrawl.credentials import CredentialStore
            store = CredentialStore(env_file=env_file)
            result = store.delete("api_token")
            assert result is True
            mock_kr.delete_password.assert_called_once_with("forma-flarecrawl", "api_token")
            assert "FLARECRAWL_API_TOKEN" not in env_file.read_text()

    def test_delete_returns_false_when_nothing_to_delete(self, monkeypatch, tmp_path):
        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", False)
        from flarecrawl.credentials import CredentialStore
        store = CredentialStore(env_file=tmp_path / ".env")
        assert store.delete("api_token") is False


# ---------------------------------------------------------------------------
# No-keyring fallback
# ---------------------------------------------------------------------------


class TestNoKeyring:
    """Everything works when keyring package isn't installed."""

    def test_get_without_keyring(self, monkeypatch, tmp_path):
        monkeypatch.delenv("FLARECRAWL_API_TOKEN", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("FLARECRAWL_API_TOKEN=from-dotenv\n")
        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", False)
        monkeypatch.setattr("flarecrawl.credentials._legacy_config_path", lambda: tmp_path / "nonexistent.json")
        from flarecrawl.credentials import CredentialStore
        store = CredentialStore(env_file=env_file)
        assert store.get("api_token") == "from-dotenv"

    def test_set_without_keyring(self, monkeypatch, tmp_path):
        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", False)
        from flarecrawl.credentials import CredentialStore
        env_file = tmp_path / ".env"
        store = CredentialStore(env_file=env_file)
        store.set("api_token", "val")
        assert "FLARECRAWL_API_TOKEN=val" in env_file.read_text()

    def test_status_reports_keyring_unavailable(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FLARECRAWL_ACCOUNT_ID", "acct")
        monkeypatch.setenv("FLARECRAWL_API_TOKEN", "tok")
        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", False)
        from flarecrawl.credentials import CredentialStore
        s = CredentialStore(env_file=tmp_path / ".env").status()
        assert s["keyring_available"] is False


# ---------------------------------------------------------------------------
# config.py wrapper compatibility
# ---------------------------------------------------------------------------


class TestConfigWrappers:
    """Existing config.get_api_token() etc. must still resolve via the new store."""

    def test_get_api_token_via_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FLARECRAWL_API_TOKEN", "compat-tok")
        import flarecrawl.credentials as _creds
        monkeypatch.setattr(_creds, "_store", None)
        from flarecrawl.config import get_api_token
        assert get_api_token() == "compat-tok"

    def test_get_account_id_via_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FLARECRAWL_ACCOUNT_ID", "compat-acct")
        import flarecrawl.credentials as _creds
        monkeypatch.setattr(_creds, "_store", None)
        from flarecrawl.config import get_account_id
        assert get_account_id() == "compat-acct"

    def test_get_auth_status_includes_keyring_available(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FLARECRAWL_ACCOUNT_ID", "acct123456")
        monkeypatch.setenv("FLARECRAWL_API_TOKEN", "tok")
        import flarecrawl.credentials as _creds
        monkeypatch.setattr(_creds, "_store", None)
        from flarecrawl.config import get_auth_status
        status = get_auth_status()
        assert "keyring_available" in status
        assert status["authenticated"] is True
        assert status["source"] == "environment"
        # account_id should be masked
        assert status["account_id"] == "acct1234..."

    def test_get_auth_status_not_authenticated(self, monkeypatch, tmp_path):
        monkeypatch.delenv("FLARECRAWL_ACCOUNT_ID", raising=False)
        monkeypatch.delenv("FLARECRAWL_API_TOKEN", raising=False)
        monkeypatch.setattr("flarecrawl.credentials.KEYRING_AVAILABLE", False)
        monkeypatch.setattr("flarecrawl.credentials._legacy_config_path", lambda: tmp_path / "nonexistent.json")
        import flarecrawl.credentials as _creds
        monkeypatch.setattr(_creds, "_store", None)
        from flarecrawl.config import get_auth_status
        status = get_auth_status()
        assert status["authenticated"] is False
        assert "missing" in status


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    """Module-level singleton behaviour."""

    def test_get_credential_store_returns_same_instance(self, monkeypatch):
        import flarecrawl.credentials as _creds
        monkeypatch.setattr(_creds, "_store", None)
        from flarecrawl.credentials import get_credential_store
        s1 = get_credential_store()
        s2 = get_credential_store()
        assert s1 is s2


# ---------------------------------------------------------------------------
# env_var mapping
# ---------------------------------------------------------------------------


class TestEnvVar:
    """Verify _env_var produces correct names."""

    def test_api_token(self, tmp_path):
        from flarecrawl.credentials import CredentialStore
        store = CredentialStore(env_file=tmp_path / ".env")
        assert store._env_var("api_token") == "FLARECRAWL_API_TOKEN"

    def test_account_id(self, tmp_path):
        from flarecrawl.credentials import CredentialStore
        store = CredentialStore(env_file=tmp_path / ".env")
        assert store._env_var("account_id") == "FLARECRAWL_ACCOUNT_ID"
