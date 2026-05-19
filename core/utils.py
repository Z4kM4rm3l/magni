import re
import logging
import os

def setup_logger():
    log_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'logs', 'conversations.log')
    log_path = os.path.abspath(log_path)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler()
        ]
    )
    return logging.getLogger('magni')

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
