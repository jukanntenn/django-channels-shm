"""WebSocket consumer for the SHM chat demo.

A WeChat-style chat on top of nothing but the shared-memory channel layer:
presence, nicknames, private messages and group chats all coordinate across
uvicorn worker processes through /dev/shm — no Redis, no database.

Protocol (client → server):

    {"type": "hello",       "nickname": str}
    {"type": "pm",          "to": str, "text": str, "msg_id": str}
    {"type": "gm",          "group": str, "text": str}
    {"type": "join_group",  "group": str}
    {"type": "leave_group", "group": str}
    {"type": "census"}                       # refresh rosters (heartbeat)

Server → client: welcome / nickname_taken / error / pm / gm / pm_delivered /
user_online / user_offline / joined_group / left_group / group_member_joined /
group_member_left / group_member_online.

How the stateless parts work (standard channel-layer patterns, no storage):

- Nickname uniqueness is per-connection group membership: each user joins the
  group ``u_<sha256(nick)>``. A newcomer claims a name by broadcasting
  ``nick.claim`` into that group; a current holder answers ``nick.taken``
  directly to the claimant's channel. Two simultaneous claims for the same
  name can both win — acceptable for a demo (and visible immediately, since
  both holders then receive each other's messages).
- Presence is one shared group; every member answers ``census.query`` with a
  direct reply, so a fresh client learns who is online without any registry.
- Group size is capped by the layer itself (``max_members_per_group``): a
  full ``group_add`` raises, which surfaces as a ``group_full`` error.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time

from channels.generic.websocket import AsyncJsonWebsocketConsumer

# Layer group names must be ASCII [a-zA-Z0-9-._]; user-facing nicknames and
# group names (CJK welcome) are hashed into that space. "presence" cannot
# collide with the hashed forms ("u_…"/"g_…").
PRESENCE_GROUP = "presence"
# Marker used in census payloads (message bodies may contain anything).
_PRESENCE_MARKER = "@presence"

CLAIM_WINDOW_S = 0.4  # how long a nickname claim listens for conflicts
NICK_MAX_LEN = 24
GROUP_NAME_MAX_LEN = 30
TEXT_MAX_LEN = 2000
MSG_ID_MAX_LEN = 64

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _layer_group(kind: str, name: str) -> str:
    """Map a user-facing name to a valid layer group name."""
    digest = hashlib.sha256(f"{kind}:{name}".encode()).hexdigest()[:24]
    return f"{kind}_{digest}"


def _clean_name(raw: object, max_len: int, what: str) -> str:
    """Validate a nickname / chat-group name; raises ValueError with a
    user-facing message."""
    name = str(raw).strip()
    if not name:
        msg = f"{what}不能为空"
        raise ValueError(msg)
    if len(name) > max_len:
        msg = f"{what}不能超过 {max_len} 个字符"
        raise ValueError(msg)
    if _CONTROL_CHARS.search(name):
        msg = f"{what}不能包含控制字符"
        raise ValueError(msg)
    return name


def _clean_text(raw: object) -> str:
    text = str(raw)
    if not text.strip():
        msg = "消息不能为空"
        raise ValueError(msg)
    if len(text) > TEXT_MAX_LEN:
        msg = f"消息不能超过 {TEXT_MAX_LEN} 个字符"
        raise ValueError(msg)
    return text


class ChatConsumer(AsyncJsonWebsocketConsumer):
    """One WebSocket connection = one (eventually identified) chatter."""

    nickname: str | None
    _nick_group: str
    # chat-group display name -> layer group name
    _groups: dict[str, str]
    _nick_conflict: bool
    # in-flight nickname claim (cancelled by disconnect before it can complete)
    _claim_task: asyncio.Task[None] | None

    def __init__(self) -> None:
        super().__init__()
        self.nickname = None
        self._nick_group = ""
        self._groups = {}
        self._nick_conflict = False
        self._claim_task = None

    # ── connection lifecycle ─────────────────────────────────────

    async def connect(self) -> None:
        await self.accept()

    async def disconnect(self, code: int) -> None:
        if self._claim_task is not None and not self._claim_task.done():
            self._claim_task.cancel()
        if self.nickname is None:
            return
        nick = self.nickname
        # Discard BEFORE broadcasting so the departing member never receives
        # its own farewell; remaining members still get it.
        for name, group in self._groups.items():
            await self.channel_layer.group_discard(group, self.channel_name)
            await self.channel_layer.group_send(
                group, {"type": "group.left", "group": name, "nickname": nick}
            )
        self._groups.clear()
        await self.channel_layer.group_discard(PRESENCE_GROUP, self.channel_name)
        await self.channel_layer.group_send(
            PRESENCE_GROUP, {"type": "user.left", "nickname": nick}
        )
        await self.channel_layer.group_discard(self._nick_group, self.channel_name)

    # ── client → server ──────────────────────────────────────────

    async def receive_json(self, content: dict, **kwargs: object) -> None:
        kind = content.get("type")
        try:
            if kind == "hello":
                await self._hello(content.get("nickname"))
            elif kind == "pm":
                await self._pm(content)
            elif kind == "gm":
                await self._gm(content)
            elif kind == "join_group":
                await self._join_group(content.get("group"))
            elif kind == "leave_group":
                await self._leave_group(content.get("group"))
            elif kind == "census":
                await self._refresh_rosters()
            else:
                msg = f"未知消息类型: {kind!r}"
                await self._error("bad_type", msg)
        except ValueError as exc:
            await self._error("bad_request", str(exc))

    async def _hello(self, raw_nick: object) -> None:
        nick = _clean_name(raw_nick, NICK_MAX_LEN, "昵称")
        if self.nickname is not None:
            msg = "本连接已使用昵称,请刷新页面"
            await self._error("already_identified", msg)
            return

        # Claim the name: any current holder of this nickname answers
        # nick.taken directly to our channel. The verdict is awaited in a
        # SEPARATE task — channels dispatches layer events serially with
        # receive_json, so sleeping here would keep the conflict reply from
        # being dispatched until after the window had already closed.
        self._nick_conflict = False
        nick_group = _layer_group("u", nick)
        await self.channel_layer.group_send(
            nick_group,
            {"type": "nick.claim", "reply_to": self.channel_name, "nickname": nick},
        )
        self._claim_task = asyncio.create_task(self._finish_hello(nick, nick_group))

    async def _finish_hello(self, nick: str, nick_group: str) -> None:
        try:
            await asyncio.sleep(CLAIM_WINDOW_S)
        except asyncio.CancelledError:  # disconnected mid-claim
            return
        if self._nick_conflict:
            await self.send_json({"type": "nickname_taken", "nickname": nick})
            return

        await self.channel_layer.group_add(nick_group, self.channel_name)
        await self.channel_layer.group_add(PRESENCE_GROUP, self.channel_name)
        self.nickname = nick
        self._nick_group = nick_group

        await self.send_json(
            {"type": "welcome", "nickname": nick, "worker_pid": os.getpid()}
        )
        # Announce, then ask everyone present to introduce themselves.
        await self.channel_layer.group_send(
            PRESENCE_GROUP, {"type": "user.joined", "nickname": nick}
        )
        await self._census(PRESENCE_GROUP, _PRESENCE_MARKER)

    async def _pm(self, content: dict) -> None:
        if self.nickname is None:
            await self._require_nick()
            return
        to = _clean_name(content.get("to"), NICK_MAX_LEN, "对方昵称")
        text = _clean_text(content.get("text"))
        msg_id = str(content.get("msg_id", ""))[:MSG_ID_MAX_LEN]
        await self.channel_layer.group_send(
            _layer_group("u", to),
            {
                "type": "pm.recv",
                "from": self.nickname,
                "text": text,
                "ts": time.time(),
                "msg_id": msg_id,
                "reply_to": self.channel_name,
                "handled_by_pid": os.getpid(),
            },
        )

    async def _gm(self, content: dict) -> None:
        if self.nickname is None:
            await self._require_nick()
            return
        name = _clean_name(content.get("group"), GROUP_NAME_MAX_LEN, "群名称")
        group = self._groups.get(name)
        if group is None:
            msg = f"尚未加入群聊 {name!r},请先加入"
            await self._error("not_in_group", msg)
            return
        text = _clean_text(content.get("text"))
        await self.channel_layer.group_send(
            group,
            {
                "type": "gm.recv",
                "group": name,
                "from": self.nickname,
                "text": text,
                "ts": time.time(),
                "handled_by_pid": os.getpid(),
            },
        )

    async def _join_group(self, raw_name: object) -> None:
        if self.nickname is None:
            await self._require_nick()
            return
        name = _clean_name(raw_name, GROUP_NAME_MAX_LEN, "群名称")
        if name in self._groups:  # idempotent rejoin
            await self.send_json({"type": "joined_group", "group": name})
            return
        group = _layer_group("g", name)
        try:
            await self.channel_layer.group_add(group, self.channel_name)
        except RuntimeError as exc:
            if "max_members_per_group" in str(exc):
                await self._error("group_full", "群聊人数已满(500)")
            else:
                await self._error("join_failed", "暂时无法加入,请稍后再试")
            return
        self._groups[name] = group
        await self.send_json({"type": "joined_group", "group": name})
        await self.channel_layer.group_send(
            group, {"type": "group.joined", "group": name, "nickname": self.nickname}
        )
        await self._census(group, name)

    async def _leave_group(self, raw_name: object) -> None:
        name = str(raw_name).strip()
        group = self._groups.pop(name, None)
        if group is None:
            return
        await self.channel_layer.group_discard(group, self.channel_name)
        await self.channel_layer.group_send(
            group, {"type": "group.left", "group": name, "nickname": self.nickname}
        )
        await self.send_json({"type": "left_group", "group": name})

    async def _refresh_rosters(self) -> None:
        """Client heartbeat: rebuild presence and group rosters from census
        replies (self-heals members that vanished without a farewell)."""
        if self.nickname is None:
            return
        await self._census(PRESENCE_GROUP, _PRESENCE_MARKER)
        for name, group in self._groups.items():
            await self._census(group, name)

    async def _census(self, group: str, marker: str) -> None:
        await self.channel_layer.group_send(
            group,
            {"type": "census.query", "group": marker, "reply_to": self.channel_name},
        )

    # ── channel-layer events ─────────────────────────────────────

    async def nick_claim(self, event: dict[str, object]) -> None:
        """I hold this nickname; tell the claimant it is taken."""
        await self.channel_layer.send(
            str(event["reply_to"]),
            {"type": "nick.taken", "nickname": event["nickname"]},
        )

    async def nick_taken(self, _event: dict[str, object]) -> None:
        self._nick_conflict = True

    async def user_joined(self, event: dict[str, object]) -> None:
        nick = str(event["nickname"])
        if nick != self.nickname:
            await self.send_json({"type": "user_online", "nickname": nick})

    async def user_left(self, event: dict[str, object]) -> None:
        await self.send_json({"type": "user_offline", "nickname": event["nickname"]})

    async def census_query(self, event: dict[str, object]) -> None:
        marker = str(event["group"])
        member = self.nickname is not None and (
            marker == _PRESENCE_MARKER or marker in self._groups
        )
        if member:
            await self.channel_layer.send(
                str(event["reply_to"]),
                {"type": "census.reply", "group": marker, "nickname": self.nickname},
            )

    async def census_reply(self, event: dict[str, object]) -> None:
        nick = str(event["nickname"])
        if nick == self.nickname:
            return
        if str(event["group"]) == _PRESENCE_MARKER:
            await self.send_json({"type": "user_online", "nickname": nick})
        else:
            await self.send_json(
                {
                    "type": "group_member_online",
                    "group": event["group"],
                    "nickname": nick,
                }
            )

    async def pm_recv(self, event: dict) -> None:
        reply_to = str(event.get("reply_to", ""))
        if reply_to:
            await self.channel_layer.send(
                reply_to, {"type": "pm.acked", "msg_id": event.get("msg_id", "")}
            )
        if event["from"] == self.nickname:
            return  # sender's client rendered the outgoing message already
        await self.send_json(
            {
                "type": "pm",
                "from": event["from"],
                "text": event["text"],
                "ts": event["ts"],
                "handled_by_pid": event.get("handled_by_pid"),
            }
        )

    async def pm_acked(self, event: dict[str, object]) -> None:
        await self.send_json(
            {"type": "pm_delivered", "msg_id": str(event.get("msg_id", ""))}
        )

    async def gm_recv(self, event: dict) -> None:
        if event["from"] == self.nickname:
            return
        await self.send_json(
            {
                "type": "gm",
                "group": event["group"],
                "from": event["from"],
                "text": event["text"],
                "ts": event["ts"],
                "handled_by_pid": event.get("handled_by_pid"),
            }
        )

    async def group_joined(self, event: dict[str, object]) -> None:
        nick = str(event["nickname"])
        if nick == self.nickname:
            return
        await self.send_json(
            {"type": "group_member_joined", "group": event["group"], "nickname": nick}
        )

    async def group_left(self, event: dict[str, object]) -> None:
        await self.send_json(
            {
                "type": "group_member_left",
                "group": event["group"],
                "nickname": event["nickname"],
            }
        )

    # ── helpers ──────────────────────────────────────────────────

    async def _require_nick(self) -> None:
        msg = "请先设置昵称"
        await self._error("not_identified", msg)

    async def _error(self, code: str, message: str) -> None:
        await self.send_json({"type": "error", "code": code, "message": message})
