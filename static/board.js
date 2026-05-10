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
  let dragActive=false, dragFrom=null, dragEl=null, _dox=0, _doy=0, _startX=0, _startY=0;
  let _justSelected=false; // prevent mobile tap from deselecting immediately

  let _moveSeq = 0; // Sequence number to prevent race conditions during undo

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
      $openingBoxEl,   // outer card — show/hide
      $openingEl;      // inner content div - DOM-rendered

  /* ── notation ── */
  const i2n = (r,c) => String.fromCharCode(97+c)+(8-r);
  const n2i = s     => ({row:8-parseInt(s[1]), col:s.charCodeAt(0)-97});
  const UCI_RE = /^[a-h][1-8][a-h][1-8][qrbn]?$/;

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
    $status.replaceChildren();
    const dot = document.createElement('span');
    dot.className = `dot ${cls}`;
    $status.appendChild(dot);
    $status.appendChild(document.createTextNode(String(msg ?? '')));
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
      fillEl.style.height='50%'; fillEl.style.width='100%';
      if(valEl) valEl.textContent='—';
      return;
    }
    const clamped=Math.max(-2000,Math.min(2000,cp));
    const pct=50+(clamped/2000)*50;

    const isH = window.innerWidth <= 700;
    if(isH){
      fillEl.style.width=pct.toFixed(1)+'%';
      fillEl.style.height='100%';
    } else {
      fillEl.style.height=pct.toFixed(1)+'%';
      fillEl.style.width='100%';
    }

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
  function _clearOpening(){
    if($openingEl)    $openingEl.innerHTML = '';
    if($openingBoxEl) $openingBoxEl.classList.add('hidden');
  }

  function _showOutOfBook(){
    // Show a brief "Out of Book" toast — only once per game.
    if(_leftBook) return;
    _leftBook = true;
    // Remove any stale toast before creating a new one (prevents duplicates).
    document.querySelectorAll('.book-exit-toast').forEach(t => t.remove());
    const toast = document.createElement('div');
    toast.className = 'book-exit-toast';
    toast.textContent = '\uD83D\uDCCB Out of Book';
    document.body.appendChild(toast);
    setTimeout(()=>{ toast.classList.add('book-exit-fade'); }, 1800);
    setTimeout(()=>{ toast.remove(); }, 2300);
  }

  // Safe DOM helper: create an element with a class and optional text content.
  function _el(tag, cls, text){
    const e = document.createElement(tag);
    if(cls)  e.className = cls;
    if(text !== undefined) e.textContent = text;
    return e;
  }

  async function _updateOpening(inBookOverride){
    if(!$openingEl || !$openingBoxEl) return;
    if(inBookOverride === true){
      _inBook = true;
    } else if(inBookOverride === false){
      if(_inBook) _showOutOfBook();
      _inBook = false;
    }

    let name = null, eco = null, serverInBook = null;
    try{
      const data = await GET('/opening');
      if(!$openingEl || !$openingBoxEl) return;
      name         = data.name    || null;
      eco          = data.eco     || null;
      serverInBook = (data.in_book === true);
    }catch(_){
      _clearOpening();
      return;
    }

    if(inBookOverride === undefined){
      const wasInBook = _inBook;
      _inBook = serverInBook;
      if(_inBook && _leftBook) _leftBook = false;
      if(wasInBook && !_inBook) _showOutOfBook();
    }

    // —— Render opening card using safe DOM API (no innerHTML) ——
    $openingEl.innerHTML = '';
    if(name){
      $openingEl.appendChild(_el('div', 'ob-name', name));
      if(eco){
        const ecoRow = _el('div', 'ob-eco-row');
        ecoRow.appendChild(_el('span', 'ob-eco-badge', eco));
        $openingEl.appendChild(ecoRow);
      }
      if(_inBook){
        const bRow = _el('div', 'ob-book-row');
        bRow.appendChild(_el('span', 'ob-badge', '\uD83D\uDCD8 Book Move'));
        $openingEl.appendChild(bRow);
      }
      $openingBoxEl.classList.remove('hidden');
    } else if(_inBook){
      const bRow = _el('div', 'ob-book-row');
      bRow.appendChild(_el('span', 'ob-badge', '\uD83D\uDCD8 Book Move'));
      $openingEl.appendChild(bRow);
      $openingBoxEl.classList.remove('hidden');
    } else {
      _clearOpening();
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
    // NOTE: do NOT call _updateOpening() here.
    // For engine moves it is called from _doEngineMove with the correct in_book flag.
    // For human moves it is called explicitly from _commitMove below.
  }

  function _fmtEval(val){
    if(val == null) return '—';
    const s = val >= 0 ? '+' : '';
    return s + val.toFixed(2);
  }

  function _renderMoveList(){
    if(!$histSf) return;
    $histSf.replaceChildren();

    for(let i=0;i<halfMoves.length;i+=2){
      const row=document.createElement('div');
      row.className='h-row';

      const wMove = halfMoves[i]    || '';
      const bMove = halfMoves[i+1]  || '';
      const wRev  = reviewData[i]   || null;
      const bRev  = reviewData[i+1] || null;
      const cur   = halfMoves.length - 1;

      const renderHalf = (uci, rev, isCur) => {
        if(!uci) return null;

        const cls  = rev ? _classifyFromReview(rev) : null;
        const wrap = document.createElement('span');
        wrap.className = `h-move${isCur ? ' cur' : ''}`;

        const moveEl = document.createElement('span');
        moveEl.className = 'move-uci';
        moveEl.textContent = uci;
        wrap.appendChild(moveEl);

        if(cls){
          const symEl = document.createElement('span');
          symEl.className = `move-sym ${cls.cls}`;
          symEl.title = cls.label;
          symEl.textContent = cls.sym;
          wrap.appendChild(symEl);
        }

        // Show best alternative if played differs from best
        if(rev && rev.best && rev.best !== uci){
          const bestEl = document.createElement('span');
          bestEl.className = 'move-best';
          bestEl.title = `Best: ${rev.best}`;
          bestEl.textContent = `→ ${rev.best}`;
          wrap.appendChild(bestEl);
        }

        // Show eval change as small subscript
        if(rev && (rev.eval_before != null || rev.eval_after != null)){
          const eb = _fmtEval(rev.eval_before);
          const ea = _fmtEval(rev.eval_after);
          const evalEl = document.createElement('span');
          evalEl.className = 'move-eval';
          evalEl.textContent = `${eb} → ${ea}`;
          wrap.appendChild(evalEl);
        }

        return wrap;
      };

      const num = document.createElement('span');
      num.className = 'h-num';
      num.textContent = `${Math.floor(i/2)+1}.`;
      row.appendChild(num);

      const whiteHalf = renderHalf(wMove, wRev, cur===i);
      const blackHalf = renderHalf(bMove, bRev, bMove && cur===i+1);
      if(whiteHalf) row.appendChild(whiteHalf);
      if(blackHalf) row.appendChild(blackHalf);

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
    _moveSeq++;
    if(window.engineMoveTimeout) {
      clearTimeout(window.engineMoveTimeout);
      window.engineMoveTimeout = null;
    }
    if(gameOver) gameOver=false;
    try{
      let data=await POST('/undo',{});
      const mode=window.App?.getMode();
      // If playing vs engine, and the first undo left it as the engine's turn, undo once more
      if((mode==='engine'||mode==='stockfish') && data.current_turn !== playerColor && data.can_undo){
        data=await POST('/undo',{});
      }
      _rebuildCap(data.board);
      lastMove=null; selected=null; legal=[];
      _clearHint(); _prevBestFrom=null; _prevBestTo=null;
      applyState(data);
      _rebuildMoveList(data.move_history);
      _updateTurnStatus();
      _refreshEval();
      _bmRefresh();
      // refresh opening card after undo; _updateOpening(undefined) will also reset
      // _leftBook if server says we're back in book (allows toast to fire again later).
      await _updateOpening();
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
      // refresh opening card after redo; _updateOpening(undefined) will also reset
      // _leftBook if server says we're back in book (allows toast to fire again later).
      await _updateOpening();
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
    _bmSetLoading('Computing…');
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
      .catch(()=>{ _bmPending=false; _clearHint(); render(); _bmSetLoading('—'); });
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
      _bmSetRows([
        { label: 'Played', value: played },
        { label: 'Best was', value: best, match: best === played },
      ]);
    } else {
      _bmShowPanel();
      _bmSetLoading('No suggestion.');
    }
  }

  function _bmRenderCurrentPanel(data){
    const eng = data.engine    || '—';
    const sf  = data.stockfish || '—';
    _bmShowPanel();
    _bmSetRows([
      { label: 'Engine', value: eng },
      { label: 'Stockfish', value: sf },
    ]);
  }

  function _bmSetLoading(text){
    if(!$bmPanel) return;
    const el = document.createElement('div');
    el.className = 'bm-loading';
    el.textContent = text;
    $bmPanel.replaceChildren(el);
  }
  function _bmSetRows(rows){
    if(!$bmPanel) return;
    $bmPanel.replaceChildren();
    for(const row of rows){
      const wrap = document.createElement('div');
      wrap.className = 'bm-row';
      const label = document.createElement('span');
      label.className = 'bm-lbl';
      label.textContent = row.label;
      const value = document.createElement('span');
      value.className = `bm-val${row.match ? ' bm-match' : ''}`;
      value.textContent = row.value;
      wrap.append(label, value);
      $bmPanel.appendChild(wrap);
    }
  }
  function _bmShowPanel(){ document.getElementById('bm-box')?.classList.remove('hidden'); }
  function _bmHidePanel(){
    document.getElementById('bm-box')?.classList.add('hidden');
    if($bmPanel) $bmPanel.replaceChildren();
  }
    _bmPending=false;
    if($histSf)    $histSf.replaceChildren();
    if($bmPanel)   $bmPanel.replaceChildren();
    _clearOpening();
    _inBook  = false;
    _leftBook = false;
    setStatus('dot-t','Resetting…');
    const data=await GET('/state');
    applyState(data);
    _updateTurnStatus();
    if(_bmMode!=='none') _bmRefresh();
    else _bmHidePanel();
  }

  /**
   * resign(mode)
   * Immediately ends the game with a resignation.
   * In engine modes the human player always resigns; in HvH the current-turn player resigns.
   */
  /** Return a copy of the current half-move list (for session saves). */
  function getMoves(){ return halfMoves.slice(); }

  /**
   * applyRestoredState(data, moves)
   * Used by the session-save restore flow.
   * Applies /load_position server response and rebuilds the move list.
   */
  function applyRestoredState(data, moves){
    // Rebuild internal move list (no review data — just UCI strings)
    halfMoves  = Array.isArray(moves) ? moves.filter(m => typeof m === 'string' && UCI_RE.test(m)) : [];
    reviewData = halfMoves.map(()=>null);
    // Clear game-over and selection state
    gameOver=false; selected=null; legal=[]; lastMove=null;
    promoWait=null; _gameResult=null;
    _inBook=false; _leftBook=false;
    _clearHint(); _prevBestFrom=null; _prevBestTo=null;
    _clearOpening();
    applyState(data);
    _rebuildCap(data.board);
    _updateTurnStatus();
    _updateOpening(); // floating promise is fine here
    if($histSf)  $histSf.replaceChildren();
    _renderMoveList();
  }

  function resign(mode){
    if(gameOver) return;
    const resigningColor = (mode && mode !== 'hvh') ? playerColor : turn;
    const winner = resigningColor === 'white' ? 'black' : 'white';
    gameOver = true;
    _gameResult = winner === 'white' ? '1-0' : '0-1';
    _clearHint();
    render();
    _showOver({ status:'resign', resigned:resigningColor, winner });
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
    resign,
    getMoves,
    applyRestoredState,
  };
})();
