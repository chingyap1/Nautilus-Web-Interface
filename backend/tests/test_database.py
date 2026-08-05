"""B3a — Coverage tests for database.py.

Tests user CRUD, role assignment, component state persistence, settings,
orders, alerts, positions, adapter configs, audit logs, token revocation,
and risk helpers — all using an isolated SQLite database.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Ensure backend root is on sys.path
BACKEND_ROOT = str(Path(__file__).resolve().parent.parent)
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

import database


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Isolated DB for each test."""
    path = tmp_path / "test_database.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    asyncio.run(database.init_db())
    return path


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


class TestOrders:
    def test_create_and_list_order(self, db):
        order = asyncio.run(database.create_order("BTC/USD", "BUY", quantity=0.5))
        assert order["instrument"] == "BTC/USD"
        assert order["side"] == "BUY"
        assert order["status"] == "PENDING"

        orders = asyncio.run(database.list_orders())
        assert any(o["id"] == order["id"] for o in orders)

    def test_cancel_order(self, db):
        order = asyncio.run(database.create_order("ETH/USD", "SELL", quantity=1.0))
        result = asyncio.run(database.cancel_order(order["id"]))
        assert result is True

        orders = asyncio.run(database.list_orders())
        cancelled = [o for o in orders if o["id"] == order["id"]]
        assert cancelled[0]["status"] == "CANCELLED"

    def test_cancel_nonexistent_order(self, db):
        result = asyncio.run(database.cancel_order("ORD-NONEXISTENT"))
        assert result is False

    def test_create_order_with_price(self, db):
        order = asyncio.run(
            database.create_order("BTC/USD", "BUY", order_type="LIMIT", quantity=0.1, price=50000.0)
        )
        assert order["type"] == "LIMIT"
        assert order["price"] == 50000.0


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


class TestAlerts:
    def test_create_and_list_alert(self, db):
        alert = asyncio.run(database.create_alert("BTC/USD", ">", 50000, "price alert"))
        assert alert["symbol"] == "BTC/USD"
        assert alert["status"] == "active"

        alerts = asyncio.run(database.list_alerts())
        assert any(a["id"] == alert["id"] for a in alerts)

    def test_list_active_alerts(self, db):
        alert = asyncio.run(database.create_alert("ETH/USD", "<", 3000))
        active = asyncio.run(database.list_active_alerts())
        assert any(a["id"] == alert["id"] for a in active)

    def test_trigger_alert(self, db):
        alert = asyncio.run(database.create_alert("BTC/USD", ">", 50000))
        result = asyncio.run(database.trigger_alert(alert["id"]))
        assert result is True

        active = asyncio.run(database.list_active_alerts())
        assert not any(a["id"] == alert["id"] for a in active)

    def test_trigger_nonexistent_alert(self, db):
        result = asyncio.run(database.trigger_alert("ALT-NONEXISTENT"))
        assert result is False

    def test_dismiss_alert(self, db):
        alert = asyncio.run(database.create_alert("BTC/USD", ">", 50000))
        result = asyncio.run(database.dismiss_alert(alert["id"]))
        assert result is True

        active = asyncio.run(database.list_active_alerts())
        assert not any(a["id"] == alert["id"] for a in active)

    def test_dismiss_nonexistent_alert(self, db):
        result = asyncio.run(database.dismiss_alert("ALT-NONEXISTENT"))
        assert result is False

    def test_delete_alert(self, db):
        alert = asyncio.run(database.create_alert("BTC/USD", ">", 50000))
        result = asyncio.run(database.delete_alert(alert["id"]))
        assert result is True

        alerts = asyncio.run(database.list_alerts())
        assert not any(a["id"] == alert["id"] for a in alerts)

    def test_delete_nonexistent_alert(self, db):
        result = asyncio.run(database.delete_alert("ALT-NONEXISTENT"))
        assert result is False


# ---------------------------------------------------------------------------
# Risk limits
# ---------------------------------------------------------------------------


class TestRiskLimits:
    def test_get_default_risk_limits(self, db):
        limits = asyncio.run(database.get_risk_limits())
        assert "max_position_size" in limits
        assert limits["max_position_size"] == 100_000

    def test_update_risk_limits(self, db):
        updated = asyncio.run(database.update_risk_limits({"max_daily_loss": 10000}))
        assert updated["max_daily_loss"] == 10000

        limits = asyncio.run(database.get_risk_limits())
        assert limits["max_daily_loss"] == 10000

    def test_risk_limits_explicitly_set(self, db):
        assert asyncio.run(database.risk_limits_explicitly_set()) is False
        asyncio.run(database.update_risk_limits({"max_daily_loss": 10000}))
        assert asyncio.run(database.risk_limits_explicitly_set()) is True


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestSettings:
    def test_get_default_settings(self, db):
        settings = asyncio.run(database.get_settings())
        assert "general" in settings
        assert "notifications" in settings

    def test_update_settings(self, db):
        updated = asyncio.run(database.update_settings({"general": {"system_name": "Test"}}))
        assert updated["general"]["system_name"] == "Test"

    def test_get_settings_raw(self, db):
        settings = asyncio.run(database.get_settings_raw())
        assert "general" in settings

    def test_update_settings_new_section(self, db):
        updated = asyncio.run(database.update_settings({"custom": {"key": "value"}}))
        assert updated["custom"]["key"] == "value"

    def test_mask_sensitive_settings(self, db):
        asyncio.run(
            database.update_settings(
                {"notifications": {"smtp_password": "secret123", "telegram_bot_token": "tok456"}}
            )
        )
        settings = asyncio.run(database.get_settings(mask_sensitive=True))
        assert settings["notifications"]["smtp_password"] == "****"
        assert settings["notifications"]["telegram_bot_token"] == "****"


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


class TestStrategies:
    def test_save_and_list_strategy(self, db):
        asyncio.run(
            database.save_strategy(
                {"id": "str-1", "name": "MA Cross", "type": "sma_crossover", "status": "running"}
            )
        )
        strategies = asyncio.run(database.list_strategies())
        assert any(s["id"] == "str-1" for s in strategies)

    def test_update_strategy_status(self, db):
        asyncio.run(database.save_strategy({"id": "str-2", "name": "Breakout"}))
        result = asyncio.run(database.update_strategy_status("str-2", "stopped"))
        assert result is True

    def test_update_nonexistent_strategy(self, db):
        result = asyncio.run(database.update_strategy_status("str-nonexistent", "stopped"))
        assert result is False

    def test_delete_strategy(self, db):
        asyncio.run(database.save_strategy({"id": "str-3", "name": "RSI"}))
        result = asyncio.run(database.delete_strategy("str-3"))
        assert result is True

    def test_delete_nonexistent_strategy(self, db):
        result = asyncio.run(database.delete_strategy("str-nonexistent"))
        assert result is False


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------


class TestPositions:
    def test_save_and_list_positions(self, db):
        asyncio.run(
            database.save_positions(
                [{"id": "pos-1", "instrument": "BTC/USD", "side": "LONG", "quantity": 0.1,
                  "is_open": True}],
                strategy_id="str-1",
            )
        )
        positions = asyncio.run(database.list_db_positions(open_only=True))
        assert any(p["id"] == "pos-1" for p in positions)

    def test_list_all_positions(self, db):
        asyncio.run(
            database.save_positions(
                [{"id": "pos-2", "instrument": "ETH/USD", "side": "LONG", "quantity": 1.0,
                  "is_open": False}],
            )
        )
        positions = asyncio.run(database.list_db_positions(open_only=False))
        assert any(p["id"] == "pos-2" for p in positions)

    def test_close_db_position(self, db):
        asyncio.run(
            database.save_positions(
                [{"id": "pos-3", "instrument": "BTC/USD", "side": "LONG", "quantity": 0.5,
                  "is_open": True}],
            )
        )
        result = asyncio.run(database.close_db_position("pos-3"))
        assert result is True

    def test_close_nonexistent_position(self, db):
        result = asyncio.run(database.close_db_position("pos-nonexistent"))
        assert result is False


# ---------------------------------------------------------------------------
# Adapter configs
# ---------------------------------------------------------------------------


class TestAdapterConfigs:
    def test_upsert_and_get_adapter_config(self, db):
        asyncio.run(
            database.upsert_adapter_config(
                "kraken", "connected", api_key="key123", api_secret="secret456"
            )
        )
        config = asyncio.run(database.get_adapter_config("kraken"))
        assert config is not None
        assert config["status"] == "connected"
        assert config["api_key"] == "key123"

    def test_upsert_adapter_config_disconnected(self, db):
        asyncio.run(database.upsert_adapter_config("binance", "disconnected"))
        config = asyncio.run(database.get_adapter_config("binance"))
        assert config["status"] == "disconnected"
        assert config["last_connected"] is None

    def test_get_nonexistent_adapter_config(self, db):
        config = asyncio.run(database.get_adapter_config("nonexistent"))
        assert config is None

    def test_upsert_adapter_config_with_extra_config(self, db):
        asyncio.run(
            database.upsert_adapter_config(
                "kraken", "connected", extra_config={"testnet": True}
            )
        )
        config = asyncio.run(database.get_adapter_config("kraken"))
        assert json.loads(config["extra_config"]) == {"testnet": True}

    def test_has_connected_adapter(self, db):
        assert asyncio.run(database.has_connected_adapter()) is False
        asyncio.run(database.upsert_adapter_config("kraken", "connected"))
        assert asyncio.run(database.has_connected_adapter()) is True


# ---------------------------------------------------------------------------
# Component states
# ---------------------------------------------------------------------------


class TestComponentStates:
    def test_set_and_get_component_state(self, db):
        asyncio.run(database.set_component_state("agent-btc", "running"))
        states = asyncio.run(database.get_component_states())
        assert states["agent-btc"] == "running"

    def test_update_component_state(self, db):
        asyncio.run(database.set_component_state("agent-eth", "running"))
        asyncio.run(database.set_component_state("agent-eth", "stopped"))
        states = asyncio.run(database.get_component_states())
        assert states["agent-eth"] == "stopped"

    def test_get_empty_component_states(self, db):
        states = asyncio.run(database.get_component_states())
        assert states == {}


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


class TestUsers:
    def test_create_and_get_user(self, db):
        user = asyncio.run(database.create_user("testuser", "hashedpw", role="trader"))
        assert user["username"] == "testuser"
        assert user["role"] == "trader"

        fetched = asyncio.run(database.get_user("testuser"))
        assert fetched is not None
        assert fetched["username"] == "testuser"

    def test_create_duplicate_user_raises(self, db):
        asyncio.run(database.create_user("dup", "hashedpw"))
        with pytest.raises(ValueError, match="already exists"):
            asyncio.run(database.create_user("dup", "hashedpw"))

    def test_create_user_invalid_role(self, db):
        with pytest.raises(ValueError, match="Invalid role"):
            asyncio.run(database.create_user("bad", "pw", role="superuser"))

    def test_create_user_invalid_principal_type(self, db):
        with pytest.raises(ValueError, match="Invalid principal_type"):
            asyncio.run(database.create_user("bad2", "pw", principal_type="robot"))

    def test_create_service_principal_approver_rejected(self, db):
        with pytest.raises(ValueError, match="Service principals cannot hold"):
            asyncio.run(
                database.create_user("svc1", "pw", role="approver", principal_type="service")
            )

    def test_create_service_principal_admin_rejected(self, db):
        with pytest.raises(ValueError, match="Service principals cannot hold"):
            asyncio.run(
                database.create_user("svc2", "pw", role="admin", principal_type="service")
            )

    def test_list_users(self, db):
        asyncio.run(database.create_user("user1", "pw"))
        asyncio.run(database.create_user("user2", "pw"))
        users = asyncio.run(database.list_users())
        usernames = [u["username"] for u in users]
        assert "user1" in usernames
        assert "user2" in usernames

    def test_delete_user(self, db):
        user = asyncio.run(database.create_user("todelete", "pw"))
        result = asyncio.run(database.delete_user(user["id"]))
        assert result is True

        fetched = asyncio.run(database.get_user("todelete"))
        assert fetched is None

    def test_delete_nonexistent_user(self, db):
        result = asyncio.run(database.delete_user("USR-NONEXISTENT"))
        assert result is False

    def test_update_user_password(self, db):
        user = asyncio.run(database.create_user("pwuser", "oldpw"))
        result = asyncio.run(database.update_user_password(user["id"], "newpw"))
        assert result is True

    def test_update_password_nonexistent_user(self, db):
        result = asyncio.run(database.update_user_password("USR-NONEXISTENT", "newpw"))
        assert result is False

    def test_get_nonexistent_user(self, db):
        fetched = asyncio.run(database.get_user("nonexistent"))
        assert fetched is None


# ---------------------------------------------------------------------------
# 2FA
# ---------------------------------------------------------------------------


class Test2FA:
    def test_set_totp_secret(self, db):
        asyncio.run(database.create_user("2fauser", "pw"))
        asyncio.run(database.set_totp_secret("2fauser", "SECRET123"))
        info = asyncio.run(database.get_user_2fa("2fauser"))
        assert info is not None
        assert info["totp_secret"] == "SECRET123"
        assert info["two_factor_enabled"] == 0

    def test_enable_2fa(self, db):
        asyncio.run(database.create_user("2fauser2", "pw"))
        asyncio.run(database.set_totp_secret("2fauser2", "SECRET456"))
        result = asyncio.run(database.enable_2fa("2fauser2"))
        assert result is True

        info = asyncio.run(database.get_user_2fa("2fauser2"))
        assert info["two_factor_enabled"] == 1

    def test_enable_2fa_without_secret(self, db):
        asyncio.run(database.create_user("2fauser3", "pw"))
        result = asyncio.run(database.enable_2fa("2fauser3"))
        assert result is False

    def test_disable_2fa(self, db):
        asyncio.run(database.create_user("2fauser4", "pw"))
        asyncio.run(database.set_totp_secret("2fauser4", "SECRET789"))
        asyncio.run(database.enable_2fa("2fauser4"))
        result = asyncio.run(database.disable_2fa("2fauser4"))
        assert result is True

        info = asyncio.run(database.get_user_2fa("2fauser4"))
        assert info["two_factor_enabled"] == 0
        assert info["totp_secret"] is None

    def test_get_2fa_nonexistent_user(self, db):
        info = asyncio.run(database.get_user_2fa("nonexistent"))
        assert info is None


# ---------------------------------------------------------------------------
# Audit logs
# ---------------------------------------------------------------------------


class TestAuditLogs:
    def test_log_and_get_audit_logs(self, db):
        asyncio.run(database.log_action("LOGIN", user_id="USR-1", resource="auth"))
        logs = asyncio.run(database.get_audit_logs())
        assert any(l["action"] == "LOGIN" for l in logs)

    def test_get_audit_logs_filtered_by_user(self, db):
        asyncio.run(database.log_action("ACTION1", user_id="USR-A"))
        asyncio.run(database.log_action("ACTION2", user_id="USR-B"))
        logs = asyncio.run(database.get_audit_logs(user_id="USR-A"))
        assert all(l["user_id"] == "USR-A" for l in logs)

    def test_get_audit_logs_filtered_by_action(self, db):
        asyncio.run(database.log_action("SPECIAL", user_id="USR-X"))
        asyncio.run(database.log_action("OTHER", user_id="USR-X"))
        logs = asyncio.run(database.get_audit_logs(action="SPECIAL"))
        assert all(l["action"] == "SPECIAL" for l in logs)


# ---------------------------------------------------------------------------
# Token revocation
# ---------------------------------------------------------------------------


class TestTokenRevocation:
    def test_revoke_and_check_token(self, db):
        future = datetime(2099, 1, 1, tzinfo=timezone.utc).isoformat()
        asyncio.run(database.revoke_token("jti-1", future))
        assert asyncio.run(database.is_token_revoked("jti-1")) is True

    def test_non_revoked_token(self, db):
        assert asyncio.run(database.is_token_revoked("jti-nonexistent")) is False

    def test_expired_revoked_token_not_revoked(self, db):
        past = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
        asyncio.run(database.revoke_token("jti-expired", past))
        assert asyncio.run(database.is_token_revoked("jti-expired")) is False

    def test_purge_expired_tokens(self, db):
        past = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
        future = datetime(2099, 1, 1, tzinfo=timezone.utc).isoformat()
        asyncio.run(database.revoke_token("jti-old", past))
        asyncio.run(database.revoke_token("jti-fresh", future))

        count = asyncio.run(database.purge_expired_revoked_tokens())
        assert count >= 1

        assert asyncio.run(database.is_token_revoked("jti-old")) is False
        assert asyncio.run(database.is_token_revoked("jti-fresh")) is True


# ---------------------------------------------------------------------------
# Risk helpers
# ---------------------------------------------------------------------------


class TestRiskHelpers:
    def test_get_daily_realized_loss_empty(self, db):
        loss = asyncio.run(database.get_daily_realized_loss())
        assert loss == 0.0

    def test_count_orders_today_empty(self, db):
        count = asyncio.run(database.count_orders_today())
        assert count == 0

    def test_count_orders_today_with_orders(self, db):
        asyncio.run(database.create_order("BTC/USD", "BUY", quantity=0.1))
        asyncio.run(database.create_order("ETH/USD", "SELL", quantity=1.0))
        count = asyncio.run(database.count_orders_today())
        assert count == 2


# ---------------------------------------------------------------------------
# Seed defaults
# ---------------------------------------------------------------------------


class TestSeedDefaults:
    def test_init_db_seeds_risk_limits(self, db):
        limits = asyncio.run(database.get_risk_limits())
        assert limits == database.DEFAULT_RISK_LIMITS

    def test_init_db_seeds_settings(self, db):
        settings = asyncio.run(database.get_settings())
        assert "general" in settings
        assert "notifications" in settings

    def test_init_db_seeds_admin_user(self, db):
        admin = asyncio.run(database.get_user("admin"))
        assert admin is not None
        assert admin["role"] == "admin"
        assert admin["principal_type"] == "human"


# ---------------------------------------------------------------------------
# Encrypt/decrypt/mask helpers
# ---------------------------------------------------------------------------


class TestSensitiveSettingsHelpers:
    def test_mask_sensitive_settings(self):
        notif = {"smtp_password": "secret", "telegram_bot_token": "tok", "email_to": "a@b.com"}
        masked = database._mask_sensitive_settings(notif)
        assert masked["smtp_password"] == "****"
        assert masked["telegram_bot_token"] == "****"
        assert masked["email_to"] == "a@b.com"

    def test_mask_sensitive_settings_empty(self):
        notif = {"smtp_password": "", "telegram_bot_token": ""}
        masked = database._mask_sensitive_settings(notif)
        assert masked["smtp_password"] == ""
        assert masked["telegram_bot_token"] == ""

    def test_encrypt_sensitive_settings(self):
        notif = {"smtp_password": "secret", "email_to": "a@b.com"}
        encrypted = database._encrypt_sensitive_settings(notif)
        assert encrypted["smtp_password"] != "secret"
        assert encrypted["email_to"] == "a@b.com"

    def test_decrypt_sensitive_settings(self):
        notif = {"smtp_password": "secret", "email_to": "a@b.com"}
        encrypted = database._encrypt_sensitive_settings(notif)
        decrypted = database._decrypt_sensitive_settings(encrypted)
        assert decrypted["smtp_password"] == "secret"
        assert decrypted["email_to"] == "a@b.com"
