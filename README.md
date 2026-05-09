# ♟️ Chessau — Modern Chess Analysis Platform

## 🔗 Live Demo

[https://chessau.onrender.com](https://chessau.onrender.com)

---

# Overview

Chessau is a full-stack browser-based chess platform focused on real-time analysis, opening intelligence, move review, and a polished voxel-inspired UI experience.

The project combines:

* Real-time Stockfish analysis
* A custom Python chess engine
* Opening-book integration
* ECO opening recognition
* Move classification & accuracy analysis
* Save/load functionality
* SPA-style frontend architecture
* Responsive modern UI/UX

Chessau was designed as a lightweight but feature-rich chess experience inspired by platforms like Chess.com and Lichess while maintaining a fully custom architecture.

---

# ✨ Core Features

## 🎮 Game Modes

* Human vs Human
* Human vs Stockfish
* Human vs MyEngine
* Master Bot (experimental / disabled)

---

## 📊 Real-Time Analysis

* Live Stockfish evaluation bar
* Best-move suggestions
* Move review system
* Accuracy calculation system
* MultiPV-based analysis
* Move classifications:

| Classification | Meaning                              |
| -------------- | ------------------------------------ |
| Brilliant 💎   | High-value tactical/sacrificial move |
| Best           | Engine top move                      |
| Excellent      | Near-perfect move                    |
| Good           | Solid move                           |
| Inaccuracy     | Small positional loss                |
| Mistake        | Significant positional loss          |
| Blunder        | Severe losing move                   |

---

## 📖 Opening System

* Polyglot opening-book integration
* ECO opening recognition
* Opening-name display
* In-book / Out-of-book detection
* Book-move indicators

Examples:

* Sicilian Defense
* Queen's Gambit
* Giuoco Piano
* French Defense

---

## 🔁 Game Features

* Drag-and-drop board
* Click-to-move support
* Undo / Redo
* Save game system
* Session-based restore system
* Move sounds
* Responsive board UI
* Resign system

---

## 💾 Save System

Chessau includes a lightweight session-based save architecture.

Features:

* Save current games instantly
* Restore saved games
* Delete individual saves
* Clear all saves
* Saved-games navigation page

Technical details:

* Uses browser `sessionStorage`
* No database required
* Saves persist until browser refresh

---

# 🧠 Tech Stack

| Layer         | Technology                 |
| ------------- | -------------------------- |
| Backend       | Python + Flask             |
| Frontend      | Vanilla JavaScript SPA     |
| Engine        | Custom Python Chess Engine |
| Analysis      | Stockfish                  |
| Styling       | CSS3 + Grid/Flexbox        |
| Opening Books | python-chess Polyglot      |
| Deployment    | Render + Gunicorn          |

---

# 🏗️ Architecture

## Frontend

The frontend is implemented as a lightweight Single Page Application (SPA) without React/Vue.

Core responsibilities:

* Route handling
* Board rendering
* UI state management
* API communication
* Review rendering
* Save management
* Opening-card rendering

### Key Files

| File                   | Purpose                                  |
| ---------------------- | ---------------------------------------- |
| `static/main.js`       | Global app controller, routing, UI state |
| `static/board.js`      | Chessboard rendering & interaction logic |
| `static/style.css`     | Full design system & UI styling          |
| `templates/index.html` | App bootstrap & font imports             |

---

## Backend

The backend is fully stateless and FEN-driven.

Core responsibilities:

* Move validation
* Stockfish integration
* Review generation
* Accuracy analysis
* Opening-book lookup
* ECO recognition
* Engine move generation

### Key Files

| File        | Purpose                           |
| ----------- | --------------------------------- |
| `app.py`    | Flask server + API routes         |
| `engine.py` | Custom chess engine               |
| `books/`    | Polyglot opening books + ECO data |

---

# ⚙️ Engineering Highlights

## 🔹 Stockfish Integration

Chessau uses a persistent Stockfish process instead of spawning new processes per request.

Benefits:

* Lower latency
* Reduced memory overhead
* Better stability on Render free-tier
* Consistent evaluations

Additional optimizations:

* Depth separation:

  * Live eval bar → lower depth
  * Move review → higher depth
* MultiPV move comparison
* Evaluation normalization to White POV
* Crash recovery & respawn handling

---

## 🔹 Custom Chess Engine

The project includes a custom-built Python chess engine.

Implemented concepts:

* Minimax
* Alpha-Beta Pruning
* Iterative Deepening
* Piece-Square Tables
* Move Ordering
* Transposition Tables
* Zobrist Hashing

Limitations:

* Python-based node search becomes slow at high depths
* Experimental Master Bot mode remains disabled for stability

---

## 🔹 Move Review Pipeline

The move-review system compares:

* Evaluation before move
* Evaluation after move
* Best engine continuation
* MultiPV alternatives

The system then computes:

* Centipawn loss
* Move classification
* Accuracy contribution

This architecture evolved significantly to solve:

* Eval-sign flipping bugs
* Perspective inconsistencies
* Unrealistic accuracy swings
* Race-condition desyncs

---

## 🔹 Opening Recognition System

The opening system combines:

* Polyglot opening books
* ECO move matching
* Longest-prefix recognition

Flow:

1. Game moves converted to UCI
2. ECO table scanned for longest matching prefix
3. Opening name + ECO displayed in UI
4. Out-of-book transition triggered when theory ends

---

# 🎨 UI / UX Design

Chessau uses a custom voxel-inspired design language.

## Typography System

| Purpose                | Font          |
| ---------------------- | ------------- |
| Branding / Hero Titles | Mojangles     |
| Section Headings       | Space Grotesk |
| Body Text              | Inter         |
| Technical / Chess Data | IBM Plex Mono |

---

## Design Philosophy

Goals:

* Dark premium aesthetic
* Lightweight rendering
* Geometric layout hierarchy
* Smooth interactions
* Responsive gameplay UI

Key features:

* Interactive grid background
* Hard-shadow voxel styling
* Tactile button interactions
* Responsive layouts
* Minimal animation philosophy

---

# 📁 Project Structure

```text
Chessau/
├── app.py
├── engine.py
├── requirements.txt
├── Procfile
├── books/
├── static/
│   ├── main.js
│   ├── board.js
│   ├── style.css
│   ├── sounds/
│   └── images/
├── templates/
│   └── index.html
└── README.md
```

---

# 📸 Screenshots

## 🏠 Home Page

Add screenshot here

---

## 🎮 Gameplay

Add screenshot here

---

## 📊 Move Review & Accuracy

Add screenshot here

---

## 📖 Opening Recognition

Add screenshot here

---

# 🚀 Run Locally

## 1. Clone Repository

```bash
git clone <repo-url>
cd Chessau
```

---

## 2. Install Dependencies

```bash
pip install -r requirements.txt
```

---

## 3. Configure Stockfish

Download Stockfish:

👉 [https://stockfishchess.org/download/](https://stockfishchess.org/download/)

Place binary at:

```text
stockfish/stockfish
```

Linux/macOS:

```bash
chmod +x stockfish/stockfish
```

---

## 4. Run Application

```bash
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

---

# ☁️ Deploy on Render

## Build Command

```bash
pip install -r requirements.txt
```

## Start Command

```bash
gunicorn app:app
```

---

# ⚠️ Deployment Notes

## Render Free-Tier Constraints

The project was optimized specifically around Render limitations.

Implemented optimizations:

* Persistent Stockfish process
* Reduced hash/thread usage
* Lower live-eval depth
* Separate review depth
* Stateless FEN-driven backend
* Lightweight frontend rendering

---

# 🔮 Future Improvements

Potential future upgrades:

* WebSocket-based live engine streaming
* Multiplayer support
* WASM-based browser engine
* Cloud save system
* User accounts
* Opening explorer
* Engine-vs-engine mode
* Puzzle generation system

---

# 👨‍💻 Author

## Sujal Ajit Ushir

* First-year student
* IIIT Kottayam

Chessau was built as a deep exploration into:

* Full-stack architecture
* Chess-engine integration
* Async frontend/backend systems
* UI/UX engineering
* Performance optimization
* State synchronization

---

# 📌 Project Status

✅ Feature-complete
✅ Stable
✅ Fully playable
✅ Deployment-ready

Chessau is now in maintenance/polish phase with core systems complete.
