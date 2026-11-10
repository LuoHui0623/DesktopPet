"""快照令牌：把一次读取的内容状态固化成可回传的凭据。

令牌是内容状态的摘要，不是锁。它的作用只有一个：让"我编辑的是我读到的那一版"
成为可以被服务端校验的事实，从而在编辑期拒绝过期编辑，而不是等到提交期才发现冲突。
"""

import hashlib

from module.files.resolver import FileError

_TOKEN_CHARS = 12
TOKEN_HINT = "编辑此文件时作为 token 传入；已过期请重新 read"


class StaleSnapshot(FileError):
    """读取时留底的版本已不再适用，必须重新 read。"""

    code = "stale_snapshot"


def digest(display: str, text: str) -> str:
    """展示路径参与摘要，避免不同文件内容相同却共享令牌。"""
    payload = f"{display}\0{text}".encode()
    return hashlib.sha256(payload).hexdigest()[:_TOKEN_CHARS]


def token_line(version: str) -> str:
    return f"快照 token：{version}（{TOKEN_HINT}）"


def require_match(current: str, token: str, display: str) -> None:
    if token == current:
        return
    raise StaleSnapshot(
        f"{display} 的快照已变化：读到的是 {token}，当前是 {current}；请重新 read 后再编辑"
    )
