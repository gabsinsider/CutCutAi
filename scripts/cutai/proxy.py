import re
import shlex
from urllib.parse import quote


def normalize_proxy_url(raw: str) -> str:
    """Normalize common Bright Data proxy snippets without exposing credentials."""
    value = raw.strip().strip("'\"")
    if not value:
        return ""
    if value.startswith(("http://", "https://", "socks4://", "socks5://")):
        return value
    if "--proxy" in value:
        parts = shlex.split(value)
        host = _option(parts, "--proxy")
        credentials = _option(parts, "--proxy-user")
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
    # Bright Data sometimes presents host:port:username:password.
    match = re.fullmatch(r"([^:]+):(\d+):([^:]+):(.+)", value)
    if match:
        host, port, user, password = match.groups()
        return f"http://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}"
    if re.fullmatch(r"[^:]+:\d+", value):
        return "http://" + value
    raise ValueError("Formato de proxy não reconhecido")


def _option(parts: list[str], name: str) -> str:
    try:
        return parts[parts.index(name) + 1]
    except (ValueError, IndexError):
        return ""

