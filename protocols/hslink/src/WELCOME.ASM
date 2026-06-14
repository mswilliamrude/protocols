
PAGE	58,	132
TITLE	WELCOME
SUBTTL WELCOME

.model small

EXTRN	BORDER:FAR
EXTRN	GETKEY:FAR

EXTRN text_color:byte
EXTRN back_color:byte
EXTRN hi_light_color:byte
EXTRN Reverse_color:byte
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
EXTRN ANYKEY:byte
EXTRN logo1:byte
EXTRN logo2:byte
EXTRN fname:byte
EXTRN source:byte
EXTRN sourcename:byte
EXTRN destname:byte
EXTRN recommend:byte
EXTRN return:byte
EXTRN escape:byte
EXTRN destination:byte
EXTRN filopn:byte
EXTRN stat_port:word
EXTRN video_address:word
EXTRN filename:word

IF1
INCLUDE 	ASSEMBLY.MAC
ENDIF

PAGE
WELCOMES       SEGMENT PUBLIC  'CODE'

SUBTTL WELCOME
WELCOME PROC	FAR
	ASSUME	CS:WELCOMES
	PUBLIC	WELCOME

     MAKEBOX   3,14,8,64    ;startline,startcol,stopline,stopcol

	mov	ax,0100h		;turn on curser
	mov	bx,0
	mov	cx,0b0ch
	mov	dx,00
	int	10h

     calc_addr 5,24
     mov   si,offset logo1
     mov   cx,20
     w_f_scr

     calc_addr 6,24
     mov   si,offset logo2
     mov   cx,20
     w_f_scr

     calc_addr 10,22
     mov   si,offset recommend
     mov   cx,38
     w_f_scr

     calc_addr 12,30
     mov   si,offset return
     mov   cx,23
     w_f_scr

     calc_addr 22,30
     mov   si,offset escape
     mov   cx,21
     w_f_scr

     calc_addr 14,21
     mov   si,offset source
     mov   cx,14
     w_f_scr

	mov	row,14
	mov	col,30
     mov   si,offset sourcename
     mov   cx,34
     w_i_scr			    ;write inverted characters to screen

     calc_addr 16,16
     mov   si,offset destination
     mov   cx,19
     w_f_scr

	mov	row,16
	mov	col,30
     mov   si,offset destname
     mov   cx,34
     w_i_scr			    ;write inverted characters to screen

	mov	row,14
	mov	col,35
source_loop:
	cursor
	CALL	GETKEY
	cmp	al,1bh
	je	esc_key
	cmp	al,0dh
	je	got_source
	mov	es:[di],al
	inc	di
	inc	di
	jmp	short source_loop
got_source:
	mov	row,16
	mov	col,35
dest_loop:
	cursor
	CALL	GETKEY
	cmp	al,1bh
	je	esc_key
	cmp	al,0dh
	je	got_dest
	mov	es:[di],al
	inc	di
	inc	di
	jmp	short dest_loop

esc_key:
got_dest:

	push	ax
	mov	ax,0100h		;turn off curser
	mov	bx,0
	mov	cx,0200h
	mov	dx,00
	int	10h
	pop	ax

		RET

WELCOME        ENDP
WELCOMES       ENDS
		END	WELCOME
