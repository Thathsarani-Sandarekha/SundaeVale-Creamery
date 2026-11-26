"""Small stdout event logger suitable for ECS awslogs. No invoice values."""
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger('sundaevale.events')


def configure_logging():
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def log_event(event, job_id, **fields):
    logger.info(json.dumps({'timestamp': datetime.now(timezone.utc).isoformat(),
                            'event': event, 'job_id': job_id, **fields}))
