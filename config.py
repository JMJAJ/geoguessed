"""Configuration settings for GeoGuessr Live Map & Bot"""

# Server Configuration
DEVTOOLS_HOST = "127.0.0.1"
DEVTOOLS_PORT = 9222
HTTP_HOST = "127.0.0.1"
HTTP_PORT = 8080

# Bot Configuration
DEFAULT_BOT_CONFIG = {
    "enabled": False,
    "delay_seconds": 0.75,
    "accuracy_mode": "perfect"  # "perfect", "nearby", "region"
}

# Bot Constraints
MIN_DELAY_SECONDS = 0.1
MAX_DELAY_SECONDS = 30.0
VALID_ACCURACY_MODES = ["perfect", "nearby", "region"]

# Accuracy Mode Settings
ACCURACY_OFFSETS = {
    "perfect": (0.0, 0.0),
    "nearby": (0.009, 0.009),    # ~1km radius
    "region": (0.45, 0.45)       # ~50km radius
}

# Logging Configuration
LOG_FORMAT = '[%(asctime)s] [%(levelname)s] %(message)s'
LOG_DATE_FORMAT = '%H:%M:%S'

# WebSocket Configuration
WS_RECONNECT_DELAY = 5  # seconds
MAX_HISTORY_ITEMS = 15

# File Paths
INDEX_HTML_PATH = "index.html"
