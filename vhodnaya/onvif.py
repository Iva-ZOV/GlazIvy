"""Лёгкий WS-Discovery и необходимые ONVIF-запросы без внешних зависимостей."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import html
import ipaddress
import os
import re
import select
import socket
import struct
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from urllib.request import Request, urlopen


WSD_ADDRESS = ("239.255.255.250", 3702)
WSD_NAMESPACE = "http://schemas.xmlsoap.org/ws/2005/04/discovery"
ADDRESSING_NAMESPACE = "http://schemas.xmlsoap.org/ws/2004/08/addressing"
DEVICE_NAMESPACE = "http://www.onvif.org/ver10/device/wsdl"
MEDIA_NAMESPACE = "http://www.onvif.org/ver10/media/wsdl"
SOAP_NAMESPACE = "http://www.w3.org/2003/05/soap-envelope"
REQUEST_TIMEOUT_SECONDS = 3.5
DEFAULT_CREDENTIALS = (
    ("", ""),
    ("admin", ""),
    ("admin", "admin"),
    ("admin", "123456"),
)


class OnvifError(RuntimeError):
    """Безопасная для интерфейса ошибка без URL и секретов камеры."""


@dataclass(frozen=True, slots=True)
class DiscoveredEndpoint:
    endpoint: str
    ip: str
    name_hint: str = ""


@dataclass(frozen=True, slots=True)
class DiscoveredCamera:
    endpoint: str
    ip: str
    name: str = ""
    media_endpoint: str = ""
    stream_url_hd: str = ""
    stream_url_sd: str = ""
    username: str = ""
    password: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.stream_url_hd or self.stream_url_sd)


@dataclass(frozen=True, slots=True)
class _Profile:
    token: str
    name: str
    width: int
    height: int
    index: int

    @property
    def area(self) -> int:
        return self.width * self.height


def _stopped(stop_event: threading.Event | None) -> bool:
    return stop_event is not None and stop_event.is_set()


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _clean_name(value: str) -> str:
    value = unquote(value).replace("_", " ")
    return " ".join(value.split())[:96]


def _canonical_ip(value: str) -> str:
    value = value.strip().strip("[]").split("%", 1)[0]
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        return value.lower()


def _endpoint_ip(endpoint: str, sender_ip: str) -> str:
    try:
        hostname = urlsplit(endpoint).hostname or ""
        if hostname:
            ipaddress.ip_address(hostname.split("%", 1)[0])
            return _canonical_ip(hostname)
    except ValueError:
        pass
    return _canonical_ip(sender_ip)


def _valid_http_endpoint(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)


def _probe_xml() -> bytes:
    message_id = f"uuid:{uuid.uuid4()}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<s:Envelope xmlns:s="{SOAP_NAMESPACE}" '
        f'xmlns:a="{ADDRESSING_NAMESPACE}" xmlns:d="{WSD_NAMESPACE}" '
        'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
        "<s:Header>"
        f"<a:MessageID>{message_id}</a:MessageID>"
        "<a:ReplyTo><a:Address>"
        f"{ADDRESSING_NAMESPACE}/role/anonymous"
        "</a:Address></a:ReplyTo>"
        '<a:To s:mustUnderstand="1">'
        "urn:schemas-xmlsoap-org:ws:2005:04:discovery"
        "</a:To>"
        f'<a:Action s:mustUnderstand="1">{WSD_NAMESPACE}/Probe</a:Action>'
        "</s:Header><s:Body><d:Probe>"
        "<d:Types>dn:NetworkVideoTransmitter</d:Types>"
        "</d:Probe></s:Body></s:Envelope>"
    ).encode("utf-8")


def _windows_ipv4_addresses() -> tuple[str, ...]:
    """Берёт адреса всех Windows-адаптеров через встроенный IP Helper API."""

    if os.name != "nt":
        return ()
    try:
        from ctypes import wintypes

        class _MibIpAddrRow(ctypes.Structure):
            _fields_ = (
                ("address", wintypes.DWORD),
                ("index", wintypes.DWORD),
                ("mask", wintypes.DWORD),
                ("broadcast", wintypes.DWORD),
                ("reassembly_size", wintypes.DWORD),
                ("unused", wintypes.WORD),
                ("kind", wintypes.WORD),
            )

        get_table = ctypes.windll.iphlpapi.GetIpAddrTable  # type: ignore[attr-defined]
        size = wintypes.ULONG(0)
        get_table(None, ctypes.byref(size), False)
        if size.value < ctypes.sizeof(wintypes.DWORD):
            return ()
        buffer = ctypes.create_string_buffer(size.value)
        if get_table(buffer, ctypes.byref(size), False) != 0:
            return ()
        raw = buffer.raw
        count = wintypes.DWORD.from_buffer_copy(raw).value
        offset = ctypes.sizeof(wintypes.DWORD)
        row_size = ctypes.sizeof(_MibIpAddrRow)
        result: list[str] = []
        for index in range(count):
            start = offset + index * row_size
            if start + row_size > len(raw):
                break
            row = _MibIpAddrRow.from_buffer_copy(raw, start)
            address = socket.inet_ntoa(struct.pack("<I", row.address))
            if address != "0.0.0.0":
                result.append(address)
        return tuple(result)
    except Exception:
        # Ошибка вспомогательного API не мешает getaddrinfo/wildcard fallback ниже.
        return ()


def _local_ipv4_addresses() -> tuple[str, ...]:
    addresses: set[str] = set(_windows_ipv4_addresses())
    names = {socket.gethostname(), socket.getfqdn()}
    for name in names:
        try:
            infos = socket.getaddrinfo(
                name,
                None,
                socket.AF_INET,
                socket.SOCK_DGRAM,
            )
        except OSError:
            continue
        for info in infos:
            address = info[4][0]
            if address:
                addresses.add(address)

    # getaddrinfo на некоторых Windows-машинах возвращает только loopback.
    try:
        probe_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe_socket.connect(WSD_ADDRESS)
            addresses.add(probe_socket.getsockname()[0])
        finally:
            probe_socket.close()
    except OSError:
        pass

    addresses.add("0.0.0.0")
    return tuple(
        sorted(addresses, key=lambda item: (item == "0.0.0.0", item))
    )


def _open_discovery_sockets() -> list[socket.socket]:
    sockets: list[socket.socket] = []
    payload = _probe_xml()
    for local_ip in _local_ipv4_addresses():
        sock: socket.socket | None = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            if local_ip != "0.0.0.0":
                sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_MULTICAST_IF,
                    socket.inet_aton(local_ip),
                )
            sock.bind((local_ip, 0))
            sock.setblocking(False)
            # Повтор уменьшает шанс потерять единственный UDP Probe в Wi-Fi-сети.
            sock.sendto(payload, WSD_ADDRESS)
            sock.sendto(payload, WSD_ADDRESS)
            sockets.append(sock)
        except OSError:
            if sock is not None:
                sock.close()
    return sockets


def _scope_name(root: ET.Element) -> str:
    for element in root.iter():
        if _local_name(element.tag) != "Scopes" or not element.text:
            continue
        for scope in element.text.split():
            marker = "/name/"
            if marker in scope:
                return _clean_name(scope.split(marker, 1)[1])
    return ""


def _parse_probe_response(payload: bytes, sender_ip: str) -> list[DiscoveredEndpoint]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        text = payload.decode("utf-8", "ignore")
        matches = re.findall(r"<[^>]*XAddrs[^>]*>(.*?)</[^>]*XAddrs>", text, re.I | re.S)
        xaddrs = " ".join(html.unescape(item) for item in matches).split()
        name_hint = ""
    else:
        xaddrs = []
        for element in root.iter():
            if _local_name(element.tag) == "XAddrs" and element.text:
                xaddrs.extend(element.text.split())
        name_hint = _scope_name(root)

    result: list[DiscoveredEndpoint] = []
    for endpoint in xaddrs:
        endpoint = html.unescape(endpoint).strip()
        if not _valid_http_endpoint(endpoint):
            continue
        ip = _endpoint_ip(endpoint, sender_ip)
        if not ip:
            continue
        result.append(DiscoveredEndpoint(endpoint, ip, name_hint))
    return result


def discover_endpoints(
    *,
    timeout: float = 4.0,
    stop_event: threading.Event | None = None,
) -> tuple[DiscoveredEndpoint, ...]:
    """Рассылает WS-Discovery Probe и возвращает уникальные устройства по IP."""

    sockets = _open_discovery_sockets()
    if not sockets:
        return ()
    deadline = time.monotonic() + max(0.2, timeout)
    found: dict[str, DiscoveredEndpoint] = {}
    try:
        while not _stopped(stop_event):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                readable, _, _ = select.select(sockets, [], [], min(0.2, remaining))
            except (OSError, ValueError):
                break
            for sock in readable:
                try:
                    payload, sender = sock.recvfrom(64 * 1024)
                except (BlockingIOError, OSError):
                    continue
                for candidate in _parse_probe_response(payload, sender[0]):
                    key = _canonical_ip(candidate.ip)
                    previous = found.get(key)
                    if previous is None or (
                        "device_service" in candidate.endpoint.lower()
                        and "device_service" not in previous.endpoint.lower()
                    ):
                        found[key] = candidate
    finally:
        for sock in sockets:
            sock.close()
    return tuple(sorted(found.values(), key=lambda item: _ip_sort_key(item.ip)))


def _ip_sort_key(value: str) -> tuple[int, str]:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return (2, value.lower())
    return (0 if address.version == 4 else 1, address.packed.hex())


def _ws_security_header(username: str, password: str) -> str:
    if not username and not password:
        return ""
    nonce = os.urandom(16)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode("utf-8") + password.encode("utf-8")).digest()
    ).decode("ascii")
    nonce_b64 = base64.b64encode(nonce).decode("ascii")
    return (
        '<s:Header><wsse:Security s:mustUnderstand="1" '
        'xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-wssecurity-secext-1.0.xsd" '
        'xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-wssecurity-utility-1.0.xsd">'
        "<wsse:UsernameToken>"
        f"<wsse:Username>{html.escape(username)}</wsse:Username>"
        '<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
        f"{digest}</wsse:Password>"
        '<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
        f"{nonce_b64}</wsse:Nonce>"
        f"<wsu:Created>{created}</wsu:Created>"
        "</wsse:UsernameToken></wsse:Security></s:Header>"
    )


def _soap_envelope(body: str, username: str, password: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<s:Envelope xmlns:s="{SOAP_NAMESPACE}" xmlns:tds="{DEVICE_NAMESPACE}" '
        f'xmlns:trt="{MEDIA_NAMESPACE}">'
        f"{_ws_security_header(username, password)}"
        f"<s:Body>{body}</s:Body></s:Envelope>"
    ).encode("utf-8")


def _post_soap(
    endpoint: str,
    body: str,
    action: str,
    username: str,
    password: str,
) -> bytes:
    if not _valid_http_endpoint(endpoint):
        raise OnvifError("Камера вернула некорректный ONVIF endpoint.")
    headers = {
        "Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"',
        "SOAPAction": f'"{action}"',
        "User-Agent": "GlazIvy/1.0 ONVIF",
    }
    request = Request(
        endpoint,
        data=_soap_envelope(body, username, password),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.read(2 * 1024 * 1024)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        raise OnvifError("Камера не ответила на ONVIF-запрос.") from exc


def _parse_xml(payload: bytes) -> ET.Element:
    try:
        return ET.fromstring(payload)
    except ET.ParseError as exc:
        raise OnvifError("Камера вернула некорректный ONVIF-ответ.") from exc


def _first_text(element: ET.Element, local_name: str) -> str:
    for child in element.iter():
        if _local_name(child.tag) == local_name and child.text:
            return _clean_name(child.text)
    return ""


def _media_endpoint(
    device_endpoint: str,
    username: str,
    password: str,
) -> str:
    payload = _post_soap(
        device_endpoint,
        "<tds:GetCapabilities><tds:Category>All</tds:Category></tds:GetCapabilities>",
        f"{DEVICE_NAMESPACE}/GetCapabilities",
        username,
        password,
    )
    root = _parse_xml(payload)
    for element in root.iter():
        if _local_name(element.tag) != "Media":
            continue
        for child in element.iter():
            if _local_name(child.tag) == "XAddr" and child.text:
                endpoint = html.unescape(child.text).strip()
                if _valid_http_endpoint(endpoint):
                    return endpoint
    return ""


def _profile_token(element: ET.Element) -> str:
    for key, value in element.attrib.items():
        if _local_name(key) == "token":
            return value.strip()
    return ""


def _profiles(
    endpoint: str,
    username: str,
    password: str,
) -> tuple[_Profile, ...]:
    payload = _post_soap(
        endpoint,
        "<trt:GetProfiles/>",
        f"{MEDIA_NAMESPACE}/GetProfiles",
        username,
        password,
    )
    try:
        root = _parse_xml(payload)
    except OnvifError:
        text = payload.decode("utf-8", "ignore")
        tokens = re.findall(
            r"<(?:\w+:)?Profiles\b[^>]*\btoken=[\"']([^\"']+)[\"']",
            text,
            re.I,
        )
        fallback = [
            _Profile(html.unescape(token), "", 0, 0, index)
            for index, token in enumerate(dict.fromkeys(tokens))
        ]
        if fallback:
            return tuple(fallback)
        raise
    result: list[_Profile] = []
    seen: set[str] = set()
    for element in root.iter():
        if _local_name(element.tag) != "Profiles":
            continue
        token = _profile_token(element)
        if not token or token in seen:
            continue
        seen.add(token)
        width = 0
        height = 0
        for child in element.iter():
            local_name = _local_name(child.tag)
            if local_name == "Width" and child.text:
                try:
                    width = int(child.text)
                except ValueError:
                    pass
            elif local_name == "Height" and child.text:
                try:
                    height = int(child.text)
                except ValueError:
                    pass
        result.append(
            _Profile(
                token=token,
                name=_first_text(element, "Name"),
                width=width,
                height=height,
                index=len(result),
            )
        )
    if not result:
        raise OnvifError("Камера не вернула ONVIF-профили.")
    return tuple(result)


def _stream_uri(
    endpoint: str,
    profile_token: str,
    username: str,
    password: str,
) -> str:
    token = html.escape(profile_token)
    body = (
        "<trt:GetStreamUri><trt:StreamSetup>"
        '<Stream xmlns="http://www.onvif.org/ver10/schema">RTP-Unicast</Stream>'
        '<Transport xmlns="http://www.onvif.org/ver10/schema">'
        "<Protocol>RTSP</Protocol></Transport>"
        f"</trt:StreamSetup><trt:ProfileToken>{token}</trt:ProfileToken>"
        "</trt:GetStreamUri>"
    )
    payload = _post_soap(
        endpoint,
        body,
        f"{MEDIA_NAMESPACE}/GetStreamUri",
        username,
        password,
    )
    try:
        root = _parse_xml(payload)
        candidates = [
            element.text
            for element in root.iter()
            if _local_name(element.tag) == "Uri" and element.text
        ]
    except OnvifError:
        text = payload.decode("utf-8", "ignore")
        candidates = re.findall(
            r"<[^>]*Uri[^>]*>(rtsp[s]?://.*?)</[^>]*Uri>",
            text,
            re.I | re.S,
        )
    for candidate in candidates:
        uri = html.unescape(candidate).strip()
        try:
            parsed = urlsplit(uri)
        except ValueError:
            continue
        if parsed.scheme.lower() in {"rtsp", "rtsps"} and parsed.hostname:
            return uri
    raise OnvifError("Камера не вернула RTSP-URL.")


def _contains_credentials(uri: str) -> bool:
    try:
        if urlsplit(uri).username is not None:
            return True
    except ValueError:
        return False
    decoded = unquote(uri).lower()
    has_user = re.search(r"(?:^|[/?&;])(?:user|username|login)=", decoded) is not None
    has_password = re.search(r"(?:^|[/?&;])(?:password|passwd|pass|pwd)=", decoded) is not None
    return has_user and has_password


def _with_credentials(uri: str, username: str, password: str) -> str:
    # Служебные реквизиты внутри URL камеры важнее подобранной ONVIF-пары.
    if _contains_credentials(uri) or (not username and not password):
        return uri
    try:
        parsed = urlsplit(uri)
    except ValueError:
        return uri
    userinfo = f"{quote(username, safe='')}:{quote(password, safe='')}@"
    return urlunsplit(
        (parsed.scheme, userinfo + parsed.netloc, parsed.path, parsed.query, parsed.fragment)
    )


def _select_streams(entries: list[tuple[_Profile, str]]) -> tuple[str, str]:
    if not entries:
        return "", ""
    if len(entries) == 1:
        return entries[0][1], entries[0][1]

    known = [entry for entry in entries if entry[0].area > 0]
    if len(known) >= 2 and len({entry[0].area for entry in known}) > 1:
        hd = max(known, key=lambda entry: (entry[0].area, -entry[0].index))[1]
        sd = min(known, key=lambda entry: (entry[0].area, entry[0].index))[1]
        return hd, sd
    # ONVIF обычно перечисляет main первым, sub вторым.
    return entries[0][1], entries[-1][1]


def _device_name(endpoint: str, username: str, password: str) -> str:
    payload = _post_soap(
        endpoint,
        "<tds:GetDeviceInformation/>",
        f"{DEVICE_NAMESPACE}/GetDeviceInformation",
        username,
        password,
    )
    root = _parse_xml(payload)
    manufacturer = _first_text(root, "Manufacturer")
    model = _first_text(root, "Model")
    if manufacturer and model:
        if manufacturer.lower() in model.lower():
            return model
        return f"{manufacturer} {model}"[:96]
    return model or manufacturer


def _hostname(endpoint: str, username: str, password: str) -> str:
    payload = _post_soap(
        endpoint,
        "<tds:GetHostname/>",
        f"{DEVICE_NAMESPACE}/GetHostname",
        username,
        password,
    )
    return _first_text(_parse_xml(payload), "Name")


def _profiles_for_credentials(
    device_endpoint: str,
    username: str,
    password: str,
) -> tuple[str, tuple[_Profile, ...]]:
    media_endpoint = ""
    try:
        media_endpoint = _media_endpoint(device_endpoint, username, password)
    except OnvifError:
        pass
    candidates = [media_endpoint] if media_endpoint else []
    if device_endpoint not in candidates:
        candidates.append(device_endpoint)
    last_error: OnvifError | None = None
    for endpoint in candidates:
        try:
            return endpoint, _profiles(endpoint, username, password)
        except OnvifError as exc:
            last_error = exc
    raise last_error or OnvifError("Не удалось получить профили камеры.")


def resolve_onvif_camera(
    endpoint: str,
    *,
    ip: str = "",
    credentials: tuple[str, str] | None = None,
    name_hint: str = "",
    stop_event: threading.Event | None = None,
) -> DiscoveredCamera:
    """Получает профили и выданные камерой RTSP-URL, не проверяя видео."""

    if not _valid_http_endpoint(endpoint):
        raise OnvifError("Некорректный ONVIF endpoint камеры.")
    camera_ip = _canonical_ip(ip) if ip else _endpoint_ip(endpoint, "")
    credential_pairs: Iterable[tuple[str, str]] = (
        (credentials,) if credentials is not None else DEFAULT_CREDENTIALS
    )
    best_name = _clean_name(name_hint)
    best_profile_name = ""
    matched_credentials: tuple[str, str] | None = None

    for username, password in credential_pairs:
        if _stopped(stop_event):
            break
        try:
            media_endpoint, profiles = _profiles_for_credentials(
                endpoint,
                username,
                password,
            )
        except OnvifError:
            continue
        matched_credentials = (username, password)
        if profiles and profiles[0].name:
            best_profile_name = profiles[0].name

        streams: list[tuple[_Profile, str]] = []
        for profile in profiles:
            if _stopped(stop_event):
                break
            try:
                uri = _stream_uri(media_endpoint, profile.token, username, password)
            except OnvifError:
                continue
            streams.append((profile, _with_credentials(uri, username, password)))
        if _stopped(stop_event):
            break
        if not streams:
            continue

        try:
            discovered_name = _device_name(endpoint, username, password)
        except OnvifError:
            discovered_name = ""
        if not discovered_name:
            discovered_name = best_name or best_profile_name
        if not discovered_name:
            try:
                discovered_name = _hostname(endpoint, username, password)
            except OnvifError:
                discovered_name = ""
        hd_url, sd_url = _select_streams(streams)
        return DiscoveredCamera(
            endpoint=endpoint,
            ip=camera_ip,
            name=discovered_name,
            media_endpoint=media_endpoint,
            stream_url_hd=hd_url,
            stream_url_sd=sd_url,
            username=username,
            password=password,
        )

    lookup_username, lookup_password = matched_credentials or credentials or ("", "")
    saved_username, saved_password = credentials or ("", "")
    if _stopped(stop_event):
        return DiscoveredCamera(
            endpoint=endpoint,
            ip=camera_ip,
            name=best_name or best_profile_name,
            username=saved_username,
            password=saved_password,
        )
    if not best_name:
        try:
            best_name = _device_name(endpoint, lookup_username, lookup_password)
        except OnvifError:
            best_name = ""
    if not best_name:
        best_name = best_profile_name
    if not best_name:
        try:
            best_name = _hostname(endpoint, lookup_username, lookup_password)
        except OnvifError:
            best_name = ""
    return DiscoveredCamera(
        endpoint=endpoint,
        ip=camera_ip,
        name=best_name,
        username=saved_username,
        password=saved_password,
    )


def discover_cameras(
    *,
    timeout: float = 4.0,
    stop_event: threading.Event | None = None,
) -> tuple[DiscoveredCamera, ...]:
    """Находит устройства и параллельно пробует безопасный набор учёток."""

    endpoints = discover_endpoints(timeout=timeout, stop_event=stop_event)
    if not endpoints or _stopped(stop_event):
        return ()
    workers = min(8, len(endpoints))
    resolved: list[DiscoveredCamera] = []
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="onvif") as executor:
        futures = {
            executor.submit(
                resolve_onvif_camera,
                candidate.endpoint,
                ip=candidate.ip,
                name_hint=candidate.name_hint,
                stop_event=stop_event,
            ): candidate
            for candidate in endpoints
        }
        for future in as_completed(futures):
            candidate = futures[future]
            if _stopped(stop_event):
                continue
            try:
                camera = future.result()
            except Exception:
                camera = DiscoveredCamera(
                    endpoint=candidate.endpoint,
                    ip=candidate.ip,
                    name=candidate.name_hint,
                )
            resolved.append(camera)
    return tuple(sorted(resolved, key=lambda item: _ip_sort_key(item.ip)))
