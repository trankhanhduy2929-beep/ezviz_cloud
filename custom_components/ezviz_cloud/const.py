"""Constants for the EZVIZ Cloud integration."""

from typing import Final

DOMAIN: Final = "ezviz_cloud"
MANUFACTURER: Final = "EZVIZ"

# Entry/data typing
ATTR_TYPE_CLOUD: Final = "EZVIZ_CLOUD_ACCOUNT"
ATTR_TYPE_CAMERA: Final = "CAMERA_ACCOUNT"
ATTR_SERIAL: Final = "serial"

# Cloud authentication data
CONF_SESSION_ID: Final = "session_id"
CONF_RF_SESSION_ID: Final = "rf_session_id"
CONF_USER_ID: Final = "user_id"
CONF_EZVIZ_ACCOUNT: Final = "ezviz_account"
CONF_AREA_ID: Final = "area_id"

# Camera credentials and stream settings
CONF_ENC_KEY: Final = "enc_key"
CONF_TEST_RTSP_CREDENTIALS: Final = "test_rtsp_credentials"
CONF_RTSP_USES_VERIFICATION_CODE: Final = "rtsp_uses_verification_code"
CONF_STREAM_MODE: Final = "stream_mode"
CONF_KEY_STATUS: Final = "key_status"
CONF_AUTO_CONFIGURED: Final = "auto_configured"

# Account behavior settings
CONF_MOTION_CLEAR_SECONDS: Final = "motion_clear_seconds"

# One-time values; never persist these in config entries.
CONF_ACCOUNT_OTP: Final = "sms_code"
CONF_DEVICE_KEY_OTP: Final = "device_key_otp"
CONF_SKIP_CAMERA_KEYS: Final = "skip_camera_keys"
CONF_CAM_VERIFICATION_2FA_CODE: Final = "cam_verification_2fa_code"
CONF_CAM_ENC_2FA_CODE: Final = "cam_encryption_2fa_code"

# Legacy naming retained for compatibility. This value is the RTSP path, not
# a string of ffmpeg command-line arguments.
CONF_FFMPEG_ARGUMENTS: Final = "ffmpeg_arguments"

# Region handling
CONF_REGION: Final = "region"
REGION_AUTO: Final = "auto"
REGION_EU: Final = "eu"
REGION_RU: Final = "ru"
REGION_CUSTOM: Final = "custom"

EU_URL: Final = "apiieu.ezvizlife.com"
RUSSIA_URL: Final = "apirus.ezvizru.com"

REGION_URLS: Final = {
    REGION_AUTO: EU_URL,
    REGION_EU: EU_URL,
    REGION_RU: RUSSIA_URL,
}

# Defaults
DEFAULT_CAMERA_USERNAME: Final = "admin"
DEFAULT_TIMEOUT: Final = 25
DEFAULT_MOTION_CLEAR_SECONDS: Final = 60
MIN_MOTION_CLEAR_SECONDS: Final = 1
MAX_MOTION_CLEAR_SECONDS: Final = 3600
DEFAULT_FFMPEG_ARGUMENTS: Final = "/Streaming/Channels/102"
DEFAULT_FETCH_MY_KEY: Final = "fetch_my_key"
DEFAULT_STREAM_MODE: Final = "local_rtsp"

STREAM_MODE_LOCAL_RTSP: Final = "local_rtsp"
STREAM_MODE_DISABLED: Final = "disabled"

KEY_STATUS_READY: Final = "ready"
KEY_STATUS_MISSING: Final = "missing"
KEY_STATUS_NOT_REQUIRED: Final = "not_required"

# Services
SERVICE_WAKE_DEVICE: Final = "wake_device"

# hass.data keys
DATA_COORDINATOR: Final = "coordinator"
MQTT_HANDLER: Final = "mqtt_handler"
OPTIONS_KEY_CAMERAS: Final = "cameras"

# Repair issue ids
ISSUE_CAMERA_KEYS_MISSING: Final = "camera_keys_missing"
