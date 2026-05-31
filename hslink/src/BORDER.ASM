PAGE	58,	132
TITLE	BORDER
SUBTTL BORDER

.model small

EXTRN	screen_attr:byte

TR	MACRO
	PUSH	DI
	CLD
	MOV	DI,158
	MOV	AL,187
	MOV	AH,screen_attr
	MOV	CX,1
	REP	STOSW
	POP	DI
	ENDM

BR	MACRO
	PUSH	DI
	CLD
	MOV	DI,3998
	MOV	AL,188
	MOV	AH,screen_attr
	MOV	CX,1
	REP	STOSW
	POP	DI
	ENDM


TL	MACRO
	PUSH	DI
	CLD
	MOV	DI,0
	MOV	AL,201
	MOV	AH,screen_attr
	MOV	CX,1
	REP	STOSW
	POP	DI
	ENDM

BOTL	  MACRO
	PUSH	DI
	CLD
	MOV	DI,3840
	MOV	AL,200
	MOV	AH,screen_attr
	MOV	CX,1
	REP	STOSW
	POP	DI
	ENDM


TOP	MACRO
	PUSH	DI
	CLD
	MOV	DI,2
	MOV	AL,205
	MOV	AH,screen_attr
	MOV	CX,78
	REP	STOSW
	POP	DI
	ENDM

BOTTOM	MACRO
	PUSH	DI
	CLD
	MOV	DI,3842
	MOV	AL,205
	MOV	AH,screen_attr
	MOV	CX,78
	REP	STOSW
	POP	DI
	ENDM

LEFT	MACRO
LOCAL	leftloop
	PUSH	DI
	CLD
	MOV	DI,160
	mov	  bl,23
leftloop:
	MOV	AL,186
	MOV	AH,screen_attr
	MOV	CX,1
	REP	STOSW
	add	  di,158
	dec	  bl
	cmp	  bl,0
	jne	  leftloop
	POP	DI
	ENDM

RIGHT	MACRO
LOCAL	RIGHTLOOP
	PUSH	DI
	CLD
	MOV	DI,318
	mov	  bl,23
RIGHTLOOP:
	MOV	AL,186
	MOV	AH,screen_attr
	MOV	CX,1
	REP	STOSW
	add	  di,158
	dec	  bl
	cmp	  bl,0
	jne	  rightloop
	POP	DI
	ENDM

PAGE
BORDER_CODE   SEGMENT PUBLIC	'CODE'

SUBTTL BORDER
BORDER	  PROC	  FAR
	ASSUME	CS:BORDER_CODE
	PUBLIC	BORDER

	TL
	TOP
	TR
	BOTL
	BOTTOM
	BR
	LEFT
	RIGHT

	RET
BORDER	ENDP
BORDER_CODE   ENDS
	END	BORDER
