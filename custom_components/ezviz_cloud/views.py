"""Authenticated EZVIZ image proxy and decryption view."""

from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
import binascii
from dataclasses import dataclass
from http import HTTPStatus
import ipaddress
import logging
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientTimeout, web

from custom_components.ezviz_cloud.vendor.pyezvizapi.constants import HIK_ENCRYPTION_HEADER
from custom_components.ezviz_cloud.vendor.pyezvizapi.exceptions import PyEzvizError
from custom_components.ezviz_cloud.vendor.pyezvizapi.utils import decrypt_image
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_ENC_KEY, DOMAIN, OPTIONS_KEY_CAMERAS

_LOGGER = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _ImageProxyError(Exception):
    """Safe error returned by the image proxy."""

    message: str
    status: HTTPStatus


@callback
def async_generate_image_proxy_url(config_entry_id: str, serial: str, url: str) -> str:
    """Generate a URL for an authenticated, optionally decrypted image."""
    return ImageProxyView.url.format(
        config_entry_id=config_entry_id,
        serial=serial,
        url=urlsafe_b64encode(url.encode("utf-8")).decode("utf-8"),
    )


class ImageProxyView(HomeAssistantView):
    """Proxy EZVIZ alarm images without forwarding HA request headers."""

    requires_auth = True
    url = "/api/ezviz_cloud/image/{config_entry_id}/{serial}/{url}"
    name = "api:ezviz_cloud_image"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the proxy view."""
        self.hass = hass
        self.session = async_get_clientsession(hass)

    async def get(
        self, request: web.Request, config_entry_id: str, serial: str, url: str
    ) -> web.StreamResponse:
        """Return a redacted-error response or decrypted image bytes."""
        del request
        try:
            raw_url = _decode_url(url)
            entry = self.hass.config_entries.async_get_entry(config_entry_id)
            if entry is None or entry.domain != DOMAIN:
                raise _ImageProxyError("Unknown config entry", HTTPStatus.BAD_REQUEST)

            encryption_key = (
                (entry.options.get(OPTIONS_KEY_CAMERAS, {}) or {}).get(serial, {}).get(CONF_ENC_KEY)
            )
            body, content_type = await self._async_fetch_image(raw_url)
            if encryption_key:
                body = decrypt_image(body, encryption_key)
            elif body.startswith(HIK_ENCRYPTION_HEADER):
                _LOGGER.warning("An EZVIZ image is encrypted but has no stored key")
            return web.Response(body=body, content_type=content_type)
        except _ImageProxyError as err:
            return web.Response(text=err.message, status=err.status)
        except PyEzvizError:
            return web.Response(
                text="Unable to decrypt image",
                status=HTTPStatus.BAD_REQUEST,
            )

    async def _async_fetch_image(self, raw_url: str) -> tuple[bytes, str]:
        """Fetch one bounded image response from a validated URL."""
        _validate_remote_url(raw_url)
        headers = {
            "User-Agent": "HomeAssistant/ezviz_cloud",
            "Accept": "*/*",
        }
        try:
            async with self.session.get(
                raw_url,
                headers=headers,
                timeout=ClientTimeout(connect=10, sock_connect=10, sock_read=20),
            ) as response:
                final_host = response.url.host
                if final_host and _is_unsafe_literal_host(final_host):
                    raise _ImageProxyError("Unsupported image URL", HTTPStatus.BAD_REQUEST)
                if response.status != HTTPStatus.OK:
                    raise _ImageProxyError("Upstream image request failed", HTTPStatus.BAD_GATEWAY)
                content_length = response.headers.get("Content-Length")
                try:
                    content_length_value = int(content_length) if content_length else 0
                except ValueError:
                    content_length_value = 0
                if content_length_value > MAX_IMAGE_BYTES:
                    raise _ImageProxyError(
                        "Image is too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                    )
                body = await response.content.read(MAX_IMAGE_BYTES + 1)
                if len(body) > MAX_IMAGE_BYTES:
                    raise _ImageProxyError(
                        "Image is too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                    )
                content_type = response.headers.get("Content-Type", "image/jpeg").split(";", 1)[0]
                return body, content_type
        except _ImageProxyError:
            raise
        except ClientError as err:
            _LOGGER.debug("EZVIZ image request failed with %s", type(err).__name__)
            raise _ImageProxyError("Unable to fetch image", HTTPStatus.BAD_GATEWAY) from err


def _decode_url(encoded_url: str) -> str:
    """Decode a URL-safe route segment."""
    try:
        return urlsafe_b64decode(encoded_url.encode("utf-8")).decode("utf-8")
    except (ValueError, binascii.Error, UnicodeDecodeError) as err:
        raise _ImageProxyError("Invalid encoded URL", HTTPStatus.BAD_REQUEST) from err


def _validate_remote_url(raw_url: str) -> None:
    """Allow only HTTP(S) URLs that do not target literal private hosts."""
    try:
        parsed_url = urlsplit(raw_url)
    except ValueError as err:
        raise _ImageProxyError("Unsupported image URL", HTTPStatus.BAD_REQUEST) from err
    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.hostname
        or parsed_url.username
        or parsed_url.password
        or _is_unsafe_literal_host(parsed_url.hostname)
    ):
        raise _ImageProxyError("Unsupported image URL", HTTPStatus.BAD_REQUEST)


def _is_unsafe_literal_host(hostname: str) -> bool:
    """Reject local/private literal IP targets in the authenticated proxy."""
    if hostname.casefold() in {"localhost", "localhost.localdomain"}:
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return not address.is_global
