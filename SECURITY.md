# Chessau — Security & Deployment Notes

This document covers architecture assumptions, deployment constraints, and known limitations relevant to running Chessau safely in a public environment.

---

## Architecture Assumptions

Chessau is designed as a **single-user, single-session chess platform**. It is not a multi-user application. The backend holds one authoritative game state in memory at a time.

| Assumption | Detail |
|---|---|
| Single game session | One game at a time; shared by all connected browser tabs |
| In-memory state | No database; board state, move history, and undo stacks live in Python module globals |
| Single worker required | Multi-worker Gunicorn is unsafe — see below |
| Stateless FEN analysis | Stockfish receives FEN strings per request; no UCI state persists between analysis calls |

---

## Single-Worker Requirement

**Gunicorn MUST be run with `--workers 1`.**

Chessau's game state lives in module-level Python globals:

- `engine.board` — the authoritative board position
- `_undo_stack`, `_redo_stack`, `_move_history` — game history
- `_game_uci_moves` — opening tracker
- `_sf_instance` — the single persistent Stockfish process

Multiple Gunicorn workers each get their own copy of this state via `fork()`. Any move played in worker A would be invisible to worker B, causing desyncs, illegal-state errors, and corrupted game history.

The Procfile enforces this:

```
web: gunicorn app:app --workers 1 --timeout 120 --log-level warning
```

Do **not** change `--workers` without first migrating game state to an external session store (Redis, database, etc.).

---

## Rate Limiting

Chessau includes lightweight server-side rate limiting with `flask-limiter` using per-process in-memory storage:

- Move and mutation routes: `30/min`
- Analysis/review routes: `15/min`
- Lightweight state GET routes: `60/min`

Rate-limit responses are returned as JSON (`429`) so the SPA fails gracefully without exposing stack traces.

**Mitigations also in place:**

- Engine depth is hard-capped at 5 server-side (`MAX_ENGINE_DEPTH`)
- Request body size is capped at 256 KB (`MAX_CONTENT_LENGTH`) before route handlers run
- Render's proxy provides additional connection-level protection

---

## Payload & Save-File Limits

| Limit | Value | Enforced at |
|---|---|---|
| Request body | 256 KB | Flask `MAX_CONTENT_LENGTH` |
| Moves per save | 500 | `/save_game` |
| Moves per restore | 300 | `/load_position` |
| Saved games retained | 200 | `/save_game` (oldest trimmed) |
| Result field | allowlist `1-0 / 0-1 / 1/2-1/2 / ?` | `/save_game` |

---

## Move Validation

All moves are validated **server-side**, regardless of what the client sends:

1. Source square must contain a piece belonging to the current turn
2. Move must pass the engine's full legality check (`is_valid_move`)
3. Move must not leave own king in check (`move_puts_own_king_in_check`)
4. Promotion pieces are validated against `{Q, R, B, N}` — invalid values default to Queen
5. UCI replay strings in `/load_position` must match `^[a-h][1-8][a-h][1-8][qrbn]?$`

The client has no authority over game legality.

---

## Stockfish Process Safety

- One Stockfish subprocess runs for the server's lifetime, guarded by `threading.Lock`
- Analysis depth (live and review) is controlled by server env vars and clamped to `MAX_ENGINE_DEPTH = 5`
- FEN strings are structurally validated before being sent to Stockfish
- Silent process crashes are detected and trigger automatic respawn
- Analysis results are cached by `(FEN, depth)` to avoid redundant subprocess calls

---

## Sensitive Endpoints

| Endpoint | Status |
|---|---|
| `GET /debug` | **Removed** — exposed filesystem paths and directory listings |
| `GET /test_sf` | **Production-gated** — returns 403 unless `app.debug=True` |

---

## Security Headers

All responses include:

```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: strict-origin-when-cross-origin
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline' https://gc.zgo.at; ...
```

---

## Known Limitations

| Limitation | Notes |
|---|---|
| No authentication | This is intentional — Chessau is a single-player analysis tool, not an account-based platform |
| No WebSocket / real-time push | HTTP polling only; no persistent connections |
| No multi-user isolation | All browser tabs share the same backend game state |
| `saved_games.json` is ephemeral | Render's free-tier disk resets on redeploy; saves are not truly persistent |
| Per-process rate limiting | Current deployment is intentionally single-worker; use external rate limiting if scaling beyond that |

---

## Public Deployment Guidance

Chessau is appropriate for **public portfolio hosting** under these conditions:

- ✅ Single worker enforced (`--workers 1`)
- ✅ `debug=False` (enforced in `app.run()` and Gunicorn default)
- ✅ No secrets, credentials, or PII are stored
- ✅ `/debug` route removed
- ✅ Payload size limited
- ✅ Move depth capped
- ✅ Rate limiting enabled

It is **not appropriate** for:

- ❌ Multi-user competitive play (no user isolation)
- ❌ Storing sensitive user data (no auth, no encryption at rest)
- ❌ High-traffic deployments without upstream rate limiting

---

*Last updated: 2026-05-10 | Hardening pass v1.0*
