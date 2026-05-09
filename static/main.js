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
      blackStrip.innerHTML=`<span class="p-dot b" id="dot-b"></span>${topLabel}`;
    }
    if(whiteStrip){
      const botLabel = _resolvedColor==='black' ? oppName : youLabel;
      whiteStrip.innerHTML=`<span class="p-dot w" id="dot-w"></span>${botLabel}`;
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
      <div class="nav-logo" id="nav-logo">Chess<span>Engine</span></div>
      <ul class="nav-links">
        <li><a href="#home" class="${hash==='home'?'active':''}">Home</a></li>
        <li><a href="#game" class="${hash==='game'?'active':''}">Play</a></li>
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
        <h1>Chess<br/><em>Engine</em></h1>
        <div class="mini-board">${sq}</div>
        <p class="tagline">Choose your opponent &amp; colour</p>
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
          <span class="arrow">↗</span>
        </div>
        <div class="mode-card" data-mode="stockfish">
          <span class="icon">♟♚</span>
          <h3>Human vs Stockfish</h3>
          <p>Challenge the world-class Stockfish engine.</p>
          <span class="arrow">↗</span>
        </div>
        <div class="mode-card" data-mode="engine">
          <span class="icon">♟⚙</span>
          <h3>Human vs My Engine</h3>
          <p>Alpha-beta + iterative deepening. Choose difficulty.</p>
          <span class="arrow">↗</span>
        </div>
      </div>`;

    page.querySelectorAll('.cp-btn').forEach(btn=>{
      btn.addEventListener('click',()=>{
        playerColor=btn.dataset.color;
        page.querySelectorAll('.cp-btn').forEach(b=>{ b.className='btn btn-ghost cp-btn'; });
        btn.classList.replace('btn-ghost','btn-gold');
      });
    });
    page.querySelector('#home-settings-btn').addEventListener('click',showSettings);

    page.querySelectorAll('.mode-card').forEach(card=>{
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
            <button class="btn btn-ghost" id="btn-undo" disabled>↩ Undo</button>
            <button class="btn btn-ghost" id="btn-redo" disabled>↪ Redo</button>
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
            <div class="setting-sub">Highlights squares &amp; shows move text</div>
          </div>
          <select id="bm-mode-sel" class="settings-select">
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
  function renderInfo(){
    const page=document.createElement('div');
    page.className='page info-page';
    page.innerHTML=`
      <h2>About This Project</h2>
      <p class="sub">Chess Engine — Built from scratch using Flask &amp; Stockfish</p>
      <div class="info-sec">
        <h3>About</h3>
        <ul class="feat-list">
          <li>This is a chess engine web application built using Flask and Stockfish.</li>
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
          <div class="author-info">
            <h4>Sujal Ajit Ushir</h4>
            <p style="font-weight:600;margin-bottom:4px">IIIT Kottayam</p>
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
        if(!currentMode){ navigate('home'); return; }
        const page=renderGame(_resolvedColor);
        app.appendChild(page);

        requestAnimationFrame(()=>{
          Board.init({
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
          });

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
      case 'info': app.appendChild(renderInfo()); break;
      case 'home': default: app.appendChild(renderHome()); break;
    }
  }

  function init(){
    window.addEventListener('hashchange',handleRoute);
    handleRoute();
  }

  return { getMode:()=>currentMode, navigate, init };
})();

window.App=App;
document.addEventListener('DOMContentLoaded',App.init);