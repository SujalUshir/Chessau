"""
Chess Engine — Flask Backend
python app.py
"""
import os
import copy, traceback, os, json, datetime, threading
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
_sf           = None
_sf_lock      = threading.Lock()
STOCKFISH_OK  = False
STOCKFISH_ERR = ""

def _init_stockfish():
    global _sf, STOCKFISH_OK, STOCKFISH_ERR
    try:
        from stockfish import Stockfish

        sf = Stockfish(path=os.path.join(PROJECT_DIR, "bin", "stockfish"))

        sf.set_depth(12)
        sf.set_fen_position("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1")

        move = sf.get_best_move()

        if move:
            _sf = sf
            STOCKFISH_OK = True
            log.info("[Stockfish] Ready — system binary, test_move=%s", move)
        else:
            raise Exception("Stockfish returned no move")

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

def _snap_full():
    """Snapshot both board state and move history for full undo/redo."""
    return (_snap(), copy.deepcopy(_move_history))

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
    ep = "-" \
         if engine.en_passant_target else "-"
    fullmove = max(1, _fullmove_counter)
    halfmove = max(0, engine.halfmove_clock)
    return f"{'/'.join(rows)} {t} {ca} {ep} {halfmove} {fullmove}"


def _sf_eval_at_fen(fen_str):
    """
    Get Stockfish centipawn eval at a given FEN, always from WHITE's perspective.
    Returns int or None if Stockfish not available.
    """
    if not STOCKFISH_OK or _sf is None:
        return None
    try:
        with _sf_lock:
            _sf.set_fen_position(fen_str)
            info = _sf.get_evaluation()
        if not info:
            return None
        # Determine whose turn it is from FEN
        side = fen_str.split()[1] if ' ' in fen_str else 'w'
        if info["type"] == "cp":
            cp = info["value"]
            # stockfish-python returns eval from side-to-move perspective
            if side == 'b':
                cp = -cp
            return cp
        if info["type"] == "mate":
            sign = 1 if info["value"] > 0 else -1
            if side == 'b':
                sign = -sign
            return sign * 99999
    except Exception as ex:
        log.warning(f"[SF eval_at_fen error] {ex}")
    return None

def _sf_eval():
    """Return Stockfish eval for current global board position."""
    return _sf_eval_at_fen(_fen())

def _sf_best_move_and_eval(fen_str):
    """
    Ask Stockfish for the best move at the given FEN position.
    Returns (best_move_uci, eval_after_best) where eval_after_best is
    the Stockfish eval AFTER the best move is played (white-positive).
    Returns (None, None) if unavailable.
    """
    if not STOCKFISH_OK or _sf is None:
        return None, None
    try:
        with _sf_lock:
            _sf.set_fen_position(fen_str)
            best_uci = _sf.get_best_move()
            if not best_uci or len(best_uci) < 4:
                return None, None

            best_eval = None
            try:
                _sf.set_fen_position(fen_str)
                # Use make_moves_from_current_position to apply the best move
                _sf.make_moves_from_current_position([best_uci])
                info = _sf.get_evaluation()
                if info:
                    side = fen_str.split()[1] if ' ' in fen_str else 'w'
                    # After best move, it's the opponent's turn — SF eval is from their view
                    # We want white-positive, so negate if side was white (now black to move after)
                    if info["type"] == "cp":
                        cp = info["value"]
                        if side == 'w':
                            cp = -cp
                        best_eval = cp
                    elif info["type"] == "mate":
                        sign = 1 if info["value"] > 0 else -1
                        if side == 'w':
                            sign = -sign
                        best_eval = sign * 99999
            except Exception:
                best_eval = None

        return best_uci[:4], best_eval
    except Exception as ex:
        log.error(f"[SF best_move_and_eval error] {ex}")
    return None, None

def _sf_best_move_from_fen(fen_str):
    if not STOCKFISH_OK or _sf is None:
        return None
    try:
        with _sf_lock:
            _sf.set_fen_position(fen_str)   # RESET STATE
            uci = _sf.get_best_move()
        if uci and len(uci) >= 4:
            return uci[:4]
    except Exception as ex:
        log.debug("SF ERROR: %s", ex)
    return None

def _classify_move(eval_before, best_eval, eval_after, moving_color, sacrificed_material=0):
    """
    Classify a move using delta = best_val - played_val (from moving player's view).

    eval_before         : SF eval BEFORE the move (white-positive centipawns)
    best_eval           : SF eval AFTER the best move (white-positive centipawns)
    eval_after          : SF eval AFTER the played move (white-positive centipawns)
    moving_color        : 'white' | 'black'
    sacrificed_material : centipawns of own material lost in this move (>0 = sacrifice)

    Classification order (highest priority first):
      Brilliant  — sacrificed own material AND delta <= 30 cp from best
      Best       — 0–20 cp worse than best
      Excellent  — 20–50 cp
      Good       — 50–100 cp
      Inaccuracy — 100–200 cp
      Mistake    — 200–400 cp
      Blunder    — 400+ cp
    """
    if best_eval is None or eval_after is None:
        return None

    # Convert to moving-player-positive perspective
    if moving_color == 'white':
        played_val = eval_after
        best_val   = best_eval
    else:
        played_val = -eval_after
        best_val   = -best_eval

    # delta: how much worse the played move is vs the best move
    # delta <= 0 means the played move was AT LEAST as good as Stockfish's best
    delta = best_val - played_val

    # ── Brilliant: sacrificed own material AND very close to best move ──
    if sacrificed_material > 0 and delta <= 30:
        return "Brilliant"

    if delta <= 20:
        return "Best"
    if delta <= 50:
        return "Excellent"
    if delta <= 100:
        return "Good"
    if delta <= 200:
        return "Inaccuracy"
    if delta <= 400:
        return "Mistake"
    return "Blunder"

def _is_book_move(move_number, eval_before):
    """
    Heuristic: first 10 half-moves with near-equal eval (±30 cp) are book moves.
    """
    if eval_before is None:
        return False
    return move_number <= 10 and abs(eval_before) <= 30

def _payload(with_sf=False):
    eng_eval = _engine_eval()
    sf_eval  = _sf_eval() if with_sf else None
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
    Given the pre-move snapshot and what was played, compute all review fields.
    Returns a dict ready to be stored in _move_history and returned to client.
    Fully restores global state after computation.
    """
    saved = _snap()
    try:
        _restore(pre_snap)
        fen_before     = _fen()
        moving_color   = engine.current_turn
        eval_before    = _sf_eval_at_fen(fen_before)
        best_uci, best_eval = _sf_best_move_and_eval(fen_before)

        # ── Material tracking for Brilliant detection ──
        # Record own material BEFORE the move
        own_material_before = _material_score(engine.board, moving_color)

        # Use a board copy for material counting — keeps global state clean
        # until eval_after needs the full FEN after the real move
        temp_board = copy.deepcopy(engine.board)
        temp_fr, temp_fc = engine.notation_to_index(played_uci[:2])
        temp_tr, temp_tc = engine.notation_to_index(played_uci[2:4])
        _p = temp_board[temp_fr][temp_fc]
        temp_board[temp_tr][temp_tc] = _p
        temp_board[temp_fr][temp_fc] = "."

        own_material_after  = _material_score(temp_board, moving_color)
        sacrificed_material = max(0, own_material_before - own_material_after)

        # For eval_after we need the real FEN — apply move on global board
        # (finally block will restore it regardless of what happens next)
        engine.move_piece_notation(engine.board, played_uci[:2], played_uci[2:4])
        eval_after = _sf_eval_at_fen(_fen())

        classification = _classify_move(
            eval_before, best_eval, eval_after,
            moving_color, sacrificed_material
        )

        # Override with Book Move if applicable (Book takes lowest priority —
        # only applies if not already Brilliant/Best)
        if classification not in ("Brilliant", "Best") and _is_book_move(move_number, eval_before):
            classification = "Book"

        return {
            "move_number":        move_number,
            "played":             played_uci,
            "best":               best_uci,
            "eval_before":        round(eval_before / 100, 2) if eval_before is not None else None,
            "eval_after":         round(eval_after  / 100, 2) if eval_after  is not None else None,
            "best_eval":          round(best_eval   / 100, 2) if best_eval   is not None else None,
            "classification":     classification,
            "moving_color":       moving_color,
            "sacrificed_material": sacrificed_material,
            # Raw centipawns stored for frontend classification logic
            "eval_before_cp":     eval_before,
            "eval_after_cp":      eval_after,
            "best_eval_cp":       best_eval,
        }
    except Exception as ex:
        log.error(f"[build_move_review_entry error] {ex}")
        return {
            "move_number":        move_number,
            "played":             played_uci,
            "best":               None,
            "eval_before":        None,
            "eval_after":         None,
            "best_eval":          None,
            "classification":     None,
            "moving_color":       engine.current_turn,
            "sacrificed_material": 0,
            "eval_before_cp":     None,
            "eval_after_cp":      None,
            "best_eval_cp":       None,
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

    # Increment fullmove counter after Black's move
    # (turn has already flipped; "white" now means Black just moved)
    if engine.current_turn == "white":
        _fullmove_counter += 1

    # Store in history (include snap for undo and bestmove/played endpoint)
    history_entry = dict(review)
    history_entry["snap"] = pre_snap
    _move_history.append(history_entry)

    payload = _payload(with_sf=True)
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

    # Increment fullmove counter after Black's move
    if engine.current_turn == "white":
        _fullmove_counter += 1

    history_entry = dict(review)
    history_entry["snap"] = pre_snap
    _move_history.append(history_entry)

    payload = {"engine_move": {"from": fr, "to": to}, **_payload(with_sf=True)}
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
    if not STOCKFISH_OK or _sf is None:
        return jsonify({"error": f"Stockfish not available: {STOCKFISH_ERR}"}), 501
    moves = engine.generate_all_legal_moves(engine.board, engine.current_turn)
    if not moves:
        return jsonify({"error": "no legal moves"}), 400
    try:
        fen_str = _fen()
        with _sf_lock:
            _sf.set_fen_position(fen_str)
            uci = _sf.get_best_move()
        if not uci or len(uci) < 4:
            return jsonify({"error": "Stockfish returned no move"}), 500
        fr = uci[0:2]
        to = uci[2:4]
        r1, c1 = engine.notation_to_index(fr)
        r2, c2 = engine.notation_to_index(to)
        piece  = engine.board[r1][c1]
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
            engine.board[r2][c2] = promo if engine.current_turn == "black" else promo.lower()

        # Increment fullmove counter after Black's move
        if engine.current_turn == "white":
            _fullmove_counter += 1

        history_entry = dict(review)
        history_entry["snap"] = pre_snap
        _move_history.append(history_entry)

        payload = {"engine_move": {"from": fr, "to": to}, **_payload(with_sf=True)}
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
    payload = _payload(with_sf=False)
    payload["move_history"] = _history_for_client()
    return jsonify(payload)

@app.post("/redo")
def redo():
    if not _redo_stack:
        return jsonify({"error": "nothing to redo"}), 400
    _undo_stack.append(_snap_full())
    _restore_full(_redo_stack.pop())
    payload = _payload(with_sf=False)
    payload["move_history"] = _history_for_client()
    return jsonify(payload)

@app.post("/reset")
def reset():
    _reset_globals()
    _undo_stack.clear()
    _redo_stack.clear()
    _move_history.clear()
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
            sign  = 1 if color == "white" else -1
            delta = max(0, sign * (b - a))
            scores.append(max(0.0, 100.0 - delta / 10.0))

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

# /debug removed: it exposed filesystem paths and process state.

if __name__ == "__main__":
    app.run(debug=False)
