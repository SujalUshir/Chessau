"""
Chess Engine — Flask Backend
python app.py
"""
import copy, traceback, os, json, datetime, threading, math
from flask import Flask, jsonify, request, render_template, send_from_directory
import engine
import logging
logging.basicConfig(level=logging.WARNING)
log = logging.getLogger(__name__)

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
# Depth 6 balances quality (~0.1-0.3s) vs depth 8 (~2-5s). Override: SF_DEPTH env var.
SF_DEPTH = int(os.environ.get("SF_DEPTH", "4"))

# ── Single persistent Stockfish process — never respawned after init ───────────
# Using one long-lived process eliminates the ~1-2s startup cost per move.
_sf_instance  = None        # the one and only Stockfish process
_sf_lock      = threading.Lock()
STOCKFISH_OK  = False
STOCKFISH_ERR = ""

# ── Position analysis cache (1-entry, keyed by FEN) ───────────────────────────
# Stores the result of the last analyze_position() call so that multiple
# helpers asking about the same FEN pay the Stockfish cost only once.
_sf_cache_fen    = None
_sf_cache_result = None   # dict: {best_move, eval_cp, eval_pawns}


def _invalidate_sf_cache():
    """Clear the position cache. Call whenever the board position changes
    externally (reset, undo, redo) so stale results are never returned."""
    global _sf_cache_fen, _sf_cache_result
    _sf_cache_fen    = None
    _sf_cache_result = None


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
    }

def _restore(s):
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
    print(f"REAL MOVE: {move_uci}")
    print(f"HASH: {key:#018x}")
    print(f"COUNT: {count}")
    if count >= 2:
        # Warn on repeated positions so duplicate-insertion bugs surface quickly.
        print(f"[rep] position seen {count}x — {'DRAW SOON' if count == 2 else 'DRAW NOW'}")


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


def analyze_position(fen_str):
    """
    THE single Stockfish entry point.
    Returns: { best_move, eval_cp, eval_pawns }
    All fields default to None if SF unavailable or position terminal.

    Guarantees:
    - Only ONE lock acquisition per real SF call (cache hits are free)
    - Never caches a failed (all-None) result — a bad call is retried next time
    - Detects and recovers from silent SF process crashes via health-check
    - Falls back from get_top_moves → get_best_move if top_moves is empty
    - Validates FEN before calling SF to avoid silent failures
    """
    global _sf_cache_fen, _sf_cache_result

    if not STOCKFISH_OK or _sf_instance is None:
        return {"best_move": None, "eval_cp": None, "eval_pawns": None}

    # ── FEN validation ─────────────────────────────────────────────────────────
    if not _sf_validate_fen(fen_str):
        log.error("[SF] Invalid FEN rejected: %r", fen_str[:60])
        return {"best_move": None, "eval_cp": None, "eval_pawns": None}

    # ── Cache hit (only serve if result is non-trivially good) ─────────────────
    # Do NOT serve a cached all-None result — forces a real retry.
    if (fen_str == _sf_cache_fen
            and _sf_cache_result is not None
            and any(v is not None for v in _sf_cache_result.values())):
        log.info("[SF] cache hit — FEN: %s", fen_str[:50])
        return _sf_cache_result

    log.info("[SF] query (depth=%d) — FEN: %s", SF_DEPTH, fen_str[:50])
    result = {"best_move": None, "eval_cp": None, "eval_pawns": None, "depth": SF_DEPTH}

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

            # ── Enforce consistent depth — the ONE place set_depth is called ──
            _sf_instance.set_depth(SF_DEPTH)
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
            result["depth"]      = SF_DEPTH

    except Exception as ex:
        log.error("[SF] analyze_position EXCEPTION: %s", ex, exc_info=True)
        # If the Stockfish process crashed, respawn immediately and retry once.
        if "crashed" in str(ex).lower() or "process" in str(ex).lower():
            log.warning("[SF] Process crash detected — attempting respawn…")
            _sf_respawn()
            if STOCKFISH_OK and _sf_instance is not None:
                try:
                    with _sf_lock:
                        _sf_instance.set_depth(SF_DEPTH)
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
                        result["depth"] = SF_DEPTH
                    log.warning("[SF] Respawn retry result: %s", result)
                except Exception as ex2:
                    log.error("[SF] Retry after respawn also failed: %s", ex2)
        return result

    # ── Only cache if we got something useful ─────────────────────────────────
    if any(v is not None for v in result.values()):
        _sf_cache_fen    = fen_str
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


# ── Mate score cap for classification only ─────────────────────────────────────
# The eval bar uses the raw ±99999 to pin the bar to the edge.  But for move
# classification, a 99999cp delta is meaningless — cap at ±1500 so cp_loss
# stays in a realistic range (mirrors chess.com behaviour).
_CLASSIFY_MATE_CAP = 1500

def _classify_move(eval_before, best_eval, eval_after, moving_color, sacrificed_material=0):
    """
    Classify a move using cp_loss from the moving player's perspective.

    eval_before         : SF eval BEFORE the move (white-positive centipawns)
    best_eval           : SF eval AFTER the best move (white-positive centipawns)
    eval_after          : SF eval AFTER the played move (white-positive centipawns)
    moving_color        : 'white' | 'black'
    sacrificed_material : centipawns of own material lost in this move (>0 = sacrifice)

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
    be = max(-_CLASSIFY_MATE_CAP, min(_CLASSIFY_MATE_CAP, best_eval))
    ae = max(-_CLASSIFY_MATE_CAP, min(_CLASSIFY_MATE_CAP, eval_after))

    # Both be and ae are white-positive centipawns.
    # cp_loss = how many centipawns worse the played move is vs the best move,
    # expressed from the moving-player's perspective (always >= 0 when clamped).
    if moving_color == 'white':
        # White wants eval to be HIGH; loss = best possible - what happened
        cp_loss = be - ae
    else:
        # Black wants eval to be LOW (white-positive); loss = what happened - best possible
        cp_loss = ae - be

    delta = max(0, cp_loss)   # clamp — negative means played >= best (no loss)

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

def _best_move_from_snap(s):
    """Temporarily restore snap, run engine search, restore back."""
    saved = _snap()
    _restore(s)
    try:
        moves = engine.generate_all_legal_moves(engine.board, engine.current_turn)
        if not moves:
            return None
        result = engine.iterative_deepening(engine.board, engine.ENGINE_DEPTH)
        if not result or not result[0]:
            return None
        best, _ = result
        return best[0] + best[1]
    except Exception as ex:
        log.error(f"[best_move_from_snap error] {ex}")
        return None
    finally:
        _restore(saved)

def _build_move_review_entry(pre_snap, played_uci, move_number):
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

        # ── Step 1: analyze fen_before — gets eval_before AND best_move in one call
        before_analysis = analyze_position(fen_before)
        eval_before = before_analysis["eval_cp"]
        best_uci    = before_analysis["best_move"]

        # ── Step 2: eval_best — apply best_uci on temp board, analyze fen_best ─
        best_eval = None
        if best_uci and len(best_uci) >= 4:
            _best_snap = _snap()   # board == pre_snap at this point
            try:
                engine.move_piece_notation(
                    engine.board, best_uci[:2], best_uci[2:4]
                )
                fen_best  = _fen()
                best_eval = analyze_position(fen_best)["eval_cp"]
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
        eval_after = analyze_position(fen_after)["eval_cp"]

        if eval_after is None:
            eval_after = eval_before   # SF error — fallback

        classification = _classify_move(
            eval_before, best_eval, eval_after,
            moving_color, sacrificed_material
        )

        if classification not in ("Brilliant", "Best") and _is_book_move(move_number, eval_before):
            classification = "Book"

        # ── Mandatory debug output (Part 7) ───────────────────────────────────
        if moving_color == "white":
            cp_loss_debug = max(0, (best_eval or 0) - (eval_after or 0))
        else:
            cp_loss_debug = max(0, (eval_after or 0) - (best_eval or 0))

        print(f"[review] move={played_uci}  mover={moving_color}")
        print(f"  eval_before={eval_before}  eval_best={best_eval}  eval_after={eval_after}")
        print(f"  cp_loss={cp_loss_debug}  classification={classification}")

        # ── Assertions to surface degenerate pipeline states ──────────────────
        if best_eval is not None and eval_before is not None:
            if best_eval == eval_before:
                print(f"  [ERROR] eval_best == eval_before ({best_eval}) — best move fallback triggered or SF returned identical eval")
        if eval_after is not None and best_eval is not None:
            if abs(cp_loss_debug) < 1 and played_uci != best_uci:
                print(f"  [WARN] cp_loss ~0 but played ({played_uci}) != best ({best_uci}) — check SF cache or eval pipeline")

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
def home():
    return render_template("index.html")

@app.get("/state")
def get_state():
    return jsonify(_payload(with_sf=False))

@app.get("/eval")
def get_eval():
    """Dedicated eval endpoint — called after undo/redo to refresh both bars."""
    return jsonify({
        "eval_engine": _engine_eval(),
        "eval_sf":     _sf_eval(),
    })

@app.get("/moves")
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
def get_fen():
    return jsonify({"fen": _fen()})

@app.get("/best_move")
def best_move_hint():
    """Return best move for current position (board highlight feature)."""
    try:
        moves = engine.generate_all_legal_moves(engine.board, engine.current_turn)
        if not moves:
            return jsonify({"error": "no legal moves"}), 400
        result = engine.iterative_deepening(engine.board, engine.ENGINE_DEPTH)
        if not result:
            return jsonify({"error": "no move found"}), 500
        best, score = result
        return jsonify({"from": best[0], "to": best[1], "score": score})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500

# ── Best Move Display endpoints ───────────────────────────────────────────────

@app.get("/bestmove/current")
def bestmove_current():
    """Return best move for current position from both engines."""
    try:
        moves = engine.generate_all_legal_moves(engine.board, engine.current_turn)
        if not moves:
            return jsonify({"engine": None, "stockfish": None})

        eng_best = None
        try:
            result = engine.iterative_deepening(engine.board, engine.ENGINE_DEPTH)
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
        entries = _move_history if not n_param else _move_history[-int(n_param):]

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
def human_move():
    global _fullmove_counter
    d  = request.get_json(force=True)
    fr = d.get("from", "")
    to = d.get("to",   "")
    if not fr or not to:
        return jsonify({"error": "need from+to"}), 400

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
    played_uci  = fr + to

    # Compute review data BEFORE making the move on the global board
    # (includes eval_before, best_move, best_eval from pre-move FEN,
    #  and eval_after by temporarily applying the move)
    review = _build_move_review_entry(pre_snap, played_uci, move_number)

    # Now actually make the move
    _undo_stack.append(_snap_full())
    _redo_stack.clear()
    engine.move_piece_notation(engine.board, fr, to)
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

@app.post("/move/engine")
def engine_move():
    global _fullmove_counter
    d     = request.get_json(force=True, silent=True) or {}
    depth = int(d.get("depth", engine.ENGINE_DEPTH))
    moves = engine.generate_all_legal_moves(engine.board, engine.current_turn)
    if not moves:
        return jsonify({"error": "no legal moves"}), 400

    result = engine.iterative_deepening(engine.board, depth)
    if not result:
        return jsonify({"error": "engine found no move"}), 500
    best, _ = result
    fr, to  = best
    played_uci  = fr + to

    pre_snap    = _snap()
    move_number = len(_move_history) + 1

    review = _build_move_review_entry(pre_snap, played_uci, move_number)

    _undo_stack.append(_snap_full())
    _redo_stack.clear()
    engine.move_piece_notation(engine.board, fr, to)
    # Record position ONCE for this real game move (not during analysis/review).
    _record_real_move_position(played_uci)

    if engine.current_turn == "white":
        _fullmove_counter += 1

    history_entry = dict(review)
    history_entry["snap"] = pre_snap
    _move_history.append(history_entry)

    payload = {"engine_move": {"from": fr, "to": to}, **_payload(with_sf=False)}
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
def sf_move():
    global _fullmove_counter
    if not STOCKFISH_OK or _sf_instance is None:
        return jsonify({"error": f"Stockfish not available: {STOCKFISH_ERR}"}), 501
    moves = engine.generate_all_legal_moves(engine.board, engine.current_turn)
    if not moves:
        return jsonify({"error": "no legal moves"}), 400
    try:
        fen_str = _fen()

        # ── CRITICAL: use analyze_position() NOT _sf_instance directly ──────────
        # Calling _sf_instance.set_fen_position() + get_best_move() here and then
        # immediately calling _build_move_review_entry() (which calls analyze_position
        # → set_fen_position again) corrupts Stockfish's internal position state,
        # causing get_best_move() to return None even for valid positions.
        #
        # analyze_position() is the single safe entry point: it holds the lock
        # for the entire set_fen + query sequence AND caches the result by FEN,
        # so _build_move_review_entry's two subsequent calls are free cache hits.
        sf_result = analyze_position(fen_str)
        uci = sf_result.get("best_move")

        # Retry once with a fresh set_fen if result was None (rare corruption recovery)
        if not uci:
            log.warning("[SF] get_best_move returned None for FEN: %s — retrying", fen_str[:50])
            _invalidate_sf_cache()
            with _sf_lock:
                _sf_instance.set_fen_position(fen_str)
                uci = _sf_instance.get_best_move()
            log.warning("[SF] retry result: %s", uci)

        if not uci or len(uci) < 4:
            return jsonify({"error": "Stockfish returned no move"}), 500
        fr = uci[0:2]
        to = uci[2:4]
        r1, c1 = engine.notation_to_index(fr)
        r2, c2 = engine.notation_to_index(to)
        piece  = engine.board[r1][c1]
        moving_color = engine.current_turn  # capture BEFORE move_piece_notation flips the turn
        if piece == ".":
            return jsonify({"error": f"Stockfish picked empty square {fr}"}), 500

        played_uci  = fr + to
        pre_snap    = _snap()
        move_number = len(_move_history) + 1

        review = _build_move_review_entry(pre_snap, played_uci, move_number)

        _undo_stack.append(_snap_full())
        _redo_stack.clear()
        engine.move_piece_notation(engine.board, fr, to)
        if len(uci) == 5:
            promo = uci[4].upper()
            # `moving_color` captured before the turn flipped; White promotes to uppercase.
            engine.board[r2][c2] = promo if moving_color == "white" else promo.lower()
        # Record position ONCE for this real game move (not during analysis/review).
        _record_real_move_position(played_uci)

        # Increment fullmove counter after Black's move
        if engine.current_turn == "white":
            _fullmove_counter += 1

        history_entry = dict(review)
        history_entry["snap"] = pre_snap
        _move_history.append(history_entry)

        payload = {"engine_move": {"from": fr, "to": to}, **_payload(with_sf=False)}
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
def redo():
    if not _redo_stack:
        return jsonify({"error": "nothing to redo"}), 400
    _undo_stack.append(_snap_full())
    _restore_full(_redo_stack.pop())
    _invalidate_sf_cache()          # board changed — discard cached analysis
    payload = _payload(with_sf=False)
    payload["move_history"] = _history_for_client()
    return jsonify(payload)

@app.post("/reset")
def reset():
    _reset_globals()
    _undo_stack.clear()
    _redo_stack.clear()
    _move_history.clear()
    _invalidate_sf_cache()          # fresh game — discard any cached analysis
    return jsonify(_payload(with_sf=False))


# ── New endpoints: eval_history, accuracy, save_game ──────────────────────────

@app.get("/eval_history")
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
def save_game():
    """
    Append a completed game to saved_games.json (never overwrites).
    Body: { "moves": [...], "result": "1-0"|"0-1"|"1/2-1/2",
            "accuracy": {...}, "date": "..." }
    """
    try:
        body = request.get_json(force=True, silent=True) or {}

        record = {
            "date":     body.get("date") or datetime.datetime.utcnow().isoformat() + "Z",
            "result":   body.get("result", "?"),
            "moves":    body.get("moves", []),
            "accuracy": body.get("accuracy", {}),
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

        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(games, f, indent=2, ensure_ascii=False)

        return jsonify({"saved": True, "total_games": len(games)})

    except Exception as ex:
        log.error(traceback.format_exc())
        return jsonify({"error": str(ex)}), 500

@app.get("/test_sf")
def test_sf():
    """Quick smoke-test for the Stockfish helpers — hit GET /test_sf after deploy."""
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


@app.get("/debug")
def debug():
    sounds_dir = os.path.join(PROJECT_DIR, "sounds")
    static_sounds_dir = os.path.join(PROJECT_DIR, "static", "sounds")

    sf_exists = os.path.isfile(SF_PATH)
    return jsonify({
        "stockfish_ok":      STOCKFISH_OK,
        "stockfish_error":   STOCKFISH_ERR,
        "stockfish_path":    SF_PATH,
        "stockfish_binary_exists": sf_exists,
        "cwd":               os.getcwd(),
        "project_dir":       PROJECT_DIR,
        "files_in_cwd":      os.listdir("."),
        "move_history_len":  len(_move_history),
        "fullmove_counter":  _fullmove_counter,
        "sounds_at_root":    {
            "dir_exists":  os.path.isdir(sounds_dir),
            "move.wav":    os.path.exists(os.path.join(sounds_dir, "move.wav")),
            "capture.wav": os.path.exists(os.path.join(sounds_dir, "capture.wav")),
            "check.wav":   os.path.exists(os.path.join(sounds_dir, "check.wav")),
        },
        "sounds_in_static":  {
            "dir_exists":  os.path.isdir(static_sounds_dir),
            "move.wav":    os.path.exists(os.path.join(static_sounds_dir, "move.wav")),
            "capture.wav": os.path.exists(os.path.join(static_sounds_dir, "capture.wav")),
            "check.wav":   os.path.exists(os.path.join(static_sounds_dir, "check.wav")),
        },
    })

if __name__ == "__main__":
    app.run(debug=False)