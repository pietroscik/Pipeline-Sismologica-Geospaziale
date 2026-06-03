"""
Alert System for Seismic Risk Monitoring.

This module provides a comprehensive alerting system for the mobile analysis pipeline,
supporting multiple notification channels (email, webhook, SMS) and alert logging.

Environment Variables:
    The system can be configured using environment variables with the following naming convention:
    - SMTP_SERVER, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, etc.
    - WEBHOOK_URL, WEBHOOK_ENABLED
    - TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, SMS_TO_NUMBERS, SMS_ENABLED
    - ALERT_THRESHOLD_LOW, ALERT_THRESHOLD_MEDIUM, ALERT_THRESHOLD_HIGH, ALERT_THRESHOLD_CRITICAL
    - ENVIRONMENT (dev, prod, test) for multi-environment configuration
    - ENCRYPTION_KEY for optional credential encryption
    
    Environment variables take precedence over YAML configuration.
    Multi-environment configuration loads: alert_config.{environment}.yaml
"""

import json
import logging
import os
import smtplib
import requests
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import yaml

# Import PROJECT_ROOT for consistent path resolution
from path_utils import PROJECT_ROOT

logger = logging.getLogger(__name__)


class AlertSystem:
    """
    Comprehensive alerting system for seismic risk monitoring.
    
    Supports multiple notification channels:
    - Email (SMTP)
    - Webhooks (Discord, Slack, Teams, custom)
    - SMS (Twilio)
    - Local file logging
    
    Configuration can be provided via:
    1. Environment variables (highest priority)
    2. Environment-specific YAML config file (alert_config.{ENV}.yaml)
    3. Default YAML configuration file
    4. Direct dictionary parameter
    
    Supports multi-environment configuration (dev, prod, test) via ENVIRONMENT variable.
    Supports optional encryption for sensitive credentials.
    
    Usage:
        # Using environment variables (recommended for production)
        os.environ['ENVIRONMENT'] = 'prod'
        alert_system = AlertSystem()
        
        # Using YAML config file
        alert_system = AlertSystem(config_path=str(PROJECT_ROOT / "mobile" / "config" / "alert_config.yaml"))
        
        # Using direct dictionary
        alert_system = AlertSystem(config={"email_enabled": True})
        
        alert_system.check_threshold(risk_index=0.85)
    """
    
    # Default alert level thresholds
    DEFAULT_THRESHOLDS = {
        "LOW": 0.5,
        "MEDIUM": 0.7,
        "HIGH": 0.9,
        "CRITICAL": 0.95
    }
    
    # Alert level colors for webhook embeds
    LEVEL_COLORS = {
        "LOW": 0xFFFF00,
        "MEDIUM": 0xFFA500,
        "HIGH": 0xFF5E00,
        "CRITICAL": 0xFF0000
    }
    
    # Required configuration keys for each channel
    REQUIRED_CONFIG = {
        "email": ["email_smtp", "email_port", "email_user", "email_password", "email_from"],
        "webhook": ["webhook_url"],
        "sms": ["sms_account_sid", "sms_auth_token", "sms_from", "sms_to"]
    }
    
    def __init__(self, config_path: Optional[str] = None, config: Optional[Dict] = None):
        self.config = {}
        self.environment = os.getenv('ENVIRONMENT', 'dev').lower()
        self.encryption_key = os.getenv('ENCRYPTION_KEY')
        
        # Load configuration in priority order
        if config_path is not None:
            yaml_config = self._load_yaml_config(config_path)
            self.config.update(yaml_config)
        else:
            # Try to load environment-specific config first
            env_config_path = PROJECT_ROOT / "mobile" / "config" / f"alert_config.{self.environment}.yaml"
            if env_config_path.exists():
                env_config = self._load_yaml_config(str(env_config_path))
                self.config.update(env_config)
            else:
                # Fall back to default config
                default_config_path = PROJECT_ROOT / "mobile" / "config" / "alert_config.yaml"
                if default_config_path.exists():
                    default_config = self._load_yaml_config(str(default_config_path))
                    self.config.update(default_config)
        
        # Load environment variables (highest priority)
        env_config = self._load_env_config()
        self.config.update(env_config)
        
        # Apply direct config (highest priority after env vars)
        if config is not None:
            self.config.update(config)
        
        # Decrypt encrypted values if encryption key is available
        if self.encryption_key:
            self._decrypt_config_values()
        
        self.alerts_log: List[Dict] = []
        self.alerts_dir = PROJECT_ROOT / "mobile" / "alerts"
        self.alerts_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize channels
        self._init_channels()
        
        # Validate configuration
        self.validate_config()
        
        logger.info(f"AlertSystem initialized for environment: {self.environment}")
    
    def _load_yaml_config(self, config_path: str) -> Dict:
        if config_path is None:
            return {}
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
            logger.info(f"Configuration loaded from {config_path}")
            return config or {}
        except FileNotFoundError:
            logger.warning(f"Config file not found: {config_path}, using defaults")
            return {}
        except yaml.YAMLError as e:
            logger.error(f"Error parsing config file: {e}")
            return {}
    
    def _load_env_config(self) -> Dict:
        config = {}
        if os.getenv('SMTP_SERVER'):
            config['email_smtp'] = os.getenv('SMTP_SERVER')
        if os.getenv('SMTP_PORT'):
            try:
                config['email_port'] = int(os.getenv('SMTP_PORT'))
            except ValueError:
                logger.warning(f"Invalid SMTP_PORT value: {os.getenv('SMTP_PORT')}")
        if os.getenv('SMTP_USERNAME'):
            config['email_user'] = os.getenv('SMTP_USERNAME')
        if os.getenv('SMTP_PASSWORD'):
            config['email_password'] = os.getenv('SMTP_PASSWORD')
        if os.getenv('SMTP_FROM_ADDR'):
            config['email_from'] = os.getenv('SMTP_FROM_ADDR')
        if os.getenv('SMTP_TO_ADDRS'):
            config['email_to'] = [addr.strip() for addr in os.getenv('SMTP_TO_ADDRS').split(',')]
        if os.getenv('EMAIL_ENABLED'):
            config['email_enabled'] = os.getenv('EMAIL_ENABLED').lower() in ('true', '1', 'yes')
        if os.getenv('DISCORD_WEBHOOK_URL'):
            config['webhook_url'] = os.getenv('DISCORD_WEBHOOK_URL')
        elif os.getenv('SLACK_WEBHOOK_URL'):
            config['webhook_url'] = os.getenv('SLACK_WEBHOOK_URL')
        elif os.getenv('WEBHOOK_URL'):
            config['webhook_url'] = os.getenv('WEBHOOK_URL')
        if os.getenv('WEBHOOK_ENABLED'):
            config['webhook_enabled'] = os.getenv('WEBHOOK_ENABLED').lower() in ('true', '1', 'yes')
        if os.getenv('TWILIO_ACCOUNT_SID'):
            config['sms_account_sid'] = os.getenv('TWILIO_ACCOUNT_SID')
        if os.getenv('TWILIO_AUTH_TOKEN'):
            config['sms_auth_token'] = os.getenv('TWILIO_AUTH_TOKEN')
        if os.getenv('TWILIO_PHONE_NUMBER'):
            config['sms_from'] = os.getenv('TWILIO_PHONE_NUMBER')
        if os.getenv('SMS_TO_NUMBERS'):
            config['sms_to'] = [num.strip() for num in os.getenv('SMS_TO_NUMBERS').split(',')]
        if os.getenv('SMS_ENABLED'):
            config['sms_enabled'] = os.getenv('SMS_ENABLED').lower() in ('true', '1', 'yes')
        thresholds = {}
        for level in ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']:
            env_var = f'ALERT_THRESHOLD_{level}'
            if os.getenv(env_var):
                try:
                    thresholds[level.lower()] = float(os.getenv(env_var))
                except ValueError:
                    logger.warning(f"Invalid {env_var} value: {os.getenv(env_var)}")
        if thresholds:
            config['alert_thresholds'] = thresholds
        return config
    
    def _decrypt_config_values(self):
        """Decrypt encrypted values in configuration if encryption key is available."""
        try:
            from cryptography.fernet import Fernet
            fernet = Fernet(self.encryption_key.encode())
            
            # List of potentially encrypted keys
            encrypted_keys = [
                'email_password', 'sms_auth_token', 'sms_account_sid',
                'webhook_url', 'email_user'
            ]
            
            for key in encrypted_keys:
                if key in self.config and isinstance(self.config[key], str):
                    if self.config[key].startswith('ENC:'):
                        try:
                            decrypted = fernet.decrypt(self.config[key][4:].encode()).decode()
                            self.config[key] = decrypted
                            logger.info(f"Decrypted configuration key: {key}")
                        except Exception as e:
                            logger.warning(f"Failed to decrypt {key}: {e}")
        except ImportError:
            logger.warning("Cryptography library not installed. Install with: pip install cryptography")
        except Exception as e:
            logger.warning(f"Decryption failed: {e}")
    
    def _encrypt_value(self, value: str) -> str:
        """Encrypt a sensitive value for storage."""
        try:
            from cryptography.fernet import Fernet
            if not self.encryption_key:
                raise ValueError("ENCRYPTION_KEY environment variable not set")
            fernet = Fernet(self.encryption_key.encode())
            encrypted = fernet.encrypt(value.encode())
            return f"ENC:{encrypted.decode()}"
        except ImportError:
            raise ImportError("Cryptography library not installed. Install with: pip install cryptography")
        except Exception as e:
            raise ValueError(f"Encryption failed: {e}")
    
    def encrypt_credential(self, key: str, value: str) -> str:
        """Encrypt a credential and return the encrypted string."""
        encrypted = self._encrypt_value(value)
        logger.info(f"Encrypted credential for {key}")
        return encrypted
    
    def validate_config(self) -> Tuple[bool, List[str]]:
        """
        Validate the configuration to ensure all required credentials are present.
        
        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []
        
        # Check email configuration if enabled
        if self.config.get("email_enabled", False):
            email_required = self.REQUIRED_CONFIG.get("email", [])
            for key in email_required:
                value = self.config.get(key)
                if not value or (isinstance(value, list) and not value):
                    errors.append(f"Email configuration incomplete: {key} is required but not set")
            # Also check SMTP_TO_ADDRS
            if not self.config.get("email_to"):
                errors.append("Email configuration incomplete: email_to (SMTP_TO_ADDRS) is required")
        
        # Check webhook configuration if enabled
        if self.config.get("webhook_enabled", False):
            webhook_required = self.REQUIRED_CONFIG.get("webhook", [])
            for key in webhook_required:
                value = self.config.get(key)
                if not value:
                    errors.append(f"Webhook configuration incomplete: {key} is required but not set")
        
        # Check SMS configuration if enabled
        if self.config.get("sms_enabled", False):
            sms_required = self.REQUIRED_CONFIG.get("sms", [])
            for key in sms_required:
                value = self.config.get(key)
                if not value or (isinstance(value, list) and not value):
                    errors.append(f"SMS configuration incomplete: {key} is required but not set")
        
        # Check if at least one channel is enabled
        if not any([
            self.config.get("email_enabled", False),
            self.config.get("webhook_enabled", False),
            self.config.get("sms_enabled", False)
        ]):
            errors.append("At least one notification channel (email, webhook, or SMS) must be enabled")
        
        # Log warnings for each error
        for error in errors:
            logger.warning(error)
        
        if errors:
            logger.warning(f"Configuration validation failed with {len(errors)} error(s)")
        else:
            logger.info("Configuration validation passed")
        
        return (len(errors) == 0, errors)
    
    def _init_channels(self):
        self.email_enabled = self.config.get("email_enabled", False)
        self.webhook_enabled = self.config.get("webhook_enabled", False)
        self.sms_enabled = self.config.get("sms_enabled", False)
        if "alert_thresholds" in self.config:
            # Normalize threshold keys to uppercase
            normalized_thresholds = {}
            for key, value in self.config["alert_thresholds"].items():
                normalized_thresholds[key.upper()] = value
            self.LEVEL_THRESHOLDS = {**self.DEFAULT_THRESHOLDS, **normalized_thresholds}
        else:
            self.LEVEL_THRESHOLDS = self.DEFAULT_THRESHOLDS.copy()
    
    def check_threshold(self, risk_index: float, threshold: Optional[float] = None, min_stations: int = 18, additional_info: Optional[Dict] = None) -> bool:
        if threshold is None:
            threshold = self.LEVEL_THRESHOLDS.get("HIGH", 0.7)
        alert_triggered = risk_index >= threshold
        if alert_triggered:
            alert = self._create_alert(risk_index, threshold, min_stations, additional_info)
            self.alerts_log.append(alert)
            self._save_alert(alert)
            self._trigger_notifications(alert)
            logger.info(f"Alert triggered: {alert['level']} (risk={risk_index:.2f})")
        return alert_triggered
    
    def _create_alert(self, risk_index: float, threshold: float, min_stations: int, additional_info: Optional[Dict] = None) -> Dict:
        level = self._get_alert_level(risk_index)
        alert = {
            "timestamp": datetime.now().isoformat(),
            "risk_index": float(risk_index),
            "threshold": float(threshold),
            "min_stations": int(min_stations),
            "level": level,
            "message": self._generate_message(risk_index, level, min_stations),
            "additional_info": additional_info or {}
        }
        return alert
    
    def _get_alert_level(self, risk_index: float) -> str:
        for level, threshold in sorted(self.LEVEL_THRESHOLDS.items(), key=lambda x: x[1], reverse=True):
            if risk_index >= threshold:
                return level
        return "LOW"
    
    def _generate_message(self, risk_index: float, level: str, min_stations: int) -> str:
        return (f"Alert: {level} - Risk: {risk_index:.2f} - Stations: {min_stations}")
    
    def _save_alert(self, alert: Dict) -> None:
        try:
            with open(self.alerts_dir / "alerts_log.jsonl", "a") as f:
                json.dump(alert, f)
                f.write("
")
            df_path = self.alerts_dir / "alerts_log.csv"
            import pandas as pd
            if not df_path.exists():
                pd.DataFrame([alert]).to_csv(df_path, index=False)
            else:
                pd.DataFrame([alert]).to_csv(df_path, mode='a', header=False, index=False)
            logger.info(f"Alert saved to {self.alerts_dir}")
        except Exception as e:
            logger.error(f"Failed to save alert: {e}")
    
    def _trigger_notifications(self, alert: Dict) -> None:
        if self.email_enabled:
            self._send_email(alert)
        if self.webhook_enabled:
            self._send_webhook(alert)
        if self.sms_enabled:
            self._send_sms(alert)
    
    def _send_email(self, alert: Dict) -> None:
        try:
            smtp_server = self.config.get("email_smtp", os.getenv('SMTP_SERVER', "smtp.gmail.com"))
            smtp_port = self.config.get("email_port", int(os.getenv('SMTP_PORT', 587)))
            email_user = self.config.get("email_user", os.getenv('SMTP_USERNAME', ""))
            email_password = self.config.get("email_password", os.getenv('SMTP_PASSWORD', ""))
            email_from = self.config.get("email_from", os.getenv('SMTP_FROM_ADDR', email_user))
            email_to_list = self.config.get("email_to", [])
            if not email_to_list:
                email_to_env = os.getenv('SMTP_TO_ADDRS', '')
                if email_to_env:
                    email_to_list = [addr.strip() for addr in email_to_env.split(',')]
            if not all([email_user, email_password, email_to_list]):
                logger.warning("Email configuration incomplete, skipping email alert")
                return
            msg = MIMEMultipart()
            msg['From'] = email_from or email_user
            msg['To'] = ", ".join(email_to_list)
            msg['Subject'] = f"[{alert['level']}] Sismic Risk Alert - {alert['risk_index']:.2f}"
            body = f"Risk Index: {alert['risk_index']:.2f}, Level: {alert['level']}"
            msg.attach(MIMEText(body, 'plain'))
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(email_user, email_password)
                server.send_message(msg)
            logger.info(f"Email alert sent to {email_to_list}")
        except Exception as e:
            logger.error(f"Failed to send email alert: {e}")
    
    def _send_webhook(self, alert: Dict) -> None:
        webhook_url = self.config.get("webhook_url", os.getenv('WEBHOOK_URL', ''))
        if not webhook_url:
            webhook_url = os.getenv('DISCORD_WEBHOOK_URL', '') or os.getenv('SLACK_WEBHOOK_URL', '')
        if not webhook_url:
            logger.warning("Webhook URL not configured, skipping webhook alert")
            return
        try:
            payload = {
                "content": f"Alert: {alert['level']} - Risk: {alert['risk_index']:.2f}",
                "embeds": [{
                    "title": f"Risk Index: {alert['risk_index']:.2f}",
                    "description": f"Level: {alert['level']}",
                    "color": self.LEVEL_COLORS.get(alert['level'], 0x0000FF),
                    "timestamp": alert['timestamp'],
                    "footer": {"text": "Pipeline Sismologica Geospaziale"}
                }]
            }
            response = requests.post(webhook_url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info(f"Webhook alert sent to {webhook_url}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send webhook alert: {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending webhook: {e}")
    
    def _send_sms(self, alert: Dict) -> None:
        try:
            from twilio.rest import Client
            account_sid = self.config.get("sms_account_sid", os.getenv('TWILIO_ACCOUNT_SID', ""))
            auth_token = self.config.get("sms_auth_token", os.getenv('TWILIO_AUTH_TOKEN', ""))
            from_number = self.config.get("sms_from", os.getenv('TWILIO_PHONE_NUMBER', ""))
            to_numbers = self.config.get("sms_to", [])
            if not to_numbers:
                sms_to_env = os.getenv('SMS_TO_NUMBERS', '')
                if sms_to_env:
                    to_numbers = [num.strip() for num in sms_to_env.split(',')]
            if not all([account_sid, auth_token, from_number, to_numbers]):
                logger.warning("SMS configuration incomplete, skipping SMS alert")
                return
            client = Client(account_sid, auth_token)
            message_body = f"Alert: {alert['level']} - Risk: {alert['risk_index']:.2f}"
            for to_number in to_numbers:
                client.messages.create(body=message_body, from_=from_number, to=to_number)
            logger.info(f"SMS alert sent to {to_numbers}")
        except ImportError:
            logger.warning("Twilio not installed, cannot send SMS. Install with: pip install twilio")
        except Exception as e:
            logger.error(f"Failed to send SMS alert: {e}")
    
    def trigger_error_alert(self, error: Exception, context: str = "") -> None:
        alert = {
            "timestamp": datetime.now().isoformat(),
            "level": "CRITICAL",
            "type": "ERROR",
            "message": f"Critical Error in {context}: {str(error)}",
            "error_type": type(error).__name__,
            "error_details": str(error)
        }
        self.alerts_log.append(alert)
        self._save_alert(alert)
        self._trigger_notifications(alert)
        logger.error(f"Error alert triggered: {alert['message']}")
    
    def get_alert_history(self, limit: int = 100) -> List[Dict]:
        try:
            with open(self.alerts_dir / "alerts_log.jsonl", "r") as f:
                lines = f.readlines()
            alerts = []
            for line in lines[-limit:]:
                try:
                    alerts.append(json.loads(line.strip()))
                except json.JSONDecodeError:
                    continue
            return alerts
        except FileNotFoundError:
            return []
        except Exception as e:
            logger.error(f"Failed to read alert history: {e}")
            return []
    
    def clear_alert_history(self) -> None:
        try:
            (self.alerts_dir / "alerts_log.jsonl").unlink(missing_ok=True)
            (self.alerts_dir / "alerts_log.csv").unlink(missing_ok=True)
            logger.info("Alert history cleared")
        except Exception as e:
            logger.error(f"Failed to clear alert history: {e}")


_alert_system: Optional[AlertSystem] = None


def get_alert_system(config_path: Optional[str] = None) -> AlertSystem:
    global _alert_system
    if _alert_system is None:
        _alert_system = AlertSystem(config_path=config_path)
    return _alert_system


def reset_alert_system() -> None:
    global _alert_system
    _alert_system = None


def validate_alert_config() -> Tuple[bool, List[str]]:
    """
    Standalone function to validate alert configuration without initializing the system.
    Useful for CI/CD pipelines and startup checks.
    """
    alert_system = AlertSystem()
    return alert_system.validate_config()


def encrypt_credential(key: str, value: str, encryption_key: Optional[str] = None) -> str:
    """
    Standalone function to encrypt a credential.
    
    Args:
        key: The configuration key name (for logging)
        value: The sensitive value to encrypt
        encryption_key: Optional encryption key (defaults to ENCRYPTION_KEY env var)
    
    Returns:
        Encrypted string prefixed with 'ENC:'
    """
    if encryption_key is None:
        encryption_key = os.getenv('ENCRYPTION_KEY')
    if not encryption_key:
        raise ValueError("ENCRYPTION_KEY environment variable not set")
    
    alert_system = AlertSystem()
    alert_system.encryption_key = encryption_key
    return alert_system._encrypt_value(value)
