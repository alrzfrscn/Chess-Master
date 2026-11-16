"""Authoritative game session, navigation and branching."""

from __future__ import annotations

import json
import random
import re
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

import chess

from chess_master import __version__
from chess_master.engine.clock import TIME_CONTROL_PRESETS, GameClock, resolve_time_control
from chess_master.engine.errors import (
    GameOverError,
    IllegalMoveError,
    InvalidInputError,
    InvalidModeError,
    InvalidPgnError,
    InvalidPlyError,
    PromotionRequiredError,
)
from chess_master.engine.fen import validate_fen
from chess_master.engine.material import material_summary
from chess_master.engine.pgn_io import PgnImport, build_pgn, parse_pgn, players_from_headers
from chess_master.engine.replay import capture_on_move, replay_board
from chess_master.engine.status import position_status, result_from_status
from chess_master.models.game import MoveRecord, PlayerInfo

__all__ = [
    "GameSession",
    "VALID_MODES",
    "MIN_BOT_ELO",
    "MAX_BOT_ELO",
    "BOT_PACES",
    "DEFAULT_BOT_PACE",
    "bot_pace_for_time_control",
]

VALID_MODES = ("pass_play", "stockfish", "online")
MIN_BOT_ELO = 400
MAX_BOT_ELO = 3000
DEFAULT_BOT_ELO = 1400
BOT_PACES = ("instant", "bullet", "blitz", "rapid")
# Tests and programmatic callers that do not ask for a pace keep the old
# instant replies; the UI passes "blitz" (or the clock's pace) explicitly.
DEFAULT_BOT_PACE = "instant"
_PACE_BY_CLOCK_CATEGORY = {"bullet": "bullet", "blitz": "blitz", "rapid": "rapid"}
_PLAYER_COLORS = ("white", "black", "random")
_UCI_RE = re.compile(r"^[a-h][1-8][a-h][1-8][qrbn]?$")
_PROMOTION_ORDER = ("q", "r", "b", "n")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clean_name(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    text = " ".join(value.strip().split())
    if not text:
        return fallback
    return text[:48]


def _player(color: str, name: str, **kwargs: Any) -> PlayerInfo:
    return PlayerInfo(color=color, name=name, **kwargs)


def _opposite(color: str) -> str:
    return "black" if color == "white" else "white"


def _json_list(value: Any) -> list[Any]:
    if not isinstance(value, str) or not value:
        return []
    try:
        parsed = json.loads(value)
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


def _json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _timeout_from_record(record: Mapping[str, Any]) -> dict[str, Any] | None:
    if record.get("termination_state") != "timeout":
        return None
    winner = record.get("winner")
    if winner not in ("white", "black"):
        return None
    return {
        "state": "timeout",
        "reason": "time_forfeit",
        "winner": winner,
        "result": "1-0" if winner == "white" else "0-1",
        "check": False,
        "check_square": None,
        "claimable_draw": None,
    }


def _coerce_player_color(value: Any) -> str:
    if value is None:
        return "white"
    if not isinstance(value, str):
        raise InvalidInputError("Player color must be white, black or random.")
    color = value.strip().lower()
    if color not in _PLAYER_COLORS:
        raise InvalidInputError(
            "Player color must be white, black or random.",
            details={"player_color": value, "valid": list(_PLAYER_COLORS)},
        )
    return color


def _coerce_bot_elo(value: Any) -> int:
    if value is None:
        return DEFAULT_BOT_ELO
    if isinstance(value, bool):
        raise InvalidInputError("Bot Elo must be a whole number from 400 to 3000.")
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if not isinstance(value, (int, float)) or not float(value).is_integer():
        raise InvalidInputError(
            "Bot Elo must be a whole number from 400 to 3000.",
            details={"bot_elo": value},
        )
    elo = int(value)
    if elo < MIN_BOT_ELO or elo > MAX_BOT_ELO:
        raise InvalidInputError(
            f"Bot Elo must be between {MIN_BOT_ELO} and {MAX_BOT_ELO}.",
            details={"bot_elo": elo, "min": MIN_BOT_ELO, "max": MAX_BOT_ELO},
        )
    return elo


def _engine_label(elo: int) -> str:
    from chess_master.engine.stockfish import strength_label

    return strength_label(elo)


def _coerce_bot_pace(value: Any) -> str:
    if value is None:
        return DEFAULT_BOT_PACE
    if not isinstance(value, str):
        raise InvalidInputError(
            "Bot pace must be instant, bullet, blitz or rapid.",
            details={"bot_pace": value, "valid": list(BOT_PACES)},
        )
    pace = value.strip().lower()
    if pace not in BOT_PACES:
        raise InvalidInputError(
            "Bot pace must be instant, bullet, blitz or rapid.",
            details={"bot_pace": value, "valid": list(BOT_PACES)},
        )
    return pace


def bot_pace_for_time_control(value: Any) -> str:
    """Mirror a clock preset/category onto a bot pace; unlimited stays instant."""

    try:
        control = resolve_time_control(value)
    except InvalidInputError:
        return DEFAULT_BOT_PACE
    if control is None:
        return DEFAULT_BOT_PACE
    return _PACE_BY_CLOCK_CATEGORY.get(control.category, DEFAULT_BOT_PACE)


class GameSession:
    """A single game table.

    The session stores one authoritative main line. Navigation only changes a
    view index; making a move while viewing an earlier ply truncates the future
    of the line (a new branch) and appends the move. The board is rebuilt from
    the root FEN on every navigation so the python-chess move stack stays
    intact for future repetition/fifty-move analysis.
    """

    def __init__(
        self,
        *,
        mode: str,
        start_fen: str,
        moves: list[MoveRecord] | None = None,
        headers: dict[str, str] | None = None,
        players: dict[str, PlayerInfo] | None = None,
        source: str = "new",
        import_meta: dict[str, Any] | None = None,
        start_comment: str = "",
        game_id: str | None = None,
        created_at: str | None = None,
        time_control: Any = None,
        clock: GameClock | None = None,
        timeout: Mapping[str, Any] | None = None,
    ) -> None:
        if mode not in VALID_MODES and mode != "import":
            raise InvalidModeError(
                "Unknown game mode.",
                details={"mode": mode, "valid_modes": list(VALID_MODES) + ["import"]},
            )

        root = validate_fen(start_fen)
        self.mode = mode
        self.source = source
        self._start_fen = root.fen()
        self._root_board = root
        self._moves: list[MoveRecord] = list(moves or [])
        self._headers: dict[str, str] = dict(headers or {})
        self._players: dict[str, PlayerInfo] = players or {
            "white": _player("white", "White"),
            "black": _player("black", "Black"),
        }
        self._import_meta = import_meta
        self._start_comment = start_comment
        self._game_id = game_id or uuid.uuid4().hex[:12]
        self._created_at = created_at or _utc_now()
        control = resolve_time_control(time_control)
        if clock is None and control is not None:
            clock = GameClock(
                control,
                active_color="white" if root.turn == chess.WHITE else "black",
            )
        self._clock = clock
        self._timeout: dict[str, Any] | None = dict(timeout) if timeout else None
        self._resigned_by: str | None = None
        self._draw_agreed = False
        self._draw_offer_by: str | None = None
        self._engine_player_color: str | None = None
        self._engine_bot_color: str | None = None
        self._engine_bot_elo: int | None = None
        self._engine_bot_pace: str | None = None
        self.revision = 1
        self._view_ply = len(self._moves)
        self._board = replay_board(self._start_fen, self._moves, self._view_ply)

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------
    @classmethod
    def new_game(cls, mode: str = "pass_play", options: Mapping[str, Any] | None = None) -> GameSession:
        if mode not in VALID_MODES:
            raise InvalidModeError(
                "Choose a supported game mode.",
                details={"mode": mode, "valid_modes": list(VALID_MODES)},
            )
        if options is None:
            options = {}
        if not isinstance(options, Mapping):
            raise InvalidInputError("Game options must be an object.")

        requested_color = _coerce_player_color(options.get("player_color"))
        if requested_color == "random":
            player_color = random.SystemRandom().choice(("white", "black"))
        else:
            player_color = requested_color

        raw_start_fen = options.get("start_fen")
        if raw_start_fen is None:
            start_fen = chess.STARTING_FEN
        else:
            if not isinstance(raw_start_fen, str):
                raise InvalidInputError("The starting position must be a FEN string.")
            start_fen = validate_fen(raw_start_fen).fen()

        bot_elo = _coerce_bot_elo(options.get("bot_elo")) if mode == "stockfish" else None
        bot_pace = _coerce_bot_pace(options.get("bot_pace")) if mode == "stockfish" else None
        bot_color = _opposite(player_color) if mode == "stockfish" else None
        bot_name = f"Stockfish {bot_elo}" if bot_elo is not None else "Stockfish"
        human_name = _clean_name(options.get("human_name"), "Player")

        white_default = bot_name if bot_color == "white" else (human_name if mode == "stockfish" else "White")
        black_default = bot_name if bot_color == "black" else (human_name if mode == "stockfish" else "Black")
        white_name = _clean_name(options.get("white_name"), white_default)
        black_name = _clean_name(options.get("black_name"), black_default)

        white = _player(
            "white",
            white_name,
            rating=bot_elo if bot_color == "white" else None,
            is_engine=bot_color == "white",
            extras=(
                {"engine_elo": bot_elo, "engine_label": _engine_label(bot_elo)}
                if bot_color == "white" and bot_elo is not None
                else {}
            ),
        )
        black = _player(
            "black",
            black_name,
            rating=bot_elo if bot_color == "black" else None,
            is_engine=bot_color == "black",
            extras=(
                {"engine_elo": bot_elo, "engine_label": _engine_label(bot_elo)}
                if bot_color == "black" and bot_elo is not None
                else {}
            ),
        )

        today = datetime.now(timezone.utc).strftime("%Y.%m.%d")
        default_event = {
            "pass_play": "Chess Master - Pass & Play",
            "stockfish": "Chess Master - Play vs Stockfish",
            "online": "Chess Master - Online Room",
        }[mode]
        headers = {
            "Event": _clean_name(options.get("event_name"), default_event),
            "Site": "Chess Master",
            "Date": today,
            "Round": "1",
            "White": white.name,
            "Black": black.name,
            "Result": "*",
        }
        if start_fen != chess.STARTING_FEN:
            headers["SetUp"] = "1"
            headers["FEN"] = start_fen
        session = cls(
            mode=mode,
            start_fen=start_fen,
            headers=headers,
            players={"white": white, "black": black},
            source="new",
            time_control=options.get("time_control"),
        )
        session._engine_player_color = player_color if mode == "stockfish" else None
        session._engine_bot_color = bot_color
        session._engine_bot_elo = bot_elo
        session._engine_bot_pace = bot_pace
        return session

    @classmethod
    def from_pgn(cls, pgn_text: str) -> GameSession:
        parsed: PgnImport = parse_pgn(pgn_text)
        header_players = players_from_headers(parsed.headers)
        players = {
            "white": _player(
                "white",
                header_players["white"]["name"],
                title=header_players["white"]["title"],
                rating=header_players["white"]["rating"],
            ),
            "black": _player(
                "black",
                header_players["black"]["name"],
                title=header_players["black"]["title"],
                rating=header_players["black"]["rating"],
            ),
        }
        return cls(
            mode="import",
            start_fen=parsed.start_fen,
            moves=parsed.moves,
            headers=parsed.headers,
            players=players,
            source="pgn",
            import_meta=parsed.meta,
            start_comment=parsed.start_comment,
        )

    @classmethod
    def from_fen(
        cls,
        fen_text: str,
        options: Mapping[str, Any] | None = None,
        *,
        source: str = "fen",
    ) -> GameSession:
        root = validate_fen(fen_text)
        if options is None:
            options = {}
        if not isinstance(options, Mapping):
            raise InvalidInputError("Position options must be an object.")

        white = _player("white", _clean_name(options.get("white_name"), "White"))
        black = _player("black", _clean_name(options.get("black_name"), "Black"))
        headers = {
            "Event": "Imported position",
            "Site": "Chess Master",
            "Date": datetime.now(timezone.utc).strftime("%Y.%m.%d"),
            "Round": "-",
            "White": white.name,
            "Black": black.name,
            "Result": "*",
            "SetUp": "1",
            "FEN": root.fen(),
        }
        return cls(
            mode="import",
            start_fen=root.fen(),
            headers=headers,
            players={"white": white, "black": black},
            source=source,
        )

    @classmethod
    def from_archive(
        cls, record: Mapping[str, Any], review: Mapping[str, Any] | None = None
    ) -> GameSession:
        """Rebuild a session from a row written by :meth:`to_archive`."""

        start_fen = str(record.get("start_fen") or chess.STARTING_FEN)
        mode = str(record.get("mode") or "import")
        if mode not in VALID_MODES and mode != "import":
            mode = "import"
        headers = _json_object(record.get("headers_json"))
        time_control: Any = record.get("time_control")
        clock_spec = str(headers.get("X-ClockMs") or "")
        label = str(time_control or "").strip()
        if "+" in clock_spec and label.lower() not in TIME_CONTROL_PRESETS:
            initial, increment = clock_spec.split("+", 1)
            if initial.isdigit() and increment.isdigit():
                time_control = {
                    "initial_ms": int(initial),
                    "increment_ms": int(increment),
                    "label": label or None,
                }
        board = chess.Board(start_fen)
        moves: list[MoveRecord] = []
        items = _json_list(record.get("moves_json"))
        if not items:
            # The archive list omits moves_json; the stored PGN can rebuild it.
            pgn_text = str(record.get("pgn") or "").strip()
            if pgn_text:
                try:
                    items = [move.to_dict() for move in parse_pgn(pgn_text).moves]
                except InvalidPgnError:
                    items = []
        for item in items:
            try:
                move = chess.Move.from_uci(str(item.get("uci") or ""))
            except ValueError:
                break
            if move not in board.legal_moves:
                break
            fen_before = board.fen()
            move_number = board.fullmove_number
            color = "white" if board.turn == chess.WHITE else "black"
            san = str(item.get("san") or board.san(move))
            captured = capture_on_move(board, move)
            board.push(move)
            moves.append(
                MoveRecord(
                    ply=len(moves) + 1,
                    move_number=move_number,
                    color=color,
                    san=san,
                    uci=move.uci(),
                    fen_before=fen_before,
                    fen_after=board.fen(),
                    comment=str(item.get("comment") or ""),
                    captured=item.get("captured") or captured,
                )
            )

        white_name = str(record.get("white_name") or headers.get("White") or "White")
        black_name = str(record.get("black_name") or headers.get("Black") or "Black")
        player_color = record.get("player_color")
        bot_color = (
            _opposite(player_color)
            if mode == "stockfish" and player_color in _PLAYER_COLORS
            else None
        )
        bot_elo = record.get("bot_elo") if mode == "stockfish" else None
        players = {
            color: _player(
                color,
                white_name if color == "white" else black_name,
                rating=bot_elo if bot_color == color else None,
                is_engine=bot_color == color,
                extras=(
                    {"engine_elo": bot_elo, "engine_label": _engine_label(int(bot_elo))}
                    if bot_color == color and bot_elo
                    else {}
                ),
            )
            for color in ("white", "black")
        }
        headers.setdefault("Result", str(record.get("result") or "*"))
        session = cls(
            mode=mode,
            start_fen=start_fen,
            moves=moves,
            headers=headers,
            players=players,
            source=str(record.get("source") or "archive"),
            game_id=str(record.get("game_id") or "") or None,
            created_at=str(record.get("created_at") or "") or None,
            time_control=time_control,
            timeout=_timeout_from_record(record),
        )
        if session._clock is not None:
            # An archived table is finished or paused: never keep ticking it.
            session._clock.running = False
            session._clock.last_tick_monotonic = time.monotonic()
            if session._timeout is not None:
                loser = _opposite(str(session._timeout.get("winner")))
                if loser == "white":
                    session._clock.white_ms = 0
                else:
                    session._clock.black_ms = 0
        session._engine_player_color = player_color if mode == "stockfish" else None
        session._engine_bot_color = bot_color
        session._engine_bot_elo = int(bot_elo) if bot_elo else None
        try:
            pace = _coerce_bot_pace(record.get("bot_pace"))
        except InvalidInputError:
            pace = DEFAULT_BOT_PACE
        session._engine_bot_pace = pace if mode == "stockfish" else DEFAULT_BOT_PACE
        termination = str(record.get("termination_state") or "")
        if termination == "resigned":
            winner = record.get("winner")
            if winner in ("white", "black"):
                session._resigned_by = _opposite(str(winner))
        elif termination == "draw" and str(record.get("termination_reason") or "") == "agreement":
            session._draw_agreed = True
        return session

    # ------------------------------------------------------------------
    # Views and mutation
    # ------------------------------------------------------------------
    @property
    def board(self) -> chess.Board:
        return self._board

    @property
    def game_id(self) -> str:
        return self._game_id

    @property
    def start_fen(self) -> str:
        return self._start_fen

    @property
    def engine_player_color(self) -> str | None:
        return self._engine_player_color

    @property
    def engine_bot_color(self) -> str | None:
        return self._engine_bot_color

    @property
    def engine_bot_elo(self) -> int | None:
        return self._engine_bot_elo

    @property
    def bot_pace(self) -> str:
        return self._engine_bot_pace or DEFAULT_BOT_PACE

    @property
    def turn(self) -> str:
        return "white" if self._board.turn == chess.WHITE else "black"

    @property
    def total_plies(self) -> int:
        return len(self._moves)

    @property
    def view_ply(self) -> int:
        return self._view_ply

    @property
    def is_viewing_live(self) -> bool:
        return self._view_ply == len(self._moves)

    @property
    def clock(self) -> GameClock | None:
        return self._clock

    @property
    def time_control(self) -> str:
        return self._clock.control.label if self._clock is not None else "unlimited"

    @property
    def is_over(self) -> bool:
        return self.status()["state"] != "in_progress"

    def status(self) -> dict[str, Any]:
        """Board status with a clock forfeit taking precedence while running."""

        self._sync_clock()
        if self._timeout is not None:
            return dict(self._timeout)
        status = position_status(
            self._board,
            resigned_by=self._resigned_by,
            draw_agreed=self._draw_agreed,
        )
        status["draw_offer_by"] = (
            self._draw_offer_by if status["state"] == "in_progress" else None
        )
        return status

    def _sync_clock(self) -> None:
        if self._clock is None or self._timeout is not None or not self._clock.running:
            return
        if position_status(self._board)["state"] != "in_progress":
            self._clock.stop()
            return
        self._clock.tick()
        self._check_timeout()

    def _check_timeout(self) -> None:
        if self._clock is None or self._timeout is not None:
            return
        loser = self._clock.flagged_color()
        if loser is None:
            return
        winner = _opposite(loser)
        base = position_status(self._board)
        self._clock.stop()
        self._timeout = {
            "state": "timeout",
            "reason": "time_forfeit",
            "winner": winner,
            "result": "1-0" if winner == "white" else "0-1",
            "check": base["check"],
            "check_square": base["check_square"],
            "claimable_draw": None,
        }
        self._headers["Result"] = self._timeout["result"]
        self.revision += 1

    def _clock_payload(self) -> dict[str, Any]:
        if self._clock is None:
            return {
                "enabled": False,
                "control": None,
                "label": "Unlimited",
                "category": "unlimited",
                "initial_ms": None,
                "increment_ms": 0,
                "white_ms": None,
                "black_ms": None,
                "active_color": None,
                "running": False,
                "flagged": False,
            }
        return self._clock.to_state()

    def move_ucis(self, up_to: int | None = None) -> list[str]:
        """The move stack replayed from the root, as UCI strings."""

        limit = len(self._moves) if up_to is None else max(0, min(up_to, len(self._moves)))
        return [record.uci for record in self._moves[:limit]]

    def navigate_ply(self, ply_index: Any) -> dict[str, Any]:
        index = self._coerce_ply(ply_index)
        self._board = replay_board(self._start_fen, self._moves, index)
        self._view_ply = index
        self.revision += 1
        return self.to_state()

    def make_move(self, move_input: Any) -> dict[str, Any]:
        if not isinstance(move_input, str) or not move_input.strip():
            raise InvalidInputError("Enter a move in UCI or SAN notation.")

        status = self.status()
        if status["state"] != "in_progress":
            raise GameOverError(
                "The game is already over in this position.",
                details={"state": status["state"], "reason": status["reason"]},
            )

        move = self._parse_move(move_input)
        self._draw_offer_by = None
        board = self._board
        fen_before = board.fen()
        color = "white" if board.turn == chess.WHITE else "black"
        move_number = board.fullmove_number
        san = board.san(move)
        captured = capture_on_move(board, move)

        # Branching: playing from an earlier ply discards the old future.
        if self._view_ply < len(self._moves):
            del self._moves[self._view_ply :]

        board.push(move)
        self._moves.append(
            MoveRecord(
                ply=len(self._moves) + 1,
                move_number=move_number,
                color=color,
                san=san,
                uci=move.uci(),
                fen_before=fen_before,
                fen_after=board.fen(),
                captured=captured,
            )
        )
        self._view_ply = len(self._moves)
        self._board = board
        self.revision += 1

        if self._clock is not None and self._clock.running:
            self._clock.apply_move(color)
        status = self.status()
        if status["state"] != "in_progress" and self._clock is not None:
            self._clock.stop()
        self._headers["Result"] = status["result"] if status["state"] != "in_progress" else "*"
        return self.to_state()

    def resign(self, color: str | None = None) -> dict[str, Any]:
        """End the game by resignation; ``None`` resigns for the side on move."""

        status = self.status()
        if status["state"] != "in_progress":
            raise GameOverError(
                "The game is already over in this position.",
                details={"state": status["state"], "reason": status["reason"]},
            )
        self._resigned_by = self._resolve_color(color)
        self._draw_offer_by = None
        if self._clock is not None:
            self._clock.stop()
        status = self.status()
        self._headers["Result"] = status["result"]
        self.revision += 1
        return self.to_state()

    def offer_draw(self, color: str | None = None) -> dict[str, Any]:
        """Record a draw offer; a move or a decline clears it again."""

        status = self.status()
        if status["state"] != "in_progress":
            raise GameOverError(
                "The game is already over in this position.",
                details={"state": status["state"], "reason": status["reason"]},
            )
        self._draw_offer_by = self._resolve_color(color)
        self.revision += 1
        return self.to_state()

    def respond_draw(self, accept: bool, color: str | None = None) -> dict[str, Any]:
        """Accept or decline the pending offer. ``None`` is the offering side's opponent."""

        status = self.status()
        if status["state"] != "in_progress":
            raise GameOverError(
                "The game is already over in this position.",
                details={"state": status["state"], "reason": status["reason"]},
            )
        offer = self._draw_offer_by
        if offer is None:
            raise InvalidInputError("There is no draw offer to answer.")
        responder = self._resolve_color(color) if color is not None else _opposite(offer)
        if responder == offer:
            raise InvalidInputError("You cannot answer your own draw offer.")
        self._draw_offer_by = None
        if accept:
            self._draw_agreed = True
            if self._clock is not None:
                self._clock.stop()
        status = self.status()
        self._headers["Result"] = (
            status["result"] if status["state"] != "in_progress" else "*"
        )
        self.revision += 1
        return self.to_state()

    def rematch_swap(self) -> dict[str, Any]:
        """Start a fresh game on the same table with the colors swapped."""

        self.reset()
        white, black = self._players["white"], self._players["black"]
        self._players = {
            "white": _player("white", black.name),
            "black": _player("black", white.name),
        }
        self._headers["White"] = self._players["white"].name
        self._headers["Black"] = self._players["black"].name
        self._game_id = uuid.uuid4().hex[:12]
        self._created_at = _utc_now()
        return self.to_state()

    def _resolve_color(self, color: str | None) -> str:
        if color is None:
            return "white" if self._board.turn == chess.WHITE else "black"
        text = str(color).strip().lower()
        if text not in ("white", "black"):
            raise InvalidInputError(
                "Player color must be white or black.",
                details={"color": color},
            )
        return text

    def reset(self) -> dict[str, Any]:
        self._board = self._root_board.copy()
        self._moves.clear()
        self._view_ply = 0
        self._headers["Result"] = "*"
        self._import_meta = None
        self._start_comment = ""
        if self._clock is not None:
            self._clock = GameClock(
                self._clock.control,
                active_color="white" if self._root_board.turn == chess.WHITE else "black",
            )
        self._timeout = None
        self._resigned_by = None
        self._draw_agreed = False
        self._draw_offer_by = None
        self.revision += 1
        return self.to_state()

    # ------------------------------------------------------------------
    # Move parsing
    # ------------------------------------------------------------------
    def _parse_move(self, raw: str) -> chess.Move:
        text = raw.strip()
        lowered = text.lower()

        if _UCI_RE.fullmatch(lowered):
            try:
                move = chess.Move.from_uci(lowered)
            except ValueError as exc:
                raise IllegalMoveError("That UCI move is malformed.") from exc

            if move in self._board.legal_moves:
                return move

            if self._needs_promotion(move):
                raise PromotionRequiredError(
                    "Choose a promotion piece for that move.",
                    details={
                        "from": chess.square_name(move.from_square),
                        "to": chess.square_name(move.to_square),
                        "options": list(_PROMOTION_ORDER),
                    },
                )

            raise IllegalMoveError(
                "That move is not legal in this position.",
                details={"input": text, "fen": self._board.fen()},
            )

        san = re.sub(r"[!?]+$", "", text)
        san = san.replace("0-0-0", "O-O-O").replace("0-0", "O-O")
        try:
            return self._board.parse_san(san)
        except ValueError as exc:
            raise IllegalMoveError(
                "That SAN move is not legal in this position.",
                details={"input": text, "fen": self._board.fen()},
            ) from exc

    def _needs_promotion(self, move: chess.Move) -> bool:
        piece = self._board.piece_at(move.from_square)
        if piece is None or piece.piece_type != chess.PAWN:
            return False
        target_rank = chess.square_rank(move.to_square)
        if target_rank not in (0, 7):
            return False
        return any(
            candidate.from_square == move.from_square
            and candidate.to_square == move.to_square
            and candidate.promotion is not None
            for candidate in self._board.legal_moves
        )

    def _coerce_ply(self, value: Any) -> int:
        if isinstance(value, bool):
            raise InvalidPlyError("A ply index must be an integer.")
        if isinstance(value, int):
            index = value
        elif isinstance(value, str) and value.strip().isdigit():
            index = int(value.strip())
        else:
            raise InvalidPlyError("A ply index must be an integer.")
        if index < 0 or index > len(self._moves):
            raise InvalidPlyError(
                "That ply is outside the current game.",
                details={"requested": index, "total_plies": len(self._moves)},
            )
        return index

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------
    def to_state(self) -> dict[str, Any]:
        status = self.status()
        fallback_result = self._headers.get("Result", "*")
        result = result_from_status(status, fallback_result)
        status["result"] = result

        last_move = None
        if self._view_ply > 0:
            record = self._moves[self._view_ply - 1]
            last_move = {
                "ply": record.ply,
                "move_number": record.move_number,
                "color": record.color,
                "san": record.san,
                "uci": record.uci,
                "from": record.from_square,
                "to": record.to_square,
            }

        material = material_summary(self._board, self._moves, self._view_ply)

        return {
            "schema_version": 1,
            "phase": 4,
            "app_version": __version__,
            "game_id": self._game_id,
            "revision": self.revision,
            "created_at": self._created_at,
            "mode": self.mode,
            "mode_label": self._mode_label(),
            "source": self.source,
            "start_fen": self._start_fen,
            "fen": self._board.fen(),
            "headers": dict(self._headers),
            "event": self._headers.get("Event") or self._mode_label(),
            "players": {color: player.to_dict() for color, player in self._players.items()},
            "turn": self.turn,
            "orientation": self._engine_player_color or "white",
            "ply": self._view_ply,
            "total_plies": len(self._moves),
            "move_number": self._board.fullmove_number,
            "is_viewing_live": self._view_ply == len(self._moves),
            "branch_available": self._view_ply < len(self._moves),
            "can_prev": self._view_ply > 0,
            "can_next": self._view_ply < len(self._moves),
            "last_move": last_move,
            "moves": [record.to_dict() for record in self._moves],
            "status": status,
            "clocks": self._clock_payload(),
            "material": material,
            "engine": self._engine_payload(),
            "evaluation": self._evaluation_placeholder(),
            "review": {
                "available": False,
                "status": "idle",
                "signature": None,
                "progress": {"done": 0, "total": 0, "phase": "idle", "percent": 0.0},
                "summary": None,
                "opening": None,
                "error": None,
            },
            "import_meta": dict(self._import_meta) if self._import_meta else None,
        }

    def export_pgn(self) -> dict[str, Any]:
        pgn = build_pgn(
            self._start_fen,
            self._headers,
            self._moves,
            start_comment=self._start_comment,
        )
        return {
            "pgn": pgn,
            "fen": self._board.fen(),
            "headers": dict(self._headers),
            "ply": self._view_ply,
            "total_plies": len(self._moves),
        }

    def to_archive(self) -> dict[str, Any]:
        """One flat row for the local SQLite archive."""

        status = self.status()
        headers = dict(self._headers)
        if self._clock is not None:
            # Keep the raw custom control so it can be rebuilt on load; ``time_control``
            # alone is a human label and may not match a preset.
            headers["X-ClockMs"] = (
                f"{self._clock.control.initial_ms}+{self._clock.control.increment_ms}"
            )
        return {
            "game_id": self._game_id,
            "created_at": self._created_at,
            "updated_at": _utc_now(),
            "mode": self.mode,
            "source": self.source,
            "event": self._headers.get("Event") or self._mode_label(),
            "white_name": self._players["white"].name,
            "black_name": self._players["black"].name,
            "bot_elo": self._engine_bot_elo,
            "bot_pace": self._engine_bot_pace,
            "player_color": self._engine_player_color,
            "time_control": self.time_control,
            "result": result_from_status(status, self._headers.get("Result", "*")),
            "termination_state": status["state"],
            "termination_reason": status["reason"],
            "winner": status["winner"],
            "total_plies": len(self._moves),
            "start_fen": self._start_fen,
            "final_fen": self._board.fen(),
            "pgn": self.export_pgn()["pgn"],
            "moves_json": json.dumps(
                [record.to_dict() for record in self._moves], ensure_ascii=False
            ),
            "headers_json": json.dumps(headers, ensure_ascii=False),
        }

    def _mode_label(self) -> str:
        return {
            "pass_play": "Pass & Play",
            "stockfish": "Play vs Stockfish",
            "online": "Online Room",
            "import": "Imported game",
        }.get(self.mode, self.mode)

    def _engine_payload(self) -> dict[str, Any]:
        enabled = self.mode == "stockfish"
        payload: dict[str, Any] = {
            "name": "Stockfish",
            "enabled": enabled,
            "available": False,
            "status": "unmanaged" if enabled else "disabled",
            "status_label": "Engine available in the desktop app" if enabled else "Engine off",
            "note": (
                "A game manager supplies the live Stockfish worker."
                if enabled
                else "Start a game against Stockfish to use the engine."
            ),
            "player_color": self._engine_player_color,
            "bot_color": self._engine_bot_color,
            "bot_elo": self._engine_bot_elo,
            "bot_pace": self.bot_pace if enabled else None,
            "thinking": False,
            "analysis": {"enabled": False, "arrows": False, "depth": 16, "multipv": 3},
        }
        if enabled and self._engine_bot_elo is not None:
            from chess_master.engine.stockfish import elo_profile

            payload["bot"] = elo_profile(self._engine_bot_elo).to_dict()
        return payload

    def _evaluation_placeholder(self) -> dict[str, Any]:
        return {
            "available": False,
            "enabled": False,
            "arrows": False,
            "thinking": False,
            "depth": 0,
            "multipv": 0,
            "score_cp": None,
            "mate": None,
            "score_text": None,
            "win_prob_white": 50.0,
            "best_move": None,
            "pv": [],
            "pv_san": [],
            "lines": [],
            "cached": False,
            "updated_at": None,
            "token": {
                "game_id": self._game_id,
                "revision": self.revision,
                "ply": self._view_ply,
                "fen": self._board.fen(),
            },
        }
