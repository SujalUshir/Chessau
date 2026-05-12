"""
Chess Engine — Flask Backend
python app.py
"""
import copy, traceback, os, json, datetime, threading, math, random, struct, re, tempfile
from flask import Flask, jsonify, request, render_template, send_from_directory
from flask_limiter import Limiter
from flask_limiter.errors import RateLimitExceeded
from flask_limiter.util import get_remote_address
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge
import engine
import logging
logging.basicConfig(level=logging.WARNING)
log = logging.getLogger(__name__)

# ── Input validation ──────────────────────────────────────────────────────────
# Strict UCI move regex: a-h file, 1-8 rank, optional promotion piece
_UCI_RE = re.compile(r'^[a-h][1-8][a-h][1-8][qrbn]?$')
_SQUARE_RE = re.compile(r'^[a-h][1-8]$')

# ── Engine depth safety ───────────────────────────────────────────────────────
# Hard cap on client-supplied depth to prevent CPU exhaustion attacks.
MAX_ENGINE_DEPTH = 5

# ── Save-file limits ─────────────────────────────────────────────────────────
MAX_SAVE_MOVES    = 500  # max moves per saved game
MAX_RESTORE_MOVES = 300  # max moves replayed from a restore request
MAX_SAVED_GAMES   = 200  # max games kept in saved_games.json
VALID_RESULTS     = {"1-0", "0-1", "1/2-1/2", "?"}

LIMIT_LIGHT    = "60 per minute"
LIMIT_MOVE     = "30 per minute"
LIMIT_ANALYSIS = "15 per minute"

def _clamp_engine_depth(value, default=MAX_ENGINE_DEPTH):
    """Parse and clamp any engine depth value to the server safety cap."""
    try:
        depth = int(value)
    except (TypeError, ValueError):
        depth = default
    return max(1, min(depth, MAX_ENGINE_DEPTH))


def _json_object_payload():
    """Return a JSON object body, empty dict for no body, or None for bad shape."""
    body = request.get_json(force=True, silent=True)
    if body is None:
        return {}
    if not isinstance(body, dict):
        return None
    return body


def _validated_uci_moves(value, limit):
    """Validate a move list and cap its length without trusting client shape."""
    if not isinstance(value, list):
        return None, "moves must be a list"
    capped = value[:limit]
    for move in capped:
        if not isinstance(move, str) or not _UCI_RE.fullmatch(move):
            return None, "invalid move format"
    return capped, None


def _is_promotion_move(piece, to_square):
    return piece.lower() == "p" and to_square[1] in ("1", "8")

# python-chess (for Polyglot book) — optional, graceful fallback if missing
try:
    import chess
    import chess.polyglot
    CHESS_LIB_OK = True
except ImportError:
    CHESS_LIB_OK = False
    log.warning("[book] python-chess not installed — opening book disabled")

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))



def _ensure_save_file():
    path = os.path.join(PROJECT_DIR, "saved_games.json")
    if not os.path.exists(path):
        try:
            with open(path, "w") as f:
                json.dump([], f)
        except OSError:
            pass

_ensure_save_file()

app = Flask(__name__)

# ── Payload size limit: 256 KB hard cap (prevents save-file DOS) ──────────────
app.config['MAX_CONTENT_LENGTH'] = 256 * 1024

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

# ── Opening book (Polyglot .bin) ───────────────────────────────────────────────────
BOOK_PATH = os.path.join(PROJECT_DIR, "books", "gm2001.bin")
BOOK_OK   = False

if CHESS_LIB_OK and os.path.exists(BOOK_PATH):
    try:
        # Probe once to confirm the file is a valid Polyglot book
        with chess.polyglot.open_reader(BOOK_PATH) as _probe:
            BOOK_OK = True
        _book_size_kb = os.path.getsize(BOOK_PATH) // 1024
        log.warning("[book] Loaded %s successfully (%d KB)", os.path.basename(BOOK_PATH), _book_size_kb)
    except Exception as _be:
        log.warning("[book] %s failed to open: %s", os.path.basename(BOOK_PATH), _be)
else:
    log.info("[book] %s not found or chess lib missing — book disabled", os.path.basename(BOOK_PATH))


def _book_move(fen_str):
    """
    Look up fen_str in Titans.bin.  Returns a UCI string (e.g. 'e2e4') or None.
    Uses weighted random selection so games don't repeat identically.
    Silently returns None on any error (file corrupt, position not found, etc).
    """
    if not BOOK_OK:
        return None
    try:
        board = chess.Board(fen_str)
        with chess.polyglot.open_reader(BOOK_PATH) as reader:
            entries = list(reader.find_all(board))
        if not entries:
            return None
        # Weighted random — weight = entry.weight (higher = more commonly played)
        weights = [max(1, e.weight) for e in entries]
        chosen  = random.choices(entries, weights=weights, k=1)[0]
        uci = chosen.move.uci()
        log.info("[book] hit: FEN=%s -> %s (weight=%d, %d candidates)",
                 fen_str[:40], uci, chosen.weight, len(entries))
        return uci
    except Exception as ex:
        log.warning("[book] lookup error: %s", ex)
        return None


# ── ECO opening recognition ───────────────────────────────────────────────────────────
# Loaded once at startup from books/eco/*.tsv (format: eco TAB name TAB pgn)
# _ECO_TABLE is a list of (move_list, eco, name) sorted by move_list length DESC
# so longest-match wins.
_ECO_TABLE = []   # [(moves_tuple, eco_str, name_str), ...]

def _pgn_to_uci_moves(pgn_line):
    """
    Convert a PGN move sequence like '1. e4 e5 2. Nf3' into a list of UCI strings
    using python-chess.  Returns [] on any parse error.
    """
    if not CHESS_LIB_OK:
        return []
    try:
        import chess.pgn, io
        # Build a minimal PGN string — avoid escaped whitespace that triggers SyntaxWarning
        pgn_text = '[Event "?"]\n\n' + pgn_line.strip()
        game = chess.pgn.read_game(io.StringIO(pgn_text))
        if not game:
            return []
        uci_moves = []
        node = game
        while node.variations:
            node = node.variations[0]
            uci_moves.append(node.move.uci())
        return uci_moves
    except Exception:
        return []


def _load_eco_table():
    eco_dir = os.path.join(PROJECT_DIR, "books", "eco")
    if not os.path.isdir(eco_dir):
        log.warning("[ECO] eco directory not found")
        return
    rows = []
    for fname in sorted(os.listdir(eco_dir)):   # sorted for deterministic load order
        if not fname.endswith(".tsv"):
            continue
        fpath = os.path.join(eco_dir, fname)
        loaded = 0
        try:
            with open(fpath, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()          # strips \r\n, \n, and trailing spaces
                    if not line:
                        continue
                    parts = line.split("\t")
                    if len(parts) < 3:
                        continue
                    eco_code, name, pgn = parts[0].strip(), parts[1].strip(), parts[2].strip()
                    if eco_code.lower() == "eco":   # header row
                        continue
                    moves = _pgn_to_uci_moves(pgn)
                    if moves:
                        rows.append((tuple(moves), eco_code, name))
                        loaded += 1
        except Exception as ex:
            log.warning("[ECO] failed to load %s: %s", fname, ex)
        log.info("[ECO] %s → %d openings", fname, loaded)
    # Sort longest-first so longest match wins
    rows.sort(key=lambda r: len(r[0]), reverse=True)
    _ECO_TABLE.extend(rows)
    log.info("[ECO] loaded %d openings", len(_ECO_TABLE))


# Load ECO table at startup (runs once, takes ~2-4s due to PGN parsing)
_load_eco_table()


# In-memory list of game move UCIs for opening tracking (reset with board).
_game_uci_moves: list = []


def _match_opening(uci_moves):
    """
    Return (eco_code, name) for the longest matching ECO opening, or (None, None).
    Compares _game_uci_moves against _ECO_TABLE using longest-prefix match.
    """
    if not _ECO_TABLE or not uci_moves:
        return None, None
    moves_tuple = tuple(uci_moves)
    for entry_moves, eco, name in _ECO_TABLE:   # already sorted longest-first
        n = len(entry_moves)
        if len(moves_tuple) >= n and moves_tuple[:n] == entry_moves:
            return eco, name
    return None, None


# ── Mate sentinel constant ─────────────────────────────────────────────────────
MATE_SCORE = -999.99

# ── Serve sounds from project-root sounds/ folder ─────────────────────────────
@app.route('/sounds/<path:filename>')
def serve_sound(filename):
    sounds_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sounds')
    return send_from_directory(sounds_dir, filename)

# ── Stockfish init ─────────────────────────────────────────────────────────────
# Absolute path — independent of Gunicorn's working directory.
SF_PATH  = os.path.join(PROJECT_DIR, "bin", "stockfish")
# ── Depth config ───────────────────────────────────────────────────────────────
# LIVE depth: used for eval bar during play — must be fast/responsive.
#   depth=4: ~0.02–0.08s, sufficient for live feedback.
# REVIEW depth: used for move review / accuracy, capped for public hosting.
#   depth=5 keeps review responsive and bounds CPU use on Render.
# Override via Render env vars: SF_DEPTH, SF_REVIEW_DEPTH.
# Both are clamped to MAX_ENGINE_DEPTH to keep public requests bounded.
SF_DEPTH        = _clamp_engine_depth(os.environ.get("SF_DEPTH", "4"), default=4)
SF_REVIEW_DEPTH = _clamp_engine_depth(os.environ.get("SF_REVIEW_DEPTH", "5"), default=5)

# ── Single persistent Stockfish process — never respawned after init ───────────
# Using one long-lived process eliminates the ~1-2s startup cost per move.
_sf_instance  = None        # the one and only Stockfish process
_sf_lock      = threading.Lock()
STOCKFISH_OK  = False
STOCKFISH_ERR = ""

# ── Position analysis cache (keyed by (FEN, depth)) ───────────────────────────
# Stores the result of the last analyze_position() call so that multiple
# helpers asking about the same FEN+depth pay the Stockfish cost only once.
# Using (FEN, depth) as key so live and review calls don't collide.
_sf_cache_key    = None   # tuple: (fen_str, depth)
_sf_cache_result = None   # dict: {best_move, eval_cp, eval_pawns, depth}

# ── Opening name cache (keyed by current FEN string) ─────────────────────────
# Avoids re-opening the Polyglot book file on every /opening request.
_opening_cache_fen    = None   # str: FEN when result was cached
_opening_cache_result = None   # dict: {eco, name, in_book}


def _invalidate_sf_cache():
    """Clear the position and opening caches. Call whenever the board position
    changes externally (reset, undo, redo) so stale results are never returned."""
    global _sf_cache_key, _sf_cache_result, _opening_cache_fen, _opening_cache_result
    _sf_cache_key         = None
    _sf_cache_result      = None
    _opening_cache_fen    = None
    _opening_cache_result = None


def _new_sf():
    """
    Create and return a fresh, configured Stockfish instance.
    Called ONCE at startup — thereafter _sf_instance is reused.
    Raises if the binary is missing or Stockfish fails to start.
    """
    from stockfish import Stockfish
    if not os.path.isfile(SF_PATH):
        raise FileNotFoundError(f"Stockfish binary not found: {SF_PATH}")
    sf = Stockfish(path=SF_PATH)
    sf.set_depth(SF_DEPTH)
    # Limit memory use so Render free-tier (512 MB) doesn't OOM-kill the process.
    # Hash=16 → 16 MB transposition table.  Threads=1 → no extra CPU pressure.
    sf.update_engine_parameters({"Hash": 16, "Threads": 1})
    return sf


def _init_stockfish():
    global _sf_instance, STOCKFISH_OK, STOCKFISH_ERR
    try:
        sf = _new_sf()
        sf.set_fen_position("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")
        move = sf.get_best_move()
        if move:
            # Keep the fully-warmed-up instance alive for all future calls.
            _sf_instance = sf
            STOCKFISH_OK = True
            log.info("[Stockfish] Ready — path=%s depth=%d test_move=%s", SF_PATH, SF_DEPTH, move)
        else:
            raise RuntimeError("Stockfish returned no move on start position")
    except Exception as e:
        STOCKFISH_ERR = str(e)
        log.warning("[Stockfish] Init failed: %s", e)
        log.warning("[Stockfish] Stockfish features will be disabled.")

# ── Undo / Redo stacks ────────────────────────────────────────────────────────
# Each entry is a tuple: (board_snap, move_history_snapshot)
# board_snap          — dict returned by _snap()
# move_history_snapshot — deep copy of _move_history at that point
_undo_stack = []
_redo_stack = []

# Hard cap on undo depth — each entry is a deep copy of full board state.
# 100 plies covers any realistic game while bounding RAM usage on Render free-tier.
MAX_UNDO_DEPTH = 100

def _push_undo(snap):
    """Push a snapshot onto the undo stack, enforcing the depth cap."""
    _undo_stack.append(snap)
    if len(_undo_stack) > MAX_UNDO_DEPTH:
        _undo_stack.pop(0)  # drop the oldest entry

# ── Move history ──────────────────────────────────────────────────────────────
# Each entry stores everything needed for move review:
#   move_number  : int   — half-move index (1-based)
#   played       : str   — UCI string, e.g. "e2e4"
#   snap         : dict  — board state BEFORE the move
#   eval_before  : int|None  — Stockfish centipawn BEFORE move (white-positive)
#   eval_after   : int|None  — Stockfish centipawn AFTER move  (white-positive)
#   best_move    : str|None  — Stockfish's best move UCI in pre-move position
#   best_eval    : int|None  — Stockfish eval AFTER the best move (white-positive)
#   moving_color : str   — 'white' | 'black'
_move_history = []

# ── Fullmove counter (increments after Black's move, resets on new game) ──────
_fullmove_counter = 1

_init_stockfish()

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _snap():
    return {
        "board":              copy.deepcopy(engine.board),
        "current_turn":       engine.current_turn,
        "en_passant_target":  engine.en_passant_target,
        "halfmove_clock":     engine.halfmove_clock,
        "white_king_moved":   engine.white_king_moved,
        "black_king_moved":   engine.black_king_moved,
        "white_rook_a_moved": engine.white_rook_a_moved,
        "white_rook_h_moved": engine.white_rook_h_moved,
        "black_rook_a_moved": engine.black_rook_a_moved,
        "black_rook_h_moved": engine.black_rook_h_moved,
        # position_history is included so undo/redo correctly reverts repetition counts.
        "position_history":   dict(engine.position_history),
        # Opening tracker and fullmove counter — needed for correct FEN and opening card after undo/redo.
        "game_uci_moves":     list(_game_uci_moves),
        "fullmove_counter":   _fullmove_counter,
    }

def _restore(s):
    global _fullmove_counter
    engine.board[:]            = s["board"]
    engine.current_turn        = s["current_turn"]
    engine.en_passant_target   = s["en_passant_target"]
    engine.halfmove_clock      = s["halfmove_clock"]
    engine.white_king_moved    = s["white_king_moved"]
    engine.black_king_moved    = s["black_king_moved"]
    engine.white_rook_a_moved  = s["white_rook_a_moved"]
    engine.white_rook_h_moved  = s["white_rook_h_moved"]
    engine.black_rook_a_moved  = s["black_rook_a_moved"]
    engine.black_rook_h_moved  = s["black_rook_h_moved"]
    # Restore repetition history so undo/redo reverts draw-detection state.
    if "position_history" in s:
        engine.position_history.clear()
        engine.position_history.update(s["position_history"])
    # Restore opening tracker so the opening card is correct after undo/redo.
    if "game_uci_moves" in s:
        _game_uci_moves.clear()
        _game_uci_moves.extend(s["game_uci_moves"])
    # Restore fullmove counter so FEN export is accurate after undo/redo.
    if "fullmove_counter" in s:
        _fullmove_counter = s["fullmove_counter"]


def _snap_full():
    """Snapshot both board state and move history for full undo/redo."""
    return (_snap(), copy.deepcopy(_move_history))


def _record_real_move_position(move_uci):
    """
    Record the current board position in position_history after a REAL game move.
    Called ONLY from the three move route handlers (human/engine/stockfish),
    NEVER from analysis helpers, temp-board computations, or undo/redo.

    position_history maps Zobrist hash → occurrence count.
    Threefold repetition triggers when any count reaches 3.
    """
    key   = engine.hash_board(engine.board, engine.current_turn)
    count = engine.position_history.get(key, 0) + 1
    engine.position_history[key] = count
    log.debug("REAL MOVE: %s", move_uci)
    log.debug("HASH: %#018x", key)
    log.debug("COUNT: %s", count)
    if count >= 2:
        # Warn on repeated positions so duplicate-insertion bugs surface quickly.
        log.debug("[rep] position seen %sx - %s", count, "DRAW SOON" if count == 2 else "DRAW NOW")


def _restore_full(entry):
    """Restore board state and move history from a full snapshot."""
    board_snap, history_snap = entry
    _restore(board_snap)
    _move_history.clear()
    _move_history.extend(history_snap)

def _game_status():
    turn = engine.current_turn
    if engine.is_checkmate(engine.board, turn):
        return {"status": "checkmate", "winner": "black" if turn == "white" else "white"}
    if engine.is_stalemate(engine.board, turn):
        return {"status": "stalemate", "winner": None}
    if engine.halfmove_clock >= 100:
        return {"status": "draw_50_move", "winner": None}
    if _is_insufficient_material():
        return {"status": "draw_material", "winner": None}
    if any(v >= 3 for v in engine.position_history.values()):
        return {"status": "draw_repetition", "winner": None}
    if engine.is_king_in_check(engine.board, turn):
        return {"status": "check", "winner": None}
    return {"status": "ongoing", "winner": None}

def _is_insufficient_material():
    pieces = {}
    for row in engine.board:
        for cell in row:
            if cell != '.':
                pieces[cell] = pieces.get(cell, 0) + 1
    white = {k: v for k, v in pieces.items() if k.isupper() and k != 'K'}
    black = {k: v for k, v in pieces.items() if k.islower() and k != 'k'}
    def only_minor(d):
        total = sum(d.values())
        if total == 0: return True
        if total == 1 and ('N' in d or 'B' in d or 'n' in d or 'b' in d):
            return True
        return False
    return only_minor(white) and only_minor(black)

# Piece values for material counting (centipawns)
_PIECE_VALUES = {
    'P': 100, 'N': 320, 'B': 330, 'R': 500, 'Q': 900,
    'p': 100, 'n': 320, 'b': 330, 'r': 500, 'q': 900,
}

def _material_score(board, color):
    """
    Total material value (centipawns) for the given color on the given board.
    color: 'white' (uppercase pieces) or 'black' (lowercase pieces)
    """
    total = 0
    for row in board:
        for cell in row:
            if cell == '.':
                continue
            if color == 'white' and cell.isupper() and cell != 'K':
                total += _PIECE_VALUES.get(cell, 0)
            elif color == 'black' and cell.islower() and cell != 'k':
                total += _PIECE_VALUES.get(cell.upper(), 0)
    return total

def _engine_eval():
    try:
        return engine.evaluate_board(engine.board)
    except Exception:
        return 0

def _fen():
    rows = []
    for row in engine.board:
        e, s = 0, ""
        for cell in row:
            if cell == ".":
                e += 1
            else:
                if e: s += str(e); e = 0
                s += cell
        if e: s += str(e)
        rows.append(s)
    t  = "w" if engine.current_turn == "white" else "b"
    ca = ""
    if not engine.white_king_moved:
        if not engine.white_rook_h_moved: ca += "K"
        if not engine.white_rook_a_moved: ca += "Q"
    if not engine.black_king_moved:
        if not engine.black_rook_h_moved: ca += "k"
        if not engine.black_rook_a_moved: ca += "q"
    ca = ca or "-"
    ep = engine.index_to_notation(*engine.en_passant_target) \
         if engine.en_passant_target else "-"
    return f"{'/'.join(rows)} {t} {ca} {ep} {engine.halfmove_clock} {_fullmove_counter}"

def _sf_is_healthy():
    """
    Quick health-check: asks Stockfish to evaluate the start position.
    Returns True if it responds with a valid move, False otherwise.
    Used to detect silent process crashes (OOM, timeout, etc.).
    """
    try:
        _sf_instance.set_fen_position(
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        )
        mv = _sf_instance.get_best_move()
        return bool(mv)
    except Exception:
        return False


def _sf_respawn():
    """
    Respawn the Stockfish process after a detected crash.
    Updates _sf_instance in-place so all callers pick it up automatically.
    """
    global _sf_instance, STOCKFISH_OK, STOCKFISH_ERR
    log.warning("[SF] Respawning Stockfish process…")
    try:
        sf = _new_sf()
        sf.set_fen_position(
            "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        )
        if sf.get_best_move():
            _sf_instance = sf
            STOCKFISH_OK = True
            _invalidate_sf_cache()
            log.warning("[SF] Respawn succeeded.")
        else:
            raise RuntimeError("Stockfish returned no move after respawn")
    except Exception as e:
        STOCKFISH_ERR = str(e)
        STOCKFISH_OK = False
        _sf_instance = None
        log.error("[SF] Respawn FAILED: %s — Stockfish disabled.", e)


def _sf_validate_fen(fen_str):
    """
    Basic FEN sanity check before sending to Stockfish.
    Returns True if the FEN looks structurally valid.
    """
    if not fen_str or not isinstance(fen_str, str):
        return False
    parts = fen_str.strip().split()
    if len(parts) < 2:
        return False
    rows = parts[0].split('/')
    if len(rows) != 8:
        return False
    if parts[1] not in ('w', 'b'):
        return False
    return True


def analyze_position(fen_str, depth=None):
    """
    THE single Stockfish entry point.
    Returns: { best_move, eval_cp, eval_pawns, depth }
    All fields default to None if SF unavailable or position terminal.

    depth: analysis depth. Defaults to SF_DEPTH (live/fast).
           Pass SF_REVIEW_DEPTH for move review (slower, higher quality).

    Cache is keyed by (FEN, depth) so live and review results don't collide.
    """
    global _sf_cache_key, _sf_cache_result

    if depth is None:
        depth = SF_DEPTH

    if not STOCKFISH_OK or _sf_instance is None:
        return {"best_move": None, "eval_cp": None, "eval_pawns": None}

    # ── FEN validation ─────────────────────────────────────────────────────────
    if not _sf_validate_fen(fen_str):
        log.error("[SF] Invalid FEN rejected: %r", fen_str[:60])
        return {"best_move": None, "eval_cp": None, "eval_pawns": None}

    # ── Cache hit (keyed by FEN + depth) ───────────────────────────────────────
    cache_key = (fen_str, depth)
    if (cache_key == _sf_cache_key
            and _sf_cache_result is not None
            and any(v is not None for v in _sf_cache_result.values())):
        log.info("[SF] cache hit (depth=%d) — FEN: %s", depth, fen_str[:50])
        return _sf_cache_result

    log.info("[SF] query (depth=%d) — FEN: %s", depth, fen_str[:50])
    result = {"best_move": None, "eval_cp": None, "eval_pawns": None, "depth": depth}

    try:
        with _sf_lock:
            # ── Process health check (catches silent crashes) ──────────────────
            # Only run if the previous result was bad, to avoid overhead.
            if _sf_cache_result is not None and not any(
                    v is not None for v in _sf_cache_result.values()):
                log.warning("[SF] previous result was all-None — running health check")
                if not _sf_is_healthy():
                    _sf_respawn()
                    if not STOCKFISH_OK:
                        return result   # respawn failed — give up

            # ── Set depth per call — live vs review use different depths ──
            _sf_instance.set_depth(depth)
            _sf_instance.set_fen_position(fen_str)

            # ── get_best_move() + get_evaluation() — stateless, version-stable ─
            # Replaced get_top_moves(1) which sets persistent MultiPV UCI state
            # on the session and returns [] inconsistently on some SF builds.
            # Both calls happen under the SAME lock so position cannot be mutated
            # between them.
            best_uci    = _sf_instance.get_best_move()
            eval_result = _sf_instance.get_evaluation()   # {"type":"cp"/"mate","value":int}

            log.info("[SF] best_move=%s  eval=%s", best_uci, eval_result)

            # ── Parse eval ────────────────────────────────────────────────────
            cp   = None

            if eval_result:
                etype = eval_result.get("type")
                val   = eval_result.get("value")
                # get_evaluation() already returns WHITE-positive (library normalises
                # internally: compare = -1 when black to move). No flip needed here.
                if etype == "mate" and val is not None:
                    cp = 99999 if val > 0 else -99999
                elif etype == "cp" and val is not None:
                    cp = val   # already white-positive

            if best_uci and len(best_uci) >= 4:
                result["best_move"] = best_uci[:4]
            result["eval_cp"]    = cp
            result["eval_pawns"] = round(cp / 100.0, 2) if cp is not None else None
            result["depth"]      = depth

    except Exception as ex:
        log.error("[SF] analyze_position EXCEPTION: %s", ex, exc_info=True)
        # If the Stockfish process crashed, respawn immediately and retry once.
        if "crashed" in str(ex).lower() or "process" in str(ex).lower():
            log.warning("[SF] Process crash detected — attempting respawn…")
            _sf_respawn()
            if STOCKFISH_OK and _sf_instance is not None:
                try:
                    with _sf_lock:
                        _sf_instance.set_depth(depth)
                        _sf_instance.set_fen_position(fen_str)
                        best_uci    = _sf_instance.get_best_move()
                        eval_result = _sf_instance.get_evaluation()
                    if best_uci and len(best_uci) >= 4:
                        result["best_move"] = best_uci[:4]
                    if eval_result:
                        etype = eval_result.get("type")
                        val   = eval_result.get("value")
                        # Same logic as primary path — already white-positive, no flip.
                        if etype == "mate" and val is not None:
                            result["eval_cp"] = 99999 if val > 0 else -99999
                        elif etype == "cp" and val is not None:
                            result["eval_cp"] = val  # already white-positive
                        if result["eval_cp"] is not None:
                            result["eval_pawns"] = round(result["eval_cp"] / 100.0, 2)
                        result["depth"] = depth
                    log.warning("[SF] Respawn retry result: %s", result)
                except Exception as ex2:
                    log.error("[SF] Retry after respawn also failed: %s", ex2)
        return result

    # ── Only cache if we got something useful ─────────────────────────────────
    if any(v is not None for v in result.values()):
        _sf_cache_key    = cache_key
        _sf_cache_result = result
    else:
        log.warning("[SF] all-None result for FEN: %s — NOT caching", fen_str[:50])

    return result


# ── Thin compatibility wrappers ───────────────────────────────────────────────
# _sf_eval_white_pov has been REMOVED. All eval is routed through analyze_position.

def _sf_eval_at_fen(fen_str):
    """White-positive centipawn eval via analyze_position cache."""
    return analyze_position(fen_str)["eval_cp"]


def _sf_eval():
    """Stockfish eval (centipawns, white-positive) for current board."""
    return _sf_eval_at_fen(_fen())


def _sf_best_move_and_eval(fen_str):
    """
    Returns (best_move_uci, eval_cp).
    Single analyze_position() call — cache means second callers are free.
    """
    r = analyze_position(fen_str)
    return r["best_move"], r["eval_cp"]


def _sf_best_move_from_fen(fen_str):
    """Best move UCI for a given FEN. Uses analyze_position cache."""
    return analyze_position(fen_str)["best_move"]


# ── MultiPV analysis (review only) ─────────────────────────────────────────────
# Used ONLY in _build_move_review_entry to get top 3 moves.
# The played move is compared against ALL top moves, not just #1.
# This prevents practical human moves from being harshly penalised.
SF_MULTIPV = 3

def _analyze_multipv(fen_str, depth=None, num_moves=None):
    """
    Get top N moves with evals for a position.  Used for review only.
    Returns list of dicts: [{"move": "e2e4", "eval_cp": 30}, ...]
    All evals are white-positive.  Falls back to single-move analysis on error.
    """
    if depth is None:
        depth = SF_REVIEW_DEPTH
    if num_moves is None:
        num_moves = SF_MULTIPV
    if not STOCKFISH_OK or _sf_instance is None:
        return []
    if not _sf_validate_fen(fen_str):
        return []

    try:
        with _sf_lock:
            _sf_instance.set_depth(depth)
            _sf_instance.set_fen_position(fen_str)
            top = _sf_instance.get_top_moves(num_moves)

        if not top:
            # Fallback: single move analysis
            r = analyze_position(fen_str, depth=depth)
            if r["best_move"]:
                return [{"move": r["best_move"], "eval_cp": r["eval_cp"]}]
            return []

        result = []
        for entry in top:
            uci = entry.get("Move", "")
            if not uci or len(uci) < 4:
                continue
            # get_top_moves already returns white-positive values
            # (library applies perspective internally, same as get_evaluation).
            mate = entry.get("Mate")
            centipawn = entry.get("Centipawn")
            if mate is not None:
                cp = 99999 if mate > 0 else -99999
            elif centipawn is not None:
                cp = centipawn   # already white-positive
            else:
                cp = None
            result.append({"move": uci[:4], "eval_cp": cp})

        log.info("[SF] MultiPV(%d, depth=%d): %s", num_moves, depth, result)
        return result

    except Exception as ex:
        log.warning("[SF] _analyze_multipv error: %s", ex)
        return []


# ── Mate score cap for classification only ─────────────────────────────────────
# The eval bar uses the raw ±99999 to pin the bar to the edge.  But for move
# classification, a 99999cp delta is meaningless — cap at ±1500 so cp_loss
# stays in a realistic range (mirrors chess.com behaviour).
_CLASSIFY_MATE_CAP = 1500

def _classify_move(eval_before, best_eval, eval_after, moving_color,
                   sacrificed_material=0, multipv_evals=None):
    """
    Classify a move using cp_loss from the moving player's perspective.

    eval_before         : SF eval BEFORE the move (white-positive centipawns)
    best_eval           : SF eval AFTER the best move (white-positive centipawns)
    eval_after          : SF eval AFTER the played move (white-positive centipawns)
    moving_color        : 'white' | 'black'
    sacrificed_material : centipawns of own material lost in this move (>0 = sacrifice)
    multipv_evals       : list of white-positive evals from top-N moves (optional)
                          If provided, cp_loss is computed against the CLOSEST top move
                          rather than just the #1 move.  This gives leniency to
                          practical human moves that are near any strong alternative.

    Classification thresholds (cp_loss from mover's perspective):
      Brilliant  — sacrificed own material AND cp_loss <= 30
      Best       — 0–20 cp
      Excellent  — 20–50 cp
      Good       — 50–100 cp
      Inaccuracy — 100–300 cp
      Mistake    — 300–700 cp
      Blunder    — 700+ cp
    """
    if best_eval is None or eval_after is None:
        return None

    # Cap mate scores so cp_loss doesn't explode to 99699.
    ae = max(-_CLASSIFY_MATE_CAP, min(_CLASSIFY_MATE_CAP, eval_after))

    # ── MultiPV leniency: find the MINIMUM cp_loss across all top moves ───────
    # If the played move is close to ANY strong alternative (not just #1),
    # use that smaller loss.  This prevents "Inaccuracy" for moves that are
    # only slightly worse than the 2nd or 3rd best continuation.
    candidates = [max(-_CLASSIFY_MATE_CAP, min(_CLASSIFY_MATE_CAP, best_eval))]
    if multipv_evals:
        for e in multipv_evals:
            if e is not None:
                candidates.append(max(-_CLASSIFY_MATE_CAP, min(_CLASSIFY_MATE_CAP, e)))

    if moving_color == 'white':
        # White wants eval HIGH → loss = best_possible - what_happened
        delta = min(max(0, be - ae) for be in candidates)
    else:
        # Black wants eval LOW  → loss = what_happened - best_possible
        delta = min(max(0, ae - be) for be in candidates)

    # ── Brilliant: sacrificed own material AND very close to best move ──
    if sacrificed_material > 0 and delta <= 30:
        return "Brilliant"

    if delta <= 20:   return "Best"
    if delta <= 50:   return "Excellent"
    if delta <= 100:  return "Good"
    if delta <= 300:  return "Inaccuracy"
    if delta <= 700:  return "Mistake"
    return "Blunder"

def _is_book_move(move_number, eval_before):
    """
    Heuristic: first 10 half-moves with near-equal eval (±30 cp) are book moves.
    """
    if eval_before is None:
        return False
    return move_number <= 10 and abs(eval_before) <= 30

def _payload(with_sf=False, precomputed_fen=None):
    """
    Build the standard board payload.
    If precomputed_fen is supplied and matches the current board FEN,
    the analyze_position cache will be hit (free) instead of spawning SF.
    """
    eng_eval = _engine_eval()
    if with_sf:
        # Use current board FEN — hits cache if analyze_position was already
        # called for this position (e.g. inside _build_move_review_entry).
        sf_eval = analyze_position(_fen())["eval_cp"]
    else:
        sf_eval = None
    return {
        "board":          engine.board,
        "current_turn":   engine.current_turn,
        "eval_engine":    eng_eval,
        "eval_sf":        sf_eval,
        "can_undo":       len(_undo_stack) > 0,
        "can_redo":       len(_redo_stack) > 0,
        "stockfish_ok":   STOCKFISH_OK,
        **_game_status(),
    }

def _reset_globals():
    global _fullmove_counter
    engine.board[:] = [
        ["r","n","b","q","k","b","n","r"],
        ["p","p","p","p","p","p","p","p"],
        [".",".",".",".",".",".",".","."],
        [".",".",".",".",".",".",".","."],
        [".",".",".",".",".",".",".","."],
        [".",".",".",".",".",".",".","."],
        ["P","P","P","P","P","P","P","P"],
        ["R","N","B","Q","K","B","N","R"],
    ]
    engine.current_turn        = "white"
    engine.white_king_moved    = False
    engine.black_king_moved    = False
    engine.white_rook_a_moved  = False
    engine.white_rook_h_moved  = False
    engine.black_rook_a_moved  = False
    engine.black_rook_h_moved  = False
    engine.en_passant_target   = None
    engine.halfmove_clock      = 0
    engine.position_history.clear()
    engine.transposition_table.clear()
    engine.history_heuristic.clear()
    engine.principal_variation_move = None
    engine.killer_moves = [[None, None] for _ in range(50)]
    engine.position_history[engine.hash_board(engine.board, engine.current_turn)] = 1
    _fullmove_counter = 1
    _game_uci_moves.clear()   # reset opening tracker

def _best_move_from_snap(s):
    """Temporarily restore snap, run engine search, restore back."""
    saved = _snap()
    _restore(s)
    try:
        moves = engine.generate_all_legal_moves(engine.board, engine.current_turn)
        if not moves:
            return None
        result = engine.iterative_deepening(
            engine.board, _clamp_engine_depth(engine.ENGINE_DEPTH, default=engine.ENGINE_DEPTH)
        )
        if not result or not result[0]:
            return None
        best, _ = result
        return best[0] + best[1]
    except Exception as ex:
        log.error(f"[best_move_from_snap error] {ex}")
        return None
    finally:
        _restore(saved)

def _build_move_review_entry(pre_snap, played_uci, move_number, is_book_move=False):
    """
    Compute all review fields for one move.

    Stockfish cost (exact):
      Call 1 — analyze_position(fen_before)  → eval_before + best_move
      Call 2 — analyze_position(fen_best)    → best_eval   (cache miss, new FEN)
      Call 3 — analyze_position(fen_after)   → eval_after  (cache miss, new FEN)
    All three results are cached, so subsequent callers (_payload) pay nothing.
    Fully restores global state after computation.
    """
    saved = _snap()
    try:
        _restore(pre_snap)
        fen_before   = _fen()
        moving_color = engine.current_turn

        # ── Step 1: MultiPV analysis at REVIEW depth ─────────────────────────
        top_moves = _analyze_multipv(fen_before, depth=SF_REVIEW_DEPTH)
        if top_moves:
            eval_before = top_moves[0]["eval_cp"]
            best_uci    = top_moves[0]["move"]
            multipv_evals = [m["eval_cp"] for m in top_moves if m.get("eval_cp") is not None]
        else:
            # Fallback to single-move analysis
            before_analysis = analyze_position(fen_before, depth=SF_REVIEW_DEPTH)
            eval_before = before_analysis["eval_cp"]
            best_uci    = before_analysis["best_move"]
            multipv_evals = []

        # ── Step 2: eval_best — apply best_uci on temp board, analyze fen_best ─
        best_eval = None
        if best_uci and len(best_uci) >= 4:
            _best_snap = _snap()   # board == pre_snap at this point
            try:
                engine.move_piece_notation(
                    engine.board, best_uci[:2], best_uci[2:4]
                )
                fen_best  = _fen()
                best_eval = analyze_position(fen_best, depth=SF_REVIEW_DEPTH)["eval_cp"]
            except Exception as _bex:
                log.warning("[build_review] fen_best eval failed: %s", _bex)
            finally:
                _restore(_best_snap)   # board back to pre_snap

        if best_eval is None:
            best_eval = eval_before   # terminal or SF error — graceful fallback

        # ── Step 3: eval_after — apply played_uci on board, analyze fen_after ─
        # Board is currently at pre_snap (restored above); apply played_uci.
        own_material_before = _material_score(engine.board, moving_color)

        temp_board = copy.deepcopy(engine.board)
        temp_fr, temp_fc = engine.notation_to_index(played_uci[:2])
        temp_tr, temp_tc = engine.notation_to_index(played_uci[2:4])
        _p = temp_board[temp_fr][temp_fc]
        temp_board[temp_tr][temp_tc] = _p
        temp_board[temp_fr][temp_fc] = "."
        own_material_after  = _material_score(temp_board, moving_color)
        sacrificed_material = max(0, own_material_before - own_material_after)

        engine.move_piece_notation(engine.board, played_uci[:2], played_uci[2:4])
        fen_after  = _fen()
        eval_after = analyze_position(fen_after, depth=SF_REVIEW_DEPTH)["eval_cp"]

        if eval_after is None:
            eval_after = eval_before   # SF error — fallback

        # ── Step 4: classify with MultiPV leniency ────────────────────────
        classification = _classify_move(
            eval_before, best_eval, eval_after,
            moving_color, sacrificed_material,
            multipv_evals=multipv_evals
        )

        # Only mark as "Book" when the move was actually sourced from the Polyglot
        # opening book (is_book_move=True passed by the engine/SF route).  The old
        # heuristic (move_number<=10 AND eval<=30) incorrectly labelled early
        # inaccuracies as Book moves.
        if is_book_move and classification not in ("Brilliant", "Best"):
            classification = "Book"

        # ── Debug output ──────────────────────────────────────────────────────
        if moving_color == "white":
            cp_loss_debug = max(0, (best_eval or 0) - (eval_after or 0))
        else:
            cp_loss_debug = max(0, (eval_after or 0) - (best_eval or 0))

        log.debug(
            "[review] move=%s mover=%s multipv=%d eval_before=%s eval_best=%s "
            "eval_after=%s multipv_evals=%s cp_loss=%s classification=%s",
            played_uci, moving_color, len(top_moves), eval_before, best_eval,
            eval_after, multipv_evals, cp_loss_debug, classification
        )

        return {
            "move_number":         move_number,
            "played":              played_uci,
            "best":                best_uci,
            "eval_before":         round(eval_before / 100, 2) if eval_before is not None else None,
            "eval_after":          round(eval_after  / 100, 2) if eval_after  is not None else None,
            "best_eval":           round(best_eval   / 100, 2) if best_eval   is not None else None,
            "classification":      classification,
            "moving_color":        moving_color,
            "sacrificed_material": sacrificed_material,
            "eval_before_cp":      eval_before,
            "eval_after_cp":       eval_after,
            "best_eval_cp":        best_eval,
            # Stash fen_after so _payload can reuse the cached analysis
            "_fen_after":          fen_after,
        }
    except Exception as ex:
        log.error("[build_move_review_entry error] %s", ex)
        # NOTE: moving_color is captured at function entry (before any board mutation)
        # and stored in a local variable, so it is always the correct value here.
        return {
            "move_number":         move_number,
            "played":              played_uci,
            "best":                None,
            "eval_before":         None,
            "eval_after":          None,
            "best_eval":           None,
            "classification":      None,
            "moving_color":        moving_color,
            "sacrificed_material": 0,
            "eval_before_cp":      None,
            "eval_after_cp":       None,
            "best_eval_cp":        None,
            "_fen_after":          None,
        }
    finally:
        _restore(saved)

# ─────────────────────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/")
@limiter.limit(LIMIT_LIGHT)
def home():
    return render_template("index.html")

@app.get("/state")
@limiter.limit(LIMIT_LIGHT)
def get_state():
    return jsonify(_payload(with_sf=False))

@app.get("/eval")
@limiter.limit(LIMIT_ANALYSIS)
def get_eval():
    """Dedicated eval endpoint — called after undo/redo to refresh both bars."""
    return jsonify({
        "eval_engine": _engine_eval(),
        "eval_sf":     _sf_eval(),
    })

@app.get("/moves")
@limiter.limit(LIMIT_LIGHT)
def legal_moves():
    sq    = request.args.get("square")
    moves = engine.generate_all_legal_moves(engine.board, engine.current_turn)
    if sq:
        moves = [m for m in moves if m[0] == sq]
    return jsonify({
        "turn":  engine.current_turn,
        "moves": [{"from": f, "to": t} for f, t in moves],
    })

@app.get("/fen")
@limiter.limit(LIMIT_LIGHT)
def get_fen():
    return jsonify({"fen": _fen()})

@app.get("/best_move")
@limiter.limit(LIMIT_ANALYSIS)
def best_move_hint():
    """Return best move for current position (board highlight feature)."""
    try:
        moves = engine.generate_all_legal_moves(engine.board, engine.current_turn)
        if not moves:
            return jsonify({"error": "no legal moves"}), 400
        result = engine.iterative_deepening(
            engine.board, _clamp_engine_depth(engine.ENGINE_DEPTH, default=engine.ENGINE_DEPTH)
        )
        if not result:
            return jsonify({"error": "no move found"}), 500
        best, score = result
        return jsonify({"from": best[0], "to": best[1], "score": score})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500

# ── Best Move Display endpoints ───────────────────────────────────────────────

@app.get("/bestmove/current")
@limiter.limit(LIMIT_ANALYSIS)
def bestmove_current():
    """Return best move for current position from both engines."""
    try:
        moves = engine.generate_all_legal_moves(engine.board, engine.current_turn)
        if not moves:
            return jsonify({"engine": None, "stockfish": None})

        eng_best = None
        try:
            result = engine.iterative_deepening(
                engine.board, _clamp_engine_depth(engine.ENGINE_DEPTH, default=engine.ENGINE_DEPTH)
            )
            if result and result[0]:
                eng_best = result[0][0] + result[0][1]
        except Exception as ex:
            log.error(f"[bestmove/current engine error] {ex}")

        sf_best = _sf_best_move_from_fen(_fen())
        return jsonify({"engine": eng_best, "stockfish": sf_best})
    except Exception as ex:
        log.error(traceback.format_exc())
        return jsonify({"error": str(ex)}), 500

@app.get("/bestmove/played")
@limiter.limit(LIMIT_ANALYSIS)
def bestmove_played():
    """Return best move for the position BEFORE the last played move."""
    try:
        if not _move_history:
            return jsonify({
                "played": None, "best_engine": None,
                "best_sf": None, "move_number": 0
            })
        last    = _move_history[-1]
        snap    = last["snap"]
        played  = last["played"]
        move_no = last["move_number"]

        best_eng = _best_move_from_snap(snap)
        saved = _snap()
        _restore(snap)
        fen_before = _fen()
        _restore(saved)
        best_sf = _sf_best_move_from_fen(fen_before)

        return jsonify({
            "played":      played,
            "best_engine": best_eng,
            "best_sf":     best_sf,
            "move_number": move_no,
        })
    except Exception as ex:
        log.error(traceback.format_exc())
        return jsonify({"error": str(ex)}), 500

# ── Move review endpoint ──────────────────────────────────────────────────────

@app.get("/review")
@limiter.limit(LIMIT_ANALYSIS)
def get_review():
    """
    Return full move review for all played moves (or last N if ?n= given).
    Each entry:
    {
      "move_number":    1,
      "played":        "e2e4",
      "best":          "d2d4",
      "eval_before":    0.10,   # in pawns, white-positive
      "eval_after":    -0.15,
      "best_eval":      0.10,
      "classification": "Excellent",
      "moving_color":  "white"
    }
    Uses stored review data collected during the game (fast, no replay).
    Falls back to on-demand computation if data missing.
    """
    try:
        n_param = request.args.get("n")
        if n_param:
            try:
                n = int(n_param)
            except (TypeError, ValueError):
                return jsonify({"error": "n must be an integer"}), 400
            n = max(1, min(n, MAX_SAVE_MOVES))
            entries = _move_history[-n:]
        else:
            entries = _move_history

        results = []
        for entry in entries:
            results.append({
                "move_number":    entry["move_number"],
                "played":         entry["played"],
                "best":           entry.get("best"),
                "eval_before":    entry.get("eval_before"),
                "eval_after":     entry.get("eval_after"),
                "best_eval":      entry.get("best_eval"),
                "classification": entry.get("classification"),
                "moving_color":   entry.get("moving_color", "white"),
            })
        return jsonify(results)
    except Exception as ex:
        log.error(traceback.format_exc())
        return jsonify({"error": str(ex)}), 500

# ─────────────────────────────────────────────────────────────────────────────
# MOVE ENDPOINTS
# Each move endpoint now:
#  1. Records pre-move snapshot
#  2. Computes full review data (eval_before, best_move, best_eval, eval_after, classification)
#  3. Returns review fields in the payload for immediate frontend use
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/move/human")
@limiter.limit(LIMIT_MOVE)
def human_move():
    global _fullmove_counter
    d = _json_object_payload()
    if d is None:
        return jsonify({"error": "JSON body must be an object"}), 400
    fr = d.get("from", "")
    to = d.get("to",   "")
    if not fr or not to:
        return jsonify({"error": "need from+to"}), 400
    if not isinstance(fr, str) or not isinstance(to, str) or not _SQUARE_RE.fullmatch(fr) or not _SQUARE_RE.fullmatch(to):
        return jsonify({"error": "invalid square"}), 400

    r1, c1 = engine.notation_to_index(fr)
    piece  = engine.board[r1][c1]
    if piece == ".":
        return jsonify({"error": "no piece on source square"}), 400
    if engine.current_turn == "white" and piece.islower():
        return jsonify({"error": "it is white's turn"}), 400
    if engine.current_turn == "black" and piece.isupper():
        return jsonify({"error": "it is black's turn"}), 400
    r2, c2 = engine.notation_to_index(to)
    if not engine.is_valid_move(engine.board, r1, c1, r2, c2, piece):
        return jsonify({"error": "invalid move"}), 400
    if engine.move_puts_own_king_in_check(engine.board, r1, c1, r2, c2, piece):
        return jsonify({"error": "move leaves king in check"}), 400

    pre_snap    = _snap()
    move_number = len(_move_history) + 1
    # Promotion piece sent by frontend: 'Q','R','B','N' (white) or 'q','r','b','n' (black).
    # Lowercase for black, uppercase for white — matches engine.py piece conventions.
    promotion = d.get("promotion", None)
    # Normalise: accept either case, force correct case for the moving color
    if promotion:
        promotion = promotion.upper() if engine.current_turn == 'white' else promotion.lower()
        if promotion.upper() not in ('Q', 'R', 'B', 'N'):
            promotion = None   # invalid value — ignore, will fall back to queen
        elif not _is_promotion_move(piece, to):
            promotion = None   # promotion supplied for a non-promotion move
    # UCI includes promotion suffix (e.g. e7e8r) for review / FEN accuracy
    played_uci = fr + to + (promotion.lower() if promotion else '')

    # Compute review data BEFORE making the move on the global board
    # (includes eval_before, best_move, best_eval from pre-move FEN,
    #  and eval_after by temporarily applying the move)
    review = _build_move_review_entry(pre_snap, played_uci, move_number)

    # Now actually make the move.
    # move_piece_notation always promotes to queen (Q/q) — we overwrite below.
    _push_undo(_snap_full())
    _redo_stack.clear()
    engine.move_piece_notation(engine.board, fr, to)
    # Apply chosen promotion piece (overwrite the queen placed by move_piece_notation)
    if promotion:
        r2_promo, c2_promo = engine.notation_to_index(to)
        engine.board[r2_promo][c2_promo] = promotion
    _game_uci_moves.append(fr + to)   # track for opening recognition (4-char UCI, no promo suffix)
    # Record position ONCE for this real game move (not during analysis/review).
    _record_real_move_position(played_uci)


    # Increment fullmove counter after Black's move
    # (turn has already flipped; "white" now means Black just moved)
    if engine.current_turn == "white":
        _fullmove_counter += 1

    # Store in history (include snap for undo and bestmove/played endpoint)
    history_entry = dict(review)
    history_entry["snap"] = pre_snap
    _move_history.append(history_entry)

    # eval_sf is reused from the review's eval_after_cp — already cached by
    # analyze_position(fen_after) inside _build_move_review_entry, zero extra SF cost.
    payload = _payload(with_sf=False)
    payload["eval_sf"] = review["eval_after_cp"]
    # eval_before_sf / eval_after_sf kept for backward compat with board.js
    payload["eval_before_sf"]   = review["eval_before_cp"]
    payload["eval_before_engine"] = _engine_eval()   # already applied
    payload["move_from"]        = fr
    payload["move_to"]          = to
    # New review fields
    payload["review"]           = {
        "move":           played_uci,
        "best":           review["best"],
        "eval_before":    review["eval_before"],
        "eval_after":     review["eval_after"],
        "best_eval":      review["best_eval"],
        "classification": review["classification"],
        "moving_color":   review["moving_color"],
        # raw cp values for frontend classification logic
        "eval_before_cp": review["eval_before_cp"],
        "eval_after_cp":  review["eval_after_cp"],
        "best_eval_cp":   review["best_eval_cp"],
    }
    return jsonify(payload)

@app.get("/opening")
@limiter.limit(LIMIT_LIGHT)
def opening_name():
    """Return the current opening name, ECO code, and whether the current
    position still has book moves available (used by all game modes for
    consistent opening-card and badge rendering).
    Result is cached by FEN; cache is invalidated on every board mutation."""
    global _opening_cache_fen, _opening_cache_result
    current_fen = _fen()
    if _opening_cache_fen == current_fen and _opening_cache_result is not None:
        return jsonify(_opening_cache_result)

    eco, name = _match_opening(_game_uci_moves)
    # Lightweight probe: does the current position have any book entries?
    in_book = False
    if BOOK_OK:
        try:
            import chess
            with chess.polyglot.open_reader(BOOK_PATH) as reader:
                in_book = bool(list(reader.find_all(chess.Board(current_fen))))
        except Exception:
            in_book = False

    result = {"eco": eco, "name": name, "in_book": in_book}
    _opening_cache_fen    = current_fen
    _opening_cache_result = result
    return jsonify(result)


@app.post("/move/engine")
@limiter.limit(LIMIT_MOVE)
def engine_move():
    global _fullmove_counter
    d = _json_object_payload()
    if d is None:
        return jsonify({"error": "JSON body must be an object"}), 400
    # Clamp depth — never trust client-supplied value (DOS prevention)
    depth = _clamp_engine_depth(d.get("depth", engine.ENGINE_DEPTH), default=engine.ENGINE_DEPTH)
    moves = engine.generate_all_legal_moves(engine.board, engine.current_turn)
    if not moves:
        return jsonify({"error": "no legal moves"}), 400

    # ── Try opening book first ─────────────────────────────────────────
    book_uci = _book_move(_fen())
    if book_uci and len(book_uci) >= 4:
        fr, to = book_uci[:2], book_uci[2:4]
        # Cross-reference against legal move list — safe and engine-agnostic
        legal_moves = engine.generate_all_legal_moves(engine.board, engine.current_turn)
        legal_set   = {(r1, c1, r2, c2)
                       for (r1, c1), (r2, c2) in legal_moves}
        try:
            r1, c1 = engine.notation_to_index(fr)
            r2, c2 = engine.notation_to_index(to)
            if (r1, c1, r2, c2) in legal_set:
                log.info("[book] engine using book move: %s", book_uci)
                played_uci  = fr + to
                pre_snap    = _snap()
                move_number = len(_move_history) + 1
                review = _build_move_review_entry(pre_snap, played_uci, move_number, is_book_move=True)
                _push_undo(_snap_full())
                _redo_stack.clear()
                engine.move_piece_notation(engine.board, fr, to)
                _game_uci_moves.append(played_uci)
                _record_real_move_position(played_uci)
                if engine.current_turn == "white":
                    _fullmove_counter += 1
                history_entry = dict(review)
                history_entry["snap"] = pre_snap
                _move_history.append(history_entry)
                payload = {"engine_move": {"from": fr, "to": to}, "in_book": True, **_payload(with_sf=False)}
                payload["eval_sf"]             = review["eval_after_cp"]
                payload["eval_before_sf"]      = review["eval_before_cp"]
                payload["eval_before_engine"]  = _engine_eval()
                payload["move_from"]           = fr
                payload["move_to"]             = to
                payload["review"]              = {
                    "move": played_uci, "best": review["best"],
                    "eval_before": review["eval_before"], "eval_after": review["eval_after"],
                    "best_eval": review["best_eval"], "classification": review["classification"],
                    "moving_color": review["moving_color"],
                    "eval_before_cp": review["eval_before_cp"], "eval_after_cp": review["eval_after_cp"],
                    "best_eval_cp": review["best_eval_cp"],
                }
                return jsonify(payload)
            else:
                log.warning("[book] engine book move %s not in legal set — falling back", book_uci)
        except Exception as _bex:
            log.warning("[book] engine book validation error: %s", _bex)

    # ── Fallback: normal engine calculation ──────────────────────────────
    result = engine.iterative_deepening(engine.board, depth)
    if not result:
        return jsonify({"error": "engine found no move"}), 500
    best, _ = result
    fr, to  = best
    played_uci  = fr + to

    pre_snap    = _snap()
    move_number = len(_move_history) + 1

    review = _build_move_review_entry(pre_snap, played_uci, move_number)

    _push_undo(_snap_full())
    _redo_stack.clear()
    engine.move_piece_notation(engine.board, fr, to)
    _game_uci_moves.append(played_uci)
    # Record position ONCE for this real game move (not during analysis/review).
    _record_real_move_position(played_uci)

    if engine.current_turn == "white":
        _fullmove_counter += 1

    history_entry = dict(review)
    history_entry["snap"] = pre_snap
    _move_history.append(history_entry)

    payload = {"engine_move": {"from": fr, "to": to}, "in_book": False, **_payload(with_sf=False)}
    payload["eval_sf"] = review["eval_after_cp"]
    payload["eval_before_sf"]     = review["eval_before_cp"]
    payload["eval_before_engine"] = _engine_eval()
    payload["move_from"]          = fr
    payload["move_to"]            = to
    payload["review"]             = {
        "move":           played_uci,
        "best":           review["best"],
        "eval_before":    review["eval_before"],
        "eval_after":     review["eval_after"],
        "best_eval":      review["best_eval"],
        "classification": review["classification"],
        "moving_color":   review["moving_color"],
        "eval_before_cp": review["eval_before_cp"],
        "eval_after_cp":  review["eval_after_cp"],
        "best_eval_cp":   review["best_eval_cp"],
    }
    return jsonify(payload)

@app.post("/move/stockfish")
@limiter.limit(LIMIT_MOVE)
def sf_move():
    global _fullmove_counter
    if not STOCKFISH_OK or _sf_instance is None:
        return jsonify({"error": f"Stockfish not available: {STOCKFISH_ERR}"}), 501
    moves = engine.generate_all_legal_moves(engine.board, engine.current_turn)
    if not moves:
        return jsonify({"error": "no legal moves"}), 400
    try:
        fen_str = _fen()

        # ── Try opening book first ────────────────────────────────────────
        uci = _book_move(fen_str)
        is_book_move = bool(uci)
        if uci:
            log.info("[book] SF route using book move: %s", uci)
        else:
            # ── No book hit: use Stockfish engine ────────────────────────
            sf_result = analyze_position(fen_str)
            uci = sf_result.get("best_move")
            if not uci:
                log.warning("[SF] get_best_move returned None for FEN: %s — retrying", fen_str[:50])
                _invalidate_sf_cache()
                with _sf_lock:
                    _sf_instance.set_fen_position(fen_str)
                    uci = _sf_instance.get_best_move()
                log.warning("[SF] retry result: %s", uci)

        if not uci or len(uci) < 4:
            return jsonify({"error": "No move available"}), 500
        fr = uci[0:2]
        to = uci[2:4]
        r1, c1 = engine.notation_to_index(fr)
        r2, c2 = engine.notation_to_index(to)
        piece  = engine.board[r1][c1]
        moving_color = engine.current_turn
        if piece == ".":
            return jsonify({"error": f"Move picked empty square {fr}"}), 500

        played_uci  = fr + to
        pre_snap    = _snap()
        move_number = len(_move_history) + 1

        review = _build_move_review_entry(pre_snap, played_uci, move_number, is_book_move=is_book_move)

        _push_undo(_snap_full())
        _redo_stack.clear()
        engine.move_piece_notation(engine.board, fr, to)
        if len(uci) == 5:
            promo = uci[4].upper()
            engine.board[r2][c2] = promo if moving_color == "white" else promo.lower()
        _game_uci_moves.append(played_uci)
        # Record position ONCE for this real game move (not during analysis/review).
        _record_real_move_position(played_uci)

        if engine.current_turn == "white":
            _fullmove_counter += 1

        history_entry = dict(review)
        history_entry["snap"] = pre_snap
        _move_history.append(history_entry)

        payload = {"engine_move": {"from": fr, "to": to}, "in_book": is_book_move, **_payload(with_sf=False)}
        payload["eval_sf"] = review["eval_after_cp"]
        payload["eval_before_sf"]     = review["eval_before_cp"]
        payload["eval_before_engine"] = _engine_eval()
        payload["move_from"]          = fr
        payload["move_to"]            = to
        payload["review"]             = {
            "move":           played_uci,
            "best":           review["best"],
            "eval_before":    review["eval_before"],
            "eval_after":     review["eval_after"],
            "best_eval":      review["best_eval"],
            "classification": review["classification"],
            "moving_color":   review["moving_color"],
            "eval_before_cp": review["eval_before_cp"],
            "eval_after_cp":  review["eval_after_cp"],
            "best_eval_cp":   review["best_eval_cp"],
        }
        return jsonify(payload)
    except Exception as ex:
        log.error(f"[SF move error]\n{traceback.format_exc()}")
        return jsonify({"error": f"Stockfish error: {str(ex)}"}), 500


def _history_for_client():
    return [
        {
            'move':           e.get('played'),
            'best':           e.get('best'),
            'eval_before':    e.get('eval_before'),
            'eval_after':     e.get('eval_after'),
            'best_eval':      e.get('best_eval'),
            'classification': e.get('classification'),
            'moving_color':   e.get('moving_color', 'white'),
            'eval_before_cp': e.get('eval_before_cp'),
            'eval_after_cp':  e.get('eval_after_cp'),
            'best_eval_cp':   e.get('best_eval_cp'),
        }
        for e in _move_history
    ]

@app.post("/undo")
@limiter.limit(LIMIT_MOVE)
def undo():
    if not _undo_stack:
        return jsonify({"error": "nothing to undo"}), 400
    _redo_stack.append(_snap_full())
    _restore_full(_undo_stack.pop())
    _invalidate_sf_cache()          # board changed — discard cached analysis
    payload = _payload(with_sf=False)
    payload["move_history"] = _history_for_client()
    return jsonify(payload)

@app.post("/redo")
@limiter.limit(LIMIT_MOVE)
def redo():
    if not _redo_stack:
        return jsonify({"error": "nothing to redo"}), 400
    _push_undo(_snap_full())
    _restore_full(_redo_stack.pop())
    _invalidate_sf_cache()          # board changed — discard cached analysis
    payload = _payload(with_sf=False)
    payload["move_history"] = _history_for_client()
    return jsonify(payload)

@app.post("/reset")
@limiter.limit(LIMIT_MOVE)
def reset():
    _reset_globals()
    _undo_stack.clear()
    _redo_stack.clear()
    _move_history.clear()
    _invalidate_sf_cache()          # fresh game — discard any cached analysis
    return jsonify(_payload(with_sf=False))


@app.post("/load_position")
@limiter.limit(LIMIT_MOVE)
def load_position():
    """Restore a saved game by replaying a list of UCI moves from the start.
    Body: { "moves": ["e2e4", "e7e5", ...] }
    Returns the resulting board state (same shape as /state).
    No engine analysis, no review computation — fast and side-effect-free.
    """
    global _fullmove_counter
    d = _json_object_payload()
    if d is None:
        return jsonify({"error": "JSON body must be an object"}), 400
    moves, error = _validated_uci_moves(d.get("moves", []), MAX_RESTORE_MOVES)
    if error:
        return jsonify({"error": error}), 400
    previous_full = _snap_full()
    previous_undo = copy.deepcopy(_undo_stack)
    previous_redo = copy.deepcopy(_redo_stack)
    previous_counter = _fullmove_counter
    previous_game_moves = list(_game_uci_moves)

    def rollback_restore():
        global _fullmove_counter
        _restore_full(previous_full)
        _undo_stack.clear()
        _undo_stack.extend(previous_undo)
        _redo_stack.clear()
        _redo_stack.extend(previous_redo)
        _fullmove_counter = previous_counter
        _game_uci_moves.clear()
        _game_uci_moves.extend(previous_game_moves)

    _reset_globals()
    _undo_stack.clear()
    _redo_stack.clear()
    _move_history.clear()
    _invalidate_sf_cache()
    for uci in moves:
        fr, to = uci[:2], uci[2:4]
        promo = uci[4].lower() if len(uci) > 4 else None
        try:
            r1, c1 = engine.notation_to_index(fr)
            r2, c2 = engine.notation_to_index(to)
            piece = engine.board[r1][c1]
            if piece == ".":
                rollback_restore()
                return jsonify({"error": "invalid replay move"}), 400
            if engine.current_turn == "white" and piece.islower():
                rollback_restore()
                return jsonify({"error": "invalid replay turn"}), 400
            if engine.current_turn == "black" and piece.isupper():
                rollback_restore()
                return jsonify({"error": "invalid replay turn"}), 400
            if not engine.is_valid_move(engine.board, r1, c1, r2, c2, piece):
                rollback_restore()
                return jsonify({"error": "invalid replay move"}), 400
            if engine.move_puts_own_king_in_check(engine.board, r1, c1, r2, c2, piece):
                rollback_restore()
                return jsonify({"error": "invalid replay move"}), 400
            if promo and not _is_promotion_move(piece, to):
                rollback_restore()
                return jsonify({"error": "invalid replay promotion"}), 400
            # Capture moving color BEFORE move_piece_notation flips current_turn.
            # The old code used engine.current_turn AFTER the flip — inversion bug.
            moving_color = engine.current_turn
            engine.move_piece_notation(engine.board, fr, to)
            if promo:
                engine.board[r2][c2] = promo.upper() if moving_color == 'white' else promo.lower()
            _game_uci_moves.append(fr + to)
            if engine.current_turn == "white":
                _fullmove_counter += 1
        except Exception as ex:
            log.warning("[load_position] move %s failed: %s", uci, ex)
            rollback_restore()
            return jsonify({"error": "invalid replay move"}), 400
    return jsonify(_payload(with_sf=False))


# ── New endpoints: eval_history, accuracy, save_game ──────────────────────────

@app.get("/eval_history")
@limiter.limit(LIMIT_ANALYSIS)
def eval_history():
    """
    Return eval_after (in pawns, white-positive) for every played move.
    Used by the frontend eval graph.  [ 0.10, -0.15, 0.42, ... ]
    """
    return jsonify([
        round(e["eval_after"], 2) if e.get("eval_after") is not None else None
        for e in _move_history
    ])


def _compute_side_stats(moves):
    """
    Given a list of move-history entries for one side, return accuracy stats.
    Used by /accuracy to compute per-color breakdowns.
    """
    scores       = []
    blunders     = 0
    mistakes     = 0
    inaccuracies = 0
    brilliants   = 0

    for e in moves:
        cl = e.get("classification")
        if   cl == "Blunder":    blunders     += 1
        elif cl == "Mistake":    mistakes     += 1
        elif cl == "Inaccuracy": inaccuracies += 1
        elif cl == "Brilliant":  brilliants   += 1

        b     = e.get("best_eval_cp")
        a     = e.get("eval_after_cp")
        color = e.get("moving_color", "white")
        if b is not None and a is not None:
            # b and a are both white-positive centipawns.
            # cp_loss from mover's perspective:
            #   White wants high eval → loss = b - a (positive when played < best)
            #   Black wants low eval  → loss = a - b (positive when played > best)
            if color == "white":
                cp_loss = max(0, b - a)
            else:
                cp_loss = max(0, a - b)
            # Exponential accuracy: 100% for perfect moves, decays with cp_loss.
            # 300cp loss ≈ 37%, 700cp loss ≈ 10%, matching real accuracy tools.
            scores.append(max(0.0, 100.0 * math.exp(-cp_loss / 300.0)))

    accuracy = round(sum(scores) / len(scores)) if scores else 100
    return {
        "accuracy":     accuracy,
        "blunders":     blunders,
        "mistakes":     mistakes,
        "inaccuracies": inaccuracies,
        "brilliants":   brilliants,
        "moves_scored": len(scores),
    }


@app.get("/accuracy")
@limiter.limit(LIMIT_ANALYSIS)
def get_accuracy():
    """
    Compute accuracy stats for the current game — overall and per side.
    score_per_move = max(0, 100 - abs(best_eval_cp - eval_after_cp) / 10)
    accuracy       = average of all move scores  (0–100, integer)

    Returns both a flat overall block (backward-compatible) and
    a 'white' / 'black' breakdown keyed by color.
    """
    # Split history by color using moving_color field
    # (more reliable than even/odd index because undo/redo can shift parity)
    white_moves = [e for e in _move_history if e.get("moving_color", "white") == "white"]
    black_moves = [e for e in _move_history if e.get("moving_color", "white") == "black"]

    overall = _compute_side_stats(_move_history)
    white   = _compute_side_stats(white_moves)
    black   = _compute_side_stats(black_moves)

    return jsonify({
        # ── backward-compatible flat keys ──
        "accuracy":     overall["accuracy"],
        "blunders":     overall["blunders"],
        "mistakes":     overall["mistakes"],
        "inaccuracies": overall["inaccuracies"],
        "brilliants":   overall["brilliants"],
        "moves_scored": overall["moves_scored"],
        # ── per-side breakdown ──
        "white": white,
        "black": black,
    })


@app.post("/save_game")
@limiter.limit(LIMIT_MOVE)
def save_game():
    """
    Append a completed game to saved_games.json (never overwrites).
    Body: { "moves": [...], "result": "1-0"|"0-1"|"1/2-1/2",
            "accuracy": {...}, "date": "..." }
    """
    try:
        body = _json_object_payload()
        if body is None:
            return jsonify({"error": "JSON body must be an object"}), 400

        # ── Input validation ──────────────────────────────────────────────────
        moves, error = _validated_uci_moves(body.get("moves", []), MAX_SAVE_MOVES)
        if error:
            return jsonify({"error": error}), 400
        # Sanitise result against allowlist
        result = body.get("result", "?")
        if not isinstance(result, str) or result not in VALID_RESULTS:
            result = "?"
        date = body.get("date")
        if not isinstance(date, str) or len(date) > 64:
            date = datetime.datetime.utcnow().isoformat() + "Z"
        accuracy = body.get("accuracy", {})
        if not isinstance(accuracy, dict):
            accuracy = {}

        record = {
            "date":     date,
            "result":   result,
            "moves":    moves,
            "accuracy": accuracy,
            # Server-side full review (more detail than the client sends)
            "review": [
                {
                    "move":           e.get("played"),
                    "best":           e.get("best"),
                    "eval_before":    e.get("eval_before"),
                    "eval_after":     e.get("eval_after"),
                    "best_eval":      e.get("best_eval"),
                    "classification": e.get("classification"),
                    "moving_color":   e.get("moving_color"),
                }
                for e in _move_history
            ],
        }

        save_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "saved_games.json"
        )

        games = []
        if os.path.exists(save_path):
            try:
                with open(save_path, "r", encoding="utf-8") as f:
                    games = json.load(f)
                if not isinstance(games, list):
                    games = []
            except Exception:
                games = []

        games.append(record)
        # Keep only the most recent MAX_SAVED_GAMES to prevent unbounded disk growth
        games = games[-MAX_SAVED_GAMES:]

        # ── Atomic write: write to tmp then rename (safe on Render restarts) ──
        save_dir = os.path.dirname(save_path)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=save_dir, suffix='.tmp')
        try:
            with os.fdopen(tmp_fd, 'w', encoding='utf-8') as f:
                json.dump(games, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, save_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        return jsonify({"saved": True, "total_games": len(games)})

    except RequestEntityTooLarge:
        raise
    except Exception:
        log.error(traceback.format_exc())
        return jsonify({"error": "save failed"}), 500

@app.get("/test_sf")
@limiter.limit(LIMIT_ANALYSIS)
def test_sf():
    """Smoke-test for Stockfish — gated to debug mode only."""
    if not app.debug:
        return jsonify({"error": "forbidden"}), 403
    start_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    bm  = _sf_best_move_from_fen(start_fen)
    ev  = _sf_eval_at_fen(start_fen)
    bm2, be = _sf_best_move_and_eval(start_fen)
    return jsonify({
        "stockfish_ok":         STOCKFISH_OK,
        "startpos_best_move":   bm,
        "startpos_eval_cp":     ev,
        "best_move_and_eval":   {"move": bm2, "eval_cp": be},
        "all_not_none":         all(x is not None for x in [bm, ev, bm2]),
    })


# /debug route removed — previously exposed filesystem paths, directory listings,
# and internal server state to unauthenticated requests. Removed in security hardening pass.

@app.errorhandler(RateLimitExceeded)
def handle_rate_limit(error):
    return jsonify({"error": "rate limit exceeded"}), 429


@app.errorhandler(RequestEntityTooLarge)
def handle_request_too_large(error):
    return jsonify({"error": "payload too large"}), 413


@app.errorhandler(BadRequest)
def handle_bad_request(error):
    return jsonify({"error": "bad request"}), 400

# ── Security headers ─────────────────────────────────────────────────────────
@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options']        = 'DENY'
    response.headers['Referrer-Policy']        = 'strict-origin-when-cross-origin'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://gc.zgo.at; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://fonts.cdnfonts.com; "
        "font-src 'self' https://fonts.gstatic.com https://fonts.cdnfonts.com; "
        "connect-src 'self' https://chessau.goatcounter.com; "
        "img-src 'self' data:;"
    )
    return response


if __name__ == "__main__":
    # NOTE: debug=False is required. Debug mode exposes the Werkzeug interactive
    # debugger and disables the security headers added above.
    app.run(debug=False)
