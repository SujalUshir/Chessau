/* ============================================================
   static/board.js
   - Eval bars preserved (untouched)
   - Move Review: Stockfish-based, delta = abs(best_eval - played_eval)
     Classifications: Book / Best / Excellent / Good / Inaccuracy / Mistake / Blunder
   - Best Move Display: 'none' | 'current_position' | 'previous_move'
     * Dropdown controls board highlights + sidebar text
   ============================================================ */
'use strict';

const Board = (() => {

  /* ── piece maps ── */
  const IMG = {
    K:'wk.png',Q:'wq.png',R:'wr.png',B:'wb.png',N:'wn.png',P:'wp.png',
    k:'bk.png',q:'bq.png',r:'br.png',b:'bb.png',n:'bn.png',p:'bp.png'
  };
  const NAME = {
    K:'King',Q:'Queen',R:'Rook',B:'Bishop',N:'Knight',P:'Pawn',
    k:'King',q:'Queen',r:'Rook',b:'Bishop',n:'Knight',p:'Pawn'
  };
  const VAL = {K:0,Q:9,R:5,B:3,N:3,P:1,k:0,q:9,r:5,b:3,n:3,p:1};

  function pUrl(c){ return `${window.STATIC_URL}${IMG[c]}`; }

  /* ════════════════════════════════════════════
     SOUND
  ════════════════════════════════════════════ */
  const SND_URLS = {};
  let _soundOn = true;
  function _initSounds(){
    ['move','capture','check'].forEach(n=>{ SND_URLS[n]=`/sounds/${n}.wav`; });
  }
  function playSound(name){
    if(!_soundOn) return;
    const url=SND_URLS[name]; if(!url) return;
    const a=new Audio(url); a.volume=0.75;
    a.play().catch(()=>{});
  }

  /* ════════════════════════════════════════════
     MOVE CLASSIFICATION
     Uses server-computed review data when available.
     Falls back to client-side delta computation.

     Server provides:
       review.eval_before_cp  — SF eval before move (white-positive cp)
       review.eval_after_cp   — SF eval after played move (white-positive cp)
       review.best_eval_cp    — SF eval after best move (white-positive cp)
       review.classification  — pre-computed label from server
       review.best            — best move UCI string

     Thresholds (centipawns):
       delta = abs(best_eval_cp - eval_after_cp), from moving player's view
       0   – 20  → Best
       20  – 50  → Excellent
       50  – 100 → Good
       100 – 200 → Inaccuracy
       200 – 400 → Mistake
       400+      → Blunder
       (Book move overrides all if server says so)
  ════════════════════════════════════════════ */
  const CLASS = {
    BRILLIANT:  { label:'Brilliant',  sym:'💎', cls:'cl-brilliant'  },
    BOOK:       { label:'Book',       sym:'📖', cls:'cl-book'       },
    BEST:       { label:'Best',       sym:'!!', cls:'cl-best'       },
    EXCELLENT:  { label:'Excellent',  sym:'!',  cls:'cl-excellent'  },
    GOOD:       { label:'Good',       sym:'✓',  cls:'cl-good'       },
    INACCURACY: { label:'Inaccuracy', sym:'?!', cls:'cl-inaccuracy' },
    MISTAKE:    { label:'Mistake',    sym:'?',  cls:'cl-mistake'    },
    BLUNDER:    { label:'Blunder',    sym:'??', cls:'cl-blunder'    },
  };

  /**
   * Resolve a classification label string (from server) to a CLASS entry.
   */
  function _resolveClass(label){
    if(!label) return null;
    const k = label.toUpperCase();
    return CLASS[k] || null;
  }

  /**
   * Client-side fallback: classify from raw cp values.
   * delta = abs(best_eval_cp - eval_after_cp) from moving player's perspective.
   */
  function _classifyFromCp(evalBeforeCp, evalAfterCp, bestEvalCp, movingColor){
    if(bestEvalCp == null || evalAfterCp == null) return null;

    // Convert to moving-player-positive
    const sign = (movingColor === 'white') ? 1 : -1;
    const played_val = sign * evalAfterCp;
    const best_val   = sign * bestEvalCp;

    const delta = best_val - played_val;  // positive = played was worse than best

    if(delta <= 0)   return CLASS.BEST;
    if(delta <= 20)  return CLASS.BEST;
    if(delta <= 50)  return CLASS.EXCELLENT;
    if(delta <= 100) return CLASS.GOOD;
    if(delta <= 300) return CLASS.INACCURACY;  // matches server threshold
    if(delta <= 700) return CLASS.MISTAKE;     // matches server threshold
    return CLASS.BLUNDER;
  }

  /**
   * Resolve classification from a review object (returned by server).
   * Uses server label first; falls back to client cp computation.
   */
  function _classifyFromReview(review){
    if(!review) return null;
    // Server label takes priority
    if(review.classification){
      return _resolveClass(review.classification);
    }
    // Fallback: compute from raw cp
    return _classifyFromCp(
      review.eval_before_cp,
      review.eval_after_cp,
      review.best_eval_cp,
      review.moving_color || 'white'
    );
  }

  /* ════════════════════════════════════════════
     STATE
  ════════════════════════════════════════════ */
  let board        = null;
  let turn         = 'white';
  let selected     = null;
  let legal        = [];
  let lastMove     = null;
  let capByW       = [];
  let capByB       = [];
  let gameOver     = false;
  let promoWait    = null;
  // Move list: parallel arrays
  //   halfMoves[i]  : UCI string e.g. "e2e4"
  //   reviewData[i] : review object from server or null
  let halfMoves    = [];
  let reviewData   = [];
  let _gameResult  = null;   // '1-0' | '0-1' | '1/2-1/2' — set when game ends
  let flipped      = false;
  let playerColor  = 'white';
  let _engineDepth = 3;
  let _undoEnabled = true;

  /* ── Best Move Display + Board Highlight ──
   *  'none'             — nothing shown, no highlights
   *  'current_position' — show best move for current board; highlight squares
   *  'previous_move'    — after a move show what the best move WAS; highlight
   */
  let _bmMode        = 'none';
  let _bmPending     = false;
  let $bmPanel       = null;

  /* opening book UX state */
  let _inBook   = false;   // true while engine is playing book moves
  let _leftBook = false;   // true once we've already shown "Out of Book" this game

  let _hintFrom      = null;
  let _hintTo        = null;
  let _prevBestFrom  = null;
  let _prevBestTo    = null;
  let _hintPending   = false;

  /* drag */
  let dragActive=false, dragFrom=null, dragEl=null, _dox=0, _doy=0;

  /* ════════════════════════════════════════════
     EVAL BAR SMOOTHING — module scope so state
     persists across applyState / _refreshEval calls
  ════════════════════════════════════════════ */
  let _prevEngineCp = null;
  let _prevSfCp     = null;

  function _smoothEval(prev, next) {
    // Smoothing removed — returns the raw value immediately.
    // The 0.8/0.2 weighted average was masking real eval changes and
    // caused the bar to show the wrong side as winning after undo/redo.
    return next;
  }

  function _applyBars(data) {
    const rawE = data.eval_engine ?? null;
    const rawS = data.eval_sf     ?? null;
    // No smoothing — draw bars with the exact values returned by the server.
    _drawBar($eFill, $eVal, rawE);
    _drawBar($sFill, $sVal, rawS);
  }

  /* DOM refs */
  let $board, $status, $eFill, $sFill, $eVal, $sVal,
      $capTop, $capBot,
      $histSf,
      $undo, $redo,
      $blackName, $whiteName,
      $evalBarsEl,
      $fenBtn,
      $openingEl;   // opening name banner

  /* ── notation ── */
  const i2n = (r,c) => String.fromCharCode(97+c)+(8-r);
  const n2i = s     => ({row:8-parseInt(s[1]), col:s.charCodeAt(0)-97});

  /* ── API ── */
  async function GET(p){
    const r=await fetch(p); if(!r.ok) throw new Error(await r.text()); return r.json();
  }
  async function POST(p,b){
    const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
    if(!r.ok) throw new Error(await r.text()); return r.json();
  }

  function setStatus(cls,msg){
    if(!$status) return;
    $status.innerHTML=`<span class="dot ${cls}"></span>${msg}`;
  }

  /* ════════════════════════════════════════════
     APPLY STATE
  ════════════════════════════════════════════ */
  function applyState(data){
    board = data.board;
    turn  = data.current_turn || 'white';
    window._chkSq = null;

    if(data.status==='check'){
      const k=turn==='white'?'K':'k';
      outer: for(let r=0;r<8;r++)
        for(let c=0;c<8;c++)
          if(board[r][c]===k){ window._chkSq=i2n(r,c); break outer; }
    }

    // Update eval bars using module-scoped _applyBars (keeps smoothing state)
    _applyBars(data);
    _updateUndoRedo(data.can_undo,data.can_redo);
    render();
    _updateDots();
  }


  /* ════════════════════════════════════════════
     RENDER BOARD
  ════════════════════════════════════════════ */
  function render(){
    if(!$board||!board) return;
    $board.innerHTML='';

    for(let vr=0;vr<8;vr++){
      for(let vc=0;vc<8;vc++){
        const r=flipped?7-vr:vr;
        const c=flipped?7-vc:vc;
        const not=i2n(r,c);
        const sq=document.createElement('div');
        sq.className=`square ${(r+c)%2===0?'light':'dark'}`;
        sq.dataset.square=not; sq.dataset.r=r; sq.dataset.c=c;

        if(lastMove){
          if(not===lastMove.from) sq.classList.add('last-from');
          if(not===lastMove.to)   sq.classList.add('last-to');
        }

        if(_hintFrom && _hintTo){
          if(not===_hintFrom) sq.classList.add('hint-from');
          if(not===_hintTo)   sq.classList.add('hint-to');
        }

        if(selected&&r===selected.row&&c===selected.col) sq.classList.add('selected');
        if(legal.includes(not)) sq.classList.add(board[r][c]!=='.'?'legal-capture':'legal-move');
        if(window._chkSq&&not===window._chkSq) sq.classList.add('in-check');

        if(vc===0){
          const sp=document.createElement('span');
          sp.className='crd rank';
          sp.textContent=flipped?(r+1):(8-r);
          sq.appendChild(sp);
        }
        if(vr===7){
          const sp=document.createElement('span');
          sp.className='crd file';
          sp.textContent=String.fromCharCode(97+(flipped?7-c:c));
          sq.appendChild(sp);
        }

        const p=board[r][c];
        if(p&&p!=='.'){
          const img=document.createElement('img');
          img.src=pUrl(p); img.alt=NAME[p]||p;
          img.className='piece-img'; img.draggable=false;
          sq.appendChild(img);
        }

        sq.addEventListener('click',()=>{ if(!dragActive) onClickSq(r,c,not); });
        sq.addEventListener('pointerdown',e=>onDragStart(e,r,c,not));
        $board.appendChild(sq);
      }
    }
    _updateCaptures();
  }

  /* ════════════════════════════════════════════
     EVAL BARS  (untouched)
  ════════════════════════════════════════════ */
  function _drawBar(fillEl,valEl,cp){
    if(!fillEl) return;
    if(cp===null||cp===undefined){
      fillEl.style.height='50%';
      if(valEl) valEl.textContent='—';
      return;
    }
    const clamped=Math.max(-2000,Math.min(2000,cp));
    const pct=50+(clamped/2000)*50;
    fillEl.style.height=pct.toFixed(1)+'%';
    if(valEl){
      const abs=(Math.abs(cp)/100).toFixed(1);
      valEl.textContent=cp===0?'0.0':(cp>0?'+':'-')+abs;
    }
  }

  /* ════════════════════════════════════════════
     PLAYER DOTS + CAPTURES
  ════════════════════════════════════════════ */
  function _updateDots(){
    $blackName?.querySelector('.p-dot.b')?.classList.toggle('active',turn==='black');
    $whiteName?.querySelector('.p-dot.w')?.classList.toggle('active',turn==='white');
  }
  function _updateCaptures(){
    _drawCap($capTop,capByW);
    _drawCap($capBot,capByB);
  }
  function _drawCap(el,pieces){
    if(!el) return;
    const sorted=[...pieces].sort((a,b)=>(VAL[b]||0)-(VAL[a]||0));
    const adv=pieces.reduce((s,p)=>s+(VAL[p]||0),0);
    el.innerHTML=sorted.map(p=>`<img class="cap-img" src="${pUrl(p)}" alt="${NAME[p]}" title="${NAME[p]}">`).join('')
      +(adv>0?`<span class="mat-adv">+${adv}</span>`:'');
  }

  /* ════════════════════════════════════════════
     OPENING RECOGNITION
  ════════════════════════════════════════════ */
  function _showOutOfBook(){
    // Show a brief "Out of Book" toast — only once per game.
    if(_leftBook) return;
    _leftBook = true;
    const toast = document.createElement('div');
    toast.className = 'book-exit-toast';
    toast.textContent = '\uD83D\uDCCB Out of Book';
    document.body.appendChild(toast);
    setTimeout(()=>{ toast.classList.add('book-exit-fade'); }, 1800);
    setTimeout(()=>{ toast.remove(); }, 2300);
  }

  async function _updateOpening(inBookOverride){
    // inBookOverride: boolean from engine response (true = book move, false = engine calc)
    // undefined = human move, don't touch book badge
    if(!$openingEl) return;

    // Handle book-state transitions when we know the answer from the payload
    if(inBookOverride === true){
      _inBook = true;
    } else if(inBookOverride === false){
      if(_inBook) _showOutOfBook();  // was in book, just left it
      _inBook = false;
    }

    try{
      const data = await GET('/opening');
      if(!$openingEl) return;  // guard against navigate-away

      if(data.name){
        const eco = data.eco ? `<span class="ob-eco">(${data.eco})</span>` : '';
        const badge = _inBook
          ? '<span class="ob-badge">📘 Book Move</span>'
          : '';
        $openingEl.innerHTML = `<span class="ob-name">${data.name}</span>${eco}${badge}`;
        $openingEl.classList.remove('hidden');
      } else if(_inBook){
        // In book but opening name not yet found (very early moves)
        $openingEl.innerHTML = '<span class="ob-badge">📘 Book Move</span>';
        $openingEl.classList.remove('hidden');
      } else {
        $openingEl.innerHTML = '';
        $openingEl.classList.add('hidden');
      }
    }catch(e){
      $openingEl.innerHTML = '';
      $openingEl.classList.add('hidden');
    }
  }

  /* ════════════════════════════════════════════
     MOVE LIST  — Stockfish move review
     reviewData[i] comes from server response.review
  ════════════════════════════════════════════ */
  function _pushMove(uci, review){
    halfMoves.push(uci);
    reviewData.push(review || null);
    _renderMoveList();
    _updateOpening();  // human move — no inBookOverride; book badge unchanged
  }

  function _fmtEval(val){
    if(val == null) return '—';
    const s = val >= 0 ? '+' : '';
    return s + val.toFixed(2);
  }

  function _renderMoveList(){
    if(!$histSf) return;
    $histSf.innerHTML='';

    for(let i=0;i<halfMoves.length;i+=2){
      const row=document.createElement('div');
      row.className='h-row';

      const wMove = halfMoves[i]    || '';
      const bMove = halfMoves[i+1]  || '';
      const wRev  = reviewData[i]   || null;
      const bRev  = reviewData[i+1] || null;
      const cur   = halfMoves.length - 1;

      const renderHalf = (uci, rev, isCur) => {
        if(!uci) return '';

        const cls  = rev ? _classifyFromReview(rev) : null;
        const sym  = cls ? `<span class="move-sym ${cls.cls}" title="${cls.label}">${cls.sym}</span>` : '';

        // Show best alternative if played differs from best
        let bestLine = '';
        if(rev && rev.best && rev.best !== uci){
          bestLine = `<span class="move-best" title="Best: ${rev.best}">→ ${rev.best}</span>`;
        }

        // Show eval change as small subscript
        let evalLine = '';
        if(rev && (rev.eval_before != null || rev.eval_after != null)){
          const eb = _fmtEval(rev.eval_before);
          const ea = _fmtEval(rev.eval_after);
          evalLine = `<span class="move-eval">${eb} → ${ea}</span>`;
        }

        return `<span class="h-move${isCur?' cur':''}">`+
               `<span class="move-uci">${uci}</span>${sym}`+
               `${bestLine}${evalLine}`+
               `</span>`;
      };

      row.innerHTML =
        `<span class="h-num">${Math.floor(i/2)+1}.</span>` +
        renderHalf(wMove, wRev, cur===i) +
        renderHalf(bMove, bRev, bMove && cur===i+1);

      $histSf.appendChild(row);
    }
    $histSf.scrollTop = $histSf.scrollHeight;
  }

  /* ════════════════════════════════════════════
     UNDO / REDO
  ════════════════════════════════════════════ */
  function _updateUndoRedo(cu,cr){
    if($undo) $undo.disabled=!_undoEnabled||!cu;
    if($redo) $redo.disabled=!_undoEnabled||!cr;
  }

  /* Rebuild halfMoves + reviewData from server-provided move_history.
     Called after both undo and redo so the sidebar always matches the board. */
  function _rebuildMoveList(moveHistory){
    halfMoves  = [];
    reviewData = [];
    if(!moveHistory) return;
    for(const entry of moveHistory){
      halfMoves.push(entry.move || '');
      reviewData.push(entry);
    }
    _renderMoveList();
  }

  async function doUndo(){
    if(!_undoEnabled) return;
    if(gameOver) gameOver=false;
    try{
      const data=await POST('/undo',{});
      _rebuildCap(data.board);
      lastMove=null; selected=null; legal=[];
      _clearHint(); _prevBestFrom=null; _prevBestTo=null;
      applyState(data);
      _rebuildMoveList(data.move_history);
      _updateTurnStatus();
      _refreshEval();
      _bmRefresh();
    }catch(e){ setStatus('dot-x','Undo error: '+e.message); }
  }

  async function doRedo(){
    if(!_undoEnabled) return;
    try{
      const data=await POST('/redo',{});
      _rebuildCap(data.board);
      selected=null; legal=[]; lastMove=null;
      _clearHint(); _prevBestFrom=null; _prevBestTo=null;
      applyState(data);
      _rebuildMoveList(data.move_history);
      _updateTurnStatus();
      _refreshEval();
      _bmRefresh();
    }catch(e){ setStatus('dot-x','Redo error: '+e.message); }
  }

  async function _refreshEval(){
    try{
      const data=await GET('/eval');
      _applyBars(data);

    }catch(e){}
  }

  function _rebuildCap(b){
    const sw={P:8,N:2,B:2,R:2,Q:1},sb={p:8,n:2,b:2,r:2,q:1};
    const cur={};
    for(let r=0;r<8;r++) for(let c=0;c<8;c++){
      const p=b[r][c]; if(p!=='.') cur[p]=(cur[p]||0)+1;
    }
    capByW=[]; capByB=[];
    for(const[p,n] of Object.entries(sw)){ const l=n-(cur[p]||0); for(let i=0;i<l;i++) capByB.push(p); }
    for(const[p,n] of Object.entries(sb)){ const l=n-(cur[p]||0); for(let i=0;i<l;i++) capByW.push(p); }
  }

  /* ════════════════════════════════════════════
     BEST MOVE DISPLAY + BOARD HIGHLIGHTS
  ════════════════════════════════════════════ */
  function _clearHint(){ _hintFrom=null; _hintTo=null; }

  function _bmRefresh(){
    if(_bmMode==='none'){
      _clearHint(); render();
      _bmHidePanel();
      return;
    }
    if(_bmMode==='current_position') _bmFetchCurrent();
  }

  function _bmFetchCurrent(){
    if(_bmPending||gameOver) return;
    _bmPending=true;
    _bmShowPanel();
    _bmSetContent('<div class="bm-loading">Computing…</div>');
    GET('/bestmove/current')
      .then(data=>{
        _bmPending=false;
        if(_bmMode!=='current_position'){ _clearHint(); render(); return; }
        const mv = data.engine || data.stockfish || null;
        if(mv && mv.length>=4){ _hintFrom=mv.slice(0,2); _hintTo=mv.slice(2,4); }
        else { _clearHint(); }
        render();
        _bmRenderCurrentPanel(data);
      })
      .catch(()=>{ _bmPending=false; _clearHint(); render(); _bmSetContent('<div class="bm-loading">—</div>'); });
  }

  async function _bmCapturePrevBest(){
    if(_bmMode!=='previous_move') return;
    try{
      const data=await GET('/bestmove/current');
      const mv = data.engine || data.stockfish || null;
      if(mv && mv.length>=4){ _prevBestFrom=mv.slice(0,2); _prevBestTo=mv.slice(2,4); }
      else { _prevBestFrom=null; _prevBestTo=null; }
    }catch(e){ _prevBestFrom=null; _prevBestTo=null; }
  }

  function _bmApplyPrevHint(playedFrom, playedTo){
    if(_bmMode!=='previous_move'){ _clearHint(); render(); return; }
    _hintFrom=_prevBestFrom; _hintTo=_prevBestTo;
    render();
    if(_prevBestFrom && _prevBestTo){
      const best = _prevBestFrom+_prevBestTo;
      const played = playedFrom+playedTo;
      _bmShowPanel();
      _bmSetContent(`
        <div class="bm-row">
          <span class="bm-lbl">Played</span>
          <span class="bm-val">${played}</span>
        </div>
        <div class="bm-row">
          <span class="bm-lbl">Best was</span>
          <span class="bm-val ${best===played?'bm-match':''}">${best}</span>
        </div>`);
    } else {
      _bmShowPanel();
      _bmSetContent('<div class="bm-loading">No suggestion.</div>');
    }
  }

  function _bmRenderCurrentPanel(data){
    const eng = data.engine    || '—';
    const sf  = data.stockfish || '—';
    _bmShowPanel();
    _bmSetContent(`
      <div class="bm-row">
        <span class="bm-lbl">Engine</span>
        <span class="bm-val">${eng}</span>
      </div>
      <div class="bm-row">
        <span class="bm-lbl">Stockfish</span>
        <span class="bm-val">${sf}</span>
      </div>`);
  }

  function _bmSetContent(html){ if($bmPanel) $bmPanel.innerHTML=html; }
  function _bmShowPanel(){ document.getElementById('bm-box')?.classList.remove('hidden'); }
  function _bmHidePanel(){
    document.getElementById('bm-box')?.classList.add('hidden');
    if($bmPanel) $bmPanel.innerHTML='';
  }
  function _bmUpdateHead(){
    const head=document.getElementById('bm-head');
    if(!head) return;
    head.textContent = _bmMode==='previous_move' ? 'Previous Best' : 'Best Move';
  }

  /* ════════════════════════════════════════════
     COPY FEN
  ════════════════════════════════════════════ */
  async function _copyFen(){
    try{
      const data=await GET('/fen');
      await navigator.clipboard.writeText(data.fen);
      _showToast('FEN copied!');
    }catch(e){ _showToast('Copy failed'); }
  }
  function _showToast(msg){
    const t=document.createElement('div');
    t.className='fen-toast'; t.textContent=msg;
    document.body.appendChild(t);
    setTimeout(()=>t.remove(),2000);
  }

  /* ════════════════════════════════════════════
     DRAG & DROP
  ════════════════════════════════════════════ */
  function onDragStart(e,r,c,not){
    if(gameOver) return;
    const mode=window.App?.getMode();
    if(mode!=='hvh'&&turn!==playerColor) return;
    const piece=board[r][c];
    if(!piece||piece==='.') return;
    const isW=piece===piece.toUpperCase();
    if(turn==='white'&&!isW) return;
    if(turn==='black'&& isW) return;

    e.preventDefault();
    const sqEl=$board.querySelector(`[data-square="${not}"]`);
    const rect=sqEl.getBoundingClientRect();
    _dox=e.clientX-rect.left; _doy=e.clientY-rect.top;

    dragEl=document.createElement('img');
    dragEl.src=pUrl(piece);
    const sz=rect.width;
    Object.assign(dragEl.style,{
      position:'fixed',zIndex:'9999',pointerEvents:'none',
      width:sz+'px',height:sz+'px',objectFit:'contain',
      left:(e.clientX-_dox)+'px',top:(e.clientY-_doy)+'px',
      filter:'drop-shadow(2px 5px 10px rgba(0,0,0,.8))',
      transition:'none',
    });
    document.body.appendChild(dragEl);
    dragFrom={r,c,not}; dragActive=false;
    selected={row:r,col:c}; legal=[]; render();

    GET(`/moves?square=${not}`).then(d=>{
      legal=(d.moves||[]).map(m=>typeof m==='string'?m:m.to);
      render();
    }).catch(()=>{});

    document.addEventListener('pointermove',onDragMove);
    document.addEventListener('pointerup',  onDragEnd);
  }
  function onDragMove(e){
    if(!dragEl) return;
    e.preventDefault(); dragActive=true;
    dragEl.style.left=(e.clientX-_dox)+'px';
    dragEl.style.top =(e.clientY-_doy)+'px';
  }
  function onDragEnd(e){
    document.removeEventListener('pointermove',onDragMove);
    document.removeEventListener('pointerup',  onDragEnd);
    if(!dragEl){ dragActive=false; dragFrom=null; return; }
    dragEl.remove(); dragEl=null;
    if(!dragActive){ dragActive=false; dragFrom=null; return; }
    dragActive=false;
    const target=document.elementFromPoint(e.clientX,e.clientY);
    const sqEl=target?.closest('[data-square]');
    if(!sqEl||!dragFrom){ dragFrom=null; return; }
    const toNot=sqEl.dataset.square;
    const from=dragFrom.not; dragFrom=null;
    if(toNot===from) return;
    if(legal.includes(toNot)){
      const piece=board[selected.row][selected.col];
      const{row:tr}=n2i(toNot);
      if((piece==='P'&&tr===0)||(piece==='p'&&tr===7)){
        promoWait={from,to:toNot}; selected=null; legal=[]; render();
        _showPromoModal(piece==='P'?'white':'black'); return;
      }
      _commitMove(from,toNot,null);
    }else{ selected=null; legal=[]; render(); }
  }

  /* ════════════════════════════════════════════
     CLICK
  ════════════════════════════════════════════ */
  async function onClickSq(r,c,not){
    if(gameOver) return;
    const mode=window.App?.getMode();
    if(mode!=='hvh'&&turn!==playerColor) return;

    if(!selected){
      const piece=board[r][c];
      if(!piece||piece==='.') return;
      const isW=piece===piece.toUpperCase();
      if(turn==='white'&&!isW) return;
      if(turn==='black'&& isW) return;
      selected={row:r,col:c}; legal=[]; render();
      try{
        const d=await GET(`/moves?square=${not}`);
        legal=(d.moves||[]).map(m=>typeof m==='string'?m:m.to);
        render();
      }catch{ selected=null; legal=[]; render(); }
      return;
    }

    const fromNot=i2n(selected.row,selected.col);
    if(not===fromNot){ selected=null; legal=[]; render(); return; }

    if(legal.includes(not)){
      const piece=board[selected.row][selected.col];
      const{row:tr}=n2i(not);
      if((piece==='P'&&tr===0)||(piece==='p'&&tr===7)){
        promoWait={from:fromNot,to:not}; selected=null; legal=[]; render();
        _showPromoModal(piece==='P'?'white':'black'); return;
      }
      await _commitMove(fromNot,not,null); return;
    }

    const piece=board[r][c];
    if(piece&&piece!=='.'){
      const isW=piece===piece.toUpperCase();
      if((turn==='white'&&isW)||(turn==='black'&&!isW)){
        selected={row:r,col:c}; legal=[]; render();
        try{
          const d=await GET(`/moves?square=${not}`);
          legal=(d.moves||[]).map(m=>typeof m==='string'?m:m.to);
          render();
        }catch{}
        return;
      }
    }
    selected=null; legal=[]; render();
  }

  /* ════════════════════════════════════════════
     COMMIT HUMAN MOVE
  ════════════════════════════════════════════ */
  async function _commitMove(from,to,promotion){
    selected=null; legal=[];
    const{row:tr,col:tc}=n2i(to);
    const cap=board[tr][tc];

    if(cap&&cap!=='.'){
      (cap===cap.toUpperCase()?capByB:capByW).push(cap);
      playSound('capture');
    }else{ playSound('move'); }

    lastMove={from,to};

    if(_bmMode==='previous_move'){
      _clearHint(); render();
      await _bmCapturePrevBest();
    } else {
      _clearHint(); render();
    }

    setStatus('dot-t','Playing move…');

    try{
      const body={from,to};
      if(promotion) body.promotion=promotion;
      const data=await POST('/move/human',body);

      // Use server-computed review data
      const review = data.review || null;
      _pushMove(from+to, review);

      applyState(data);
      if(data.status==='check') playSound('check');
      if(_isOver(data)){ gameOver=true; _showOver(data); return; }

      const mode=window.App?.getMode();
      if(mode&&mode!=='hvh'){
        await _doEngineMove(mode);
      } else {
        _updateTurnStatus();
        if(_bmMode==='previous_move')        _bmApplyPrevHint(from,to);
        else if(_bmMode==='current_position') _bmRefresh();
      }
    }catch(e){
      setStatus('dot-x','Error: '+e.message);
      const data=await GET('/state'); applyState(data);
    }
  }

  /* ════════════════════════════════════════════
     ENGINE MOVE
  ════════════════════════════════════════════ */
  async function _doEngineMove(mode){
    setStatus('dot-t','Engine thinking…');
    const ep=mode==='stockfish'?'/move/stockfish':'/move/engine';
    const body=mode==='stockfish'?{}:{depth:_engineDepth};
    try{
      const data=await POST(ep,body);
      const mv=data.engine_move;
      if(mv&&mv.from&&mv.to){
        const{row:tr,col:tc}=n2i(mv.to);
        const cap=board[tr][tc];
        if(cap&&cap!=='.'){
          (cap===cap.toUpperCase()?capByB:capByW).push(cap);
          playSound('capture');
        }else{ playSound('move'); }
        const review = data.review || null;
        _pushMove(mv.from+mv.to, review);
        lastMove={from:mv.from,to:mv.to};
        // Update opening + book badge using the payload flag (no extra network call)
        _updateOpening(data.in_book === true);
      }
      applyState(data);
      if(data.status==='check') playSound('check');
      if(_isOver(data)){ gameOver=true; _showOver(data); return; }
      _updateTurnStatus();
      _bmRefresh();
    }catch(e){
      setStatus('dot-x','Engine error: '+e.message);
      const data=await GET('/state'); applyState(data);
    }
  }

  /* ════════════════════════════════════════════
     ACCURACY — computed from reviewData
     Human-feeling model:
     - cp_loss < 20   → score ≈ 100 (tiny errors don't matter)
     - cp_loss 20-100  → gentle slope (mild penalty)
     - cp_loss 100-500 → steeper slope (clear penalty)
     - cp_loss > 500   → floor at ~10 (one blunder doesn't force 0%)
     Uses weighted average so blunders hurt but don't destroy everything.
  ════════════════════════════════════════════ */
  /**
   * Convert a single move's cp_loss into a 0-100 accuracy score.
   * Designed to produce believable game-level averages:
   *   Very clean game  → 90-98
   *   Strong rapid      → 80-92
   *   Average club      → 65-85
   *   Blunder-heavy     → 30-60
   */
  function _moveAccuracyScore(cpLoss){
    if(cpLoss <= 0)  return 100;
    if(cpLoss <= 20) return 100 - cpLoss * 0.1;    // 0-20cp → 100-98 (almost perfect)
    if(cpLoss <= 100) return 98 - (cpLoss - 20) * 0.35;  // 20-100cp → 98-70
    if(cpLoss <= 300) return 70 - (cpLoss - 100) * 0.20;  // 100-300cp → 70-30
    if(cpLoss <= 700) return 30 - (cpLoss - 300) * 0.04;  // 300-700cp → 30-14
    return Math.max(5, 14 - (cpLoss - 700) * 0.01);       // 700+cp → floor at 5
  }

  /** Compute accuracy stats for a single side from a filtered array of review objects. */
  function _statsForSide(revs){
    let scores=[], blunders=0, mistakes=0, inaccuracies=0, brilliants=0;
    for(const rev of revs){
      if(!rev) continue;
      const cl=_classifyFromReview(rev);
      if(cl){
        if(cl.cls==='cl-blunder')         blunders++;
        else if(cl.cls==='cl-mistake')    mistakes++;
        else if(cl.cls==='cl-inaccuracy') inaccuracies++;
        else if(cl.cls==='cl-brilliant')  brilliants++;
      }
      const b=rev.best_eval_cp, a=rev.eval_after_cp;
      if(b!=null && a!=null){
        // b and a are both WHITE-positive centipawns.
        // cp_loss from mover's perspective:
        const cp_loss = rev.moving_color==='white'
          ? Math.max(0, b - a)
          : Math.max(0, a - b);
        scores.push(_moveAccuracyScore(cp_loss));
      }
    }
    const accuracy = scores.length
      ? Math.round(scores.reduce((s,x)=>s+x,0)/scores.length)
      : 100;
    return { accuracy, blunders, mistakes, inaccuracies, brilliants };
  }

  /**
   * Compute accuracy stats for both sides.
   * Uses moving_color field — reliable even after undo/redo.
   * Returns { overall, white, black }.
   */
  function _computeAccuracyStats(){
    // Filter by moving_color (set by server per move — not index-based)
    const whiteRevs = reviewData.filter(r=>r && r.moving_color==='white');
    const blackRevs = reviewData.filter(r=>r && r.moving_color==='black');
    const overall   = _statsForSide(reviewData.filter(Boolean));
    const white     = _statsForSide(whiteRevs);
    const black     = _statsForSide(blackRevs);
    return { overall, white, black };
  }

  /* ════════════════════════════════════════════
     EVAL GRAPH — drawn on a <canvas>
  ════════════════════════════════════════════ */
  function _drawEvalGraph(canvasId){
    const canvas=document.getElementById(canvasId);
    if(!canvas) return;
    const ctx=canvas.getContext('2d');
    const W=canvas.width, H=canvas.height;
    ctx.clearRect(0,0,W,H);

    const evals=reviewData
      .filter(r=>r && r.eval_after!=null)
      .map(r=>r.eval_after);

    if(evals.length<2){
      ctx.fillStyle='#1a1a1a'; ctx.fillRect(0,0,W,H);
      ctx.fillStyle='#444'; ctx.font='11px monospace';
      ctx.textAlign='center'; ctx.fillText('No eval data',W/2,H/2+4);
      return;
    }

    const clamp=(v,lo,hi)=>Math.max(lo,Math.min(hi,v));
    const lo=-6, hi=6;
    const toY=v=>H-(clamp(v,lo,hi)-lo)/(hi-lo)*H;
    const toX=i=>i/(evals.length-1)*(W-2)+1;
    const zy=toY(0);

    // background
    ctx.fillStyle='#111'; ctx.fillRect(0,0,W,H);

    // white-advantage fill (above zero line)
    ctx.beginPath();
    ctx.moveTo(toX(0),zy);
    for(let i=0;i<evals.length;i++){
      const y=toY(evals[i]);
      ctx.lineTo(toX(i), Math.min(y,zy));
    }
    ctx.lineTo(toX(evals.length-1),zy);
    ctx.closePath();
    ctx.fillStyle='rgba(220,210,190,0.45)'; ctx.fill();

    // black-advantage fill (below zero line)
    ctx.beginPath();
    ctx.moveTo(toX(0),zy);
    for(let i=0;i<evals.length;i++){
      const y=toY(evals[i]);
      ctx.lineTo(toX(i), Math.max(y,zy));
    }
    ctx.lineTo(toX(evals.length-1),zy);
    ctx.closePath();
    ctx.fillStyle='rgba(40,40,40,0.6)'; ctx.fill();

    // zero line
    ctx.strokeStyle='#444'; ctx.lineWidth=1;
    ctx.beginPath(); ctx.moveTo(0,zy); ctx.lineTo(W,zy); ctx.stroke();

    // eval line
    ctx.beginPath();
    ctx.strokeStyle='#c8a96e'; ctx.lineWidth=1.5; ctx.lineJoin='round';
    evals.forEach((v,i)=>{
      if(i===0) ctx.moveTo(toX(i),toY(v));
      else ctx.lineTo(toX(i),toY(v));
    });
    ctx.stroke();
  }

  /* ════════════════════════════════════════════
     SAVE GAME — POST /save_game
  ════════════════════════════════════════════ */
  async function _doSaveGame(stats){
    const body={
      moves:    halfMoves,
      result:   _gameResult||'?',
      accuracy: stats,
      date:     new Date().toISOString(),
    };
    const r=await fetch('/save_game',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body),
    });
    if(!r.ok) throw new Error(await r.text());
    return r.json();
  }

  /* ════════════════════════════════════════════
     GAME OVER
  ════════════════════════════════════════════ */
  function _isOver(d){
    return ['checkmate','stalemate','draw_50_move','draw_material','draw_repetition'].includes(d.status);
  }
  function _showOver(d){
    let msg;
    if(d.status==='checkmate')            msg=`♛ Checkmate! ${d.winner} wins!`;
    else if(d.status==='stalemate')       msg='Stalemate — draw.';
    else if(d.status==='draw_material')   msg='Draw — insufficient material.';
    else if(d.status==='draw_repetition') msg='Draw — threefold repetition.';
    else                                  msg='Draw (50-move rule).';
    setStatus('dot-x',msg);

    // Capture result for save game
    if(d.status==='checkmate') _gameResult=d.winner==='white'?'1-0':'0-1';
    else _gameResult='1/2-1/2';

    _clearHint(); render();
    _showEndModal(d);
  }
  function _showEndModal(d){
    document.getElementById('end-game-modal')?.remove();
    let title,subtitle,icon;
    if(d.status==='checkmate'){
      icon='♛'; title='Checkmate!';
      subtitle=`${d.winner.charAt(0).toUpperCase()+d.winner.slice(1)} wins`;
    }else if(d.status==='stalemate'){
      icon='½'; title='Stalemate'; subtitle='The game is a draw';
    }else if(d.status==='draw_material'){
      icon='½'; title='Draw'; subtitle='Insufficient material';
    }else if(d.status==='draw_repetition'){
      icon='½'; title='Draw'; subtitle='Threefold repetition';
    }else{
      icon='½'; title='Draw'; subtitle='50-move rule';
    }

    const stats=_computeAccuracyStats();

    // Helper: render one side's stat block
    const _sideBlock=(label, s, colorClass)=>`
      <div class="pg-side-block ${colorClass}">
        <div class="pg-side-header">${label}</div>
        <div class="pg-side-accuracy ${_accClass(s.accuracy)}">${s.accuracy}%</div>
        <div class="pg-side-counters">
          ${s.brilliants?`<span class="pg-sc pg-brilliant" title="Brilliant">💎${s.brilliants}</span>`:''}
          <span class="pg-sc pg-blunder"    title="Blunders">??${s.blunders}</span>
          <span class="pg-sc pg-mistake"    title="Mistakes">?${s.mistakes}</span>
          <span class="pg-sc pg-inaccuracy" title="Inaccuracies">?!${s.inaccuracies}</span>
        </div>
      </div>`;

    const ov=document.createElement('div');
    ov.id='end-game-modal'; ov.className='end-modal-overlay';
    ov.innerHTML=`
      <div class="end-modal">
        <div class="end-modal-icon">${icon}</div>
        <h2 class="end-modal-title">${title}</h2>
        <p class="end-modal-sub">${subtitle}</p>

        <div class="pg-sides">
          ${_sideBlock('♙ White', stats.white, 'pg-side-white')}
          ${_sideBlock('♟ Black', stats.black, 'pg-side-black')}
        </div>

        <canvas id="end-eval-graph" width="300" height="68"
                style="display:block;margin:12px auto 16px;border-radius:4px;"></canvas>

        <div class="end-modal-btns">
          <button class="btn btn-gold"  id="end-new-game">↺ New Game</button>
          <button class="btn btn-ghost" id="end-save"    >💾 Save</button>
          <button class="btn btn-ghost" id="end-close"   >✕ Close</button>
        </div>
      </div>`;

    ov.querySelector('#end-new-game').addEventListener('click',()=>{ ov.remove(); resetGame(); });
    ov.querySelector('#end-close').addEventListener('click',()=>ov.remove());
    ov.addEventListener('click',e=>{ if(e.target===ov) ov.remove(); });

    const saveBtn=ov.querySelector('#end-save');
    saveBtn.addEventListener('click',async()=>{
      saveBtn.disabled=true; saveBtn.textContent='Saving…';
      try{
        await _doSaveGame(stats.overall);
        saveBtn.textContent='✓ Saved';
      }catch(e){
        saveBtn.textContent='Failed';
        saveBtn.disabled=false;
      }
    });

    document.body.appendChild(ov);

    // Draw graph after modal is in the DOM (requestAnimationFrame to be safe)
    requestAnimationFrame(()=>_drawEvalGraph('end-eval-graph'));
  }

  /** Return CSS class for accuracy value colour coding. */
  function _accClass(acc){
    if(acc>=80) return 'acc-high';
    if(acc>=55) return 'acc-mid';
    return 'acc-low';
  }
  function _updateTurnStatus(){
    setStatus(turn==='white'?'dot-w':'dot-b',
      `${turn.charAt(0).toUpperCase()+turn.slice(1)}'s turn`);
  }

  /* ════════════════════════════════════════════
     PROMOTION MODAL
  ════════════════════════════════════════════ */
  function _showPromoModal(color){
    const pieces=color==='white'?['Q','R','B','N']:['q','r','b','n'];
    const ov=document.createElement('div'); ov.className='modal-overlay';
    ov.innerHTML=`<div class="modal"><h4>Promote Pawn</h4><div class="promo-choices">
      ${pieces.map(p=>`<button class="promo-btn" data-p="${p}"><img src="${pUrl(p)}" alt="${p}"/></button>`).join('')}
    </div></div>`;
    ov.querySelectorAll('.promo-btn').forEach(btn=>{
      btn.addEventListener('click',async()=>{
        ov.remove();
        const p=btn.dataset.p;
        const{from:f,to:t}=promoWait; promoWait=null;
        await _commitMove(f,t,p);
      });
    });
    document.body.appendChild(ov);
  }

  /* ════════════════════════════════════════════
     PUBLIC SETTINGS API
  ════════════════════════════════════════════ */
  function setEvalBars(on){
    if($evalBarsEl) $evalBarsEl.classList.toggle('hidden',!on);
  }
  function setSoundEnabled(on){ _soundOn=on; }
  function flipBoard(){ flipped=!flipped; render(); }
  function setPlayerColor(c){ playerColor=c; flipped=(c==='black'); render(); }
  function setUndoEnabled(on){
    _undoEnabled=on;
    if($undo) $undo.disabled=!on;
    if($redo) $redo.disabled=!on;
  }
  function setEngineDepth(d){ _engineDepth=d; }

  /**
   * setBestMoveMode(mode)
   * mode: 'none' | 'current_position' | 'previous_move'
   */
  function setBestMoveMode(mode){
    _bmMode    = mode || 'none';
    _bmPending = false;
    _clearHint(); _prevBestFrom=null; _prevBestTo=null;
    _bmUpdateHead();
    if(_bmMode==='none'){
      _bmHidePanel(); render();
    } else {
      _bmShowPanel();
      if(!gameOver) _bmRefresh();
      else render();
    }
  }

  /* ════════════════════════════════════════════
     INIT
  ════════════════════════════════════════════ */
  async function init(opts){
    $board       = opts.container;
    $status      = opts.statusEl;
    $eFill       = opts.evalEngFill  || null;
    $sFill       = opts.evalSfFill   || null;
    $eVal        = opts.evalEngVal   || null;
    $sVal        = opts.evalSfVal    || null;
    $capTop      = opts.capTop       || null;
    $capBot      = opts.capBot       || null;
    $histSf      = opts.histSf       || null;
    $undo        = opts.undoBtn      || null;
    $redo        = opts.redoBtn      || null;
    $blackName   = opts.blackName    || null;
    $whiteName   = opts.whiteName    || null;
    $evalBarsEl  = opts.evalBarsEl   || null;
    $fenBtn      = opts.fenBtn       || null;
    $bmPanel     = opts.bmPanel      || null;
    $openingEl   = opts.openingEl    || null;

    selected=null; legal=[]; lastMove=null;
    capByW=[]; capByB=[]; gameOver=false; promoWait=null;
    halfMoves=[]; reviewData=[]; _gameResult=null;
    dragActive=false; dragFrom=null;
    if(dragEl){ dragEl.remove(); dragEl=null; }
    window._chkSq=null;
    playerColor=opts.playerColor||'white';
    flipped=(playerColor==='black');
    _engineDepth=opts.engineDepth||3;
    _clearHint(); _prevBestFrom=null; _prevBestTo=null;
    _hintPending=false;
    _inBook   = false;
    _leftBook  = false;
    _bmMode='none'; _bmPending=false;

    if($histSf)  $histSf.innerHTML='';
    if($bmPanel) $bmPanel.innerHTML='';

    if($undo){ const n=$undo.cloneNode(true); $undo.replaceWith(n); $undo=n; $undo.addEventListener('click',doUndo); }
    if($redo){ const n=$redo.cloneNode(true); $redo.replaceWith(n); $redo=n; $redo.addEventListener('click',doRedo); }
    if($undo) $undo.disabled=true;
    if($redo) $redo.disabled=true;

    if($fenBtn){ const n=$fenBtn.cloneNode(true); $fenBtn.replaceWith(n); $fenBtn=n; $fenBtn.addEventListener('click',_copyFen); }

    _initSounds();

    setStatus('dot-t','Loading…');
    // Always reset backend state on init so a fresh game begins cleanly.
    // This handles: Home→Game navigation, mode changes, and page reloads.
    try{ await POST('/reset',{}); }catch(e){ console.warn('[Board.init] reset failed:',e); }
    const data=await GET('/state');
    applyState(data);
    _updateTurnStatus();

    const _initMode=window.App?.getMode();
    if(_initMode&&_initMode!=='hvh'&&playerColor==='black'&&turn==='white'){
      await _doEngineMove(_initMode);
    }
  }

  async function resetGame(){
    try{ await POST('/reset',{}); }
    catch(e){ setStatus('dot-x','Reset error: '+e.message); return; }
    selected=null; legal=[]; lastMove=null;
    capByW=[]; capByB=[]; gameOver=false; promoWait=null;
    halfMoves=[]; reviewData=[]; _gameResult=null;
    dragActive=false; dragFrom=null;
    if(dragEl){ dragEl.remove(); dragEl=null; }
    window._chkSq=null;
    _clearHint(); _prevBestFrom=null; _prevBestTo=null;
    _bmPending=false;
    if($histSf)    $histSf.innerHTML='';
    if($bmPanel)   $bmPanel.innerHTML='';
    if($openingEl){ $openingEl.innerHTML=''; $openingEl.classList.add('hidden'); }
    _inBook  = false;
    _leftBook = false;
    setStatus('dot-t','Resetting…');
    const data=await GET('/state');
    applyState(data);
    _updateTurnStatus();
    if(_bmMode!=='none') _bmRefresh();
    else _bmHidePanel();
  }

  return {
    init,
    resetGame,
    flipBoard,
    setPlayerColor,
    setEvalBars,
    setSoundEnabled,
    setUndoEnabled,
    setEngineDepth,
    setBestMoveMode,
  };
})();