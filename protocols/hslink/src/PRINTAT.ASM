CALC_ADDR	MACRO  mrow,mcol
		push	si
		push	ax
		mov	al,mrow
		mov	row,al
		mov	ax,mcol
		mov	col,ax
		mov	ah,0
		mov	al,row
		shl	ax,1	;;*2
		shl	ax,1	;;*4
		shl	ax,1	;;*8
		shl	ax,1	;;*16
		mov	di,ax	;;save partial result
		shl	ax,1	;;*32
		shl	ax,1	;;*64
		add	di,ax	;;di = line * 80
		mov	ax,col	;;add in column
		add	di,ax
		shl	di,1	;;adjust for attribute bytes
		pop	ax
		pop	si
		ENDM

CURSOR	MACRO
	push	si
	push	ax
	XOR	BX,BX
	mov	bx,col
	mov	dh,row	      ;;row
	mov	dl,bl	      ;;col
	MOV	AH,2		;;SET CURSOR
	INT	10H
	XOR	BX,BX
	MOV	AH,8	       ;;RETURN CHARACTER AND ATTRIBUTE AT CURSOR
	INT	10H
	MOV	BX,AX
	pop	ax
	pop	si
	ENDM


EQUIP_CHK	MACRO
	local	seg_ok
		int	11H
		and	al,00110000B	;;is it B/W?
		cmp	al,00110000B
		mov	ax,0b000H	;;assume B/W
		mov	stat_port,03baH
		je	seg_ok
		mov	ax,0b800H	;;no, set color
		mov	stat_port,03daH
seg_ok:
		mov	video_address,ax
		ENDM

.MODEL HUGE, C
.CODE

PRINTAT    PROC     ARG1, ARG2
PUBLIC PRINTAT

jmp	start

screen_attr	  db	    0
inv_screen_attr   db	    70H
stat_port	  dw	    0
video_address	  dw	    0
colcnt		  dw	    0
linecnt 	  dw	    0
COL		  dw	    ?	    ;Contains the current column
ROW		  db	    ?	    ;Contains the current row
startl		  db	    0	    ;offset to parameter variable
startc		  dw	    0	    ;offset to parameter variable
stradd		  dw	    0	    ;offset to parameter variable

start:
	mov	ax,arg1
	mov	startl,al
	mov	ax,arg2
	mov	startc,ax

	push	es

	EQUIP_CHK		  ;macro determines if color or mono
	MOV	ES,AX		  ;screen is connected
	CMP	AX,0B000H
	JNE	COLOR

SCREEN	SEGMENT  AT   0B000H	  ;Monochrome screen
SCREEN	ENDS
	JMP	SET_SCREEN

COLOR:
SCREEN	SEGMENT  AT   0B800H	  ;Color screen
SCREEN	ENDS

SET_SCREEN:
	ASSUME	ES:SCREEN

	XOR	BX,BX
	MOV	AH,8	       ;;RETURN CHARACTER AND ATTRIBUTE AT CURSOR
	INT	10H
	mov	screen_attr,ah
	calc_addr startl,startc     ;;location of top left
	cursor
	pop	es
	RET
PRINTAT  ENDP
	END

