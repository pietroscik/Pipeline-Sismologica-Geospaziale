"""
Test for alert_system module.
"""

import pytest
import os
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import sys

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mobile.alert_system import (
    AlertSystem,
    get_alert_system,
    reset_alert_system,
    validate_alert_config,
    encrypt_credential
)


class TestAlertSystemInitialization:
    """Tests for AlertSystem initialization."""

    def test_default_initialization(self):
        """Test initialization with default settings."""
        alert_system = AlertSystem()
        assert alert_system is not None
        assert alert_system.alerts_log == []
        assert alert_system.alerts_dir.exists()

    def test_config_path_initialization(self, tmp_path):
        """Test initialization with config file."""
        config_file = tmp_path / "alert_config.yaml"
        config_content = """
email_enabled: true
email_smtp: smtp.test.com
email_port: 587
webhook_enabled: false
"""
        config_file.write_text(config_content)
        
        alert_system = AlertSystem(config_path=str(config_file))
        assert alert_system.config.get("email_enabled") == True
        assert alert_system.config.get("email_smtp") == "smtp.test.com"

    def test_environment_variable_initialization(self):
        """Test initialization with environment variables."""
        with patch.dict(os.environ, {
            "SMTP_SERVER": "smtp.env.com",
            "SMTP_PORT": "587",
            "EMAIL_ENABLED": "true"
        }):
            reset_alert_system()
            alert_system = get_alert_system()
            assert alert_system.config.get("email_smtp") == "smtp.env.com"
            assert alert_system.config.get("email_port") == 587

    def test_environment_specific_config(self, tmp_path, monkeypatch):
        """Test loading environment-specific config files."""
        # Create test config files
        dev_config = tmp_path / "alert_config.dev.yaml"
        dev_config.write_text("email_enabled: true
webhook_enabled: false
")
        
        prod_config = tmp_path / "alert_config.prod.yaml"
        prod_config.write_text("email_enabled: false
webhook_enabled: true
")
        
        # Mock PROJECT_ROOT to use tmp_path
        with patch('mobile.alert_system.PROJECT_ROOT', tmp_path):
            # Test dev environment
            monkeypatch.setenv("ENVIRONMENT", "dev")
            reset_alert_system()
            alert_system = AlertSystem()
            assert alert_system.config.get("email_enabled") == True
            assert alert_system.config.get("webhook_enabled") == False
            
            # Test prod environment
            monkeypatch.setenv("ENVIRONMENT", "prod")
            reset_alert_system()
            alert_system = AlertSystem()
            assert alert_system.config.get("email_enabled") == False
            assert alert_system.config.get("webhook_enabled") == True


class TestAlertSystemValidation:
    """Tests for configuration validation."""

    def test_validate_config_all_disabled(self):
        """Test validation when all channels are disabled."""
        alert_system = AlertSystem(config={"email_enabled": False, "webhook_enabled": False, "sms_enabled": False})
        is_valid, errors = alert_system.validate_config()
        assert not is_valid
        assert any("at least one" in error.lower() for error in errors)

    def test_validate_config_email_missing_credentials(self):
        """Test validation when email is enabled but credentials are missing."""
        alert_system = AlertSystem(config={
            "email_enabled": True,
            "email_smtp": "",
            "email_user": "",
            "email_password": ""
        })
        is_valid, errors = alert_system.validate_config()
        assert not is_valid
        assert any("email" in error.lower() for error in errors)

    def test_validate_config_webhook_missing_url(self):
        """Test validation when webhook is enabled but URL is missing."""
        alert_system = AlertSystem(config={
            "webhook_enabled": True,
            "webhook_url": ""
        })
        is_valid, errors = alert_system.validate_config()
        assert not is_valid
        assert any("webhook" in error.lower() for error in errors)

    def test_validate_config_valid(self):
        """Test validation with valid configuration."""
        alert_system = AlertSystem(config={
            "email_enabled": False,
            "webhook_enabled": False,
            "sms_enabled": False
        })
        # Add at least one enabled channel
        alert_system.config["email_enabled"] = True
        alert_system.config["email_smtp"] = "smtp.test.com"
        alert_system.config["email_port"] = 587
        alert_system.config["email_user"] = "test@test.com"
        alert_system.config["email_password"] = "password"
        alert_system.config["email_to"] = ["recipient@test.com"]
        
        is_valid, errors = alert_system.validate_config()
        assert is_valid
        assert len(errors) == 0

    def test_validate_alert_config_function(self):
        """Test standalone validate_alert_config function."""
        is_valid, errors = validate_alert_config()
        # Should work even if config is incomplete
        assert isinstance(is_valid, bool)
        assert isinstance(errors, list)


class TestAlertSystemThresholds:
    """Tests for alert threshold functionality."""

    def test_check_threshold_below(self):
        """Test threshold check with risk below threshold."""
        alert_system = AlertSystem()
        result = alert_system.check_threshold(risk_index=0.3, threshold=0.5)
        assert not result
        assert len(alert_system.alerts_log) == 0

    def test_check_threshold_above(self):
        """Test threshold check with risk above threshold."""
        alert_system = AlertSystem()
        result = alert_system.check_threshold(risk_index=0.8, threshold=0.5)
        assert result
        assert len(alert_system.alerts_log) == 1

    def test_check_threshold_default(self):
        """Test threshold check with default threshold."""
        alert_system = AlertSystem()
        result = alert_system.check_threshold(risk_index=0.95)
        assert result
        assert len(alert_system.alerts_log) == 1

    def test_custom_thresholds(self):
        """Test with custom threshold configuration."""
        alert_system = AlertSystem(config={
            "alert_thresholds": {
                "LOW": 0.3,
                "MEDIUM": 0.5,
                "HIGH": 0.7,
                "CRITICAL": 0.9
            }
        })
        
        # Test HIGH threshold
        result = alert_system.check_threshold(risk_index=0.8)
        assert result
        assert alert_system.alerts_log[0]["level"] == "HIGH"

    def test_get_alert_level(self):
        """Test alert level determination."""
        alert_system = AlertSystem()
        
        # Test each level
        assert alert_system._get_alert_level(0.4) == "LOW"
        assert alert_system._get_alert_level(0.6) == "MEDIUM"
        assert alert_system._get_alert_level(0.85) == "HIGH"
        assert alert_system._get_alert_level(0.96) == "CRITICAL"


class TestAlertSystemNotifications:
    """Tests for notification functionality."""

    def test_trigger_notifications_no_channels(self):
        """Test notification triggering with no channels enabled."""
        alert_system = AlertSystem(config={
            "email_enabled": False,
            "webhook_enabled": False,
            "sms_enabled": False
        })
        
        alert = {
            "timestamp": "2024-01-01T00:00:00",
            "level": "HIGH",
            "risk_index": 0.8,
            "message": "Test alert"
        }
        
        # Should not raise error even with no channels
        alert_system._trigger_notifications(alert)

    @patch('mobile.alert_system.smtplib.SMTP')
    def test_send_email(self, mock_smtp):
        """Test email sending."""
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server
        
        alert_system = AlertSystem(config={
            "email_enabled": True,
            "email_smtp": "smtp.test.com",
            "email_port": 587,
            "email_user": "test@test.com",
            "email_password": "password",
            "email_from": "test@test.com",
            "email_to": ["recipient@test.com"]
        })
        
        alert = {
            "timestamp": "2024-01-01T00:00:00",
            "level": "HIGH",
            "risk_index": 0.8,
            "message": "Test alert"
        }
        
        alert_system._send_email(alert)
        
        # Check that SMTP was called
        mock_smtp.assert_called_once()

    @patch('mobile.alert_system.requests.post')
    def test_send_webhook(self, mock_post):
        """Test webhook sending."""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response
        
        alert_system = AlertSystem(config={
            "webhook_enabled": True,
            "webhook_url": "https://webhook.test.com"
        })
        
        alert = {
            "timestamp": "2024-01-01T00:00:00",
            "level": "HIGH",
            "risk_index": 0.8,
            "message": "Test alert"
        }
        
        alert_system._send_webhook(alert)
        
        # Check that webhook was called
        mock_post.assert_called_once()

    @patch('mobile.alert_system.Client')
    def test_send_sms(self, mock_client):
        """Test SMS sending."""
        mock_sms_client = MagicMock()
        mock_client.return_value = mock_sms_client
        
        alert_system = AlertSystem(config={
            "sms_enabled": True,
            "sms_account_sid": "AC123",
            "sms_auth_token": "token123",
            "sms_from": "+1234567890",
            "sms_to": ["+1987654321"]
        })
        
        alert = {
            "timestamp": "2024-01-01T00:00:00",
            "level": "HIGH",
            "risk_index": 0.8,
            "message": "Test alert"
        }
        
        alert_system._send_sms(alert)
        
        # Check that Twilio client was used
        mock_client.assert_called_once()


class TestAlertSystemHistory:
    """Tests for alert history functionality."""

    def test_save_alert(self, tmp_path, monkeypatch):
        """Test saving alert to history."""
        with patch('mobile.alert_system.PROJECT_ROOT', tmp_path):
            alert_system = AlertSystem()
            
            alert = {
                "timestamp": "2024-01-01T00:00:00",
                "level": "HIGH",
                "risk_index": 0.8,
                "message": "Test alert"
            }
            
            alert_system._save_alert(alert)
            
            # Check that files were created
            jsonl_file = alert_system.alerts_dir / "alerts_log.jsonl"
            csv_file = alert_system.alerts_dir / "alerts_log.csv"
            
            assert jsonl_file.exists()
            assert csv_file.exists()

    def test_get_alert_history(self, tmp_path, monkeypatch):
        """Test retrieving alert history."""
        with patch('mobile.alert_system.PROJECT_ROOT', tmp_path):
            alert_system = AlertSystem()
            
            # Save some alerts
            for i in range(3):
                alert = {
                    "timestamp": f"2024-01-0{i+1}T00:00:00",
                    "level": "HIGH",
                    "risk_index": 0.8 + i * 0.01,
                    "message": f"Test alert {i}"
                }
                alert_system._save_alert(alert)
            
            # Retrieve history
            history = alert_system.get_alert_history(limit=2)
            
            assert len(history) == 2

    def test_clear_alert_history(self, tmp_path, monkeypatch):
        """Test clearing alert history."""
        with patch('mobile.alert_system.PROJECT_ROOT', tmp_path):
            alert_system = AlertSystem()
            
            # Save an alert
            alert = {"timestamp": "2024-01-01T00:00:00", "level": "HIGH"}
            alert_system._save_alert(alert)
            
            # Clear history
            alert_system.clear_alert_history()
            
            # Check that files were removed
            jsonl_file = alert_system.alerts_dir / "alerts_log.jsonl"
            csv_file = alert_system.alerts_dir / "alerts_log.csv"
            
            assert not jsonl_file.exists()
            assert not csv_file.exists()


class TestAlertSystemSingleton:
    """Tests for singleton pattern."""

    def test_get_alert_system_singleton(self):
        """Test that get_alert_system returns the same instance."""
        reset_alert_system()
        system1 = get_alert_system()
        system2 = get_alert_system()
        assert system1 is system2

    def test_reset_alert_system(self):
        """Test that reset_alert_system creates a new instance."""
        reset_alert_system()
        system1 = get_alert_system()
        reset_alert_system()
        system2 = get_alert_system()
        assert system1 is not system2


class TestErrorAlert:
    """Tests for error alert functionality."""

    def test_trigger_error_alert(self):
        """Test triggering error alert."""
        alert_system = AlertSystem()
        
        try:
            raise ValueError("Test error")
        except ValueError as e:
            alert_system.trigger_error_alert(e, "test_context")
        
        assert len(alert_system.alerts_log) == 1
        assert alert_system.alerts_log[0]["type"] == "ERROR"
        assert alert_system.alerts_log[0]["level"] == "CRITICAL"


class TestEncryption:
    """Tests for encryption functionality."""

    def test_encrypt_credential(self, monkeypatch):
        """Test credential encryption."""
        test_key = "test_key_12345678901234567890123456789012="
        monkeypatch.setenv("ENCRYPTION_KEY", test_key)
        
        try:
            encrypted = encrypt_credential("test_key", "secret_value", test_key)
            assert encrypted.startswith("ENC:")
        except ImportError:
            pytest.skip("Cryptography library not installed")

    def test_encryption_without_key(self):
        """Test encryption without encryption key."""
        with pytest.raises(ValueError, match="ENCRYPTION_KEY"):
            encrypt_credential("test_key", "secret_value")

    def test_alert_system_encryption(self, tmp_path, monkeypatch):
        """Test AlertSystem encryption/decryption."""
        test_key = "test_key_12345678901234567890123456789012="
        monkeypatch.setenv("ENCRYPTION_KEY", test_key)
        
        try:
            with patch('mobile.alert_system.PROJECT_ROOT', tmp_path):
                alert_system = AlertSystem()
                alert_system.encryption_key = test_key
                
                # Test encryption
                encrypted = alert_system._encrypt_value("secret")
                assert encrypted.startswith("ENC:")
                
                # Test decryption by setting config with encrypted value
                alert_system.config["test_secret"] = encrypted
                alert_system._decrypt_config_values()
                
                # The value should be decrypted
                assert alert_system.config.get("test_secret") == "secret"
        except ImportError:
            pytest.skip("Cryptography library not installed")
