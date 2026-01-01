"""
Logging Configuration

Provides structured logging for the application with JSON formatting
for production and human-readable formatting for development.
"""

import logging
import sys
import json
from datetime import datetime
from typing import Any, Dict
from app.config.settings import settings


class JSONFormatter(logging.Formatter):
    """
    JSON formatter for structured logging.

    Outputs logs in JSON format for easy parsing by log aggregation tools.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data: Dict[str, Any] = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
        }

        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)

        # Add extra fields
        if hasattr(record, 'user_id'):
            log_data['user_id'] = record.user_id
        if hasattr(record, 'session_id'):
            log_data['session_id'] = record.session_id
        if hasattr(record, 'request_id'):
            log_data['request_id'] = record.request_id

        return json.dumps(log_data)


class TextFormatter(logging.Formatter):
    """
    Human-readable formatter for development.

    Colorizes output for better readability in terminal.
    """

    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',     # Cyan
        'INFO': '\033[32m',      # Green
        'WARNING': '\033[33m',   # Yellow
        'ERROR': '\033[31m',     # Red
        'CRITICAL': '\033[35m',  # Magenta
        'RESET': '\033[0m'       # Reset
    }

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with colors."""
        color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        reset = self.COLORS['RESET']

        # Format: [TIMESTAMP] LEVEL module.function:line - message
        timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        location = f"{record.module}.{record.funcName}:{record.lineno}"

        log_line = (
            f"{color}[{timestamp}] {record.levelname:8s}{reset} "
            f"{location:30s} - {record.getMessage()}"
        )

        if record.exc_info:
            log_line += '\n' + self.formatException(record.exc_info)

        return log_line


def setup_logging() -> None:
    """
    Configure logging for the application.

    Sets up formatters, handlers, and log levels based on settings.
    """
    # Get root logger
    root_logger = logging.getLogger()

    # Set log level from settings
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    root_logger.setLevel(log_level)

    # Remove existing handlers
    root_logger.handlers = []

    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)

    # Set formatter based on configuration
    if settings.LOG_FORMAT == "json":
        formatter = JSONFormatter()
    else:
        formatter = TextFormatter()

    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Silence noisy third-party loggers
    logging.getLogger('anthropic').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('uvicorn.access').setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance.

    Args:
        name: Name of the logger (typically __name__)

    Returns:
        Configured logger instance

    Example:
        logger = get_logger(__name__)
        logger.info("Processing message", extra={'session_id': session_id})
    """
    return logging.getLogger(name)


# Initialize logging on module import
setup_logging()
