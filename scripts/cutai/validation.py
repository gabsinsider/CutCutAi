from urllib.parse import urlparse

PLATFORMS = {
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "facebook.com": "Facebook",
    "fb.watch": "Facebook",
    "tiktok.com": "TikTok",
    "twitch.tv": "Twitch",
}


def validate_source_url(value: str) -> tuple[str, str]:
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Use uma URL completa iniciada por http:// ou https://")
    if parsed.username or parsed.password:
        raise ValueError("URLs com credenciais não são aceitas")
    host = (parsed.hostname or "").lower().removeprefix("www.")
    platform = next((name for domain, name in PLATFORMS.items() if host == domain or host.endswith(f".{domain}")), "Outro")
    return value, platform


def extract_url(text: str) -> str:
    for token in text.replace("\n", " ").split():
        candidate = token.strip("<>[](){}.,;\"'")
        if candidate.startswith(("https://", "http://")):
            return validate_source_url(candidate)[0]
    raise ValueError("Nenhum link de transmissão foi encontrado")

