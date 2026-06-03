"""
Alert System for Seismic Risk Monitoring.

This module provides a comprehensive alerting system for the mobile analysis pipeline,
supporting multiple notification channels (email, webhook, SMS) and alert logging.
"""

import json
import logging
import smtplib
import requests
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Dict, List, Optional, Any
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
    
    Usage:
        alert_system = AlertSystem(config_path=str(PROJECT_ROOT / "mobile" / "config" / "alert_config.yaml"))
        alert_system.check_threshold(risk_index=0.85)
    """
    
    # Alert level thresholds
    LEVEL_THRESHOLDS = {
        "LOW": 0.5,
        "MEDIUM": 0.7,
        "HIGH": 0.9,
        "CRITICAL": 0.95
    }
    
    # Alert level colors for webhook embeds
    LEVEL_COLORS = {
        "LOW": 0xFFFF00,     # Yellow
        "MEDIUM": 0xFFA500,  # Orange
        "HIGH": 0xFF5E00,    # Dark Orange
        "CRITICAL": 0xFF0000 # Red
    }
    
    def __init__(self, config_path: Optional[str] = None, config: Optional[Dict] = None):
        """
        Initialize the alert system.
        
        Args:
            config_path: Path to YAML config file
            config: Configuration dictionary (overrides config_path)
        """
        self.config = self._load_config(config_path) if config is None else config
        self.alerts_log: List[Dict] = []
        self.alerts_dir = PROJECT_ROOT / "mobile" / "alerts"
        self.alerts_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize notification channels
        self._init_channels()
        
        logger.info("AlertSystem initialized")
    
    def _load_config(self, config_path: str) -> Dict:
        """Load configuration from YAML file."""
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
    
    def _init_channels(self):
        """Initialize notification channels from config."""
        self.email_enabled = self.config.get("email_enabled", False)
        self.webhook_enabled = self.config.get("webhook_enabled", False)
        self.sms_enabled = self.config.get("sms_enabled", False)
        
        # Override thresholds from config
        if "alert_thresholds" in self.config:
            self.LEVEL_THRESHOLDS.update(self.config["alert_thresholds"])
    
    def check_threshold(
        self,
        risk_index: float,
        threshold: Optional[float] = None,
        min_stations: int = 18,
        additional_info: Optional[Dict] = None
    ) -> bool:
        """
        Check if risk index exceeds threshold and trigger alerts.
        
        Args:
            risk_index: Current risk index (0-1)
            threshold: Custom threshold (overrides config)
            min_stations: Minimum stations for alert
            additional_info: Additional context for alert message
            
        Returns:
            True if alert was triggered, False otherwise
        """
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
    
    def _create_alert(
        self,
        risk_index: float,
        threshold: float,
        min_stations: int,
        additional_info: Optional[Dict] = None
    ) -> Dict:
        """Create alert dictionary with all relevant information."""
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
        """Determine alert level based on risk index."""
        for level, threshold in sorted(self.LEVEL_THRESHOLDS.items(), key=lambda x: x[1], reverse=True):
            if risk_index >= threshold:
                return level
        return "LOW"
    
    def _generate_message(self, risk_index: float, level: str, min_stations: int) -> str:
        """Generate human-readable alert message."""
        return (
            f"🚨 **{level} SISMIC RISK ALERT** 🚨
"
            f"Risk Index: {risk_index:.2f} (Threshold: {self.LEVEL_THRESHOLDS.get(level, 0.7):.2f})
"
            f"Min Stations: ≥{min_stations}
"
            f"Expected: Event with ≥{min_stations} stations in next 24h
"
            f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    
    def _save_alert(self, alert: Dict) -> None:
        """Save alert to JSON Lines and CSV files."""
        try:
            # Save to JSON Lines (one JSON object per line)
            with open(self.alerts_dir / "alerts_log.jsonl", "a") as f:
                json.dump(alert, f)
                f.write("
")
            
            # Save to CSV
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
        """Trigger all enabled notification channels."""
        if self.email_enabled:
            self._send_email(alert)
        
        if self.webhook_enabled:
            self._send_webhook(alert)
        
        if self.sms_enabled:
            self._send_sms(alert)
    
    def _send_email(self, alert: Dict) -> None:
        """Send email notification."""
        try:
            # Get email config
            smtp_server = self.config.get("email_smtp", "smtp.gmail.com")
            smtp_port = self.config.get("email_port", 587)
            email_user = self.config.get("email_user", "")
            email_password = self.config.get("email_password", "")
            email_to = self.config.get("email_to", [])
            
            if not all([email_user, email_password, email_to]):
                logger.warning("Email configuration incomplete, skipping email alert")
                return
            
            # Create email message
            msg = MIMEMultipart()
            msg['From'] = email_user
            msg['To'] = ", ".join(email_to)
            msg['Subject'] = f"[{alert['level']}] Sismic Risk Alert - {alert['risk_index']:.2f}"
            
            # Email body
            body = f"""
            <h1>🚨 {alert['level']} SISMIC RISK ALERT 🚨</h1>
            <p><strong>Risk Index:</strong> {alert['risk_index']:.2f}</p>
            <p><strong>Threshold:</strong> {alert['threshold']:.2f}</p>
            <p><strong>Level:</strong> {alert['level']}</p>
            <p><strong>Timestamp:</strong> {alert['timestamp']}</p>
            <p><strong>Min Stations:</strong> ≥{alert['min_stations']}</p>
            <p><strong>Expected:</strong> Event with ≥{alert['min_stations']} stations in next 24h</p>
            <hr>
            <p><em>Generated by Pipeline Sismologica Geospaziale</em></p>
            """
            msg.attach(MIMEText(body, 'html'))
            
            # Send email
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(email_user, email_password)
                server.send_message(msg)
            
            logger.info(f"Email alert sent to {email_to}")
        except Exception as e:
            logger.error(f"Failed to send email alert: {e}")
    
    def _send_webhook(self, alert: Dict) -> None:
        """Send webhook notification (Discord, Slack, Teams, etc.)."""
        webhook_url = self.config.get("webhook_url", "")
        
        if not webhook_url:
            logger.warning("Webhook URL not configured, skipping webhook alert")
            return
        
        try:
            # Create payload (Discord-compatible format)
            payload = {
                "content": f"🚨 **{alert['level']} SISMIC ALERT** 🚨",
                "embeds": [{
                    "title": f"Risk Index: {alert['risk_index']:.2f}",
                    "description": f"Expected: Event with ≥{alert['min_stations']} stations in next 24h",
                    "color": self.LEVEL_COLORS.get(alert['level'], 0x0000FF),
                    "fields": [
                        {"name": "Threshold", "value": f"{alert['threshold']:.2f}", "inline": True},
                        {"name": "Level", "value": alert['level'], "inline": True},
                        {"name": "Timestamp", "value": alert['timestamp'], "inline": False}
                    ],
                    "timestamp": alert['timestamp'],
                    "footer": {"text": "Pipeline Sismologica Geospaziale"}
                }]
            }
            
            # Send to webhook
            response = requests.post(
                webhook_url,
                json=payload,
                timeout=10
            )
            response.raise_for_status()
            
            logger.info(f"Webhook alert sent to {webhook_url}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send webhook alert: {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending webhook: {e}")
    
    def _send_sms(self, alert: Dict) -> None:
        """Send SMS notification via Twilio."""
        try:
            from twilio.rest import Client
            
            account_sid = self.config.get("sms_account_sid", "")
            auth_token = self.config.get("sms_auth_token", "")
            from_number = self.config.get("sms_from", "")
            to_numbers = self.config.get("sms_to", [])
            
            if not all([account_sid, auth_token, from_number, to_numbers]):
                logger.warning("SMS configuration incomplete, skipping SMS alert")
                return
            
            # Initialize Twilio client
            client = Client(account_sid, auth_token)
            
            # Create SMS message
            message_body = (
                f"🚨 {alert['level']} SISMIC ALERT
"
                f"Risk: {alert['risk_index']:.2f}
"
                f"Expected: ≥{alert['min_stations']} stations in 24h
"
                f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            
            # Send to all recipients
            for to_number in to_numbers:
                client.messages.create(
                    body=message_body,
                    from_=from_number,
                    to=to_number
                )
            
            logger.info(f"SMS alert sent to {to_numbers}")
        except ImportError:
            logger.warning("Twilio not installed, cannot send SMS. Install with: pip install twilio")
        except Exception as e:
            logger.error(f"Failed to send SMS alert: {e}")
    
    def trigger_error_alert(self, error: Exception, context: str = "") -> None:
        """Trigger an alert for critical errors."""
        alert = {
            "timestamp": datetime.now().isoformat(),
            "level": "CRITICAL",
            "type": "ERROR",
            "message": f"❌ Critical Error in {context}: {str(error)}",
            "error_type": type(error).__name__,
            "error_details": str(error)
        }
        
        self.alerts_log.append(alert)
        self._save_alert(alert)
        self._trigger_notifications(alert)
        logger.error(f"Error alert triggered: {alert['message']}")
    
    def get_alert_history(self, limit: int = 100) -> List[Dict]:
        """Get recent alert history."""
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
        """Clear alert history files."""
        try:
            (self.alerts_dir / "alerts_log.jsonl").unlink(missing_ok=True)
            (self.alerts_dir / "alerts_log.csv").unlink(missing_ok=True)
            logger.info("Alert history cleared")
        except Exception as e:
            logger.error(f"Failed to clear alert history: {e}")


# Global alert system instance (can be used as singleton)
_alert_system: Optional[AlertSystem] = None


def get_alert_system(config_path: Optional[str] = None) -> AlertSystem:
    """Get or create global alert system instance."""
    global _alert_system
    if _alert_system is None:
        _alert_system = AlertSystem(config_path=config_path)
    return _alert_system


def reset_alert_system() -> None:
    """Reset global alert system instance."""
    global _alert_system
    _alert_system = None
