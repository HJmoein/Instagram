from urllib.parse import urlparse


SUPPORTED_HOSTS = {"instagram.com", "www.instagram.com", "instagr.am"}


def is_instagram_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and host in SUPPORTED_HOSTS and bool(parsed.path.strip("/"))
