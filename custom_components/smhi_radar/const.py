"""Constants for SMHI Weather Radar."""

from homeassistant.const import Platform

DOMAIN = "smhi_radar"
NAME = "SMHI Weather Radar"
PLATFORMS = [Platform.IMAGE, Platform.SENSOR, Platform.BINARY_SENSOR]

API_URL = "https://opendata-download-radar.smhi.se/api/version/latest/area/sweden/product/comp"
API_HOST = "opendata-download-radar.smhi.se"
FORECAST_URL = (
    "https://opendata-download-metfcst.smhi.se/api/category/snow1g/version/1"
    "/geotype/point/lon/{lon}/lat/{lat}/data.json"
)
DEFAULT_TIMEOUT = 30

CONF_UPDATE_INTERVAL = "update_interval"
CONF_STALE_THRESHOLD = "stale_threshold"
CONF_USE_HOME_LOCATION = "use_home_location"
CONF_LOCATION = "location"
CONF_PERSONS = "persons"
CONF_LOCAL_RADIUS = "local_radius"
CONF_NEARBY_RADIUS = "nearby_radius"
CONF_ANIMATE = "animate"

DEFAULT_UPDATE_INTERVAL = 5
DEFAULT_STALE_THRESHOLD = 15
MIN_UPDATE_INTERVAL = 5
MAX_UPDATE_INTERVAL = 60
MIN_STALE_THRESHOLD = 5
MAX_STALE_THRESHOLD = 180
DEFAULT_LOCAL_RADIUS = 100
MIN_LOCAL_RADIUS = 25
MAX_LOCAL_RADIUS = 300
DEFAULT_NEARBY_RADIUS = 10
MIN_NEARBY_RADIUS = 1
MAX_NEARBY_RADIUS = 50
DEFAULT_ANIMATE = False

FRAME_INTERVAL_MINUTES = 5
HISTORY_MINUTES = 60
# Re-render at most this often when a tracked person moves (seconds).
PERSON_RENDER_COOLDOWN = 60
PERSON_RENDER_COOLDOWN_ANIMATED = 300

# Precipitation type from the snow1g point forecast (sensors only).
FORECAST_REFRESH_MINUTES = 30
FORECAST_MAX_AGE_HOURS = 3
# WMO code table 4.201, used by snow1g predominant_precipitation_type_at_surface.
PRECIPITATION_TYPES = {
    1: "rain",
    2: "thunderstorm",
    3: "freezing_rain",
    4: "mixed_ice",
    5: "snow",
    6: "wet_snow",
    7: "rain_and_snow",
    8: "ice_pellets",
    9: "graupel",
    10: "hail",
    11: "drizzle",
    12: "freezing_drizzle",
}
TYPE_NONE = "none"

# SMHI composite extent in SWEREF99 TM (EPSG:3006), outer corners.
SWEREF_MIN_NORTHING = 5983984.0
SWEREF_MIN_EASTING = 126648.0
SWEREF_MAX_NORTHING = 7771252.0
SWEREF_MAX_EASTING = 1075693.0
