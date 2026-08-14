"""Config and options flows for the EZVIZ Cloud integration."""

from __future__ import annotations

from collections.abc import Mapping
from functools import partial
import logging
from typing import Any

import voluptuous as vol

from custom_components.ezviz_cloud.vendor.pyezvizapi.client import EzvizClient
from custom_components.ezviz_cloud.vendor.pyezvizapi.constants import DeviceCatagories
from custom_components.ezviz_cloud.vendor.pyezvizapi.exceptions import (
    AuthTestResultFailed,
    DeviceException,
    EzvizAuthVerificationCode,
    HTTPError,
    InvalidURL,
    PyEzvizError,
)
from custom_components.ezviz_cloud.vendor.pyezvizapi.test_cam_rtsp import TestRTSPAuth
from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import (
    CONF_IP_ADDRESS,
    CONF_PASSWORD,
    CONF_TIMEOUT,
    CONF_TYPE,
    CONF_URL,
    CONF_USERNAME,
)
from homeassistant.core import callback

from .auth import (
    EzvizKeyOtpInvalid,
    EzvizKeyOtpRequired,
    EzvizKeySetupError,
    EzvizLoginResult,
    close_local_client,
    fetch_device_encryption_keys,
    login_with_area_id,
)
from .camera_config import merge_camera_options, normalize_rtsp_path
from .const import (
    ATTR_SERIAL,
    ATTR_TYPE_CLOUD,
    CONF_ACCOUNT_OTP,
    CONF_AREA_ID,
    CONF_AUTO_CONFIGURED,
    CONF_CAM_ENC_2FA_CODE,
    CONF_CAM_VERIFICATION_2FA_CODE,
    CONF_DEVICE_KEY_OTP,
    CONF_ENC_KEY,
    CONF_EZVIZ_ACCOUNT,
    CONF_FFMPEG_ARGUMENTS,
    CONF_KEY_STATUS,
    CONF_MOTION_CLEAR_SECONDS,
    CONF_REGION,
    CONF_RF_SESSION_ID,
    CONF_RTSP_USES_VERIFICATION_CODE,
    CONF_SESSION_ID,
    CONF_SKIP_CAMERA_KEYS,
    CONF_STREAM_MODE,
    CONF_USER_ID,
    DATA_COORDINATOR,
    DEFAULT_CAMERA_USERNAME,
    DEFAULT_FETCH_MY_KEY,
    DEFAULT_FFMPEG_ARGUMENTS,
    DEFAULT_MOTION_CLEAR_SECONDS,
    DEFAULT_STREAM_MODE,
    DEFAULT_TIMEOUT,
    DOMAIN,
    KEY_STATUS_MISSING,
    KEY_STATUS_READY,
    MAX_MOTION_CLEAR_SECONDS,
    MIN_MOTION_CLEAR_SECONDS,
    OPTIONS_KEY_CAMERAS,
    REGION_AUTO,
    REGION_CUSTOM,
    REGION_EU,
    REGION_RU,
    REGION_URLS,
    STREAM_MODE_DISABLED,
    STREAM_MODE_LOCAL_RTSP,
)
from .coordinator import EzvizDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

VERSION = 5


def _normalize_api_host(value: str) -> str:
    """Normalize an API host string without scheme or trailing slash."""
    host = (value or "").strip()
    if host.startswith("http://"):
        host = host[7:]
    elif host.startswith("https://"):
        host = host[8:]
    return host.strip().strip("/")


def _resolve_api_host(region: str, custom_url: str | None) -> str:
    """Resolve the selected region to an EZVIZ API host."""
    if region == REGION_CUSTOM:
        host = _normalize_api_host(custom_url or "")
        if not host:
            raise vol.Invalid("invalid_url")
        return host
    return REGION_URLS[region]


def _coerce_area_id(value: Any) -> int | None:
    """Return an integer area id when present."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _get_cam_verification_code(
    data: dict[str, Any],
    ezviz_client: EzvizClient,
    verification_code: str | None = None,
) -> Any:
    """Fetch the sticker verification code, requesting OTP when needed."""
    try:
        return ezviz_client.get_cam_auth_code(
            data[ATTR_SERIAL],
            msg_auth_code=verification_code,
            sender_type=0 if verification_code else 3,
        )
    except EzvizAuthVerificationCode as err:
        if not verification_code:
            ezviz_client.get_2fa_check_code(
                username=data[CONF_EZVIZ_ACCOUNT],
                biz_type="DEVICE_AUTH_CODE",
            )
        raise EzvizAuthVerificationCode from err


def _get_cam_enc_key(
    data: dict[str, Any],
    ezviz_client: EzvizClient,
    enc_2fa_code: str | None = None,
) -> Any:
    """Fetch one camera encryption key, requesting one OTP when needed."""
    try:
        return ezviz_client.get_cam_key(
            data[ATTR_SERIAL],
            smscode=enc_2fa_code,
        )
    except EzvizAuthVerificationCode as err:
        if not enc_2fa_code:
            ezviz_client.get_2fa_check_code(
                username=data[CONF_EZVIZ_ACCOUNT],
                biz_type="DEVICE_ENCRYPTION",
            )
        raise EzvizAuthVerificationCode from err


def _test_camera_rtsp_creds(data: Mapping[str, Any]) -> None:
    """Attempt RTSP DESCRIBE with the selected camera credential."""
    password = data[CONF_PASSWORD] if data[CONF_RTSP_USES_VERIFICATION_CODE] else data[CONF_ENC_KEY]
    TestRTSPAuth(data[CONF_IP_ADDRESS], data[CONF_USERNAME], password).main()


def _wake_camera(data: Mapping[str, Any], ezviz_client: EzvizClient) -> None:
    """Wake a hibernating camera and run an RTSP DESCRIBE test."""
    ezviz_client.get_detection_sensibility(data[ATTR_SERIAL])
    _test_camera_rtsp_creds(data)


def _infer_supports_rtsp_from_category(cam_info: Mapping[str, Any]) -> bool:
    """Return a conservative default for whether to test local RTSP."""
    category = str(cam_info.get("device_category", ""))
    return DeviceCatagories.BATTERY_CAMERA_DEVICE_CATEGORY.value not in category


class EzvizConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle setup and reauthentication for one EZVIZ account."""

    VERSION = VERSION

    _pending_user_username: str = ""
    _pending_user_password: str = ""
    _pending_user_url: str = ""
    _pending_user_timeout: int = DEFAULT_TIMEOUT

    _setup_client: EzvizClient | None = None
    _setup_token: dict[str, Any] | None = None
    _setup_area_id: int | None = None
    _setup_cameras: dict[str, Mapping[str, Any]] | None = None
    _setup_contact_type: str | None = None

    _reauth_entry: ConfigEntry[Any]
    _reauth_username: str = ""
    _reauth_password: str = ""
    _reauth_url: str = ""
    _reauth_timeout: int = DEFAULT_TIMEOUT

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> EzvizOptionsFlowHandler:
        """Return the options flow handler."""
        return EzvizOptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Sign in and automatically configure every discovered camera."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD]
            await self.async_set_unique_id(username.casefold())
            self._abort_if_unique_id_configured()

            try:
                api_url = _resolve_api_host(user_input[CONF_REGION], user_input.get(CONF_URL))
            except vol.Invalid:
                errors["base"] = "invalid_url"
            else:
                timeout = user_input.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
                try:
                    return await self._async_login_and_discover(
                        username=username,
                        password=password,
                        api_url=api_url,
                        timeout=timeout,
                    )
                except EzvizAuthVerificationCode:
                    self._pending_user_username = username
                    self._pending_user_password = password
                    self._pending_user_url = api_url
                    self._pending_user_timeout = timeout
                    return await self.async_step_user_mfa_confirm()
                except (InvalidURL, HTTPError, PyEzvizError):
                    errors["base"] = "cannot_connect"
                except Exception as err:  # pragma: no cover - defensive boundary
                    _LOGGER.error(
                        "Unexpected EZVIZ setup failure of type %s",
                        type(err).__name__,
                    )
                    errors["base"] = "unknown"

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_REGION, default=REGION_AUTO): vol.In(
                    [REGION_AUTO, REGION_EU, REGION_RU, REGION_CUSTOM]
                ),
                vol.Optional(CONF_URL): str,
                vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): int,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_user_mfa_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Complete account login with the one-time account code."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                return await self._async_login_and_discover(
                    username=self._pending_user_username,
                    password=self._pending_user_password,
                    api_url=self._pending_user_url,
                    timeout=self._pending_user_timeout,
                    sms_code=user_input[CONF_ACCOUNT_OTP].strip(),
                )
            except EzvizAuthVerificationCode:
                errors["base"] = "verification_required"
            except (InvalidURL, HTTPError, PyEzvizError):
                errors["base"] = "cannot_connect"
            except Exception as err:  # pragma: no cover - defensive boundary
                _LOGGER.error(
                    "Unexpected EZVIZ account MFA failure of type %s",
                    type(err).__name__,
                )
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user_mfa_confirm",
            data_schema=vol.Schema({vol.Required(CONF_ACCOUNT_OTP): str}),
            errors=errors,
        )

    async def _async_login_and_discover(
        self,
        *,
        username: str,
        password: str,
        api_url: str,
        timeout: int,
        sms_code: str | None = None,
    ) -> ConfigFlowResult:
        """Login, discover cameras, and start automatic key setup."""
        client = EzvizClient(
            account=username,
            password=password,
            url=api_url,
            timeout=timeout,
        )
        try:
            login_result: EzvizLoginResult = await self.hass.async_add_executor_job(
                login_with_area_id,
                client,
                sms_code,
            )
            cameras = await self.hass.async_add_executor_job(
                partial(client.load_cameras, refresh=False)
            )
        except Exception:
            close_local_client(client)
            raise

        if not isinstance(cameras, Mapping):
            close_local_client(client)
            raise PyEzvizError("EZVIZ returned an invalid device list")

        self._pending_user_username = username
        self._pending_user_password = ""
        self._pending_user_url = api_url
        self._pending_user_timeout = timeout
        self._setup_client = client
        self._setup_token = login_result.token
        self._setup_area_id = login_result.area_id
        self._setup_cameras = {
            str(serial): camera for serial, camera in cameras.items() if isinstance(camera, Mapping)
        }

        try:
            keys = await self.hass.async_add_executor_job(
                partial(
                    fetch_device_encryption_keys,
                    client,
                    area_id=self._setup_area_id,
                    account=username,
                    serials=list(self._setup_cameras),
                )
            )
        except EzvizKeyOtpRequired as err:
            self._setup_contact_type = err.contact_type
            return await self.async_step_device_key_mfa()
        except EzvizKeySetupError:
            keys = {}

        return self._async_create_setup_entry(keys)

    async def async_step_device_key_mfa(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect one OTP and retrieve encryption keys for all cameras."""
        errors: dict[str, str] = {}

        if self._setup_client is None or self._setup_cameras is None:
            return self.async_abort(reason="unknown")

        if user_input is not None:
            if user_input.get(CONF_SKIP_CAMERA_KEYS, False):
                return self._async_create_setup_entry({})

            verification_code = user_input.get(CONF_DEVICE_KEY_OTP, "").strip()
            if not verification_code:
                errors["base"] = "invalid_key_code"
            else:
                try:
                    keys = await self.hass.async_add_executor_job(
                        partial(
                            fetch_device_encryption_keys,
                            self._setup_client,
                            area_id=self._setup_area_id,
                            account=self._pending_user_username,
                            serials=list(self._setup_cameras),
                            verification_code=verification_code,
                        )
                    )
                except (EzvizKeyOtpInvalid, EzvizKeyOtpRequired):
                    errors["base"] = "invalid_key_code"
                except EzvizKeySetupError:
                    errors["base"] = "key_setup_failed"
                else:
                    return self._async_create_setup_entry(keys)

        return self.async_show_form(
            step_id="device_key_mfa",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_DEVICE_KEY_OTP, default=""): str,
                    vol.Optional(CONF_SKIP_CAMERA_KEYS, default=False): bool,
                }
            ),
            errors=errors,
            description_placeholders={
                "camera_count": str(len(self._setup_cameras)),
                "contact_type": self._setup_contact_type or "EZVIZ account contact",
            },
        )

    def _async_create_setup_entry(self, keys: Mapping[str, str]) -> ConfigFlowResult:
        """Create the token-only config entry and clear setup credentials."""
        if self._setup_token is None or self._setup_cameras is None:
            return self.async_abort(reason="unknown")

        token = self._setup_token
        api_url = _normalize_api_host(str(token.get("api_url") or self._pending_user_url))
        camera_options = merge_camera_options(self._setup_cameras, keys)
        data: dict[str, Any] = {
            CONF_TYPE: ATTR_TYPE_CLOUD,
            CONF_EZVIZ_ACCOUNT: self._pending_user_username,
            CONF_SESSION_ID: token[CONF_SESSION_ID],
            CONF_RF_SESSION_ID: token[CONF_RF_SESSION_ID],
            CONF_URL: api_url,
            CONF_USER_ID: token["username"],
        }
        if self._setup_area_id is not None:
            data[CONF_AREA_ID] = self._setup_area_id

        close_local_client(self._setup_client)
        self._setup_client = None
        self._setup_token = None
        self._setup_cameras = None
        self._pending_user_password = ""

        return self.async_create_entry(
            title=self._pending_user_username,
            data=data,
            options={
                CONF_TIMEOUT: self._pending_user_timeout,
                CONF_MOTION_CLEAR_SECONDS: DEFAULT_MOTION_CLEAR_SECONDS,
                OPTIONS_KEY_CAMERAS: camera_options,
            },
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauthentication for the requested config entry."""
        entry_id = self.context.get("entry_id")
        entry = self.hass.config_entries.async_get_entry(entry_id) if entry_id else None
        if entry is None:
            return self.async_abort(reason="unknown")

        self._reauth_entry = entry
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the password and refresh account tokens."""
        errors: dict[str, str] = {}
        account = self._reauth_entry.data.get(CONF_EZVIZ_ACCOUNT, self._reauth_entry.title)

        if user_input is not None:
            self._reauth_username = account
            self._reauth_password = user_input[CONF_PASSWORD]
            self._reauth_url = self._reauth_entry.data[CONF_URL]
            self._reauth_timeout = self._reauth_entry.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
            try:
                return await self._async_finish_reauth()
            except EzvizAuthVerificationCode:
                return await self.async_step_reauth_mfa()
            except (InvalidURL, HTTPError, PyEzvizError):
                errors["base"] = "cannot_connect"
            except Exception as err:  # pragma: no cover - defensive boundary
                _LOGGER.error(
                    "Unexpected EZVIZ reauthentication failure of type %s",
                    type(err).__name__,
                )
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=account): vol.In([account]),
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth_mfa(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Complete reauthentication with an account OTP."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                return await self._async_finish_reauth(user_input[CONF_ACCOUNT_OTP].strip())
            except EzvizAuthVerificationCode:
                errors["base"] = "verification_required"
            except (InvalidURL, HTTPError, PyEzvizError):
                errors["base"] = "cannot_connect"
            except Exception as err:  # pragma: no cover - defensive boundary
                _LOGGER.error(
                    "Unexpected EZVIZ reauthentication MFA failure of type %s",
                    type(err).__name__,
                )
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reauth_mfa",
            data_schema=vol.Schema({vol.Required(CONF_ACCOUNT_OTP): str}),
            errors=errors,
        )

    async def _async_finish_reauth(self, sms_code: str | None = None) -> ConfigFlowResult:
        """Login and atomically replace the stored token fields."""
        client = EzvizClient(
            account=self._reauth_username,
            password=self._reauth_password,
            url=self._reauth_url,
            timeout=self._reauth_timeout,
        )
        try:
            result = await self.hass.async_add_executor_job(login_with_area_id, client, sms_code)
        finally:
            close_local_client(client)

        new_data = {
            **self._reauth_entry.data,
            CONF_EZVIZ_ACCOUNT: self._reauth_username,
            CONF_SESSION_ID: result.token[CONF_SESSION_ID],
            CONF_RF_SESSION_ID: result.token[CONF_RF_SESSION_ID],
            CONF_URL: _normalize_api_host(str(result.token.get("api_url") or self._reauth_url)),
            CONF_USER_ID: result.token["username"],
        }
        if result.area_id is not None:
            new_data[CONF_AREA_ID] = result.area_id

        self._reauth_password = ""
        return self.async_update_reload_and_abort(self._reauth_entry, data=new_data)


class EzvizOptionsFlowHandler(OptionsFlowWithReload):
    """Edit cloud settings and camera stream credentials."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize transient options-flow state."""
        self.coordinator: EzvizDataUpdateCoordinator
        self._cam_serial: str = ""
        self._pending: dict[str, Any] | None = None
        self._auto_contact_type: str | None = None

    async def async_step_init(self, user_input: Any | None = None) -> ConfigFlowResult:
        """Show the options menu."""
        self.coordinator = self.hass.data[DOMAIN][self.config_entry.entry_id][DATA_COORDINATOR]
        return self.async_show_menu(
            step_id="init",
            menu_options=["cloud", "camera_auto_setup", "camera_select"],
        )

    async def async_step_cloud(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Edit account polling and motion behavior."""
        options = dict(self.config_entry.options)
        if user_input is not None:
            options[CONF_TIMEOUT] = user_input[CONF_TIMEOUT]
            options[CONF_MOTION_CLEAR_SECONDS] = user_input[CONF_MOTION_CLEAR_SECONDS]
            return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="cloud",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_TIMEOUT,
                        default=options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
                    ): int,
                    vol.Required(
                        CONF_MOTION_CLEAR_SECONDS,
                        default=options.get(
                            CONF_MOTION_CLEAR_SECONDS,
                            DEFAULT_MOTION_CLEAR_SECONDS,
                        ),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(
                            min=MIN_MOTION_CLEAR_SECONDS,
                            max=MAX_MOTION_CLEAR_SECONDS,
                        ),
                    ),
                }
            ),
        )

    async def async_step_camera_auto_setup(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Fetch missing keys and configure all current cameras."""
        errors: dict[str, str] = {}
        cameras = self.coordinator.data or {}
        if not cameras:
            return self.async_abort(reason="no_cameras")

        if user_input is not None:
            if not user_input.get("confirm", True):
                return self.async_create_entry(title="", data=dict(self.config_entry.options))
            try:
                keys = await self.hass.async_add_executor_job(
                    partial(
                        fetch_device_encryption_keys,
                        self.coordinator.ezviz_client,
                        area_id=_coerce_area_id(self.config_entry.data.get(CONF_AREA_ID)),
                        account=self.config_entry.data.get(
                            CONF_EZVIZ_ACCOUNT, self.config_entry.title
                        ),
                        serials=list(cameras),
                    )
                )
            except EzvizKeyOtpRequired as err:
                self._auto_contact_type = err.contact_type
                return await self.async_step_camera_auto_setup_mfa()
            except EzvizKeySetupError:
                errors["base"] = "key_setup_failed"
            else:
                return self._save_auto_camera_options(keys)

        return self.async_show_form(
            step_id="camera_auto_setup",
            data_schema=vol.Schema({vol.Required("confirm", default=True): bool}),
            errors=errors,
            description_placeholders={"camera_count": str(len(cameras))},
        )

    async def async_step_camera_auto_setup_mfa(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Apply one device-encryption OTP to every current camera."""
        errors: dict[str, str] = {}
        cameras = self.coordinator.data or {}

        if user_input is not None:
            if user_input.get(CONF_SKIP_CAMERA_KEYS, False):
                return self._save_auto_camera_options({})
            verification_code = user_input.get(CONF_DEVICE_KEY_OTP, "").strip()
            if not verification_code:
                errors["base"] = "invalid_key_code"
            else:
                try:
                    keys = await self.hass.async_add_executor_job(
                        partial(
                            fetch_device_encryption_keys,
                            self.coordinator.ezviz_client,
                            area_id=_coerce_area_id(self.config_entry.data.get(CONF_AREA_ID)),
                            account=self.config_entry.data.get(
                                CONF_EZVIZ_ACCOUNT, self.config_entry.title
                            ),
                            serials=list(cameras),
                            verification_code=verification_code,
                        )
                    )
                except (EzvizKeyOtpInvalid, EzvizKeyOtpRequired):
                    errors["base"] = "invalid_key_code"
                except EzvizKeySetupError:
                    errors["base"] = "key_setup_failed"
                else:
                    return self._save_auto_camera_options(keys)

        return self.async_show_form(
            step_id="camera_auto_setup_mfa",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_DEVICE_KEY_OTP, default=""): str,
                    vol.Optional(CONF_SKIP_CAMERA_KEYS, default=False): bool,
                }
            ),
            errors=errors,
            description_placeholders={
                "camera_count": str(len(cameras)),
                "contact_type": self._auto_contact_type or "EZVIZ account contact",
            },
        )

    def _save_auto_camera_options(self, keys: Mapping[str, str]) -> ConfigFlowResult:
        """Merge fetched keys without replacing manual stream choices."""
        options = dict(self.config_entry.options or {})
        options[OPTIONS_KEY_CAMERAS] = merge_camera_options(
            self.coordinator.data or {},
            keys,
            options.get(OPTIONS_KEY_CAMERAS, {}) or {},
        )
        return self.async_create_entry(title="", data=options)

    async def async_step_camera_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose one camera for advanced stream settings."""
        cameras = self.coordinator.data
        if not cameras:
            return self.async_abort(reason="no_cameras")

        choices = {
            serial: f"{info.get('name', 'Camera')} ({serial})" for serial, info in cameras.items()
        }
        if user_input is not None:
            self._cam_serial = user_input[ATTR_SERIAL]
            return await self.async_step_camera_edit()

        return self.async_show_form(
            step_id="camera_select",
            data_schema=vol.Schema({vol.Required(ATTR_SERIAL): vol.In(choices)}),
        )

    async def async_step_camera_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit one camera without displaying saved credentials."""
        options = dict(self.config_entry.options or {})
        cameras_options = options.get(OPTIONS_KEY_CAMERAS, {}) or {}
        per_camera = cameras_options.get(self._cam_serial, {}) or {}
        camera_info = self.coordinator.data[self._cam_serial]
        inferred_ip = camera_info.get("local_ip") or ""
        errors: dict[str, str] = {}

        if user_input is not None:
            payload = {
                **user_input,
                CONF_PASSWORD: user_input.get(CONF_PASSWORD) or per_camera.get(CONF_PASSWORD, ""),
                CONF_ENC_KEY: user_input.get(CONF_ENC_KEY)
                or per_camera.get(CONF_ENC_KEY, DEFAULT_FETCH_MY_KEY),
                CONF_IP_ADDRESS: inferred_ip,
                CONF_EZVIZ_ACCOUNT: self.config_entry.data.get(
                    CONF_EZVIZ_ACCOUNT, self.config_entry.title
                ),
                ATTR_SERIAL: self._cam_serial,
            }
            try:
                resolved = await self._test_rtsp_credentials(payload)
            except EzvizAuthVerificationCode:
                self._pending = payload
                return await self.async_step_camera_edit_2fa()
            except AuthTestResultFailed:
                errors["base"] = "rtsp_auth_failed"
            except DeviceException:
                errors["base"] = "device_exception"
            except (InvalidURL, HTTPError, PyEzvizError):
                errors["base"] = "cannot_connect"
            except Exception as err:  # pragma: no cover - defensive boundary
                _LOGGER.error(
                    "Unexpected camera options failure of type %s",
                    type(err).__name__,
                )
                errors["base"] = "unknown"
            else:
                return self._save_manual_camera_options(resolved)

        return self.async_show_form(
            step_id="camera_edit",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=per_camera.get(CONF_USERNAME, DEFAULT_CAMERA_USERNAME),
                    ): str,
                    vol.Optional(CONF_PASSWORD, default=""): str,
                    vol.Optional(
                        CONF_ENC_KEY,
                        default="" if per_camera.get(CONF_ENC_KEY) else DEFAULT_FETCH_MY_KEY,
                    ): str,
                    vol.Required(
                        CONF_RTSP_USES_VERIFICATION_CODE,
                        default=per_camera.get(CONF_RTSP_USES_VERIFICATION_CODE, False),
                    ): bool,
                    vol.Required(
                        CONF_STREAM_MODE,
                        default=per_camera.get(CONF_STREAM_MODE, DEFAULT_STREAM_MODE),
                    ): vol.In([STREAM_MODE_LOCAL_RTSP, STREAM_MODE_DISABLED]),
                    vol.Required(
                        "ephemeral_test_rtsp",
                        default=_infer_supports_rtsp_from_category(camera_info),
                    ): bool,
                    vol.Optional(
                        CONF_FFMPEG_ARGUMENTS,
                        default=per_camera.get(CONF_FFMPEG_ARGUMENTS, DEFAULT_FFMPEG_ARGUMENTS),
                    ): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "serial": self._cam_serial,
                "ip_address": inferred_ip,
            },
        )

    async def async_step_camera_edit_2fa(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect optional one-time codes for advanced manual setup."""
        if self._pending is None:
            return await self.async_step_camera_edit()

        errors: dict[str, str] = {}
        if user_input is not None:
            payload = {
                **self._pending,
                CONF_CAM_VERIFICATION_2FA_CODE: user_input.get(CONF_CAM_VERIFICATION_2FA_CODE)
                or None,
                CONF_CAM_ENC_2FA_CODE: user_input.get(CONF_CAM_ENC_2FA_CODE) or None,
            }
            try:
                resolved = await self._test_rtsp_credentials(payload)
            except EzvizAuthVerificationCode:
                errors["base"] = "verification_required"
            except AuthTestResultFailed:
                errors["base"] = "rtsp_auth_failed"
            except DeviceException:
                errors["base"] = "device_exception"
            except (InvalidURL, HTTPError, PyEzvizError):
                errors["base"] = "cannot_connect"
            except Exception as err:  # pragma: no cover - defensive boundary
                _LOGGER.error(
                    "Unexpected camera MFA failure of type %s",
                    type(err).__name__,
                )
                errors["base"] = "unknown"
            else:
                self._pending = None
                return self._save_manual_camera_options(resolved)

        camera_info = self.coordinator.data.get(self._cam_serial, {})
        return self.async_show_form(
            step_id="camera_edit_2fa",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_CAM_VERIFICATION_2FA_CODE, default=""): str,
                    vol.Optional(CONF_CAM_ENC_2FA_CODE, default=""): str,
                }
            ),
            errors=errors,
            description_placeholders={
                "serial": self._cam_serial,
                "ip_address": camera_info.get("local_ip") or "",
            },
        )

    def _save_manual_camera_options(self, resolved: Mapping[str, Any]) -> ConfigFlowResult:
        """Persist one camera's advanced settings without one-time codes."""
        options = dict(self.config_entry.options or {})
        cameras = dict(options.get(OPTIONS_KEY_CAMERAS, {}) or {})
        use_verification_code = bool(resolved.get(CONF_RTSP_USES_VERIFICATION_CODE, False))
        has_password = bool(
            resolved.get(CONF_PASSWORD) if use_verification_code else resolved.get(CONF_ENC_KEY)
        )
        cameras[self._cam_serial] = {
            CONF_USERNAME: resolved.get(CONF_USERNAME, DEFAULT_CAMERA_USERNAME),
            CONF_PASSWORD: resolved.get(CONF_PASSWORD, ""),
            CONF_ENC_KEY: resolved.get(CONF_ENC_KEY, ""),
            CONF_RTSP_USES_VERIFICATION_CODE: use_verification_code,
            CONF_STREAM_MODE: resolved.get(CONF_STREAM_MODE, DEFAULT_STREAM_MODE),
            CONF_FFMPEG_ARGUMENTS: normalize_rtsp_path(
                resolved.get(CONF_FFMPEG_ARGUMENTS, DEFAULT_FFMPEG_ARGUMENTS)
            ),
            CONF_AUTO_CONFIGURED: False,
            CONF_KEY_STATUS: KEY_STATUS_READY if has_password else KEY_STATUS_MISSING,
        }
        options[OPTIONS_KEY_CAMERAS] = cameras
        return self.async_create_entry(title="", data=options)

    async def _test_rtsp_credentials(self, data: dict[str, Any]) -> dict[str, Any]:
        """Fetch requested credentials and optionally test the local stream."""
        client = self.coordinator.ezviz_client
        try:
            if (
                data.get(CONF_STREAM_MODE, DEFAULT_STREAM_MODE) != STREAM_MODE_DISABLED
                and data.get(CONF_ENC_KEY) == DEFAULT_FETCH_MY_KEY
            ):
                data[CONF_ENC_KEY] = await self.hass.async_add_executor_job(
                    _get_cam_enc_key,
                    data,
                    client,
                    data.get(CONF_CAM_ENC_2FA_CODE),
                )

            if (
                data.get(CONF_STREAM_MODE, DEFAULT_STREAM_MODE) != STREAM_MODE_DISABLED
                and data.get(CONF_PASSWORD) == DEFAULT_FETCH_MY_KEY
            ):
                data[CONF_PASSWORD] = await self.hass.async_add_executor_job(
                    _get_cam_verification_code,
                    data,
                    client,
                    data.get(CONF_CAM_VERIFICATION_2FA_CODE),
                )

            if (
                data.get("ephemeral_test_rtsp")
                and data.get(CONF_STREAM_MODE, DEFAULT_STREAM_MODE) != STREAM_MODE_DISABLED
            ):
                await self.hass.async_add_executor_job(_wake_camera, data, client)
        except (
            AuthTestResultFailed,
            DeviceException,
            EzvizAuthVerificationCode,
            HTTPError,
            InvalidURL,
            PyEzvizError,
        ):
            raise

        data.pop(CONF_CAM_VERIFICATION_2FA_CODE, None)
        data.pop(CONF_CAM_ENC_2FA_CODE, None)
        data.pop("ephemeral_test_rtsp", None)
        data.pop(CONF_IP_ADDRESS, None)
        data.pop(CONF_EZVIZ_ACCOUNT, None)
        if data.get(CONF_ENC_KEY) == DEFAULT_FETCH_MY_KEY:
            data[CONF_ENC_KEY] = ""
        if data.get(CONF_PASSWORD) == DEFAULT_FETCH_MY_KEY:
            data[CONF_PASSWORD] = ""
        return data
