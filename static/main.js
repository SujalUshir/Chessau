/* ============================================================
   static/main.js   SPA Router + UI shell
   - Auto-reset when difficulty or game mode changes
   - Best Move: dropdown only (none / current_position / previous_move)
   - Stockfish Move Analysis in sidebar
   - Eval bars: untouched
   ============================================================ */
'use strict';

const App = (() => {

  let currentMode = null;
  let playerColor = 'white';
  let selectedBot = null;
  let _pendingRestore = null;

  // —— XSS-safe HTML escape helper ——
  // Used whenever user-influenced strings (mode names, opening text,
  // timestamps from sessionStorage) are inserted via innerHTML.
  function esc(s){
    const d = document.createElement('div');
    d.textContent = String(s ?? '');
    return d.innerHTML;
  }

  /* ── Settings ── */
  let cfg = {
    evalBars: true,
    evalEng:  true,
    evalSf:   true,
    sound:    true,
    undo:     true,
    bmMode:   'none',   // 'none' | 'current_position' | 'previous_move'
  };
  try{ const s=sessionStorage.getItem('chess_cfg'); if(s) Object.assign(cfg,JSON.parse(s)); }catch{}
  function saveCfg(){ try{ sessionStorage.setItem('chess_cfg',JSON.stringify(cfg)); }catch{} }

  /* ── Bot definitions ── */
  const BOTS = [
    { id:'beginner',     name:'Beginner',     depth:1, icon:'🐣', desc:'Plays random-ish moves. Great for beginners.' },
    { id:'intermediate', name:'Intermediate', depth:2, icon:'🎓', desc:'Thinks 2 moves ahead. A fair challenge.' },
    { id:'advanced',     name:'Advanced',     depth:3, icon:'⚔️',  desc:'Alpha-beta at depth 3 — the default engine.' },
    { id:'master',       name:'Master',       depth:4, icon:'👑', desc:'Depth 4 with full search. Plays strong chess.', disabled:true },
  ];

  const MODE_LABELS = { hvh:'Human vs Human', stockfish:'Human vs Stockfish', engine:'Human vs My Engine' };
  const MODE_OPP    = { hvh:'Player 2', stockfish:'Stockfish', engine:'My Engine' };

  /* ════════════════════════════════════════════
     HELPERS
  ════════════════════════════════════════════ */

  /** Returns true when the game page is currently active */
  function _inGame(){
    return (location.hash.slice(1)||'home') === 'game';
  }

  /**
   * Apply a difficulty (bot depth) change.
   * Called from both the in-game settings panel AND the bot-picker.
   * If currently in a game, updates the engine depth and resets the board.
   */
  async function _applyDifficultyChange(newBot){
    selectedBot = newBot;

    if(!_inGame()) return; // not in a game yet — will take effect when game starts

    // Update the badge label
    const badge = document.querySelector('.game-mode-badge');
    if(badge){
      const label = MODE_LABELS[currentMode]||'Chess';
      badge.textContent = label + (newBot ? ` · ${newBot.name}` : '');
    }

    // Update the Bot row in Engine Info sidebar
    const botVal = document.querySelector('#ei-turn')?.closest('.sb-box')
                    ?.querySelector('.ei-row:last-child .ei-val');
    if(botVal) botVal.textContent = newBot ? `${newBot.icon} ${newBot.name}` : MODE_OPP[currentMode]||'Opponent';

    // Tell Board the new depth, then do a full reset
    Board.setEngineDepth(newBot?.depth || 3);
    // Clear best-move hints before reset so stale highlights don't flash
    Board.setBestMoveMode('none');
    Board.setBestMoveMode(cfg.bmMode);
    _showToast(`Difficulty → ${newBot?.name || 'Default'} · New game started`);
    await Board.resetGame();
  }

  /**
   * Apply a game mode change while already in a game.
   * Saves the new mode, updates the badge, resets the board.
   */
  async function _applyModeChange(newMode, newBot){
    const prevMode = currentMode;
    currentMode = newMode;
    selectedBot = newBot || null;

    if(!_inGame()) return;

    // Update badge
    const badge = document.querySelector('.game-mode-badge');
    if(badge){
      const label = MODE_LABELS[newMode]||'Chess';
      badge.textContent = label + (selectedBot ? ` · ${selectedBot.name}` : '');
    }

    // Update the opponent name strip labels
    const oppName = newMode==='engine'&&selectedBot
      ? `${selectedBot.icon} ${selectedBot.name}`
      : MODE_OPP[newMode]||'Opponent';
    const youLabel = _resolvedColor==='black' ? '♟ You (Black)' : '♙ You (White)';
    const blackStrip = document.querySelector('#black-strip .player-name');
    const whiteStrip = document.querySelector('#white-strip .player-name');
    if(blackStrip){
      const topLabel = _resolvedColor==='black' ? youLabel : oppName;
      const dot = document.createElement('span');
      dot.className = 'p-dot b';
      dot.id = 'dot-b';
      blackStrip.replaceChildren(dot, document.createTextNode(topLabel));
    }
    if(whiteStrip){
      const botLabel = _resolvedColor==='black' ? oppName : youLabel;
      const dot = document.createElement('span');
      dot.className = 'p-dot w';
      dot.id = 'dot-w';
      whiteStrip.replaceChildren(dot, document.createTextNode(botLabel));
    }

    Board.setEngineDepth(selectedBot?.depth || 3);
    // Clear best-move hints before reset so stale highlights don't flash
    Board.setBestMoveMode('none');
    Board.setBestMoveMode(cfg.bmMode);
    _showToast(`Mode → ${MODE_LABELS[newMode]||newMode} · New game started`);
    await Board.resetGame();
  }

  /* ── NAV ── */
  function renderNav(){
    document.querySelector('nav')?.remove();
    const hash=location.hash.slice(1)||'home';
    const nav=document.createElement('nav');
    nav.innerHTML=`
      <div class="nav-logo" id="nav-logo">Chess<span>au</span></div>
      <ul class="nav-links">
        <li><a href="#home" class="${hash==='home'?'active':''}">Home</a></li>
        <li><a href="#play" class="${hash==='play'?'active':''}">Play</a></li>
        <li><a href="#saves" class="${hash==='saves'?'active':''}">Saved Games</a></li>
        <li><a href="#info" class="${hash==='info'?'active':''}">Info</a></li>
      </ul>`;
    nav.querySelector('#nav-logo').addEventListener('click',()=>navigate('home'));
    document.getElementById('app').prepend(nav);
  }

  /* ════════════════════════════════════════════
     HOME PAGE
  ════════════════════════════════════════════ */
  function renderHome(){
    const page=document.createElement('div');
    page.className='page home-page';
    let sq='';
    for(let i=0;i<64;i++){ const r=Math.floor(i/8),c=i%8; sq+=`<span class="${(r+c)%2===0?'lt':'dk'}"></span>`; }

    page.innerHTML=`
      <div class="home-hero">
        <h1 style="font-family: var(--font-mojangles);">Chess<em>au</em></h1>
        <p class="tagline">Modern lightweight chess analysis platform.</p>
        <div style="margin-top: 24px;">
          <button class="btn btn-gold" onclick="location.hash='play'">Play Now</button>
        </div>
      </div>
      <div class="home-content">
        <section class="home-about">
          <p>Chessau is a modern lightweight chess analysis platform featuring opening recognition, move review, accuracy tracking, and interactive gameplay.</p>
        </section>
        
        <section class="home-stats">
          <div class="stat-card"><strong>Engine</strong><br/>Stockfish 16</div>
          <div class="stat-card"><strong>Analysis</strong><br/>Real-time</div>
          <div class="stat-card"><strong>Theory</strong><br/>ECO Support</div>
          <div class="stat-card"><strong>Platform</strong><br/>Browser Native</div>
        </section>

        <section class="home-features">
          <div class="feature-card">
            <h4>Opening Recognition</h4>
            <p>Automatic ECO detection and opening book integration.</p>
          </div>
          <div class="feature-card">
            <h4>Move Review</h4>
            <p>Centipawn-loss analysis and brilliant/blunder categorization.</p>
          </div>
          <div class="feature-card">
            <h4>Accuracy Tracking</h4>
            <p>Post-game accuracy scoring powered by Stockfish evaluation.</p>
          </div>
          <div class="feature-card">
            <h4>Session Saves</h4>
            <p>Never lose your game with automatic lightweight session saves.</p>
          </div>
        </section>
      </div>`;
      
    return page;
  }

  /* ════════════════════════════════════════════
     PLAY PAGE (Game Mode Hub)
  ════════════════════════════════════════════ */
  function renderPlay(){
    const page=document.createElement('div');
    page.className='page home-page';
    let sq='';
    for(let i=0;i<64;i++){ const r=Math.floor(i/8),c=i%8; sq+=`<span class="${(r+c)%2===0?'lt':'dk'}"></span>`; }

    page.innerHTML=`
      <div class="home-hero">
        <h2 style="font-family: var(--font-mojangles); font-size: 2.4rem; color: var(--accent-lt); margin-bottom: 12px;">Select Game Mode</h2>
        <div class="mini-board">${sq}</div>
      </div>
      <div class="color-picker">
        <span class="color-picker-label">Play as</span>
        <button class="btn ${playerColor==='white'?'btn-gold':'btn-ghost'} cp-btn" data-color="white">♙ White</button>
        <button class="btn ${playerColor==='black'?'btn-gold':'btn-ghost'} cp-btn" data-color="black">♟ Black</button>
        <button class="btn ${playerColor==='random'?'btn-gold':'btn-ghost'} cp-btn" data-color="random">? Random</button>
        <button class="btn btn-ghost" id="home-settings-btn" style="margin-left:8px">⚙ Settings</button>
      </div>
      <div class="mode-grid">
        <div class="mode-card" data-mode="hvh">
          <span class="icon">♟♙</span>
          <h3>Human vs Human</h3>
          <p>Two players share the board locally.</p>
          <div class="mode-tags"><span class="tag">Move Review</span></div>
          <span class="arrow">↗</span>
        </div>
        <div class="mode-card" data-mode="stockfish">
          <span class="icon">♟♚</span>
          <h3>Human vs Stockfish</h3>
          <p>Play against Stockfish with opening theory + review</p>
          <div class="mode-tags"><span class="tag">Opening Book</span> <span class="tag">Analysis</span></div>
          <span class="arrow">↗</span>
        </div>
        <div class="mode-card" data-mode="engine">
          <span class="icon">♟⚙</span>
          <h3>Human vs My Engine</h3>
          <p>Alpha-beta + iterative deepening. Choose difficulty.</p>
          <div class="mode-tags"><span class="tag">Local AI</span> <span class="tag">Opening Book</span></div>
          <span class="arrow">↗</span>
        </div>
        <div class="mode-card disabled" data-mode="masterbot">
          <span class="icon">🚧</span>
          <h3>Master Bot</h3>
          <p>Custom depth-4 engine.</p>
          <div class="mode-tags"><span class="tag alert">Under Development</span></div>
        </div>
      </div>
      <div style="margin:18px auto 0;max-width:560px;padding:10px 16px;border-radius:8px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.09);font-size:.72rem;color:var(--text-muted);text-align:center;line-height:1.6;">
        ⚠️ <strong style="color:var(--accent-lt)">Beta — Single Session</strong> &nbsp;·&nbsp;
        This app runs one shared game server. Opening multiple tabs simultaneously will cause state conflicts. Each tab resets the board on load.
      </div>`;

    page.querySelectorAll('.cp-btn').forEach(btn=>{
      btn.addEventListener('click',()=>{
        playerColor=btn.dataset.color;
        page.querySelectorAll('.cp-btn').forEach(b=>{ b.className='btn btn-ghost cp-btn'; });
        btn.classList.replace('btn-ghost','btn-gold');
      });
    });
    page.querySelector('#home-settings-btn').addEventListener('click',showSettings);

    page.querySelectorAll('.mode-card:not(.disabled)').forEach(card=>{
      card.addEventListener('click',()=>{
        const mode=card.dataset.mode;
        currentMode=mode;
        if(mode==='engine'){
          showBotPicker();
        }else{
          selectedBot=null;
          const col=playerColor==='random'?(Math.random()<.5?'white':'black'):playerColor;
          navigate('game',col);
        }
      });
    });

    return page;
  }

  /* ════════════════════════════════════════════
     BOT PICKER
     Used from home (fresh game) AND from in-game settings (auto-reset).
     The `fromGame` flag controls which path we take after selection.
  ════════════════════════════════════════════ */
  function showBotPicker(fromGame){
    document.getElementById('bot-picker-overlay')?.remove();
    const ov=document.createElement('div');
    ov.id='bot-picker-overlay';
    ov.className='settings-overlay';
    ov.innerHTML=`
      <div class="settings-panel bot-picker-panel">
        <h3>${fromGame ? 'Change Difficulty' : 'Choose Your Opponent'}</h3>
        <div class="bot-grid">
          ${BOTS.map(b=>`
            <div class="bot-card${selectedBot?.id===b.id?' bot-selected':''}${b.disabled?' bot-disabled':''}" data-id="${b.id}">
              <div class="bot-avatar">${b.icon}</div>
              <div class="bot-info">
                <div class="bot-name">${b.name}</div>
                <div class="bot-depth">Depth ${b.depth}</div>
                <div class="bot-desc">${b.desc}</div>
                ${b.disabled ? '<div class="bot-soon-badge">🚧 Coming Soon</div>' : ''}
              </div>
            </div>`).join('')}
        </div>
        <div style="display:flex;gap:10px;margin-top:20px">
          <button class="btn btn-ghost" id="bot-cancel" style="flex:1">← Back</button>
          <button class="btn btn-gold"  id="bot-start"  style="flex:1" disabled>
            ${fromGame ? '↺ Apply & Reset' : 'Play →'}
          </button>
        </div>
      </div>
      </div>`;


    let chosen=selectedBot;
    ov.querySelectorAll('.bot-card').forEach(card=>{
      const botDef=BOTS.find(b=>b.id===card.dataset.id);
      // Disabled bots: intercept click, show coming-soon, do NOT select.
      if(botDef?.disabled){
        card.addEventListener('click',()=>_showComingSoon(botDef.name));
        return;
      }
      card.addEventListener('click',()=>{
        ov.querySelectorAll('.bot-card').forEach(c=>c.classList.remove('bot-selected'));
        card.classList.add('bot-selected');
        chosen=BOTS.find(b=>b.id===card.dataset.id);
        ov.querySelector('#bot-start').disabled=false;
      });
    });
    ov.querySelector('#bot-cancel').addEventListener('click',()=>ov.remove());
    ov.querySelector('#bot-start').addEventListener('click',async ()=>{
      if(!chosen) return;
      ov.remove();
      if(fromGame){
        // ── AUTO-RESET: difficulty changed while in a game ──
        await _applyDifficultyChange(chosen);
      } else {
        // ── NORMAL: starting fresh game ──
        selectedBot=chosen;
        const col=playerColor==='random'?(Math.random()<.5?'white':'black'):playerColor;
        navigate('game',col);
      }
    });
    ov.addEventListener('click',e=>{ if(e.target===ov) ov.remove(); });
    document.body.appendChild(ov);
  }

  /* ════════════════════════════════════════════
     MODE PICKER (in-game)
     Shown from the in-game settings panel when user wants to
     change game mode while a game is in progress.
  ════════════════════════════════════════════ */
  function showModePicker(){
    document.getElementById('mode-picker-overlay')?.remove();
    const ov=document.createElement('div');
    ov.id='mode-picker-overlay';
    ov.className='settings-overlay';
    ov.innerHTML=`
      <div class="settings-panel bot-picker-panel">
        <h3>Change Game Mode</h3>
        <div class="bot-grid" style="gap:8px">
          ${Object.entries(MODE_LABELS).map(([mode,label])=>`
            <div class="bot-card${currentMode===mode?' bot-selected':''}" data-mode="${mode}"
                 style="padding:10px 14px">
              <div class="bot-avatar" style="font-size:1.4rem">
                ${mode==='hvh'?'♟♙':mode==='stockfish'?'♟♚':'♟⚙'}
              </div>
              <div class="bot-info">
                <div class="bot-name">${label}</div>
              </div>
            </div>`).join('')}
        </div>
        <div style="display:flex;gap:10px;margin-top:20px">
          <button class="btn btn-ghost" id="mode-cancel" style="flex:1">← Back</button>
          <button class="btn btn-gold"  id="mode-apply"  style="flex:1" disabled>↺ Apply &amp; Reset</button>
        </div>
      </div>`;

    let chosenMode=currentMode;
    let chosenBot=selectedBot;

    ov.querySelectorAll('.bot-card').forEach(card=>{
      card.addEventListener('click',()=>{
        ov.querySelectorAll('.bot-card').forEach(c=>c.classList.remove('bot-selected'));
        card.classList.add('bot-selected');
        chosenMode=card.dataset.mode;
        ov.querySelector('#mode-apply').disabled=false;
      });
    });
    ov.querySelector('#mode-cancel').addEventListener('click',()=>ov.remove());
    ov.querySelector('#mode-apply').addEventListener('click',async ()=>{
      ov.remove();
      if(chosenMode==='engine' && currentMode!=='engine'){
        // Need to pick a bot too — chain into bot picker
        currentMode=chosenMode;
        showBotPicker(true);
      } else {
        // ── AUTO-RESET: mode changed while in a game ──
        chosenBot = chosenMode==='engine' ? selectedBot : null;
        await _applyModeChange(chosenMode, chosenBot);
      }
    });
    ov.addEventListener('click',e=>{ if(e.target===ov) ov.remove(); });
    document.body.appendChild(ov);
  }

  /* ════════════════════════════════════════════
     GAME PAGE
  ════════════════════════════════════════════ */
  function renderGame(resolvedColor){
    const page=document.createElement('div');
    page.className='page game-page';
    const label   = MODE_LABELS[currentMode]||'Chess';
    const oppName = currentMode==='engine'&&selectedBot
      ? `${selectedBot.icon} ${selectedBot.name}`
      : MODE_OPP[currentMode]||'Opponent';
    const youLabel= resolvedColor==='black'?'♟ You (Black)':'♙ You (White)';
    const topLabel= resolvedColor==='black'?youLabel:oppName;
    const botLabel= resolvedColor==='black'?oppName:youLabel;

    const bmHidden = cfg.bmMode==='none' ? ' hidden' : '';
    const bmTitle  = cfg.bmMode==='previous_move' ? 'Previous Best' : 'Best Move';

    page.innerHTML=`
      <div class="game-header">
        <span class="game-mode-badge">${label}${selectedBot?` · ${selectedBot.name}`:''}</span>
        <div class="game-actions">
          <button class="btn btn-ghost" id="btn-home">← Home</button>
          <button class="btn btn-ghost" id="btn-flip">⇅ Flip</button>
          <button class="btn btn-ghost" id="btn-fen" title="Copy FEN to clipboard">📋 FEN</button>
          <button class="btn btn-ghost" id="btn-settings">⚙ Settings</button>
          <button class="btn btn-danger" id="btn-resign">🏳 Resign</button>
          <button class="btn btn-gold"  id="btn-reset">↺ New Game</button>
        </div>
      </div>
      <div class="game-area">

        <!-- EVAL BARS — untouched -->
        <div class="eval-bars${cfg.evalBars?'':' hidden'}" id="eval-bars">
          <div class="eval-col${cfg.evalEng?'':' hidden'}" id="eval-col-eng">
            <span class="eval-tag">Eng</span>
            <div class="eval-bar-outer"><div class="eval-bar-white" id="eval-eng-fill" style="height:50%"></div></div>
            <span class="eval-score-val" id="eval-eng-val">0.0</span>
          </div>
          <div class="eval-col${cfg.evalSf?'':' hidden'}" id="eval-col-sf">
            <span class="eval-tag">SF</span>
            <div class="eval-bar-outer"><div class="eval-bar-white" id="eval-sf-fill" style="height:50%"></div></div>
            <span class="eval-score-val" id="eval-sf-val">—</span>
          </div>
        </div>

        <!-- BOARD COLUMN -->
        <div class="board-col">
          <div class="player-strip" id="black-strip">
            <span class="player-name"><span class="p-dot b" id="dot-b"></span>${topLabel}</span>
          </div>
          <div class="cap-strip" id="cap-top"></div>
          <div id="board-container"></div>
          <div class="cap-strip" id="cap-bot"></div>
          <div class="player-strip" id="white-strip">
            <span class="player-name"><span class="p-dot w" id="dot-w"></span>${botLabel}</span>
          </div>
          <div class="status-bar" id="status-bar"><span class="dot dot-t"></span>Initialising…</div>
        </div>

        <!-- SIDEBAR -->
        <div class="sidebar">

          <!-- Engine Info -->
          <div class="sb-box">
            <div class="sb-head">Engine Info</div>
            <div class="ei-row"><span class="ei-lbl">My Eval</span><span class="ei-val" id="ei-eng">0.0</span></div>
            <div class="ei-row"><span class="ei-lbl">SF Eval</span><span class="ei-val" id="ei-sf">—</span></div>
            <div class="ei-row"><span class="ei-lbl">Turn</span><span class="ei-val" id="ei-turn">White</span></div>
            <div class="ei-row"><span class="ei-lbl">Bot</span><span class="ei-val" id="ei-bot" style="font-size:.6rem;color:var(--text-muted)">${oppName}</span></div>
          </div>

          <!-- Undo/Redo -->
          <div class="undo-redo">
            <button class="btn btn-ghost" id="btn-undo" disabled style="width:100%">↩ Undo</button>
            <button class="btn btn-ghost" id="btn-redo" disabled style="width:100%">↪ Redo</button>
          </div>

          <!-- Best Move Panel (hidden when bmMode=none) -->
          <div class="sb-box${bmHidden}" id="bm-box">
            <div class="sb-head" id="bm-head">${bmTitle}</div>
            <div class="bm-panel" id="bm-panel"></div>
          </div>

          <!-- Opening Information -->
          <div class="sb-box hidden" id="opening-box">
            <div class="sb-head">📖 Opening</div>
            <div class="opening-content" id="opening-content"></div>
          </div>

          <!-- Saved Games -->
          <div class="sb-box" id="saved-games-box">
            <div class="sb-save-head">
              <span>📌 Saves</span>
              <button class="btn-save-sm" id="btn-quicksave">Save</button>
            </div>
            <div class="saved-games-list" id="saved-games-list"><div class="save-empty">No saves yet</div></div>
          </div>

          <!-- Move Analysis with Stockfish classification -->
          <div class="sb-box" id="move-list-box">
            <div class="sb-head">Move Analysis</div>
            <div class="history-scroll" id="hist-sf"></div>
          </div>

        </div>
      </div>`;
    return page;
  }

  /* ════════════════════════════════════════════
     SETTINGS PANEL
     Changes from previous version:
       + "Change Game Mode" button  → triggers showModePicker() + auto-reset
       + "Change Difficulty" button → triggers showBotPicker(true) + auto-reset
         (only shown in engine mode)
       - No "Best Move Hint" toggle
  ════════════════════════════════════════════ */
  function showSettings(){
    document.querySelector('.settings-overlay')?.remove();
    const ov=document.createElement('div'); ov.className='settings-overlay';

    function tog(id,val,label,sub){
      return `<div class="setting-row">
        <div><div class="setting-label">${label}</div><div class="setting-sub">${sub}</div></div>
        <label class="toggle"><input type="checkbox" id="${id}" ${val?'checked':''}/><span class="toggle-slider"></span></label>
      </div>`;
    }

    const bmOpts=[
      {v:'none',             l:'None'},
      {v:'current_position', l:'Current Position'},
      {v:'previous_move',    l:'Previous Move'},
    ].map(o=>`<option value="${o.v}"${cfg.bmMode===o.v?' selected':''}>${o.l}</option>`).join('');

    // Only show difficulty button in engine mode; always show mode button when in-game
    const inGame = _inGame();
    const diffBtn = (inGame && currentMode==='engine')
      ? `<div class="setting-row">
           <div><div class="setting-label">Difficulty</div>
                <div class="setting-sub">Current: ${selectedBot?.name||'Advanced'}</div></div>
           <button class="btn btn-ghost" id="tog-difficulty" style="font-size:.67rem;padding:5px 10px">Change ↺</button>
         </div>`
      : '';
    const modeBtn = inGame
      ? `<div class="setting-row">
           <div><div class="setting-label">Game Mode</div>
                <div class="setting-sub">Current: ${MODE_LABELS[currentMode]||currentMode}</div></div>
           <button class="btn btn-ghost" id="tog-mode" style="font-size:.67rem;padding:5px 10px">Change ↺</button>
         </div>`
      : '';

    ov.innerHTML=`
      <div class="settings-panel">
        <h3>⚙ Settings</h3>

        ${inGame ? `<div class="settings-section-label">Game Setup</div>${modeBtn}${diffBtn}` : ''}

        <div class="settings-section-label">Evaluation</div>
        ${tog('tog-eval',    cfg.evalBars, 'Eval Bars Visible',  'Show/hide the entire eval bar column')}
        ${tog('tog-eval-eng',cfg.evalEng,  'My Engine Eval Bar', 'Show My Engine centipawn bar')}
        ${tog('tog-eval-sf', cfg.evalSf,   'Stockfish Eval Bar', 'Show Stockfish centipawn bar')}

        <div class="settings-section-label">Best Move</div>
        <div class="setting-row">
          <div>
            <div class="setting-label">Display Mode</div>
            <div class="setting-sub">${Board.getMoveCount() > 0 ? '<span style="color:var(--accent-lt)">Locked after game starts</span>' : 'Highlights squares &amp; shows move text'}</div>
          </div>
          <select id="bm-mode-sel" class="settings-select" ${Board.getMoveCount() > 0 ? 'disabled' : ''}>
            ${bmOpts}
          </select>
        </div>

        <div class="settings-section-label">Game</div>
        ${tog('tog-snd', cfg.sound, 'Sound Effects',   'Move, capture &amp; check sounds')}
        ${tog('tog-undo',cfg.undo,  'Allow Undo/Redo', 'Enable take-back moves')}

        <button class="btn btn-gold settings-close" id="sclose">Done</button>
      </div>`;

    /* ── wire toggles ── */
    const wire=(id,key,fn)=>{
      const el=ov.querySelector(`#${id}`);
      if(!el) return;
      el.addEventListener('change',e=>{
        cfg[key]=e.target.checked; saveCfg(); if(fn) fn(cfg[key]);
      });
    };
    wire('tog-eval',    'evalBars', v=>Board.setEvalBars(v));
    wire('tog-eval-eng','evalEng',  v=>{ document.getElementById('eval-col-eng')?.classList.toggle('hidden',!v); });
    wire('tog-eval-sf', 'evalSf',   v=>{ document.getElementById('eval-col-sf') ?.classList.toggle('hidden',!v); });
    wire('tog-snd',     'sound',    v=>Board.setSoundEnabled(v));
    wire('tog-undo',    'undo',     v=>Board.setUndoEnabled(v));

    /* ── wire best move dropdown ── */
    const bmSel=ov.querySelector('#bm-mode-sel');
    if(bmSel){
      bmSel.addEventListener('change',()=>{
        cfg.bmMode=bmSel.value;
        saveCfg();
        _applyBmMode();
      });
    }

    /* ── wire "Change Difficulty" button (engine mode only, in-game) ── */
    const diffBtnEl=ov.querySelector('#tog-difficulty');
    if(diffBtnEl){
      diffBtnEl.addEventListener('click',()=>{
        ov.remove(); // close settings first
        showBotPicker(true); // fromGame=true → will auto-reset on confirm
      });
    }

    /* ── wire "Change Game Mode" button (any mode, in-game) ── */
    const modeBtnEl=ov.querySelector('#tog-mode');
    if(modeBtnEl){
      modeBtnEl.addEventListener('click',()=>{
        ov.remove();
        showModePicker(); // will auto-reset on confirm
      });
    }

    ov.querySelector('#sclose').addEventListener('click',()=>ov.remove());
    ov.addEventListener('click',e=>{ if(e.target===ov) ov.remove(); });
    document.body.appendChild(ov);
  }

  /** Sync the bm panel header and call Board.setBestMoveMode */
  function _applyBmMode(){
    const head=document.getElementById('bm-head');
    if(head){
      head.textContent = cfg.bmMode==='previous_move' ? 'Previous Best' : 'Best Move';
    }
    Board.setBestMoveMode(cfg.bmMode);
  }

  /** Show "Under Development" modal for a named bot difficulty. */
  function _showComingSoon(name){
    document.getElementById('coming-soon-overlay')?.remove();
    const ov=document.createElement('div');
    ov.id='coming-soon-overlay';
    ov.className='settings-overlay';
    ov.innerHTML=`
      <div class="settings-panel" style="max-width:300px;text-align:center;padding:32px 28px">
        <div style="font-size:2.4rem;margin-bottom:12px">🚧</div>
        <h3 style="margin-bottom:8px">${name} Bot</h3>
        <p style="color:var(--text-muted);font-size:.8rem;line-height:1.65;margin-bottom:18px">
          This difficulty is currently under development and will be available in a future update.
        </p>
        <p style="color:var(--accent-dk);font-size:.68rem;letter-spacing:.12em;text-transform:uppercase;margin-bottom:22px">
          Coming Soon
        </p>
        <button class="btn btn-gold" id="cs-close" style="width:100%;justify-content:center">Got it</button>
      </div>`;
    ov.querySelector('#cs-close').addEventListener('click',()=>ov.remove());
    ov.addEventListener('click',e=>{ if(e.target===ov) ov.remove(); });
    document.body.appendChild(ov);
  }

  /** Small toast notification — reuse Board's if available, else own impl */
  function _showToast(msg){
    document.querySelectorAll('.fen-toast').forEach(t=>t.remove());
    const t=document.createElement('div');
    t.className='fen-toast'; t.textContent=msg;
    document.body.appendChild(t);
    setTimeout(()=>t.remove(),2500);
  }

  /* ════════════════════════════════════════════
     INFO PAGE
  ════════════════════════════════════════════ */
  
  /* ════════════════════════════════════════════
     SAVED GAMES PAGE
  ════════════════════════════════════════════ */
  function _deleteSave(id) {
    let saves = _getSaves();
    saves = saves.filter(s => s.id !== id);
    sessionStorage.setItem('chessau_saves', JSON.stringify(saves));
    if (location.hash === '#saves') {
      const app=document.getElementById('app');
      app.querySelectorAll('.page').forEach(el=>el.remove());
      app.appendChild(renderSavesPage());
    }
    _renderSavedGames();
  }

  function renderSavesPage(){
    const page=document.createElement('div');
    page.className='page home-page';
    
    const saves = _getSaves();
    const hasSaves = saves.length > 0;
    
    page.innerHTML=`
      <div class="home-hero">
        <h2 style="font-family: var(--font-mojangles); font-size: 2.4rem; color: var(--accent-lt); margin-bottom: 12px;">Saved Games</h2>
        <p class="tagline">Manage your session saves</p>
      </div>
      <div class="home-content" style="max-width:800px; width:100%; margin: 20px auto; padding: 0 24px;"></div>
    `;

    const content = page.querySelector('.home-content');
    if(hasSaves) {
      const controls = document.createElement('div');
      controls.style.cssText = 'display:flex;justify-content:flex-end;margin-bottom:16px;';

      const clearBtn = document.createElement('button');
      clearBtn.className = 'btn btn-clear-sm';
      clearBtn.id = 'page-clear-saves';
      clearBtn.style.cssText = 'padding: 6px 12px;';
      clearBtn.textContent = 'Clear All Saves';
      controls.appendChild(clearBtn);
      content.appendChild(controls);

      const grid = document.createElement('div');
      grid.className = 'saves-grid';
      grid.style.cssText = 'display:flex; flex-direction:column; gap:12px;';
      content.appendChild(grid);

      for(const s of saves){
        const card = document.createElement('div');
        card.className = 'feature-card';
        card.style.cssText = 'display:flex; justify-content:space-between; align-items:center;';

        const info = document.createElement('div');
        const title = document.createElement('h4');
        title.style.cssText = 'margin-bottom:4px; font-size:1.1rem; color:var(--accent-lt);';
        title.textContent = MODE_LABELS[s.mode] || s.mode || 'Saved Game';
        info.appendChild(title);

        const ts = document.createElement('p');
        ts.style.cssText = 'font-family:var(--font-mono); font-size:0.75rem; margin-bottom:4px; color:var(--text-muted);';
        ts.textContent = s.ts || '';
        info.appendChild(ts);

        if(s.opening){
          const opening = document.createElement('p');
          opening.style.cssText = 'color:var(--accent-dk); font-style:italic; font-size:0.8rem; margin-bottom:4px;';
          opening.textContent = s.opening;
          info.appendChild(opening);
        }

        const moveCount = Array.isArray(s.moves) ? s.moves.length : 0;
        const meta = document.createElement('p');
        meta.style.cssText = 'font-size:0.85rem; color:var(--text);';
        meta.textContent = `${moveCount} moves played`;
        info.appendChild(meta);
        card.appendChild(info);

        const actions = document.createElement('div');
        actions.style.cssText = 'display:flex; flex-direction:column; gap:8px;';

        const isGameActive = (Board.getMoveCount() > 0);
        const restoreBtn = document.createElement('button');
        restoreBtn.className = 'btn btn-gold btn-restore';
        restoreBtn.dataset.id = String(s.id ?? '');
        restoreBtn.style.cssText = 'padding:4px 12px; font-size:0.7rem;';
        restoreBtn.textContent = 'Restore';
        if(isGameActive){
          restoreBtn.disabled = true;
          restoreBtn.style.opacity = '0.5';
          restoreBtn.title = "Finish current game to restore this one";
        } else {
          restoreBtn.addEventListener('click', () => _restoreGame(restoreBtn.dataset.id));
        }
        actions.appendChild(restoreBtn);

        const deleteBtn = document.createElement('button');
        deleteBtn.className = 'btn btn-clear-sm btn-delete';
        deleteBtn.dataset.id = String(s.id ?? '');
        deleteBtn.style.cssText = 'padding:4px 12px; font-size:0.7rem;';
        deleteBtn.textContent = 'Delete';
        deleteBtn.addEventListener('click', () => _deleteSave(deleteBtn.dataset.id));
        actions.appendChild(deleteBtn);

        card.appendChild(actions);
        grid.appendChild(card);
      }

      clearBtn.addEventListener('click', () => {
         _clearSaves();
         if (location.hash === '#saves') {
           const app=document.getElementById('app');
           app.querySelectorAll('.page').forEach(el=>el.remove());
           app.appendChild(renderSavesPage());
         }
      });
    } else {
      const empty = document.createElement('div');
      empty.className = 'feature-card';
      empty.style.cssText = 'text-align:center; padding: 40px;';

      const h = document.createElement('h4');
      h.style.cssText = 'color:var(--accent-lt); font-size:1.2rem;';
      h.textContent = 'No Saves Found';
      empty.appendChild(h);

      const p = document.createElement('p');
      p.style.marginBottom = '20px';
      p.textContent = "You haven't saved any games in this session.";
      empty.appendChild(p);

      const play = document.createElement('button');
      play.className = 'btn btn-gold';
      play.textContent = 'Play a Game';
      play.addEventListener('click', () => { location.hash = 'play'; });
      empty.appendChild(play);

      content.appendChild(empty);
    }

    return page;
  }

  function renderInfo(){
    const page=document.createElement('div');
    page.className='page info-page';
    page.innerHTML=`
      <h2>About Chessau</h2>
      <p class="sub">Chessau — Built from scratch using Flask &amp; Stockfish</p>
      <div class="info-sec">
        <h3>About</h3>
        <ul class="feat-list">
          <li>Chessau is a chess engine web application built using Flask and Stockfish.</li>
          <li>It allows users to play chess with real-time engine analysis.</li>
        </ul>
      </div>
      <div class="info-sec">
        <h3>Features</h3>
        <ul class="feat-list">
          <li>Full legal move generation — castling, en passant, pawn promotion</li>
          <li>Alpha-beta pruning with iterative deepening</li>
          <li>Zobrist hashing &amp; transposition table</li>
          <li>Killer move &amp; history heuristics, quiescence search</li>
          <li>Piece-Square Tables (PST) positional evaluation</li>
          <li>Dual eval bars — My Engine + Stockfish centipawns</li>
          <li>Stockfish move analysis — Brilliant / Best / Excellent / Good / Inaccuracy / Mistake / Blunder</li>
          <li>Best Move Display — None / Current Position / Previous Move (with board highlights)</li>
          <li>Auto-reset on difficulty or game mode change</li>
          <li>Multiple bot difficulty levels (Beginner → Master)</li>
          <li>Draw detection — 50-move rule, stalemate, insufficient material, threefold repetition</li>
          <li>Copy FEN to clipboard button</li>
          <li>Undo / Redo with full state restoration</li>
          <li>Captured pieces + material advantage counter</li>
          <li>Drag-and-drop + click-to-move (touch &amp; mouse)</li>
          <li>Sound effects, board flip, play as black/white/random</li>
        </ul>
      </div>
      <div class="info-sec">
        <h3>Technologies</h3>
        <div class="tech-grid">
          <span class="tech-tag">Python 3</span><span class="tech-tag">Flask</span>
          <span class="tech-tag">Vanilla JS</span><span class="tech-tag">HTML5</span>
          <span class="tech-tag">CSS3</span><span class="tech-tag">Stockfish UCI</span>
          <span class="tech-tag">Zobrist Hashing</span><span class="tech-tag">Alpha-Beta</span>
          <span class="tech-tag">Iterative Deepening</span>
        </div>
      </div>
      <div class="info-sec">
        <h3>Developer</h3>
        <div class="author-card">
          <div class="author-av">S</div>
            <h4>Sujal Ajit Ushir</h4>
            <p style="font-weight:600;margin-bottom:4px">IIIT Kottayam</p>
            <div class="info-visitor-stat">👁 Chessau Visitors: <span id="info-vc-val">Global</span></div>
            <p>Full-stack chess — hand-crafted Python engine, Flask REST API, responsive SPA frontend.</p>
          </div>
        </div>
      </div>
      <button class="btn btn-gold" id="info-back" style="margin-top:18px">← Back to Home</button>`;
    page.querySelector('#info-back').addEventListener('click',()=>navigate('home'));
    return page;
  }

  /* ════════════════════════════════════════════
     ROUTER
  ════════════════════════════════════════════ */
  let _resolvedColor='white';

  function navigate(toPage,color){
    const leaving=location.hash.slice(1)||'home';
    // Reset when leaving game (clean up)
    if(leaving==='game'&&toPage!=='game'){
      fetch('/reset',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).catch(()=>{});
    }
    if(color) _resolvedColor=color;
    location.hash=toPage;
  }

  function handleRoute(){
    const hash=location.hash.slice(1)||'home';
    const app=document.getElementById('app');
    app.querySelectorAll('.page').forEach(el=>el.remove());
    renderNav();

    switch(hash){
      case 'game':{
        document.getElementById('global-grid-bg')?.classList.add('dim-grid');
        if(!currentMode){ navigate('home'); return; }
        const page=renderGame(_resolvedColor);
        app.appendChild(page);

        requestAnimationFrame(async ()=>{
          const skipReset = !!_pendingRestore;
          await Board.init({
            container:   document.getElementById('board-container'),
            statusEl:    document.getElementById('status-bar'),
            evalEngFill: document.getElementById('eval-eng-fill'),
            evalSfFill:  document.getElementById('eval-sf-fill'),
            evalEngVal:  document.getElementById('eval-eng-val'),
            evalSfVal:   document.getElementById('eval-sf-val'),
            capTop:      document.getElementById('cap-top'),
            capBot:      document.getElementById('cap-bot'),
            histSf:      document.getElementById('hist-sf'),
            undoBtn:     document.getElementById('btn-undo'),
            redoBtn:     document.getElementById('btn-redo'),
            blackName:   document.getElementById('black-strip'),
            whiteName:   document.getElementById('white-strip'),
            evalBarsEl:  document.getElementById('eval-bars'),
            fenBtn:      document.getElementById('btn-fen'),
            bmPanel:     document.getElementById('bm-panel'),
            openingBoxEl: document.getElementById('opening-box'),
            openingEl:   document.getElementById('opening-content'),
            playerColor: _resolvedColor,
            engineDepth: selectedBot?.depth || 3,
            skipReset
          });
          
          if(_pendingRestore){
            Board.applyRestoredState(_pendingRestore.data, _pendingRestore.moves);
            _pendingRestore = null;
          }

          /* apply settings */
          Board.setEvalBars(cfg.evalBars);
          Board.setSoundEnabled(cfg.sound);
          Board.setUndoEnabled(cfg.undo);
          Board.setBestMoveMode(cfg.bmMode);

          document.getElementById('eval-col-eng')?.classList.toggle('hidden',!cfg.evalEng);
          document.getElementById('eval-col-sf') ?.classList.toggle('hidden',!cfg.evalSf);

          /* wire game buttons */
          document.getElementById('btn-reset')   .addEventListener('click', ()=>Board.resetGame());
          document.getElementById('btn-flip')    .addEventListener('click', ()=>Board.flipBoard());
          document.getElementById('btn-settings').addEventListener('click', showSettings);
          document.getElementById('btn-home')    .addEventListener('click', ()=>navigate('home'));
          document.getElementById('btn-resign')  .addEventListener('click', ()=>Board.resign(currentMode));
          document.getElementById('btn-quicksave')?.addEventListener('click', _quickSave);
          document.getElementById('btn-clearsaves')?.addEventListener('click', _clearSaves);
          _renderSavedGames();

          /* keep Engine Info sidebar in sync */
          const eiEng=document.getElementById('ei-eng');
          const eiSf =document.getElementById('ei-sf');
          const eiTrn=document.getElementById('ei-turn');
          const evEng=document.getElementById('eval-eng-val');
          const evSf =document.getElementById('eval-sf-val');
          const stEl =document.getElementById('status-bar');
          if(evEng) new MutationObserver(()=>{ if(eiEng) eiEng.textContent=evEng.textContent||'0.0'; })
            .observe(evEng,{childList:true,characterData:true,subtree:true});
          if(evSf)  new MutationObserver(()=>{ if(eiSf)  eiSf.textContent =evSf.textContent||'—'; })
            .observe(evSf,{childList:true,characterData:true,subtree:true});
          if(stEl)  new MutationObserver(()=>{
            const t=(stEl.textContent||'').trim();
            if(!eiTrn) return;
            if(t.includes('White'))                              eiTrn.textContent='White';
            else if(t.includes('Black'))                         eiTrn.textContent='Black';
            else if(t.includes('think')||t.includes('Playing'))  eiTrn.textContent='…';
          }).observe(stEl,{childList:true,characterData:true,subtree:true});
        });
        break;
      }
      case 'saves':
        document.getElementById('global-grid-bg')?.classList.add('dim-grid');
        app.appendChild(renderSavesPage()); break;
      case 'info': 
        document.getElementById('global-grid-bg')?.classList.add('dim-grid');
        app.appendChild(renderInfo()); break;
      case 'play':
        document.getElementById('global-grid-bg')?.classList.add('dim-grid');
        app.appendChild(renderPlay()); break;
      case 'home': default: 
        document.getElementById('global-grid-bg')?.classList.remove('dim-grid');
        app.appendChild(renderHome()); break;
    }
  }

  function init(){
    // Setup global background grid
    const grid = document.createElement('div');
    grid.className = 'global-grid-bg';
    grid.id = 'global-grid-bg';
    document.body.insertBefore(grid, document.body.firstChild);
    
    const cellSize = 30;
    const renderGrid = () => {
      const w = window.innerWidth;
      const h = window.innerHeight;
      const cols = Math.ceil(w / cellSize) + 1;
      const rows = Math.ceil(h / cellSize) + 1;
      const totalCells = cols * rows;
      const frag = document.createDocumentFragment();
      for(let i = 0; i < totalCells; i++) {
        const cell = document.createElement('div');
        cell.className = 'grid-cell';
        frag.appendChild(cell);
      }
      grid.innerHTML = '';
      grid.appendChild(frag);
    };
    renderGrid();
    let resizeTimer;
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(renderGrid, 150);
    });

    window.addEventListener('hashchange',handleRoute);
    handleRoute();
    _renderFooter();
    _updateVisitorCount();
  }

  function _renderFooter(){
    const f=document.createElement('footer');
    f.className='global-footer';
    f.innerHTML=`
      <div>&copy; 2026 Chessau · Built for speed and accuracy</div>
      <div class="visitor-counter" id="footer-visitor-count">
        <i>👁</i> <span id="vc-val">Visitors Online</span>
      </div>`;
    document.body.appendChild(f);
  }

  async function _updateVisitorCount(){
    const footerEl = document.getElementById('vc-val');
    const infoEl = document.getElementById('info-vc-val');
    
    const setFallback = () => {
      if(footerEl) footerEl.textContent = 'Visitors Online';
      if(infoEl) infoEl.textContent = 'Global';
    };

    try {
      // Fetch total visitor count for the root path (/) from GoatCounter's public counter API
      // Root cause for 403: Dashboard > Settings > Site settings > Allow using the visitor counter
      const res = await fetch('https://chessau.goatcounter.com/counter/%2F.json');
      if(res.ok){
        const data = await res.json();
        const raw = data.count;
        if(raw !== undefined && raw !== null) {
          let num = parseInt(raw.toString().replace(/,/g, ''));
          let display;
          if(isNaN(num)) {
            display = raw;
          } else if(num >= 1000000) {
            display = (num/1000000).toFixed(1) + 'M';
          } else if(num >= 1000) {
            display = (num/1000).toFixed(1) + 'k';
          } else {
            display = num.toString();
          }
          if(footerEl) footerEl.textContent = `${display} Visitors`;
          if(infoEl) infoEl.textContent = display;
          return;
        }
      }
      setFallback();
    } catch(e) {
      setFallback();
    }
  }

  /* ════════════════════════════════════════════
     SESSION SAVE SYSTEM
     Stores game snapshots in sessionStorage (gone on refresh).
  ════════════════════════════════════════════ */
  const SAVE_KEY = 'chessau_saves';

  function _quickSave(stats){
    const moves = Board.getMoves();
    const opening = document.getElementById('opening-content')
      ?.querySelector('.ob-name')?.textContent?.trim() || '';
    const now = new Date();
    const ts = now.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
    const MODE_SHORT = {hvh:'HvH', stockfish:'vs SF', engine:'vs Eng'};
    const save = {
      id: Date.now().toString(),
      ts,
      mode: currentMode,
      botId: selectedBot?.id || null,
      playerColor: _resolvedColor,
      modeName: MODE_LABELS[currentMode]||currentMode,
      modeShort: MODE_SHORT[currentMode]||currentMode,
      moves,
      opening,
      accuracy: stats ? stats.accuracy : null
    };
    try{
      const saves = _getSaves();
      saves.unshift(save);
      if(saves.length>5) saves.pop();
      sessionStorage.setItem(SAVE_KEY, JSON.stringify(saves));
      _renderSavedGames();
      _showToast('📌 Game saved!');
    }catch(e){ _showToast('Save failed'); }
  }

  function _getSaves(){
    try{ return JSON.parse(sessionStorage.getItem(SAVE_KEY)||'[]'); }catch{ return []; }
  }

  function _clearSaves() {
    sessionStorage.removeItem('chessau_saves');
    _renderSavedGames();
  }

  function _renderSavedGames(){
    const el = document.getElementById('saved-games-list');
    if(!el) return;
    const saves = _getSaves();
    el.replaceChildren();
    if(!saves.length){
      const empty = document.createElement('div');
      empty.className = 'save-empty';
      empty.textContent = 'No saves yet';
      el.appendChild(empty);
      return;
    }
    const isGameActive = (Board.getMoveCount() > 0);
    for(const s of saves){
      const item = document.createElement('div');
      item.className = 'save-item' + (isGameActive ? ' disabled' : '');
      item.dataset.id = String(s.id ?? '');
      if(isGameActive) item.title = "Finish current game to restore";

      const mode = document.createElement('div');
      mode.className = 'save-mode';
      mode.textContent = `${s.modeShort || ''} · ${s.ts || ''}`;
      item.appendChild(mode);

      if(s.opening){
        const opening = document.createElement('div');
        opening.className = 'save-opening';
        opening.textContent = s.opening;
        item.appendChild(opening);
      }

      const moveCount = Array.isArray(s.moves) ? s.moves.length : 0;
      const meta = document.createElement('div');
      meta.className = 'save-meta';
      meta.textContent = `${moveCount} moves${s.accuracy ? ` · ${s.accuracy}% acc` : ''}`;
      item.appendChild(meta);

      if(!isGameActive) {
        item.addEventListener('click',()=>_restoreGame(item.dataset.id));
      }
      el.appendChild(item);
    }
  }

  async function _restoreGame(id){
    const save = _getSaves().find(s=>s.id===id);
    if(!save) return;
    try{
      const res = await fetch('/load_position',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({moves:save.moves}),
      });
      if(!res.ok) throw new Error(await res.text());
      const data = await res.json();
      
      // Restore App state
      currentMode = save.mode || 'hvh';
      selectedBot = BOTS.find(b => b.id === save.botId) || null;
      _resolvedColor = save.playerColor || 'white';
      
      // Setup pending restore data for Board.init
      _pendingRestore = { data, moves: save.moves };
      
      // Transition to game view
      navigate('game');
    }catch(e){ _showToast('Restore failed: '+e.message); }
  }

  return { getMode:()=>currentMode, navigate, init, quickSave: _quickSave };
})();

window.App=App;
document.addEventListener('DOMContentLoaded',App.init);
