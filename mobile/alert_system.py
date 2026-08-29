"""

Alert System for Seismic Risk Monitoring.



This module provides a comprehensive alerting system for the mobile analysis pipeline,

supporting multiple notification channels (email, webhook, SMS) and alert logging.



Environment Variables:

    The system can be configured using environment variables with the following naming convention:

    - SMTP_SERVER, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, EMAIL_ENABLED, EMAIL_FROM, EMAIL_TO

    - WEBHOOK_URL, WEBHOOK_ENABLED

    - TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER, SMS_TO_NUMBERS, SMS_ENABLED

    - TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ENABLED

    - THRESHOLD_LOW, THRESHOLD_MEDIUM, THRESHOLD_HIGH, THRESHOLD_CRITICAL (for alert thresholds)

    - ENVIRONMENT (dev, prod, test) for multi-environment configuration

    - ENCRYPTION_KEY for optional credential encryption



    Environment variables take precedence over YAML configuration.

    Multi-environment configuration loads: alert_config.{environment}.yaml

"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
import requests
from cryptography.fernet import Fernet

# opzionale per test SMS (i test spesso patchano questo simbolo)
try:
    from twilio.rest import Client  # type: ignore
except Exception:
    Client = None  # noqa: N816

try:
    from path_utils import get_project_root

    PROJECT_ROOT = Path(get_project_root())
except Exception:
    PROJECT_ROOT = Path(__file__).resolve().parents[1]

logger = logging.getLogger("alert_system")

CONFIG_DIR = PROJECT_ROOT / "mobile" / "config"
ALERTS_DIR = PROJECT_ROOT / "mobile" / "alerts"
ALERTS_DIR.mkdir(parents=True, exist_ok=True)

ALERTS_LOG_CSV = ALERTS_DIR / "alerts_log.csv"
ALERTS_LOG_JSONL = ALERTS_DIR / "alerts_log.jsonl"

ENV_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "dev": {},
    "development": {},
    "test": {},
    "prod": {},
    "production": {},
}


def _derive_fernet_key(secret: str) -> bytes:
    """Return a Fernet key, preserving compatibility with legacy passphrases.

    ``scripts/encrypt_credentials.py`` emits tokens using a standard Fernet
    key. Those keys must be used unchanged here. Older deployments used an
    arbitrary passphrase, for which we retain the deterministic derivation.
    """
    if not isinstance(secret, str) or not secret.strip():
        raise ValueError("Invalid encryption key")
    raw_key = secret.strip().encode("utf-8")
    try:
        Fernet(raw_key)
        return raw_key
    except (ValueError, TypeError):
        digest = hashlib.sha256(raw_key).digest()
        return base64.urlsafe_b64encode(digest)


def _env_bool(name: str) -> Optional[bool]:
    v = os.getenv(name)
    if v is None:
        return None
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


class AlertSystem:
    """

    Comprehensive alerting system for seismic risk monitoring.



    Supports multiple notification channels:

    - Email (SMTP)

    - Webhooks (Discord, Slack, Teams, custom)

    - SMS (Twilio)

    - Telegram

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

    DEFAULT_THRESHOLDS = {"LOW": 0.0, "MEDIUM": 0.6, "HIGH": 0.8, "CRITICAL": 0.95}

    # Alert level colors for webhook embeds

    LEVEL_COLORS = {
        "LOW": 0xFFFF00,
        "MEDIUM": 0xFFA500,
        "HIGH": 0xFF5E00,
        "CRITICAL": 0xFF0000,
    }

    # Required configuration keys for each channel

    REQUIRED_CONFIG = {
        "email": [
            "email_smtp",
            "email_port",
            "email_user",
            "email_password",
            "email_from",
        ],
        "webhook": ["webhook_url"],
        "sms": ["sms_account_sid", "sms_auth_token", "sms_from", "sms_to"],
        "telegram": ["telegram_bot_token", "telegram_chat_id"],
    }

    def __init__(
        self,
        config_path: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        env: Optional[str] = None,
    ):
        """
        Initialize AlertSystem.
        - config (dict) has highest precedence (used directly if provided)
        - otherwise config_path (file) is used if provided
        - otherwise environment/default loading is used
        """
        # determine environment early for config file resolution/tests
        self.environment: str = (
            env
            or os.environ.get("ENVIRONMENT")
            or os.environ.get("ENV")
            or "development"
        )

        # IMPORTANTE: calcolare da PROJECT_ROOT runtime (compatibile con patch nei test)
        self.config_dir = PROJECT_ROOT / "mobile" / "config"
        self.alerts_dir = PROJECT_ROOT / "mobile" / "alerts"
        self.alerts_dir.mkdir(parents=True, exist_ok=True)

        if config is not None:
            loaded_cfg = config or {}
        elif config_path:
            loaded_cfg = self._load_yaml_config(config_path) or {}
        else:
            loaded_cfg = {}
            # The environment file is an override, not a replacement for the
            # base configuration.  This keeps optional values predictable.
            for p in (
                self.config_dir / "alert_config.yaml",
                PROJECT_ROOT / "alert_config.yaml",
            ):
                if p.exists():
                    loaded_cfg.update(self._load_yaml_config(str(p)) or {})
                    break
            for p in (
                self.config_dir / f"alert_config.{self.environment}.yaml",
                PROJECT_ROOT / f"alert_config.{self.environment}.yaml",
            ):
                if p.exists():
                    loaded_cfg.update(self._load_yaml_config(str(p)) or {})
                    break
            if not loaded_cfg:
                loaded_cfg = ENV_DEFAULTS.get(self.environment, {}).copy()

        base_cfg = self._default_config() if hasattr(self, "_default_config") else {}
        self.config = {**base_cfg, **loaded_cfg}

        env_config = self._load_env_config()
        self.config.update(env_config)
        if config is not None:
            self.config.update(config)

        self.encryption_key = self.config.get("encryption_key") or os.getenv(
            "ENCRYPTION_KEY"
        )
        if self.encryption_key:
            self._decrypt_config_values()

        self.alerts_log: List[Dict] = []
        self.cooldown_seconds = self.config.get("alert_cooldown_minutes", 60) * 60
        self.last_alert_time: Optional[datetime] = self._load_last_alert_time()

        # Initialize channels

        self._init_channels()
        self.validate_config()

        logger.info(f"AlertSystem initialized for environment: {self.environment}")

    def _load_yaml_config(self, path: str) -> Dict[str, Any]:
        p = Path(path)
        if not p.exists():
            return {}
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _default_config() -> Dict[str, Any]:
        """Safe defaults when no configuration file is available."""
        return {
            "email_enabled": False,
            "webhook_enabled": False,
            "sms_enabled": False,
            "telegram_enabled": False,
            "email_smtp": "smtp.gmail.com",
            "email_port": 587,
            "email_user": "",
            "email_password": "",
            "email_from": "",
            "email_to": [],
            "webhook_url": "",
            "sms_account_sid": "",
            "sms_auth_token": "",
            "sms_from": "",
            "sms_to": [],
            "telegram_bot_token": "",
            "telegram_chat_id": "",
            "alert_cooldown_minutes": 60,
        }

    def _load_env_config(self) -> Dict[str, Any]:
        """
        Load only explicitly-set env vars.
        Do NOT inject defaults that override file config.
        """
        env_cfg: Dict[str, Any] = {}

        b = _env_bool("EMAIL_ENABLED")
        if b is not None:
            env_cfg["email_enabled"] = b

        b = _env_bool("WEBHOOK_ENABLED")
        if b is not None:
            env_cfg["webhook_enabled"] = b

        b = _env_bool("SMS_ENABLED")
        if b is not None:
            env_cfg["sms_enabled"] = b

        b = _env_bool("TELEGRAM_ENABLED")
        if b is not None:
            env_cfg["telegram_enabled"] = b

        webhook_url = os.getenv("WEBHOOK_URL")
        if webhook_url is not None:
            env_cfg["webhook_url"] = webhook_url

        string_values = {
            "email_smtp": ("SMTP_SERVER",),
            "email_user": ("SMTP_USERNAME",),
            "email_password": ("SMTP_PASSWORD",),
            "email_from": ("SMTP_FROM", "EMAIL_FROM"),
        }
        for config_key, names in string_values.items():
            value = next(
                (os.getenv(name) for name in names if os.getenv(name) is not None), None
            )
            if value is not None:
                env_cfg[config_key] = value

        smtp_port = os.getenv("SMTP_PORT")
        if smtp_port is not None:
            try:
                env_cfg["email_port"] = int(smtp_port)
            except ValueError:
                logger.warning("Ignoring invalid SMTP port: %r", smtp_port)

        recipients = os.getenv("EMAIL_TO")
        if recipients is not None:
            env_cfg["email_to"] = [
                item.strip() for item in recipients.split(",") if item.strip()
            ]

        return env_cfg

    def _decrypt_config_values(self):
        """Decrypt encrypted values in configuration if encryption key is available."""
        try:
            key = _derive_fernet_key(self.encryption_key)
            fernet = Fernet(key)

            for config_key, value in list(self.config.items()):
                if isinstance(value, str) and value.startswith("ENC:"):
                    try:
                        decrypted = fernet.decrypt(value[4:].encode()).decode()
                        self.config[config_key] = decrypted
                        logger.info(f"Decrypted configuration key: {config_key}")
                    except Exception as e:
                        logger.warning(f"Failed to decrypt {config_key}: {e}")

        except ImportError:
            logger.warning(
                "Cryptography library not installed. Install with: pip install cryptography"
            )
        except Exception as e:
            logger.warning(f"Decryption failed: {e}")

    def _encrypt_value(self, value: str) -> str:
        if not self.encryption_key:
            raise ValueError("Encryption key not configured")
        try:
            key = _derive_fernet_key(self.encryption_key)
            fernet = Fernet(key)
            token = fernet.encrypt(value.encode())
            return f"ENC:{token.decode()}"
        except Exception as e:
            logger.warning(f"Encryption failed: {e}")
            raise ValueError(f"Encryption failed: {e}")

    def _decrypt_value(self, token: str) -> str:
        if not self.encryption_key:
            raise ValueError("Encryption key not configured")
        try:
            key = _derive_fernet_key(self.encryption_key)
            fernet = Fernet(key)
            return fernet.decrypt(token.encode()).decode()
        except Exception as e:
            logger.warning(f"Decryption failed: {e}")
            raise ValueError(f"Decryption failed: {e}")

    def encrypt_credential(self, key: str, value: str) -> str:
        """Encrypt a credential and return the encrypted string."""

        encrypted = self._encrypt_value(value)

        logger.info(f"Encrypted credential for {key}")

        return encrypted

    def validate_config(self) -> Tuple[bool, List[str]]:
        """
        Validate the configuration to ensure all required credentials are present.

        Returns:
            True if configuration is valid, False otherwise
        """

        errors: List[str] = []

        channels_enabled = any(
            [
                bool(self.config.get("email_enabled", False)),
                bool(self.config.get("webhook_enabled", False)),
                bool(self.config.get("sms_enabled", False)),
                bool(self.config.get("telegram_enabled", False)),
            ]
        )
        if not channels_enabled:
            errors.append("At least one alert channel should be enabled")

        if self.config.get("email_enabled", False):
            if not self.config.get("email_user") or not self.config.get(
                "email_password"
            ):
                errors.append("Email enabled but missing credentials")

        if self.config.get("webhook_enabled", False):
            if not self.config.get("webhook_url"):
                errors.append("Webhook enabled but webhook_url is missing")

        is_valid = len(errors) == 0
        if not is_valid:
            logger.warning(
                f"Configuration validation failed with {len(errors)} error(s)"
            )
            for e in errors:
                logger.warning(f"- {e}")

        # IMPORTANT: no destructive mutation of self.config
        return is_valid, errors

    def _init_channels(self):

        self.email_enabled = self.config.get("email_enabled", False)

        self.webhook_enabled = self.config.get("webhook_enabled", False)

        self.sms_enabled = self.config.get("sms_enabled", False)

        self.telegram_enabled = self.config.get("telegram_enabled", False)

        thresholds = self.config.get("alert_thresholds", self.config.get("thresholds"))
        if thresholds:

            # Normalize threshold keys to uppercase

            normalized_thresholds = {}

            for key, value in thresholds.items():

                normalized_thresholds[key.upper()] = value

            self.LEVEL_THRESHOLDS = {**self.DEFAULT_THRESHOLDS, **normalized_thresholds}

        else:

            self.LEVEL_THRESHOLDS = self.DEFAULT_THRESHOLDS.copy()

    def _load_last_alert_time(self) -> Optional[datetime]:
        """Loads the timestamp of the last non-error alert from the log file."""

        log_file = self.alerts_dir / "alerts_log.jsonl"

        if not log_file.exists():

            return None

        try:

            with open(log_file, "r") as f:

                lines = f.readlines()

            if not lines:

                return None

            # Find the last non-error alert

            for line in reversed(lines):

                try:

                    last_alert = json.loads(line.strip())

                    if last_alert.get("type") != "ERROR":

                        return datetime.fromisoformat(last_alert["timestamp"])

                except (json.JSONDecodeError, KeyError):

                    continue

            return None

        except (IOError, IndexError):

            return None

    def check_threshold(
        self,
        risk_index: float,
        threshold: Optional[float] = None,
        min_stations: int = 18,
        additional_info: Optional[Dict] = None,
    ) -> bool:

        if threshold is None:

            threshold = self.LEVEL_THRESHOLDS.get("HIGH", 0.7)

        alert_triggered = risk_index >= threshold

        if alert_triggered:

            alert = self._create_alert(
                risk_index, threshold, min_stations, additional_info
            )

            self.alerts_log.append(alert)

            self._save_alert(alert)

            self._trigger_notifications(alert)

            logger.info(f"Alert triggered: {alert['level']} (risk={risk_index:.2f})")

        return alert_triggered

    def trigger_alert(
        self,
        risk_level: float,
        triggering_stations: int,
        timestamp: str,
        threshold: Optional[float] = None,
    ):
        """

        Triggers an alert if the risk level is above the threshold.

        This method assumes a cooldown check has been performed externally.

        """

        if threshold is None:

            threshold = self.LEVEL_THRESHOLDS.get("HIGH", 0.7)

        additional_info = {"trigger_timestamp": timestamp}

        alert = self._create_alert(
            risk_level, threshold, triggering_stations, additional_info
        )

        self.alerts_log.append(alert)

        self._save_alert(alert)

        self.last_alert_time = datetime.fromisoformat(
            alert["timestamp"]
        )  # Update in-memory state

        self._trigger_notifications(alert)

        logger.info(f"Alert triggered: {alert['level']} (risk={risk_level:.2f})")

        return True

    @property
    def active_alert(self) -> bool:
        """Checks if an alert is currently in a cooldown period."""

        if self.last_alert_time is None:

            self.last_alert_time = self._load_last_alert_time()

            if self.last_alert_time is None:

                return False

        cooldown = timedelta(seconds=self.cooldown_seconds)

        if datetime.now() < self.last_alert_time + cooldown:

            return True

        return False

    def _create_alert(
        self,
        risk_index: float,
        threshold: float,
        min_stations: int,
        additional_info: Optional[Dict] = None,
    ) -> Dict:

        level = self._get_alert_level(risk_index)

        alert = {
            "timestamp": datetime.now().isoformat(),
            "risk_index": float(risk_index),
            "threshold": float(threshold),
            "min_stations": int(min_stations),
            "level": level,
            "message": self._generate_message(risk_index, level, min_stations),
            "additional_info": additional_info or {},
        }

        return alert

    def _get_alert_level(self, risk_score: float) -> str:
        """Return alert level label based on score"""
        thresholds = self.config.get("thresholds", {})
        low = float(thresholds.get("low", 0.3))
        medium = float(thresholds.get("medium", 0.6))
        high = float(thresholds.get("high", 0.8))
        critical = float(thresholds.get("critical", 0.95))

        if risk_score >= critical:
            return "CRITICAL"
        if risk_score >= high:
            return "HIGH"
        if risk_score >= medium:
            return "MEDIUM"
        return "LOW"

    def _generate_message(
        self, risk_index: float, level: str, min_stations: int
    ) -> str:

        return f"Alert: {level} - Risk: {risk_index:.2f} - Stations: {min_stations}"

    def _save_alert(self, alert: Dict) -> None:

        try:

            with open(self.alerts_dir / "alerts_log.jsonl", "a") as f:

                json.dump(alert, f)

                f.write("\n")

            df_path = self.alerts_dir / "alerts_log.csv"

            import pandas as pd

            if not df_path.exists():

                pd.DataFrame([alert]).to_csv(df_path, index=False)

            else:

                pd.DataFrame([alert]).to_csv(
                    df_path, mode="a", header=False, index=False
                )

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

        if self.telegram_enabled:

            self._send_telegram(alert)

    def _send_email(self, alert: Dict) -> None:

        try:

            smtp_server = self.config.get(
                "email_smtp", os.getenv("SMTP_SERVER", "smtp.gmail.com")
            )

            smtp_port = self.config.get("email_port", int(os.getenv("SMTP_PORT", 587)))

            email_user = self.config.get("email_user", os.getenv("SMTP_USERNAME", ""))

            email_password = self.config.get(
                "email_password", os.getenv("SMTP_PASSWORD", "")
            )

            email_from = self.config.get(
                "email_from", os.getenv("SMTP_FROM_ADDR", email_user)
            )

            email_to_list = self.config.get("email_to", [])

            if not email_to_list:

                email_to_env = os.getenv("SMTP_TO_ADDRS", "")

                if email_to_env:

                    email_to_list = [addr.strip() for addr in email_to_env.split(",")]

            if not all([email_user, email_password, email_to_list]):

                logger.warning("Email configuration incomplete, skipping email alert")

                return

            msg = MIMEMultipart()

            msg["From"] = email_from or email_user

            msg["To"] = ", ".join(email_to_list)

            msg["Subject"] = (
                f"[{alert['level']}] Sismic Risk Alert - {alert['risk_index']:.2f}"
            )

            body = f"Risk Index: {alert['risk_index']:.2f}, Level: {alert['level']}"

            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(smtp_server, smtp_port) as server:

                server.starttls()

                server.login(email_user, email_password)

                server.send_message(msg)

            logger.info(f"Email alert sent to {email_to_list}")

        except Exception as e:

            logger.error(f"Failed to send email alert: {e}")

    def _send_webhook(self, alert: Dict) -> None:

        webhook_url = self.config.get("webhook_url", os.getenv("WEBHOOK_URL", ""))

        if not webhook_url:

            webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "") or os.getenv(
                "SLACK_WEBHOOK_URL", ""
            )

        if not webhook_url:

            logger.warning("Webhook URL not configured, skipping webhook alert")

            return

        try:

            payload = {
                "content": f"Alert: {alert['level']} - Risk: {alert['risk_index']:.2f}",
                "embeds": [
                    {
                        "title": f"Risk Index: {alert['risk_index']:.2f}",
                        "description": f"Level: {alert['level']}",
                        "color": self.LEVEL_COLORS.get(alert["level"], 0x0000FF),
                        "timestamp": alert["timestamp"],
                        "footer": {"text": "Pipeline Sismologica Geospaziale"},
                    }
                ],
            }

            response = requests.post(webhook_url, json=payload, timeout=10)

            response.raise_for_status()

            logger.info(f"Webhook alert sent to {webhook_url}")

        except requests.exceptions.RequestException as e:

            logger.error(f"Failed to send webhook alert: {e}")

        except Exception as e:

            logger.error(f"Unexpected error sending webhook: {e}")

    def _send_sms(self, alert: Dict) -> None:

        if Client is None:
            logger.warning(
                "Twilio not installed, cannot send SMS. Install with: pip install twilio"
            )
            return

        try:
            account_sid = self.config.get(
                "sms_account_sid", os.getenv("TWILIO_ACCOUNT_SID", "")
            )
            auth_token = self.config.get(
                "sms_auth_token", os.getenv("TWILIO_AUTH_TOKEN", "")
            )
            from_number = self.config.get(
                "sms_from", os.getenv("TWILIO_PHONE_NUMBER", "")
            )
            to_numbers = self.config.get("sms_to", [])

            if not to_numbers:
                sms_to_env = os.getenv("SMS_TO_NUMBERS", "")
                if sms_to_env:
                    to_numbers = [num.strip() for num in sms_to_env.split(",")]

            if not all([account_sid, auth_token, from_number, to_numbers]):
                logger.warning("SMS configuration incomplete, skipping SMS alert")
                return

            client = Client(account_sid, auth_token)
            message_body = f"Alert: {alert['level']} - Risk: {alert['risk_index']:.2f}"

            for to_number in to_numbers:
                client.messages.create(
                    body=message_body, from_=from_number, to=to_number
                )

            logger.info(f"SMS alert sent to {to_numbers}")

        except Exception as e:
            logger.error(f"Failed to send SMS alert: {e}")

    def _send_telegram(self, alert: Dict) -> None:
        """Invia una notifica tramite bot Telegram."""

        token = self.config.get("telegram_bot_token")

        chat_id = self.config.get("telegram_chat_id")

        if not token or not chat_id:

            logger.warning(
                "Credenziali Telegram (token o chat_id) mancanti, salto notifica."
            )

            return

        try:

            # Formatta il messaggio usando Markdown per una migliore leggibilità

            risk_index_str = f"{alert.get('risk_index', 0.0):.2%}"

            threshold_str = f"{alert.get('threshold', 0.0):.2%}"

            message_text = (
                f"🌋 *ALLARME SISMICO - LIVELLO {alert['level']}*\n\n"
                f"*{alert['message']}*\n\n"
                f"Indice di Rischio: *{risk_index_str}*\n"
                f"Soglia Attivazione: *{threshold_str}*\n"
                f"Timestamp: `{alert['timestamp']}`"
            )

            url = f"https://api.telegram.org/bot{token}/sendMessage"

            payload = {
                "chat_id": chat_id,
                "text": message_text,
                "parse_mode": "Markdown",
            }

            response = requests.post(url, json=payload, timeout=10)

            response.raise_for_status()

            logger.info(f"Notifica Telegram inviata a chat_id: {chat_id}")

        except Exception as e:

            logger.error(f"Errore invio notifica Telegram: {e}")

    def trigger_error_alert(self, error: Exception, context: str = "") -> None:

        alert = {
            "timestamp": datetime.now().isoformat(),
            "level": "CRITICAL",
            "type": "ERROR",
            "message": f"Critical Error in {context}: {str(error)}",
            "error_type": type(error).__name__,
            "error_details": str(error),
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


def validate_alert_config(config: Optional[Dict[str, Any]] = None):
    alert_system = AlertSystem(config=config) if config is not None else AlertSystem()
    return alert_system.validate_config()


def encrypt_credential(
    key: str, value: str, encryption_key: Optional[str] = None
) -> str:
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

        encryption_key = os.getenv("ENCRYPTION_KEY")

    if not encryption_key:

        raise ValueError("ENCRYPTION_KEY environment variable not set")

    alert_system = AlertSystem()

    alert_system.encryption_key = encryption_key

    return alert_system._encrypt_value(value)
