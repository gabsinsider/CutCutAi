import os
import re
import secrets
from urllib.parse import quote, unquote, urlsplit, urlunsplit

_SESSION_ID = os.getenv("CUTAI_PROXY_SESSION", "").strip() or secrets.token_hex(8)


def _normalize(raw: str) -> str:
    value = raw.strip().strip("'\"")
    if not value:
        return ""
    if value.startswith(("http://", "https://", "socks4://", "socks5://")):
        return value
    if "--proxy" in value:
        host_match = re.search(r"--proxy(?:=|\s+)[\"']?([^\s\"']+)", value)
        credentials_match = re.search(r"--proxy-user(?:=|\s+)[\"']?([^\s\"']+)", value)
        host = host_match.group(1) if host_match else ""
        credentials = credentials_match.group(1) if credentials_match else ""
        if not host:
            raise ValueError("Comando de proxy sem host")
        if credentials:
            user, separator, password = credentials.partition(":")
            if not separator:
                raise ValueError("Credenciais do proxy incompletas")
            return f"http://{quote(user, safe='')}:{quote(password, safe='')}@{host}"
        return f"http://{host}"
    if "@" in value:
        return "http://" + value
    match = re.fullmatch(r"([^:]+):(\d+):([^:]+):(.+)", value)
    if match:
        host, port, user, password = match.groups()
        return f"http://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}"
    if re.fullmatch(r"[^:]+:\d+", value):
        return "http://" + value
    raise ValueError("Formato de proxy não reconhecido")


def sticky_proxy_url(raw: str) -> str:
    normalized = _normalize(raw)
    if not normalized:
        return ""
    parts = urlsplit(normalized)
    host = (parts.hostname or "").lower()
    username = unquote(parts.username or "")
    password = unquote(parts.password or "")
    if host.endswith("superproxy.io"):
        if "-session-" not in username:
            username = f"{username}-session-{_SESSION_ID}-const"
        elif not username.endswith("-const"):
            username = f"{username}-const"
        auth = quote(username, safe="")
        if password:
            auth += ":" + quote(password, safe="")
        netloc = auth + "@" + (parts.hostname or "")
        if parts.port:
            netloc += f":{parts.port}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return normalized


def normalize_proxy_url(raw: str) -> str:
    """Normaliza o proxy e fixa uma sessão Bright Data por processo."""
    return sticky_proxy_url(raw)


def proxy_session_id() -> str:
    return _SESSION_ID


def _option(parts: list[str], name: str) -> str:
    try:
        return parts[parts.index(name) + 1]
    except (ValueError, IndexError):
        return ""
