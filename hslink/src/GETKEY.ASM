PAGE	58,	132
TITLE	GETKEY
SUBTTL GETKEY

EXTRN COL:word
EXTRN COL_HOLD:word
EXTRN ROW:byte
EXTRN FIL_HND:word
EXTRN inv_screen_attr:byte
EXTRN screen_attr:byte

.model small

IF1
INCLUDE 	ASSEMBLY.MAC
ENDIF

PAGE
LSE_KEY SEGMENT PUBLIC 'CODE'

SUBTTL GETKEY
GETKEY	PROC	FAR
	ASSUME	CS:LSE_KEY
	PUBLIC	GETKEY
START_GET_KEY:
;------------------------------------------------------------------------------
	PUSH	CX
	CALC_ADDR ROW,COL	  ;SET DI TO CURRENT LOCATION
	CURSOR

	MOV	AH,0
	INT	16H	       ;GET CHARACTER FROM KEYBOARD
WHAT_KEY:
	CMP		AL,0	;EXTENDED FUNCUTION KEY?
	JNE		A1
	CALL	EXKEY	;call to extended key handler
	JMP	EXIT
A1:
	CMP	AH,48H
	JE	TEN_KEY
	CMP	AH,4BH
	JE	TEN_KEY
	CMP	AH,4DH
	JE	TEN_KEY
	CMP	AH,50H
	JE	TEN_KEY
	JMP	SHORT A2
TEN_KEY:
	MOV	AH,0
A2:
	CMP	AL,08H	;BACKSPACE?
	JE	DEC_COL
	JMP	ESC?
DEC_COL:
	PUSH	BX
	DEC	COL
	CURSOR
	INC	COL
	CMP	BH,inv_screen_attr   ;make sure that we stay in field
	JNE	SKIP_BS
	CMP	BL,'.'               ;IF DECIMAL DON'T DELETE
	JE	EXIT_BS
	CMP	COL,1
	JLE	BS
	DEC	COL
BS:
	CURSOR
	MOV	BL,' '
	MOV	ES:[DI],BX
	MOV	ES:[DI-2],BX
	JMP	SHORT EXIT_BS
SKIP_BS:
	CURSOR
	MOV	BL,' '
	MOV	ES:[DI],BX
EXIT_BS:
	POP	BX
	JMP	EXIT

ESC?:	CMP	AL,1BH		;ESC?
	JNE	CR?
	MOV	AH,27
	JMP	EXIT

CR?:	CMP	AL,0DH		;CR?
	JNE	TAB_RIGHT
	MOV	AH,13
	JMP	EXIT

TAB_RIGHT:
	CMP	AX,0F09H	  ;TAB RIGHT?
	JNE	SCB
TR:
	JMP	EXIT

SCB:	CMP	AX,4B34H  ;is it the shift cursor back key?
	JNE	SCF	  ;if not, try next
;	CMP	COL,2
;	JLE	STOP_DEC
;	CMP	BL,'.'               ;IF DECIMAL DON'T DELETE
;	JE	STOP_DEC
;	MOV	BL,' '
;	MOV	ES:[DI-2],BX
;	DEC	COL
STOP_DEC:
;	JMP	PRINTABLE

SCF:	CMP	AX,4D36H  ;is it the shift cursor forward key?
;	JNE	SCU	  ;if not, try next
;	CMP	COL,78
;	JGE	STOP_INC
;	MOV	BL,' '
;	MOV	ES:[DI+2],BX
;	INC	COL
STOP_INC:
;	JMP	PRINTABLE

SCU:	CMP	AX,4838H  ;is it the shift cursor up key?
	JNE	SCD	  ;if not, try next
	MOV	BL,' '
	MOV	ES:[DI-160],BX
	CMP	ROW,1
	JLE	SKIP_SCU
	DEC	ROW
SKIP_SCU:
	JMP	SHORT PRINTABLE
;
SCD:	CMP	AX,5032H  ;is it the shift cursor down key?
	JNE	CASE
	MOV	BL,' '
	MOV	ES:[DI+160],BX
	CMP	ROW,23
	JGE	PRINTABLE
	INC	ROW
	JMP	SHORT PRINTABLE
CASE:
	MOV	BL,AL
	CMP	BL,' '
	JB	EXIT
	CMP	BL,'~'
	JA	EXIT
	CMP	BL,'a'
	JB	UPPERCASE
	CMP	BL,'z'
	JA	UPPERCASE
	AND	BL,5FH	   ;mask out bit number 5, make lowercase upper case
UPPERCASE:
	MOV	ES:[DI],BX	;Display character on screen
	INC	COL
	PUSH	BX
	CURSOR
	DEC	COL
	CMP	BH,70H
	POP	BX
	MOV	AX,0
	JNE	EXIT
	INC	COL
PRINTABLE:
	MOV	AX,0
EXIT:			       ;All done. clean up and return
	CALC_ADDR ROW,COL	  ;SET DI TO CURRENT LOCATION
	POP	CX
get_ret:
	RET
GETKEY	ENDP

;************************** EXKEY ********************************************
;*****************************************************************************
;*****************************************************************************
;*****************************************************************************

EXKEY	PROC	NEAR
START_EX_KEY:

CB:	CMP	AH,4BH	;is it the cursor back key?
	JNE	CF	;if not, try next
	CMP	COL,1
	JLE	SKIP_POP
	PUSH	BX
	DEC	COL
	CURSOR			     ;look for decimal point
	INC	COL
	CMP	BH,INV_SCREEN_ATTR	;stay inside of inverted field
	JNE	SKIP_CB
	CMP	BL,'.'               ;decimal marks begining of field
	JE	SKIP_CB
	POP	BX
	CURSOR			     ;look for decimal point
	MOV	ES:[DI],BX
	DEC	COL
	JMP	EXIT_EX_KEY		;JUMP TO EXKEY RETURN
SKIP_CB:
	CURSOR			     ;look for decimal point
	POP	BX
SKIP_POP:
	JMP	EXIT_EX_KEY		;JUMP TO EXKEY RETURN

CF:	CMP	AH,4DH	;is it the cursor forward key?
	JNE	CU	;if not, try next
	INC	COL
	PUSH	BX
	CURSOR
	DEC	COL
	CMP	BH,70H
	POP	BX
	JNE	SKIP_CF
	CMP	COL,78
	JGE	SKIP_CF
	MOV	ES:[DI],BX
	INC	COL
SKIP_CF:
	JMP	EXIT_EX_KEY

CU:	CMP	AH,48H	;is it the cursor up key?
	JNE	CDN    ;if not, try next
;	CMP	ROW,2
;	JLE	SKIP_CU
;	MOV	ES:[DI],BX
;	DEC	ROW
;SKIP_CU:
	JMP	EXIT_EX_KEY

CDN:	CMP	AH,50H	;is it the cursor down key?
	JNE	TAB_LEFT
;	CMP	ROW,23
;	JGE	SKIP_CD
;	MOV	ES:[DI],BX
;	INC	ROW
;SKIP_CD:
	JMP	EXIT_EX_KEY

TAB_LEFT:
	CMP	AX,0F00H	  ;TAB LEFT?
	JNE	BREAK
TL:
	JMP	EXIT_EX_KEY

BREAK:	CMP	AH,0H	;BREAK ?
	JNE	GKEND
	xor	ax,ax
	mov	ah,3EH		;close file
	mov	bx,fil_hnd
	int	21H
	nop
	MOV	AH,05
	MOV	DL,13
	INT	21H
	MOV	AH,8
	INT	21H
	JMP	EXIT_EX_KEY

GKEND:	CMP	AH,4FH	       ;END KEY?
	JE	END_KEY
	JMP	SHORT CE
END_KEY:
;	 MOV	 COL,78
;BACKDENT:
;	 CURSOR
;	 CMP	 BH,07
;	 JNE	 FIND_INV_CHAR
;	 CMP	 COL,1
;	 JLE	 END_LINE
;	 DEC	 COL
;	 JMP	 BACKDENT
;FIND_INV_CHAR:
;	 CURSOR
;	 CMP	 BH,70H
;	 JNE	 END_COL
;	 CMP	 BL,' '
;	 JNE	 END_COL
;	 DEC	 COL
;	 CMP	 COL,1
;	 JLE	 END_LINE
;	 JMP	 FIND_INV_CHAR
;END_LINE:
;	 MOV	 COL,1
;END_COL:
;	 INC	 COL
	JMP	EXIT_EX_KEY
;
CE:	CMP	AH,75H
	JNE	HOME
	JMP	EXIT_EX_KEY

HOME:	CMP	AH,47H
	JNE	CTH
     ;	 MOV	 COL,1
;INDENT:
;	 CURSOR
;	 CMP	 BH,07
;	 JNE	 INV_CHAR
;	 CMP	 COL,78
;	 JGE	 END_INDENT
;	 INC	 COL
;	 JMP	 INDENT
;INV_CHAR:
;	 CURSOR
;	 JMP	 EXIT_EX_KEY
;END_INDENT:
;	 MOV	 COL,1
	JMP	EXIT_EX_KEY

CTH:	CMP	AH,77H
	JNE	INSERT
     ;	 MOV	 ROW,2
     ;	 MOV	 COL,19
	JMP	EXIT_EX_KEY

INSERT: CMP	AH,52H
	JNE	DEL
	JMP	EXIT_EX_KEY

DEL:	CMP	AH,53H
	JNE	PU
	JMP	EXIT_EX_KEY

PU:	CMP	AH,49H
	JNE	PD
	JMP	EXIT_EX_KEY

PD:	CMP	AH,51H
	JNE	F1X
	JMP	EXIT_EX_KEY
F1X:
	CMP	AH,3BH	;F1?
	JNE	F2
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

F2:	CMP	AH,3CH	;F2?
	JNE	F3
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

F3:	CMP	AH,3DH	;F3?
	JNE	F4
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

F4:	CMP	AH,3EH	;F4?
	JNE	F5
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

F5:	CMP	AH,3FH	;F5?
	JNE	F6
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

F6:	CMP	AH,40H	;F4?
	JNE	F7
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

F7:	CMP	AH,41H	;F7
	JNE	F8
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

F8:	CMP	AH,42H	;F8
	JNE	F9
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

F9:	CMP	AH,43H	;F9
	JNE	F10
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

F10:	CMP	AH,44H	;F10
	JNE	AF1
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

AF1:	CMP	AH,68H	;AF1?
	JNE	AF2
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

AF2:	CMP	AH,69H	;AF2?
	JNE	AF3
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

AF3:	CMP	AH,6AH	;AF3?
	JNE	AF4
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

AF4:	CMP	AH,6BH	;AF4?
	JNE	AF5
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

AF5:	CMP	AH,6CH	;AF5?
	JNE	AF6
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

AF6:	CMP	AH,6DH	;AF4?
	JNE	AF7
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

AF7:	CMP	AH,6EH	;AF7
	JNE	AF8
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

AF8:	CMP	AH,6FH	;AF8
	JNE	AF9
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

AF9:	CMP	AH,70H	;ALT F9?
	JNE	AF10
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

AF10:	CMP	AH,71H	;ALT F10?
	JNE	CF1
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

CF1:	CMP	AH,5EH	;CF1?
	JNE	CF2
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

CF2:	CMP	AH,5FH	;CF2?
	JNE	CF3
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

CF3:	CMP	AH,60H	;CF3?
	JNE	CF4
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

CF4:	CMP	AH,61H	;CF4?
	JNE	CF5
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

CF5:	CMP	AH,62H	;CF5?
	JNE	CF6
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

CF6:	CMP	AH,63H	;CF6?
	JNE	CF7
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

CF7:	CMP	AH,64H	;CF7
	JNE	CF8
	MOV	BL,198
	MOV	ES:[DI],BX
	JMP	EXIT_EX_KEY

CF8:	CMP	AH,65H	;CF8
	JNE	CF9
	MOV	ES:[DI],BX
	JMP	SHORT EXIT_EX_KEY

CF9:	CMP	AH,66H	;ALT F9?
	JNE	CF10
	MOV	ES:[DI],BX
	JMP	SHORT EXIT_EX_KEY

CF10:	CMP	AH,67H	;ALT F10?
	JNE	SF1
	MOV	ES:[DI],BX
	JMP	SHORT EXIT_EX_KEY


SF1:	CMP	AH,54H	;SF1?
	JNE	SF2
	MOV	ES:[DI],BX
	JMP	SHORT EXIT_EX_KEY

SF2:	CMP	AH,55H	;SF2?
	JNE	SF3
	MOV	ES:[DI],BX
	JMP	SHORT EXIT_EX_KEY

SF3:	CMP	AH,56H	;SF3?
	JNE	SF4
	MOV	ES:[DI],BX
	JMP	SHORT EXIT_EX_KEY

SF4:	CMP	AH,57H	;SF4?
	JNE	SF5
	MOV	ES:[DI],BX
	JMP	SHORT EXIT_EX_KEY

SF5:	CMP	AH,58H	;F5?
	JNE	SF6
	MOV	ES:[DI],BX
	JMP	SHORT EXIT_EX_KEY

SF6:	CMP	AH,59H	;SF6?
	JNE	SF7
	MOV	ES:[DI],BX
	JMP	SHORT EXIT_EX_KEY

SF7:	CMP	AH,5AH	;SF7
	JNE	SF8
	MOV	ES:[DI],BX
	JMP	SHORT EXIT_EX_KEY

SF8:	CMP	AH,5BH	;SF8
	JNE	SF9
	MOV	ES:[DI],BX
	JMP	SHORT EXIT_EX_KEY

SF9:	CMP	AH,5CH	;ALT F9?
	JNE	SF10
	MOV	ES:[DI],BX
	JMP	SHORT EXIT_EX_KEY

SF10:	CMP	AH,5DH	;ALT F10?
	JNE	EXIT_EX_KEY
	MOV	ES:[DI],BX

EXIT_EX_KEY:
	MOV	AL,254
	CURSOR
	RET
EXKEY	ENDP

;=============================================================================

LSE_KEY ENDS
	END	GETKEY
