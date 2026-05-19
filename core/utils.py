import re
import logging
import os

def setup_logger():
    logger = logging.getLogger('magni')
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        logger.addHandler(handler)

        # Only add file handler if data/logs directory exists or can be created
        try:
            log_path = os.path.abspath(
                os.path.join(os.path.dirname(__file__), '..', 'data', 'logs', 'conversations.log')
            )
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            logger.addHandler(logging.FileHandler(log_path))
        except Exception:
            pass  # File logging unavailable — console only

    return logger

def sanitize_input(text: str) -> str:
    if not text:
        return ""
    text = text.strip()
    text = re.sub(r'<[^>]+>', '', text)
    return text[:2000]

def format_response(text: str) -> str:
    if not text:
        return ""
    return text.strip()

logger = setup_logger()
