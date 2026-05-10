# Single worker is INTENTIONAL - Chessau uses global shared in-memory board state
# (engine.board, _undo_stack, _move_history). Multiple workers would each have
# an independent copy of that state, causing game desyncs and race conditions.
# Do NOT increase --workers without first migrating to a proper session store.
web: gunicorn app:app --workers 1 --timeout 120 --log-level warning
