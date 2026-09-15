from __future__ import annotations

import re

# RFC 4122 UUID version 4 (lowercase hex + hyphens)
UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def is_uuid_v4(value: str) -> bool:
    return bool(UUID_V4_RE.match(value.lower()))
