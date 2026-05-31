
TITLE	SEQSEG

stack	segment para	public	'stack'
	db 512h dup (?)
stack	ends
code	segment para	public	'code'
code	ends
install_data	segment word	public	'data'
install_data	ends

EXTRN	INSTALL:FAR

EXTRN	error:byte

SEQSEGS   SEGMENT  PARA PUBLIC	'CODE'
SEQSEG PROC    FAR
	assume cs:seqsegs,ss:stack
PUBLIC	seqseg
	jmp	short start

RET_CD	EQU	0			;ERRORLEVEL RETURN CODE VALUE
RET_FN	EQU	4CH			;"RETURN TO DOS" FUNCTION CALL

start:
	CALL	INSTALL
	MOV	AX,RET_FN*256 + RET_CD	;RETURN TO DOS FUNCTION CALL, AND
					;VALUE TO BE PASSED TO ERRORLEVEL
	INT	21H			;RETURN TO DOS
					; (VERSION 2.00 OR LATER)

SEQSEG ENDP
SEQSEGS        ENDS
	END	SEQSEG
