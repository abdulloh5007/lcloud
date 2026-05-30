"""Userbot package: Telethon manager + session helpers + clouds + ingest handler."""

from lcloud.userbot.client import (
    AuthSnapshot,
    FlowAlreadyActiveError,
    LoginAlreadyAuthorizedError,
    LoginFlowState,
    NoActiveFlowError,
    UserbotManager,
    UserbotNotConfiguredError,
    WrongAccountError,
    get_userbot_manager,
    set_userbot_manager,
)
from lcloud.userbot.clouds import (
    CloudCreationError,
    clear_cloud_marker,
    create_cloud_chat,
)
from lcloud.userbot.handlers import (
    IngestContext,
    handle_cloud_chat_new_message,
    register_userbot_handlers,
)
from lcloud.userbot.lc1 import (
    LC1_PREFIX,
    ParsedLC1,
    build_lc1_caption,
    parse_lc1_caption,
)
from lcloud.userbot.marker import (
    MARKER_PREFIX,
    ParsedMarker,
    build_marker,
    parse_marker,
    verify_marker,
)
from lcloud.userbot.scan import scan_dialogs_for_clouds, schedule_scan
from lcloud.userbot.session import (
    archive_rejected_session,
    has_session_files,
    session_files,
)

__all__ = [
    "LC1_PREFIX",
    "MARKER_PREFIX",
    "AuthSnapshot",
    "CloudCreationError",
    "FlowAlreadyActiveError",
    "IngestContext",
    "LoginAlreadyAuthorizedError",
    "LoginFlowState",
    "NoActiveFlowError",
    "ParsedLC1",
    "ParsedMarker",
    "UserbotManager",
    "UserbotNotConfiguredError",
    "WrongAccountError",
    "archive_rejected_session",
    "build_lc1_caption",
    "build_marker",
    "clear_cloud_marker",
    "create_cloud_chat",
    "get_userbot_manager",
    "handle_cloud_chat_new_message",
    "has_session_files",
    "parse_lc1_caption",
    "parse_marker",
    "register_userbot_handlers",
    "scan_dialogs_for_clouds",
    "schedule_scan",
    "session_files",
    "set_userbot_manager",
    "verify_marker",
]
