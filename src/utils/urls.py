from urllib.parse import urlparse


SUPPORTED_HOSTS = {"instagram.com", "www.instagram.com", "instagr.am"}


def normalize_instagram_url(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    candidate = value if "://" in value else f"https://{value}"
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in SUPPORTED_HOSTS or not parsed.path.strip("/"):
        return None
    return candidate


def is_instagram_url(value: str) -> bool:
    return normalize_instagram_url(value) is not None
