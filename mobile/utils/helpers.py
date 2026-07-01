import os
from pathlib import Path
import yaml
from typing import Dict, Any, Optional, List
import logging

logger = logging.getLogger(__name__)


def setup_mobile_directories(base_path: str = "mobile") -> None:
    """
    Create all necessary directories for mobile analysis.
    
    Args:
        base_path: Base path for mobile directory (default: "mobile")
    """
    directories = [
        base_path,
        f"{base_path}/logs",
        f"{base_path}/models",
        f"{base_path}/alerts",
        f"{base_path}/config",
        f"{base_path}/output",
        f"{base_path}/data",
        f"{base_path}/utils",
    ]
    
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)
        logger.debug(f"Ensured directory exists: {directory}")


def read_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Read configuration from YAML file.
    
    Args:
        config_path: Path to config file (default: mobile/config/alert_config.yaml)
    
    Returns:
        Configuration dictionary
    """
    if config_path is None:
        config_path = "mobile/config/alert_config.yaml"
    
    # Try multiple possible paths
    possible_paths = [
        config_path,
        "mobile/config/alert_config.yaml",
        "config/alert_config.yaml",
        "alert_config.yaml"
    ]
    
    for path in possible_paths:
        try:
            with open(path, "r") as f:
                config = yaml.safe_load(f)
            logger.info(f"Loaded configuration from {path}")
            return config if config else {}
        except FileNotFoundError:
            continue
        except yaml.YAMLError as e:
            logger.error(f"Error parsing config file {path}: {e}")
            continue
    
    logger.warning("No configuration file found, using defaults")
    return {}


def save_config(config: Dict[str, Any], config_path: str = "mobile/config/alert_config.yaml") -> bool:
    """
    Save configuration to YAML file.
    
    Args:
        config: Configuration dictionary to save
        config_path: Path to save configuration
    
    Returns:
        True if successful, False otherwise
    """
    try:
        Path(config_path).parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        logger.info(f"Saved configuration to {config_path}")
        return True
    except Exception as e:
        logger.error(f"Error saving config to {config_path}: {e}")
        return False


def get_nested_config(config: Dict[str, Any], keys: List[str], default: Any = None) -> Any:
    """
    Get nested configuration value.
    
    Args:
        config: Configuration dictionary
        keys: List of keys to traverse (e.g., ["alert", "thresholds", "high"])
        default: Default value if key not found
    
    Returns:
        Configuration value or default
    """
    value = config
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return default
    return value


def update_nested_config(config: Dict[str, Any], keys: List[str], value: Any) -> Dict[str, Any]:
    """
    Update nested configuration value.
    
    Args:
        config: Configuration dictionary
        keys: List of keys to traverse
        value: Value to set
    
    Returns:
        Updated configuration dictionary
    """
    current = config
    for key in keys[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value
    return config


def validate_config(config: Dict[str, Any], required_sections: List[str] = None) -> Tuple[bool, List[str]]:
    """
    Validate configuration dictionary.
    
    Args:
        config: Configuration dictionary
        required_sections: List of required top-level sections
    
    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    if required_sections is None:
        required_sections = ["alert", "notifications"]
    
    errors = []
    
    for section in required_sections:
        if section not in config:
            errors.append(f"Missing required configuration section: {section}")
    
    # Validate email config
    if config.get("notifications", {}).get("email", {}).get("enabled", False):
        email_config = config["notifications"]["email"]
        required_email = ["smtp_server", "email_user", "email_password", "email_to"]
        for field in required_email:
            if not email_config.get(field):
                errors.append(f"Missing email configuration: {field}")
    
    # Validate webhook config
    if config.get("notifications", {}).get("webhook", {}).get("enabled", False):
        if not config["notifications"]["webhook"].get("url"):
            errors.append("Webhook enabled but URL not configured")
    
    return len(errors) == 0, errors


def get_default_config() -> Dict[str, Any]:
    """Return default configuration."""
    return {
        "alert": {
            "enabled": True,
            "min_stations": 15,
            "thresholds": {
                "low": 0.5,
                "medium": 0.7,
                "high": 0.8,
                "critical": 0.9
            },
            "cooldown_minutes": 60
        },
        "notifications": {
            "email": {
                "enabled": False,
                "smtp_server": "smtp.gmail.com",
                "smtp_port": 587,
                "username": "",
                "password": "",
                "from_addr": "alerts@pietroscik.com",
                "to_addrs": [],
                "use_tls": True
            },
            "webhook": {
                "enabled": True,
                "url": "",
                "format": "discord"
            },
            "sms": {
                "enabled": False,
                "provider": "twilio",
                "account_sid": "",
                "auth_token": "",
                "from_number": "",
                "to_numbers": []
            }
        },
        "model": {
            "type": "xgboost",
            "random_forest": {
                "n_estimators": 200,
                "max_depth": 10,
                "class_weight": "balanced",
                "random_state": 42
            },
            "xgboost": {
                "n_estimators": 200,
                "max_depth": 6,
                "learning_rate": 0.1,
                "scale_pos_weight": 10,
                "random_state": 42
            },
            "paths": {
                "model_dir": "mobile/models",
                "model_file": "modello_rischio.pkl",
                "scaler_file": "scaler.pkl"
            }
        },
        "features": {
            "temporal_windows": [6, 12, 24, 48],
            "spatial": {
                "enabled": True,
                "center_lat": 40.8062,
                "center_lon": 14.1410,
                "max_distance_km": 50.0
            },
            "seismological": {
                "enabled": True,
                "b_value_window": 24,
                "moran_window": 24
            }
        },
        "logging": {
            "level": "INFO",
            "log_dir": "mobile/logs",
            "max_file_size_mb": 10,
            "backup_count": 5
        },
        "campi_flegrei": {
            "default_min_stations": 15,
            "default_alert_threshold": 0.65,
            "coincidence_window": 5.0,
            "block_days": 1
        }
    }
