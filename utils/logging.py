import logging

def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    """Configure and return a logger instance.

    Args:
        name (str): Name of the logger (typically module name).
        level (str): Logging level (default: INFO).

    Returns:
        logging.Logger: Configured logger instance.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(getattr(logging, level.upper()))
    return logger