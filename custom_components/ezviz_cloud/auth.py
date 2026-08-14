"""EZVIZ authentication and camera-key bootstrap helpers.

Private pyezvizapi HTTP helpers are intentionally isolated here. The integration
vendors a tested pyezvizapi version, and no response body, token, account, serial,
or camera key is included in raised exception messages or logs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import requests

from custom_components.ezviz_cloud.vendor.pyezvizapi import api_endpoints
from custom_components.ezviz_cloud.vendor.pyezvizapi.client import EzvizClient
from custom_components.ezviz_cloud.vendor.pyezvizapi.constants import FEATURE_CODE
from custom_components.ezviz_cloud.vendor.pyezvizapi.exceptions import HTTPError, PyEzvizError

API_ENDPOINT_CAM_ENCRYPTKEY = api_endpoints.API_ENDPOINT_CAM_ENCRYPTKEY
API_ENDPOINT_DEVICES_ENCRYPTKEY_BATCH = getattr(
    api_endpoints,
    "API_ENDPOINT_DEVICES_ENCRYPTKEY_BATCH",
    "/v3/devices/encryptkey/query/batch/risk",
)

OTP_REQUIRED_CODES = {20002, 120002}
OTP_INVALID_CODES = {1012, 101011}
DEVICE_OFFLINE_CODES = {2009, 102003}


class EzvizKeySetupError(Exception):
    """Base error for safe camera-key setup failures."""


class EzvizKeyOtpRequired(EzvizKeySetupError):
    """Raised after a device-encryption verification code is requested."""

    def __init__(self, contact_type: str | None = None) -> None:
        super().__init__("Device encryption verification is required")
        self.contact_type = contact_type


class EzvizKeyOtpInvalid(EzvizKeySetupError):
    """Raised when EZVIZ rejects the one-time device-encryption code."""


@dataclass(frozen=True, slots=True)
class EzvizLoginResult:
    """Successful login result with the APK-required area id."""

    token: dict[str, Any]
    area_id: int | None


def _as_int(value: Any) -> int | None:
    """Return an integer for common EZVIZ number representations."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _response_code(payload: Mapping[str, Any]) -> int | None:
    """Extract a response code without serializing the payload."""
    meta = payload.get("meta")
    if isinstance(meta, Mapping):
        code = _as_int(meta.get("code"))
        if code is not None:
            return code
    return _as_int(payload.get("resultCode"))


def _extract_area_id(payload: Any) -> int | None:
    """Extract loginArea.areaId from a login response."""
    if not isinstance(payload, Mapping):
        return None
    login_area = payload.get("loginArea")
    if not isinstance(login_area, Mapping):
        return None
    return _as_int(login_area.get("areaId"))


def login_with_area_id(client: EzvizClient, sms_code: str | int | None = None) -> EzvizLoginResult:
    """Login while capturing the area id discarded by vendored pyezvizapi."""
    captured_area_id: int | None = None

    def capture_login_area(response: requests.Response, *_: Any, **__: Any) -> requests.Response:
        nonlocal captured_area_id
        try:
            area_id = _extract_area_id(response.json())
        except (ValueError, requests.RequestException):
            area_id = None
        if area_id is not None:
            captured_area_id = area_id
        return response

    hooks = client._session.hooks.setdefault("response", [])
    hooks.append(capture_login_area)
    try:
        token = client.login(sms_code)
    finally:
        with suppress(ValueError):
            hooks.remove(capture_login_area)

    return EzvizLoginResult(token=dict(token), area_id=captured_area_id)


def close_local_client(client: EzvizClient | None) -> None:
    """Drop setup-only credentials and close the local requests session."""
    if client is None:
        return
    client.account = None
    client.password = None
    with suppress(requests.RequestException):
        client._session.close()


def _parse_batch_keys(payload: Mapping[str, Any], serials: set[str]) -> dict[str, str]:
    """Return validated serial-to-key values from the APK batch response."""
    code = _response_code(payload)
    if code in OTP_REQUIRED_CODES:
        raise EzvizKeyOtpRequired
    if code in OTP_INVALID_CODES:
        raise EzvizKeyOtpInvalid
    if code != 200:
        raise EzvizKeySetupError("EZVIZ rejected the batch key request")

    response_detail = payload.get("responseDetail")
    if not isinstance(response_detail, Mapping):
        return {}

    keys: dict[str, str] = {}
    for raw_serial, raw_value in response_detail.items():
        serial = str(raw_serial)
        if serial not in serials or not isinstance(raw_value, Mapping):
            continue
        item_code = _as_int(raw_value.get("code"))
        key = raw_value.get("encryptKey") or raw_value.get("encryptkey")
        if item_code in (None, 0, 200) and isinstance(key, str) and key:
            keys[serial] = key
    return keys


def _batch_device_keys(client: EzvizClient, area_id: int, serials: Sequence[str]) -> dict[str, str]:
    """Call the APK batch/risk endpoint without exposing response contents."""
    form = {
        "checkcode": "",
        "deviceSerialList": ",".join(serials),
        "msgType": "-1",
    }
    headers = {
        **client._session.headers,
        "Content-Type": "application/x-www-form-urlencoded",
        "areaId": str(area_id),
    }
    request = requests.Request(
        method="POST",
        url=client._url(API_ENDPOINT_DEVICES_ENCRYPTKEY_BATCH),
        headers=headers,
        data=urlencode(form),
    ).prepare()

    try:
        response = client._send_prepared(
            request,
            retry_401=True,
            max_retries=1,
        )
        payload = client._parse_json(response)
    except (HTTPError, PyEzvizError, requests.RequestException) as err:
        raise EzvizKeySetupError("Unable to query camera keys") from err

    return _parse_batch_keys(payload, set(serials))


def _parse_individual_key(payload: Mapping[str, Any]) -> str | None:
    """Return one key or classify a safe per-camera API error."""
    code = _response_code(payload)
    if code in OTP_REQUIRED_CODES:
        raise EzvizKeyOtpRequired
    if code in OTP_INVALID_CODES:
        raise EzvizKeyOtpInvalid
    if code in DEVICE_OFFLINE_CODES:
        return None
    if code not in (0, 200):
        raise EzvizKeySetupError("EZVIZ rejected a camera key request")

    key = payload.get("encryptkey") or payload.get("encryptKey")
    return key if isinstance(key, str) and key else None


def _individual_device_key(
    client: EzvizClient,
    serial: str,
    verification_code: str | None,
) -> str | None:
    """Fetch one encryption key using the known pyezvizapi request contract."""
    try:
        payload = client._request_json(
            "POST",
            API_ENDPOINT_CAM_ENCRYPTKEY,
            data={
                "checkcode": verification_code,
                "serial": serial,
                "clientNo": "web_site",
                "clientType": 3,
                "netType": "WIFI",
                "featureCode": FEATURE_CODE,
                "sessionId": client.export_token().get("session_id"),
            },
            retry_401=True,
            max_retries=1,
        )
    except (HTTPError, PyEzvizError, requests.RequestException) as err:
        raise EzvizKeySetupError("Unable to query a camera key") from err
    return _parse_individual_key(payload)


def _request_device_key_otp(client: EzvizClient, account: str) -> str | None:
    """Request the DEVICE_ENCRYPTION code and return only its channel type."""
    try:
        response = client.get_2fa_check_code(
            biz_type="DEVICE_ENCRYPTION",
            username=account,
        )
    except (HTTPError, PyEzvizError, requests.RequestException):
        return None

    if not isinstance(response, Mapping):
        return None
    contact = response.get("contact")
    if not isinstance(contact, Mapping):
        return None
    contact_type = contact.get("type")
    return str(contact_type).upper() if contact_type else None


def _raise_otp_required(client: EzvizClient, account: str) -> None:
    """Request one DEVICE_ENCRYPTION code and raise the flow signal."""
    raise EzvizKeyOtpRequired(_request_device_key_otp(client, account))


def _fetch_individual_keys(
    client: EzvizClient,
    serials: Sequence[str],
    verification_code: str | None,
) -> dict[str, str]:
    """Collect available per-camera keys while tolerating offline devices."""
    keys: dict[str, str] = {}
    for serial in serials:
        try:
            key = _individual_device_key(client, serial, verification_code)
        except (EzvizKeyOtpInvalid, EzvizKeyOtpRequired):
            raise
        except EzvizKeySetupError:
            continue
        if key:
            keys[serial] = key
    return keys


def _fetch_without_code(
    client: EzvizClient,
    *,
    area_id: int | None,
    account: str,
    serials: Sequence[str],
) -> dict[str, str]:
    """Try APK batch retrieval, then fill gaps per camera."""
    keys: dict[str, str] = {}
    if area_id is not None:
        try:
            keys.update(_batch_device_keys(client, area_id, serials))
        except EzvizKeyOtpRequired:
            _raise_otp_required(client, account)
        except EzvizKeySetupError:
            pass

    if len(keys) == len(serials):
        return keys

    missing_serials = [serial for serial in serials if serial not in keys]
    try:
        keys.update(_fetch_individual_keys(client, missing_serials, None))
    except EzvizKeyOtpRequired:
        _raise_otp_required(client, account)
    return keys


def fetch_device_encryption_keys(
    client: EzvizClient,
    *,
    area_id: int | None,
    account: str,
    serials: Sequence[str],
    verification_code: str | None = None,
) -> dict[str, str]:
    """Fetch encryption keys for all cameras with at most one user OTP step."""
    unique_serials = sorted({str(serial) for serial in serials if serial})
    if not unique_serials:
        return {}

    if verification_code:
        return _fetch_individual_keys(client, unique_serials, verification_code)
    return _fetch_without_code(
        client,
        area_id=area_id,
        account=account,
        serials=unique_serials,
    )
