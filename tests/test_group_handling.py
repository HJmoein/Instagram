from aiogram.enums import ChatType

from src.handlers.media import should_process_message


def test_group_ignores_non_instagram_links() -> None:
    assert should_process_message(ChatType.GROUP, "https://example.com") is False
    assert should_process_message(ChatType.SUPERGROUP, "https://example.com") is False
    assert should_process_message(ChatType.GROUP, "https://instagram.com/p/abc123/") is True


def test_private_chat_still_validates_non_instagram_links() -> None:
    assert should_process_message(ChatType.PRIVATE, "https://example.com") is True
    assert should_process_message(ChatType.PRIVATE, "https://instagram.com/p/abc123/") is True
