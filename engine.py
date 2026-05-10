import random
import logging as _log
_logger = _log.getLogger(__name__)

########################################
# BOARD INITIALIZATION
########################################
board=[
    ["r","n","b","q","k","b","n","r"],
    ["p","p","p","p","p","p","p","p"],
    [".",".",".",".",".",".",".","."],
    [".",".",".",".",".",".",".","."],
    [".",".",".",".",".",".",".","."],
    [".",".",".",".",".",".",".","."],
    ["P","P","P","P","P","P","P","P"],
    ["R","N","B","Q","K","B","N","R"],
]
PIECE_VALUE={
"P":100,"N":320,"B":330,"R":500,"Q":900,"K":20000,"p":100,"n":320,"b":330,"r":500,"q":900,"k":20000
}
pieces = ["P","N","B","R","Q","K","p","n","b","r","q","k"]

zobrist_table = {}
zobrist_turn = random.getrandbits(64)
zobrist_enpassant = [random.getrandbits(64) for _ in range(8)]
# Castling-rights Zobrist keys: [white_K, white_Q, black_k, black_q]
zobrist_castling = [random.getrandbits(64) for _ in range(4)]

for piece in pieces:
    zobrist_table[piece] = [random.getrandbits(64) for _ in range(64)]

history_heuristic = {}

principal_variation_move = None

transposition_table = {}
killer_moves=[[None,None] for _ in range(50)]


ENGINE_DEPTH = 3
current_turn="white"
white_king_moved=False
black_king_moved=False

white_rook_a_moved=False
white_rook_h_moved=False

black_rook_a_moved=False
black_rook_h_moved=False

en_passant_target=None
halfmove_clock = 0
position_history = {}

########################################
# ACTUAL GAME FUNCTIONS
########################################
def engine_move(board,depth):
    _logger.debug("%s engine thinking...", current_turn)
    best=iterative_deepening(board,depth)
    if best is None:
        _logger.debug("Game over")
        return False
    _logger.debug("Engine plays: %s -> %s", best[0], best[1])
    move_piece_notation(board,best[0],best[1])
    if is_checkmate(board,current_turn) or is_stalemate(board,current_turn):
        _logger.debug("Game already finished")
        return False
    return True

def human_move(board):
    move=input("Enter move (e2 e4): ").split()

    if len(move)!=2:
        _logger.debug("Invalid format")
        return
    from_sq,to_sq=move
    move_piece_notation(board,from_sq,to_sq)

def engine_vs_engine(board):
    print_board(board)

    while True:
        if is_checkmate(board,current_turn) or is_stalemate(board,current_turn):
            break
        if not engine_move(board,ENGINE_DEPTH):
            break

def human_vs_engine(board):
    print_board(board)

    while True:
        if current_turn=="white":
            human_move(board)
        else:
            if not engine_move(board,ENGINE_DEPTH):
                break


########################################
# UTILITY FUNCTIONS
########################################
def hash_board(board, turn):
    """
    Compute a Zobrist hash for the position key used in threefold-repetition
    detection.  Includes: piece placement, side to move, castling rights,
    en-passant file.  Does NOT include halfmove clock or fullmove number
    (per FIDE rules).
    """
    h = 0

    # Side to move
    if turn == "white":
        h ^= zobrist_turn

    # Piece placement
    for r in range(8):
        for c in range(8):
            piece = board[r][c]
            if piece != ".":
                h ^= zobrist_table[piece][r * 8 + c]

    # Castling rights  (white-K, white-Q, black-k, black-q)
    if not white_king_moved and not white_rook_h_moved:
        h ^= zobrist_castling[0]
    if not white_king_moved and not white_rook_a_moved:
        h ^= zobrist_castling[1]
    if not black_king_moved and not black_rook_h_moved:
        h ^= zobrist_castling[2]
    if not black_king_moved and not black_rook_a_moved:
        h ^= zobrist_castling[3]

    # En-passant file (only the file matters per FIDE)
    if en_passant_target:
        _, col = en_passant_target
        h ^= zobrist_enpassant[col]

    return h

def copy_board(board):
    new_board=[]
    for row in board:
        new_board.append(row[:])
    return new_board

def can_capture(piece,target):
    if target==".":
        return True
    if piece.isupper() and target.islower():
        return True
    if piece.islower() and target.isupper():
        return True
    return False

def notation_to_index(square):
    file=square[0]
    rank=int(square[1])

    col=ord(file)-ord('a')  
    row=8-rank

    return row,col

def index_to_notation(row,col):
    file=chr(col+ord('a'))
    rank=str(8-row)
    return file+rank


########################################
# BOARD DISPLAY
########################################
def print_board(board):
    # Terminal-mode display only; not called from Flask routes.
    for i in range(8):
        _logger.debug("%d %s", 8-i, " ".join(board[i]))
    _logger.debug(" a b c d e f g h")


########################################
# PIECE SQUARE TABLES
########################################

pawn_table = [
[0,0,0,0,0,0,0,0],
[5,5,5,5,5,5,5,5],
[1,1,2,3,3,2,1,1],
[0.5,0.5,1,2.5,2.5,1,0.5,0.5],
[0,0,0,2,2,0,0,0],
[0.5,-0.5,-1,0,0,-1,-0.5,0.5],
[0.5,1,1,-2,-2,1,1,0.5],
[0,0,0,0,0,0,0,0]
]

knight_table = [
[-5,-4,-3,-3,-3,-3,-4,-5],
[-4,-2,0,0,0,0,-2,-4],
[-3,0,1,1.5,1.5,1,0,-3],
[-3,0.5,1.5,2,2,1.5,0.5,-3],
[-3,0,1.5,2,2,1.5,0,-3],
[-3,0.5,1,1.5,1.5,1,0.5,-3],
[-4,-2,0,0.5,0.5,0,-2,-4],
[-5,-4,-3,-3,-3,-3,-4,-5]
]

bishop_table = [
[-2,-1,-1,-1,-1,-1,-1,-2],
[-1,0,0,0,0,0,0,-1],
[-1,0,0.5,1,1,0.5,0,-1],
[-1,0.5,0.5,1,1,0.5,0.5,-1],
[-1,0,1,1,1,1,0,-1],
[-1,1,1,1,1,1,1,-1],
[-1,0.5,0,0,0,0,0.5,-1],
[-2,-1,-1,-1,-1,-1,-1,-2]
]

rook_table = [
[0,0,0,0,0,0,0,0],
[0.5,1,1,1,1,1,1,0.5],
[-0.5,0,0,0,0,0,0,-0.5],
[-0.5,0,0,0,0,0,0,-0.5],
[-0.5,0,0,0,0,0,0,-0.5],
[-0.5,0,0,0,0,0,0,-0.5],
[-0.5,0,0,0,0,0,0,-0.5],
[0,0,0,0.5,0.5,0,0,0]
]

queen_table = [
[-2,-1,-1,-0.5,-0.5,-1,-1,-2],
[-1,0,0,0,0,0,0,-1],
[-1,0,0.5,0.5,0.5,0.5,0,-1],
[-0.5,0,0.5,0.5,0.5,0.5,0,-0.5],
[0,0,0.5,0.5,0.5,0.5,0,-0.5],
[-1,0.5,0.5,0.5,0.5,0.5,0,-1],
[-1,0,0.5,0,0,0,0,-1],
[-2,-1,-1,-0.5,-0.5,-1,-1,-2]
]

king_table = [
[-3,-4,-4,-5,-5,-4,-4,-3],
[-3,-4,-4,-5,-5,-4,-4,-3],
[-3,-4,-4,-5,-5,-4,-4,-3],
[-3,-4,-4,-5,-5,-4,-4,-3],
[-2,-3,-3,-4,-4,-3,-3,-2],
[-1,-2,-2,-2,-2,-2,-2,-1],
[2,2,0,0,0,0,2,2],
[2,3,1,0,0,1,3,2]
]

########################################
# POSITION EVALUATION
########################################
def evaluate_board(board):
    piece_values={"P":100,"N":320,"B":330,"R":500,"Q":900,"K":0,"p":-100,"n":-320,"b":-330,"r":-500,"q":-900,"k":0}
    score=0
    for r in range(8):
        for c in range(8):
            piece=board[r][c]
            if piece==".":
                continue
            score+=piece_values[piece]
            if piece=="P":
                score+=pawn_table[r][c]*10

            elif piece=="p":
                score-=pawn_table[7-r][c]*10

            elif piece=="N":
                score+=knight_table[r][c]*10

            elif piece=="n":
                score-=knight_table[7-r][c]*10

            elif piece=="B":
                score+=bishop_table[r][c]*10

            elif piece=="b":
                score-=bishop_table[7-r][c]*10

            elif piece=="R":
                score+=rook_table[r][c]*10

            elif piece=="r":
                score-=rook_table[7-r][c]*10

            elif piece=="Q":
                score+=queen_table[r][c]*10

            elif piece=="q":
                score-=queen_table[7-r][c]*10

            elif piece=="K":
                score+=king_table[r][c]*10

            elif piece=="k":
                score-=king_table[7-r][c]*10


    return score
########################################
# PIECE MOVEMENT RULES
########################################
def is_valid_pawn_move(board,from_row,from_col,to_row,to_col,piece):
    if not (0<=to_row<8 and 0<=to_col<8):
        return False
    
    direction=-1 if piece.isupper() else 1
    start_row=6 if piece.isupper() else 1
    
    #single forward
    if to_col==from_col and to_row==from_row+direction:
        if board[to_row][to_col]==".":
            return True
        
    #double forward
    if from_row==start_row and to_row==from_row+2*direction and to_col==from_col:
        if board[from_row+direction][from_col]=="." and board[to_row][to_col]==".":
            return True
        
    #diagonal capture
    if abs(to_col-from_col)==1 and to_row==from_row+direction:
        target=board[to_row][to_col]
        if (target)!=".":
            if piece.isupper() and target.islower():
                return True
            if piece.islower() and target.isupper():
                return True
        if(to_row,to_col)==en_passant_target:
            return True
                
               

    return False

def is_valid_rook_move(board,from_row,from_col,to_row,to_col,piece):

    if not(0<=to_row<8 and 0<=to_col<8):
        return False
    if from_row!=to_row and from_col!=to_col:
        return False
    
    row_step=0
    col_step=0

    if to_row>from_row:
        row_step=1
    elif to_row<from_row:
        row_step=-1

    if to_col>from_col:
        col_step=1
    elif to_col<from_col:
        col_step=-1

    current_row=from_row+row_step
    current_col=from_col+col_step

    while(current_row!=to_row or current_col!=to_col):
        if board[current_row][current_col]!=".":
            return False
        current_row+=row_step
        current_col+=col_step

    target=board[to_row][to_col]
    return can_capture(piece, target)
    

def is_valid_bishop_move(board,from_row,from_col,to_row,to_col,piece):
    if not(0<=to_row<8 and 0<=to_col<8):
        return False
    
    if abs(to_row-from_row)!=abs(to_col-from_col):
        return False
    
    row_step=1 if to_row>from_row else -1
    col_step=1 if to_col>from_col else -1

    current_row=from_row+row_step
    current_col=from_col+col_step

    while current_row!=to_row:
        if board[current_row][current_col]!=".":
            return False
        current_row+=row_step
        current_col+=col_step

    target=board[to_row][to_col]
    return can_capture(piece, target)

def is_valid_knight_move(board,from_row,from_col,to_row,to_col,piece):
    if not (0<=to_row<8 and 0<=to_col<8):
        return False
    
    row_diff=abs(to_row-from_row)
    col_diff=abs(to_col-from_col)

    if not ((row_diff==2 and col_diff==1)or(row_diff==1 and col_diff==2)):
        return False
    
    target=board[to_row][to_col]
    return can_capture(piece, target)
    
def is_valid_queen_move(board,from_row,from_col,to_row,to_col,piece):
    return is_valid_rook_move(board,from_row,from_col,to_row,to_col,piece) or is_valid_bishop_move(board,from_row,from_col,to_row,to_col,piece)
    
def is_valid_king_move(board,from_row,from_col,to_row,to_col,piece):
    global white_king_moved,black_king_moved
    global white_rook_a_moved,white_rook_h_moved
    global black_rook_a_moved,black_rook_h_moved

    if not (0<=to_row<8 and 0<=to_col<8):
        return False
    
    row_diff=abs(to_row-from_row)
    col_diff=abs(to_col-from_col)

    if row_diff<=1 and col_diff<=1:
        if row_diff==0 and col_diff==0:
            return False
        target=board[to_row][to_col]
        return can_capture(piece, target)
    
    if piece.isupper():
        if white_king_moved:
            return False
        if from_row==7 and from_col==4 and to_row==7 and to_col==6:
            if white_rook_h_moved or board[7][7]!="R":
                return False
            if board[7][5]!="." or board[7][6]!=".":
                return False
            if is_king_in_check(board,"white"):
                return False
            if square_under_attack(board,7,5,"white"):
                return False
            if square_under_attack(board,7,6,"white"):
                return False
            return  True
        
        if from_row==7 and from_col==4 and to_row==7 and to_col==2:
            if white_rook_a_moved or board[7][0]!="R":
                return False
            if board[7][1]!="." or board[7][2]!="." or board[7][3]!=".":
                return False
            if is_king_in_check(board,"white"):
                return False
            if square_under_attack(board,7,3,"white"):
                return False
            if square_under_attack(board,7,2,"white"):
                return False
            return True
    else:
        if black_king_moved:
            return False
        if from_row==0 and from_col==4 and to_row==0 and to_col==6:
            if black_rook_h_moved or board[0][7]!="r":
                return False
            if board[0][5]!="." or board[0][6]!=".":
                return False
            if is_king_in_check(board,"black"):
                return False
            if square_under_attack(board,0,5,"black"):
                return False
            if square_under_attack(board,0,6,"black"):
                return False
            return  True
        
        if from_row==0 and from_col==4 and to_row==0 and to_col==2:
            if black_rook_a_moved or board[0][0]!="r":
                return False
            if board[0][1]!="." or board[0][2]!="." or board[0][3]!=".":
                return False
            if is_king_in_check(board,"black"):
                return False
            if square_under_attack(board,0,3,"black"):
                return False
            if square_under_attack(board,0,2,"black"):
                return False
            return True
                        
    return False


########################################
# ATTACK DETECTION
########################################
def can_attack(board,from_row,from_col,to_row,to_col,piece):
    piece_type=piece.upper()
    if piece_type=="P":
        direction=-1 if piece.isupper() else 1
        if (abs(to_col-from_col)==1 and to_row==from_row+direction):
            return True
        return False
            
    elif piece_type=="R":
        return is_valid_rook_move(board,from_row,from_col,to_row,to_col,piece)
    elif piece_type=="B":
        return is_valid_bishop_move(board,from_row,from_col,to_row,to_col,piece)
    elif piece_type=="N":
        return is_valid_knight_move(board,from_row,from_col,to_row,to_col,piece)
    elif piece_type=="Q":
        return is_valid_queen_move(board,from_row,from_col,to_row,to_col,piece)
    elif piece_type=="K":
        row_diff=abs(to_row-from_row)
        col_diff=abs(to_col-from_col)
        return row_diff<=1 and col_diff<=1
    return False

def square_under_attack(board,row,col,color):
    opponent="black" if color=="white" else "white"

    for r in range(8):
        for c in range(8):
            piece=board[r][c]
            if piece==".":
                continue
            if opponent=="white" and piece.isupper():
                if can_attack(board,r,c,row,col,piece):
                    return True
            if opponent=="black" and piece.islower():
                if can_attack(board,r,c,row,col,piece):
                    return True
    return False



########################################
# MOVE VALIDATION
########################################
def is_valid_move(board,from_row,from_col,to_row,to_col,piece):
    piece_type=piece.upper()
    if piece_type=="P":
        return is_valid_pawn_move(board,from_row,from_col,to_row,to_col,piece)
    if piece_type=="R":
        return is_valid_rook_move(board,from_row,from_col,to_row,to_col,piece)
    if piece_type=="B":
        return is_valid_bishop_move(board,from_row,from_col,to_row,to_col,piece)
    if piece_type=="N":
        return is_valid_knight_move(board,from_row,from_col,to_row,to_col,piece)
    if piece_type=="Q":
        return is_valid_queen_move(board,from_row,from_col,to_row,to_col,piece)
    if piece_type=="K":
        return is_valid_king_move(board,from_row,from_col,to_row,to_col,piece)
    return False

def move_puts_own_king_in_check(board,from_row,from_col,to_row,to_col,piece):
    temp_board=copy_board(board)

    temp_board[to_row][to_col]=piece
    temp_board[from_row][from_col]="."

    color="white" if piece.isupper() else "black"
    return is_king_in_check(temp_board,color)


########################################
# GAME STATE DETECTION
########################################
def find_king(board,color):
    king_symbol="K" if color=="white" else "k"
    for r in range(8):
        for c in range(8):
            if board[r][c]==king_symbol:
                return r,c
    return None
        
def is_king_in_check(board,color):
    king_pos=find_king(board,color)
    if not king_pos:
        return False
    
    king_row,king_col=king_pos
    opponent="black" if color=="white" else "white"

    for r in range(8):
        for c in range(8):
            piece=board[r][c]
            if piece==".":
                    continue
            
            if opponent=="white"and piece.isupper():
                if can_attack(board,r,c,king_row,king_col,piece):
                    return True
            if opponent=="black"and piece.islower():
                if can_attack(board,r,c,king_row,king_col,piece):
                    return True
                        
    return False

def has_any_legal_moves(board,color):
    for from_row in range(8):
        for from_col in range(8):
            piece=board[from_row][from_col]
            if piece==".":
                continue
            if color=="white" and piece.islower():
                continue
            if color=="black" and piece.isupper():
                continue

            for to_row in range(8):
                for to_col in range(8):

                    if not is_valid_move(board,from_row,from_col,to_row,to_col,piece):
                        continue
                    if not move_puts_own_king_in_check(board,from_row,from_col,to_row,to_col,piece):
                        return True
    
    return False
  
def is_checkmate(board,color):
    if not is_king_in_check(board,color):
        return False
    if has_any_legal_moves(board,color):
        return False

    return True

def is_stalemate(board,color):
    if is_king_in_check(board,color):
        return False
    if has_any_legal_moves(board,color):
        return False
    
    return True
 

########################################
# MOVE GENERATION
########################################
def generate_all_legal_moves(board,color):
    moves=[]

    for from_row in range(8):
        for from_col in range(8):
            piece=board[from_row][from_col]
            if piece==".":
                continue

            if color=="white" and piece.islower():
                continue
            if color=="black" and piece.isupper():
                continue

            for to_row in range(8):
                for to_col in range(8):
                    if not is_valid_move(board,from_row,from_col,to_row,to_col,piece):
                        continue
                    if move_puts_own_king_in_check(board,from_row,from_col,to_row,to_col,piece):
                        continue
                    from_sq=index_to_notation(from_row,from_col)
                    to_sq=index_to_notation(to_row,to_col)

                    moves.append((from_sq,to_sq))

    return moves

def is_capture_move(board,move):
    from_sq,to_sq=move
    fr,fc=notation_to_index(from_sq)
    tr,tc=notation_to_index(to_sq)

    return board[tr][tc]!="."

########################################
# MOVE EXECUTION
########################################
def make_move_copy(board,from_row,from_col,to_row,to_col):
    new_board=copy_board(board)
    piece=new_board[from_row][from_col]
    new_board[to_row][to_col]=piece
    new_board[from_row][from_col]="."
    return new_board


def move_piece(board,from_row,from_col,to_row,to_col):
    piece=board[from_row][from_col]
    board[to_row][to_col]=piece
    board[from_row][from_col]="."

def move_piece_notation(board,from_square,to_square):
    global current_turn
    global en_passant_target
    global halfmove_clock
   
    from_row,from_col=notation_to_index(from_square)
    to_row,to_col=notation_to_index(to_square)

    piece=board[from_row][from_col]

    if piece==".":
        _logger.debug("No piece at source square")
        return

    if current_turn=="white" and piece.islower():
        _logger.debug("It is white's turn")
        return
    if current_turn=="black" and piece.isupper():
        _logger.debug("It is black's turn")
        return

    if not is_valid_move(board,from_row,from_col,to_row,to_col,piece):
        _logger.debug("Invalid move")
        return

    if move_puts_own_king_in_check(board,from_row,from_col,to_row,to_col,piece):
        _logger.debug("Illegal move: King would be in check")
        return
    
    _logger.debug("%s: %s -> %s", current_turn, from_square, to_square)
    previous_en_passant=en_passant_target
    target_piece = board[to_row][to_col]
    move_piece(board,from_row,from_col,to_row,to_col)
    
    if piece.upper()=="P" or target_piece!=".":
        halfmove_clock = 0
    else:
        halfmove_clock += 1

    if piece.upper()=="P" and (to_row,to_col)==previous_en_passant:
        if piece.isupper():
            board[to_row+1][to_col]="."
        else:
            board[to_row-1][to_col]="."
    en_passant_target=None
    if piece.upper()=="P" and abs(to_row-from_row)==2:
        en_passant_target=((from_row+to_row)//2,from_col)
    if piece =="P" and to_row==0:
        board[to_row][to_col]="Q"
    if piece =="p" and to_row==7:
        board[to_row][to_col]="q"
    if piece.upper()=="K":
        if from_col==4 and to_col==6:
            move_piece(board,from_row,7,from_row,5)
        elif from_col==4 and to_col==2:
            move_piece(board,from_row,0,from_row,3)

    global white_king_moved,black_king_moved
    global white_rook_a_moved,white_rook_h_moved
    global black_rook_a_moved,black_rook_h_moved

    if piece=="K":
        white_king_moved=True
    if piece=="k":
        black_king_moved=True

    if piece=="R":
        if from_row==7 and from_col==0:
            white_rook_a_moved=True
        if from_row==7 and from_col==7:
            white_rook_h_moved=True
    if piece=="r":
        if from_row==0 and from_col==0:
            black_rook_a_moved=True
        if from_row==0 and from_col==7:
            black_rook_h_moved=True
        
            

    # Flip turn. This is the ONLY global side-effect besides board/castling state.
    # NOTE: position_history is intentionally NOT updated here.
    #   move_piece_notation() is called in temp/analysis contexts too
    #   (_build_move_review_entry, etc.).  Updating position_history here would
    #   increment it once per CALL (not once per real game move), causing false
    #   threefold-repetition triggers.  Repetition tracking is handled exclusively
    #   by app.py route handlers after each confirmed real game move.
    current_turn = "black" if current_turn == "white" else "white"

    # Terminal-mode game-over prints (only relevant when running engine.py directly).
    if is_checkmate(board, current_turn):
        _logger.debug("Checkmate! %s wins!", 'white' if current_turn=='black' else 'black')
        return
    elif halfmove_clock >= 100:
        _logger.debug("Draw by 50-move rule!")
        return
    elif is_stalemate(board, current_turn):
        _logger.debug("Stalemate! Draw.")
        return
    elif is_king_in_check(board, current_turn):
        _logger.debug("%s king is in check!", current_turn)
    _logger.debug("[engine] board updated")
    
########################################
# QUIESCENCE SEARCH
########################################
def quiescence(board,alpha,beta,maximizing_player,depth=0):
    stand_pat=evaluate_board(board)
    if depth>=4:
        return stand_pat
    if maximizing_player:
        if stand_pat>=beta:
            return beta
        if alpha<stand_pat:
            alpha=stand_pat
        moves = generate_capture_moves(board,"white")
        moves.sort(key=lambda m: score_moves(board,m), reverse=True)
        for move in moves:
            from_sq,to_sq=move
            fr,fc=notation_to_index(from_sq)
            tr,tc=notation_to_index(to_sq)

            new_board=make_move_copy(board,fr,fc,tr,tc)
            score=quiescence(new_board,alpha,beta,False,depth+1)

            if score>=beta:
                return beta
            if score>alpha:
                alpha=score
        return alpha
    else:
        if stand_pat<=alpha:
            return alpha
        if beta>stand_pat:
            beta=stand_pat
        moves = generate_capture_moves(board,"black")
        moves.sort(key=lambda m: score_moves(board,m), reverse=True)
        for move in moves:
            from_sq,to_sq=move
            fr,fc=notation_to_index(from_sq)
            tr,tc=notation_to_index(to_sq)


            new_board=make_move_copy(board,fr,fc,tr,tc)
            score=quiescence(new_board,alpha,beta,True,depth+1)

            if score<=alpha:
                return alpha
            if score<beta:
                beta=score
        return beta
        

def generate_capture_moves(board,color):
    moves=[]
    for from_row in range(8):
        for from_col in range(8):
            piece=board[from_row][from_col]

            if piece==".":
                continue
            if color=="white" and piece.islower():
                continue
            if color=="black" and piece.isupper():
                continue
            for to_row in range(8):
                for to_col in range(8):
                    if board[to_row][to_col]==".":
                        continue
                    if not is_valid_move(board,from_row,from_col,to_row,to_col,piece):
                        continue
                    if move_puts_own_king_in_check(board,from_row,from_col,to_row,to_col,piece):
                        continue
                    moves.append((index_to_notation(from_row,from_col),index_to_notation(to_row,to_col)))
    return moves



########################################
# MINIMAX SEARCH
########################################
piece_value = PIECE_VALUE
def score_moves(board,move):
    from_sq,to_sq=move
    fr,fc=notation_to_index(from_sq)
    tr,tc=notation_to_index(to_sq)

    piece=board[fr][fc]
    target=board[tr][tc]

    score=0

    
    if target!=".":
        score += 10000 + piece_value[target]*10 - piece_value[piece]
    score += history_heuristic.get(move, 0)
    if piece.upper()=="P":
        if(piece.isupper() and tr==0) or (piece.islower() and tr==7):
            score+=9000
    if(tr,tc)==(3,3) or (tr,tc)==(3,4) or (tr,tc)==(4,3) or (tr,tc)==(4,4):
        score+=50
    return score
    
def minimax(board,depth,alpha,beta,maximizing_player):

    if len(transposition_table) > 200000:
        transposition_table.clear()
    turn = "white" if maximizing_player else "black"
    board_key = hash_board(board,turn)

    entry = transposition_table.get(board_key)
    if entry:
        stored_depth, stored_value = entry
        if stored_depth>=depth:
            return stored_value
    
    if depth==0:
        return quiescence(board,alpha,beta,maximizing_player)
    if maximizing_player:
        max_eval=-9999
        moves=generate_all_legal_moves(board,"white")
        moves.sort(key=lambda m: score_moves(board,m), reverse=True)        
        for move in moves:
            from_sq,to_sq=move
            fr,fc=notation_to_index(from_sq)
            tr,tc=notation_to_index(to_sq)

            new_board=make_move_copy(board,fr,fc,tr,tc)
            eval=minimax(new_board,depth-1,alpha,beta,False)
            max_eval=max(max_eval,eval)
            alpha=max(alpha,eval)

            if beta<=alpha:
                if not is_capture_move(board,move):
                    killer_moves[depth][1]=killer_moves[depth][0]
                    killer_moves[depth][0]=move   
                    history_heuristic[move]=history_heuristic.get(move, 0)+depth*depth                 
                break
        transposition_table[board_key]=(depth,max_eval)
        return max_eval
    else:
        min_eval=9999
        moves=generate_all_legal_moves(board,"black")
        moves.sort(key=lambda m: score_moves(board,m), reverse=True)        
        for move in moves:
            from_sq,to_sq=move
            fr,fc=notation_to_index(from_sq)
            tr,tc=notation_to_index(to_sq)

            new_board=make_move_copy(board,fr,fc,tr,tc)
            eval=minimax(new_board,depth-1,alpha,beta,True)
            min_eval=min(min_eval,eval)
            beta=min(beta,eval)

            if beta<=alpha:
                if not is_capture_move(board,move):
                    killer_moves[depth][1]=killer_moves[depth][0]
                    killer_moves[depth][0]=move   
                    history_heuristic[move]=history_heuristic.get(move, 0)+depth*depth
                break
        transposition_table[board_key] =(depth,min_eval)
        return min_eval
    
########################################
# BEST MOVE SEARCH
########################################
def find_best_move(board,depth):

    global current_turn

    best_move=None
    if current_turn=="white":
        best_eval=-9999
    else:
        best_eval=9999

    moves=generate_all_legal_moves(board,current_turn)

    if principal_variation_move and principal_variation_move in moves:
        moves.remove(principal_variation_move)
        moves.insert(0, principal_variation_move)

    moves.sort(key=lambda m: score_moves(board,m), reverse=True)    
    for move in moves:
        from_sq,to_sq=move
        fr,fc=notation_to_index(from_sq)
        tr,tc=notation_to_index(to_sq)
        
        new_board=make_move_copy(board,fr,fc,tr,tc)

        if current_turn=="white":
            eval=minimax(new_board,depth-1,-9999,9999,False)
            if eval>best_eval:
                best_eval=eval
                best_move=move
        else:
            eval=minimax(new_board,depth-1,-9999,9999,True)
            if eval<best_eval:
                best_eval=eval
                best_move=move
    return best_move, best_eval


def iterative_deepening(board,max_depth):
    global principal_variation_move
    best_move=None

    for depth in range(1,max_depth+1):
        _logger.debug("Searching depth %d...", depth)
        move, score = find_best_move(board, depth)
        if move is not None:
            best_move=move
            principal_variation_move=move
            _logger.debug("Best move at depth %d: %s", depth, best_move)
    return best_move,score



########################################
# TESTING
########################################

if __name__ == "__main__":
    position_history[hash_board(board,current_turn)] = 1
    # move_piece_notation(board,"e2","e4")
    # print_board(board)

    # move_piece_notation(board,"e7","e5")
    # print_board(board)

    # move_piece_notation(board,"g1","f3")
    # print_board(board)

    # move_piece_notation(board,"b8","c6")
    # print_board(board)

    # move_piece_notation(board,"f1","c4")
    # print_board(board)

    # move_piece_notation(board,"f8","c5")
    # print_board(board)

    # move_piece_notation(board,"d1","e2")
    # print_board(board)

    # move_piece_notation(board,"d8","e7")
    # print_board(board)

    # move_piece_notation(board,"e1","d1")
    # print_board(board)

    # move_piece_notation(board,"e8","d8")
    # print_board(board)

    # move_piece_notation(board,"e2","e4")
    # move_piece_notation(board,"f7","f6")
    # move_piece_notation(board,"d1","h5")



    # move_piece_notation(board,"e2","e4")
    # move_piece_notation(board,"d8","h4")
    # move_piece_notation(board,"a2","a3")

    # move_piece_notation(board,"f2","f3")
    # move_piece_notation(board,"e7","e5")
    # move_piece_notation(board,"g2","g4")
    # move_piece_notation(board,"d8","h4")

    # move_piece_notation(board,"e2","e4")
    # move_piece_notation(board,"e7","e5")

    # move_piece_notation(board,"g1","f3")
    # move_piece_notation(board,"b8","c6")

    # move_piece_notation(board,"f1","e2")
    # move_piece_notation(board,"g8","f6")

    # move_piece_notation(board,"e1","g1")


    # move_piece_notation(board,"e2","e4")
    # move_piece_notation(board,"a7","a6")

    # move_piece_notation(board,"e4","e5")
    # move_piece_notation(board,"d7","d5")

    # move_piece_notation(board,"e5","d6")

    # print(len(generate_all_legal_moves(board,"white")))
    # print(evaluate_board(board))

    # print(minimax(board,1,True))
    # best = find_best_move(board,2)
    # print(best)

    # best = find_best_move(board,3)
    # if best is None:
    #     print("Game over")
    # else:
    #     move_piece_notation(board,best[0],best[1])
    print("1 - Engine vs Engine")  # terminal mode only
    print("2 - Human vs Engine")

    choice=input("Select mode: ")

    if choice=="1":
        engine_vs_engine(board)

    elif choice=="2":
        human_vs_engine(board)

