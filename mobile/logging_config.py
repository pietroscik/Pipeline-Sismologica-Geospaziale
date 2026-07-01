import logging
import logging.config
from pathlib import Path

# Import PROJECT_ROOT for consistent path resolution
from path_utils import PROJECT_ROOT

LOGGING_CONFIG = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'json': {
            '()': 'pythonjsonlogger.jsonlogger.JsonFormatter',
            'format': '%(asctime)s %(name)s %(levelname)s %(message)s %(funcName)s %(lineno)d'
        },
        'standard': {
            'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        }
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'level': 'INFO',
            'formatter': 'standard',
            'stream': 'ext://sys.stdout'
        },
        'file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(PROJECT_ROOT / "mobile" / "logs" / "analysis.log"),
            'maxBytes': 10485760,
            'backupCount': 5,
            'formatter': 'json'
        }
    },
    'root': {
        'level': 'INFO',
        'handlers': ['console', 'file']
    }
}

def setup_logging():
    (PROJECT_ROOT / "mobile" / "logs").mkdir(parents=True, exist_ok=True)
    logging.config.dictConfig(LOGGING_CONFIG)
    return logging.getLogger(__name__)
