"""Online rooms: a shareable code over one authoritative game session.

A room seats two browser clients around the same :class:`GameSession`; anyone
else who opens the link becomes a read-only spectator.  The manager owns the
rooms and one re-entrant lock, which the bridge also takes around every room
game read or write so two tabs never mutate the same session at once.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Mapping
from typing import Any

from chess_master.engine.errors import InvalidInputError
from chess_master.engine.game import GameSession

__all__ = ["DEFAULT_IDLE_TTL", "ROOM_CODE_LENGTH", "Room", "RoomManager"]

ROOM_CODE_LENGTH = 8
DEFAULT_IDLE_TTL = 2 * 60 * 60.0
# No 0/o or 1/l: room codes are read out loud and typed by hand.
_CODE_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"


class Room:
    """One shared table with up to two seated clients."""

    def __init__(self, room_id: str, session: GameSession, host: str, now: float) -> None:
        self.room_id = room_id
        self.session = session
        self.white_client = host
        self.black_client: str | None = None
        self.created_at = now
        self.updated_at = now

    @property
    def status(self) -> str:
        if self.session.is_over:
            return "finished"
        return "active" if self.black_client else "waiting"

    def seat_for(self, client_id: str | None) -> str | None:
        client = str(client_id or "")
        if client == self.white_client:
            return "white"
        if client is not None and self.black_client and client == self.black_client:
            return "black"
        return None

    def to_state(self, client_id: str | None = None) -> dict[str, Any]:
        seat = self.seat_for(client_id)
        status = self.status
        return {
            "room_id": self.room_id,
            "status": status,
            "seat": seat,
            "spectator": seat is None,
            "seats": {"white": True, "black": bool(self.black_client)},
            "invite_url": f"/?room={self.room_id}",
            # No engine help while the room is waiting or live; the finished
            # game unlocks evaluation and Game Review.
            "fair_play": status != "finished",
            "rematch_available": status == "finished" and seat is not None,
        }


class RoomManager:
    """Thread-safe room registry with code generation and an idle TTL."""

    def __init__(
        self,
        *,
        clock: Any = time.monotonic,
        idle_ttl: float = DEFAULT_IDLE_TTL,
    ) -> None:
        self.lock = threading.RLock()
        self._clock = clock
        self._idle_ttl = float(idle_ttl)
        self._rooms: dict[str, Room] = {}

    def create_room(
        self, client_id: str, options: Mapping[str, Any] | None = None
    ) -> Room:
        if options is not None and not isinstance(options, Mapping):
            raise InvalidInputError("Room options must be an object.")
        with self.lock:
            now = self._clock()
            self._prune(now)
            session = GameSession.new_game("online", options)
            if session.clock is not None:
                # The clock runs only once both players are seated.
                session.clock.stop()
            room = Room(self._new_code(), session, str(client_id), now)
            self._rooms[room.room_id] = room
            return room

    def join_room(self, room_id: Any, client_id: str) -> Room:
        if not isinstance(room_id, str) or not room_id.strip():
            raise InvalidInputError("Enter a room code to join.")
        code = room_id.strip().lower()
        with self.lock:
            now = self._clock()
            self._prune(now)
            room = self._rooms.get(code)
            if room is None:
                raise InvalidInputError(
                    "That room was not found or has expired.",
                    details={"room_id": code},
                )
            client = str(client_id)
            if room.seat_for(client) is None and room.black_client is None:
                room.black_client = client
                if room.session.clock is not None:
                    room.session.clock.start()
            # A returning seated client keeps its seat; anyone else spectates.
            room.updated_at = now
            return room

    def get_room(self, room_id: str | None) -> Room | None:
        if not room_id:
            return None
        with self.lock:
            return self._rooms.get(str(room_id))

    def touch(self, room: Room) -> None:
        with self.lock:
            room.updated_at = self._clock()

    def rematch(self, room: Room, client_id: str | None) -> Room:
        with self.lock:
            if room.seat_for(client_id) is None:
                raise InvalidInputError("Only the players can start a rematch.")
            if room.status != "finished":
                raise InvalidInputError("Finish the game before a rematch.")
            if not room.black_client:
                raise InvalidInputError("Wait for an opponent before a rematch.")
            # Colors swap for the next game: the seats move with the players.
            room.white_client, room.black_client = room.black_client, room.white_client
            room.session.rematch_swap()
            room.updated_at = self._clock()
            return room

    # -- internals -------------------------------------------------------
    def _new_code(self) -> str:
        while True:
            code = "".join(
                secrets.choice(_CODE_ALPHABET) for _ in range(ROOM_CODE_LENGTH)
            )
            if code not in self._rooms:
                return code

    def _prune(self, now: float) -> None:
        if self._idle_ttl <= 0:
            return
        for code, room in list(self._rooms.items()):
            if now - room.updated_at >= self._idle_ttl:
                self._rooms.pop(code, None)
