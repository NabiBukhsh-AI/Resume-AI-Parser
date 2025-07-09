import os
import yaml
from typing import Dict, Any
from dotenv import load_dotenv
from utils.logging import get_logger

load_dotenv()
logger = get_logger(__name__)

def load_config() -> Dict[str, Any]:
    """Load configuration from config.yml file.

    Returns:
        Dict[str, Any]: Loaded configuration dictionary.

    Raises:
        FileNotFoundError: If config.yml is not found.
        yaml.YAMLError: If config.yml is invalid.
    """
    try:
        with open("config.yml", "r") as config_file:
            config = yaml.safe_load(config_file)
            logger.info("Configuration loaded successfully")
            return config
    except FileNotFoundError:
        logger.error("Configuration file 'config.yml' not found")
        raise
    except yaml.YAMLError as e:
        logger.error(f"Invalid YAML in config.yml: {str(e)}")
        raise


CONFIG = load_config()

UPLOAD_FOLDER = CONFIG["general"]["upload_folder"]
MAX_FILE_SIZE = int(CONFIG["general"]["max_file_size"])
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
SECURE_API_KEY = os.getenv("SECURE_API_KEY", None)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)