
PAGE	58,	132
TITLE	MAKELOGO
SUBTTL MAKELOGO

.model small

EXTRN	BORDER:FAR
EXTRN	GETKEY:FAR

EXTRN text_color:byte
EXTRN back_color:byte
EXTRN hi_light_color:byte
EXTRN reverse_color:byte
EXTRN border_color:byte
EXTRN COL:word
EXTRN COL_HOLD:word
EXTRN ROW:byte
EXTRN boxtl:word
EXTRN boxtr:word
EXTRN boxbl:word
EXTRN boxbr:word
EXTRN colcnt:word
EXTRN cursor_location:word
EXTRN field_start:word
EXTRN field_stop:word
EXTRN inv_screen_attr:byte
EXTRN linecnt:word
EXTRN screen_attr:byte
EXTRN screen_title:byte
EXTRN copyright:byte
EXTRN anykey:byte
EXTRN address1:byte
EXTRN address2:byte
EXTRN phone:byte
EXTRN crlf:byte
EXTRN version:byte
EXTRN stat_port:word
EXTRN video_address:word

IF1
INCLUDE 	ASSEMBLY.MAC
ENDIF

PAGE
MAKELOGOS	SEGMENT PUBLIC	'CODE'

SUBTTL MAKELOGO
MAKELOGO PROC	 FAR
	ASSUME	CS:MAKELOGOS
	PUBLIC	MAKELOGO

LOGO:

	mov	al,text_color
	mov	screen_attr,al

	calc_addr  3,28
	mov   si,offset screen_title
	mov   cx,19
	w_f_scr 		    ;write field to screen

	mov	al,border_color
	mov	screen_attr,al

	MAKEBOX  6,12,17,66    ;startline,startcol,stopline,stopcol

	mov	al,text_color
	mov	screen_attr,al

	calc_addr  9,24
	mov   si,offset copyright
	mov   cx,32
	w_f_scr 		    ;write field to screen

	calc_addr  11,24
	mov   si,offset address1
	mov   cx,16
	w_f_scr 		    ;write field to screen

	calc_addr  12,24
	mov   si,offset address2
	mov   cx,20
	w_f_scr 		    ;write field to screen

	calc_addr  13,24
	mov   si,offset phone
	mov   cx,14
	w_f_scr 		    ;write field to screen

	calc_addr  14,24
	mov   si,offset version
	mov   cx,14
	w_f_scr 		    ;write field to screen

	calc_addr 23,24
	mov   si,offset anykey
	mov   cx,24
	w_f_scr

	calc_addr 23,47
	cursor
	CALL	GETKEY		;READ CHARACTER FROM KEYBOARD

		RET

MAKELOGO	ENDP
MAKELOGOS	ENDS
		END	MAKELOGO
