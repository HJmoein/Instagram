import uuid


_captions: dict[str, str] = {}


def save_caption(caption: str) -> str:
    key = uuid.uuid4().hex
    _captions[key] = caption
    return key


def get_caption(key: str) -> str | None:
    return _captions.get(key)