
EXTRN	ERRHAND:FAR
EXTRN	BORDER:FAR
EXTRN	MAKELOGO:FAR
EXTRN	GETKEY:FAR
EXTRN	WELCOME:FAR

.model small
.stack

IF1
INCLUDE 	ASSEMBLY.MAC
ENDIF

total_mem	equ	02H
cr	equ	0DH
lf	equ	0AH
eof	equ	1AH

psp_comnd	equ	0082H
enviorment	equ	2CH

.DATA

text_color	db	0
back_color	db	0
hi_light_color	db	0
reverse_color	db	0
border_color	db	0
screen_attr	db	0
inv_screen_attr db	0

pgm_file1	db	"BLUE.COM",0
pgm_file2	db	"CLR.COM",0
pgm_file3	db	"XCOPY.EXE",0
zip_string	db	"pkunzip.exe",0
chngdrive	db	"  ",0
root		db	"\",0
docdir		db	"document",0
updir		db	"upload",0
downdir 	db	"download",0
scriptdir	db	"script",0
extexe		db	"*.exe",0
extcom		db	"*.com",0
extmdm		db	"*.mdm",0
extkey		db	"*.key",0
extdoc		db	"*.doc",0
extnew		db	"*.new",0
extme		db	"*.me",0
extslc		db	"*.slc",0
extslt		db	"*.slt",0
todocdir	db	"\document",0
toslcdir	db	"\script",0
tlx_1		db	"tlx315-*.zip",0
tlx_2		db	"tlx315-2.zip",0
tlx_3		db	"tlx315-3.zip",0
cmd_line1	db	80	dup	(?)
cmd_line2	db	80	dup	(?)
cmd_line3	db	80	dup	(?)
cmd_line4	db	80	dup	(?)
cmd_line5	db	80	dup	(?)
zipcmdstr	db	80	dup	(?)
cmdpass 	dw	0	  ;offset to parameter variable
parm_blk	db	14	dup	(?)
reg_save	db	10	dup	(?)

ANYKEY		DB	' Hit any key to continue '
ADDRESS1	DB	' 911 Arnold Way '
ADDRESS2	DB	' San Jose, Ca 95128 '
COPYRIGHT	DB	' Copyright CUSTOM SOFTWARE 1991 '
FNAME		DB	'FileName: '
SOURCE		DB	'Source drive: '
DESTINATION	DB	'Destination drive: '
RECOMMEND	DB	'These are the Recommended directories: '
RETURN		DB	'Press return to accept: '
ESCAPE		DB	'Press escape to quit: '
PHONE		DB	' 408-297-9660 '
SCREEN_TITLE	DB	' TELIX INSTALLATION'
VERSION 	DB	' VERSION 1.00 '

logo1		db   'Welcome to the Telix'
logo2		db   'Installation Program'
nopathstr	db   ' ALL PATHS NOT SELECTED CORRECTLY '

DRIVE		DB	24	DUP(0)
FILENAME	DB	34	DUP	  (" ")
SOURCENAME	DB	'A:', 32      DUP       (" ")
DESTNAME	DB	'C:\TELIX', 28      DUP       (" ")
WILDNAME	DB	34	DUP	  (" ")
MEMORY_TOTAL	DW	0

mrow		 db	0	  ;offset to parameter variable
mcol		 dw	0	  ;offset to parameter variable
dlen		 dw	0	  ;offset to parameter variable
mscrn		 dw	0	  ;offset to parameter variable
fldname 	 dw	0	  ;offset to parameter variable
vidname 	 dw	0	  ;offset to parameter variable
fldtype 	 dw	0	  ;offset to parameter variable
updated 	 dw	0	  ;offset to parameter variable

BYTES_READ	DW	0
CHAR		DW	'  '
CHAR_STR	DB	0,1,' ',0     ;use to strsearch for a character
COL		DW	?	;Contains the current column
COL_HOLD	DW	0
ERROR		DB	0
DTA_SEARCH_BUF	DB	43	DUP(0)
DTA_STORE	DB	43		DUP(0)
FCB_1		DB	43	DUP(?)
FCB_2		DB	43	DUP(?)
FILE_SIZE	DW	4	DUP(0)
FIL_HND 	DW	0		;DOS FILE HANDLE
FL_BUF		DB	82	DUP(" ")        ;Disk file buffer
FILOPN		DB	0		;FILE OPEN FLAG
ROW		DB	?	;Contains the current row

ALLOCATED_MEMORY  DW 0
DOCUMENT_MEMORY   DW 0
dup_chk_ptr	  dw 0
boxtl		  dw	    0
boxtr		  dw	    0
boxbl		  dw	    0
boxbr		  dw	    0
colcnt		  dw	    0
card_name	  db '   '
CRLF		  DB	CR,LF
cursor_location   dw	    ?
ENVIORMENT_SEG	  dw  0
MEMORY_SEGMENT	  DW 0
field_name	  db '     '
field_start	  dw   0
field_stop	  dw   0
linecnt 	  dw	    0
maxlen		  db ?			   ;maximum input string length
max_page	  db ?			   ;number of highest directory page
space		  db	  1	  dup	  (" ")
retcode 	  dw	    ?
stat_port	  dw	    ?
video_port	  dw	    ?
video_address	  dw ?			   ;window start address
video_segment	  dw 0B800h		   ;video segment address
video_page	  db ?			   ;current video page

PUBLIC	ENVIORMENT_SEG,ERROR,ALLOCATED_MEMORY,MEMORY_SEGMENT
PUBLIC	CHAR,CHAR_STR,COL,COL_HOLD,fname,row,boxtl
PUBLIC	boxtr,boxbl,boxbr,colcnt,cursor_location,field_start,field_stop
PUBLIC	inv_screen_attr,linecnt,screen_attr,fil_hnd,fl_buf
PUBLIC	filename,sourcename,destname,wildname,source,destination,escape,return
PUBLIC	fcb_1,fcb_2,file_size,bytes_read,recommend
PUBLIC	screen_title,copyright,anykey,address1,address2,phone
PUBLIC	CRLF,version,stat_port,video_address,filopn
PUBLIC mrow,text_color
PUBLIC mcol,back_color
PUBLIC dlen,hi_light_color
PUBLIC mscrn,reverse_color
PUBLIC fldname,border_color
PUBLIC fldtype
PUBLIC vidname
PUBLIC pgm_file1,pgm_file2,pgm_file3
PUBLIC cmd_line1,cmd_line2,cmd_line3,cmd_line4,cmd_line5
PUBLIC root,docdir,updir,downdir,scriptdir,tlx_1,tlx_2,tlx_3
PUBLIC extdoc,extslc,extslt,extexe,extcom
PUBLIC cmdpass
PUBLIC parm_blk
PUBLIC reg_save
PUBLIC logo1
PUBLIC logo2

.CODE
.STARTUP

;******************************************************************************

INSTALL     PROC

	jmp	go
stk_seg dw	0
stk_ptr dw	0

go:
	mov	ax,es			;get segment of enviorment block
	mov	enviorment_seg,ax	;from psp and save it

;--------- save psp information to create child process later -----------------

	mov	word ptr parm_blk[04],ds
	mov	word ptr parm_blk[06],5CH
	mov	word ptr parm_blk[08H],ax
	mov	word ptr parm_blk[0AH],6CH
	mov	word ptr parm_blk[0CH],ax

	mov	ax,0100h		;turn off curser
	mov	bx,0
	mov	cx,0200h
	mov	dx,00
	int	10h

	CALL	CLS

;;;;;;;;;;;;;;;;;;  EQUIP_CHK ;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
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
	mov	ES,AX		  ;screen is connected
	cmp	AX,0B000H
	jne	COLOR

SCREEN	SEGMENT  AT   0B000H	  ;Monochrome screen
SCREEN	ENDS

	mov	 text_color,07h
	mov	 back_color,0
	mov	 hi_light_color,15h
	mov	 reverse_color,70h
	mov	 border_color,07h
	mov	 screen_attr,07H
	mov	 inv_screen_attr,70H

	JMP  SHORT   SET_SCREEN

COLOR:
	CALL	BLUE
SCREEN	SEGMENT  AT   0B800H	  ;Color screen
SCREEN	ENDS

	mov	 text_color,07h
	mov	 back_color,0
	mov	 hi_light_color,15h
	mov	 reverse_color,70h
	mov	 border_color,07h
	mov	 screen_attr,07H
	mov	 inv_screen_attr,70H

SET_SCREEN:
	ASSUME	ES:SCREEN

	CALL	BORDER
	CALL	MAKELOGO
get_welcome:
	CALL	CLS
	CALL	BORDER
	CALL	WELCOME
	cmp	al,1bh
	jne	storesource
	jmp	err_exit;

storesource:
	mov	row,14
	mov	col,35
	STORE_FIELD  sourcename,34
	mov	si,offset sourcename	  ;check that source name is not blank
	mov	al,[si]
	cmp	al,20h
	jne	storedest
	call	cls
	call	nopath
	jmp	get_welcome
storedest:
	mov	row,16
	mov	col,35
	STORE_FIELD  destname,34
	mov	si,offset destname	;check that dest name is not blank
	mov	al,[si]
	cmp	al,20h
	jne	dest_ok
	call	cls
	call	nopath
	jmp	get_welcome
dest_ok:
	call	cls
	calc_addr 10,10
	cursor

	xor	ax,ax
	xor	dx,dx
	mov	si,offset destname
	mov	dl,[si]
	cmp	dl,60h
	jb	nc
	sub	dl,20h
nc:
	sub	dl,40h
	dec	dl
	mov	ah,0EH
	int	21h
	jc	err_exit

	xor	ax,ax
	mov	ah,3bH
	mov	dx,offset root	;change to root dir
	int	21h
	jc	err_exit
	xor	ax,ax
	mov	ah,39H
	mov	dx,offset destname  ;make telix directory
	int	21h
	jnc	telix_ok
	cmp	al,5
	jne	err_exit
telix_ok:
	xor	ax,ax
	mov	ah,3bH
	mov	dx,offset destname   ;change to telix directory
	int	21h
	jc	err_exit
	xor	ax,ax
	mov	ah,39H
	mov	dx,offset docdir  ;make document directory
	int	21h
	jnc	doc_ok
	cmp	al,5
	jne	err_exit
doc_ok:
	xor	ax,ax
	mov	ah,39H
	mov	dx,offset updir  ;make upload directory
	int	21h
	jnc	up_ok
	cmp	al,5
	jne	err_exit
up_ok:
	xor	ax,ax
	mov	ah,39H
	mov	dx,offset downdir  ;make download directory
	int	21h
	jnc	down_ok
	cmp	al,5
	jne	err_exit
down_ok:
	xor	ax,ax
	mov	ah,39H
	mov	dx,offset scriptdir  ;make script directory
	int	21h
	jnc	script_ok
	cmp	al,5
	jne	err_exit
script_ok:
	call	cls
	call	copycmd

	mov	dx,offset tlx_1
	call	unzip_exe
	mov	dx,offset zipcmdstr
	mov	word ptr parm_blk[02],offset cmd_line2
	CALL	EXEC_COM
	cmp	error,1
	je	err_exit

	mov	dx,offset tlx_1
	call	unzip_com
	mov	dx,offset zipcmdstr
	mov	word ptr parm_blk[02],offset cmd_line2
	CALL	EXEC_COM
	cmp	error,1
	je	err_exit

	mov	dx,offset tlx_1
	call	unzip_key
	mov	dx,offset zipcmdstr
	mov	word ptr parm_blk[02],offset cmd_line2
	CALL	EXEC_COM
	cmp	error,1
	je	err_exit

	mov	dx,offset tlx_1
	call	unzip_mdm
	mov	dx,offset zipcmdstr
	mov	word ptr parm_blk[02],offset cmd_line2
	CALL	EXEC_COM
	cmp	error,1
	je	err_exit

	mov	dx,offset tlx_1
	call	unzip_doc
	mov	dx,offset zipcmdstr
	mov	word ptr parm_blk[02],offset cmd_line3
	CALL	EXEC_COM
	cmp	error,1
	je	err_exit

	mov	dx,offset tlx_1
	call	unzip_new
	mov	dx,offset zipcmdstr
	mov	word ptr parm_blk[02],offset cmd_line3
	CALL	EXEC_COM
	cmp	error,1
	je	err_exit

	mov	dx,offset tlx_1
	call	unzip_me
	mov	dx,offset zipcmdstr
	mov	word ptr parm_blk[02],offset cmd_line3
	CALL	EXEC_COM
	cmp	error,1
	je	err_exit

	mov	dx,offset tlx_1
	call	unzip_slc
	mov	dx,offset zipcmdstr
	mov	word ptr parm_blk[02],offset cmd_line3
	CALL	EXEC_COM
	cmp	error,1
	je	err_exit

	mov	dx,offset tlx_1
	call	unzip_slt
	mov	dx,offset zipcmdstr
	mov	word ptr parm_blk[02],offset cmd_line3
	CALL	EXEC_COM
	cmp	error,1
	je	err_exit

EXIT:
ERR_EXIT:
	ASSUME	ES:SCREEN
	mov	ax,0100h		;turn on curser
	mov	bx,0
	mov	cx,0b0ch
	mov	dx,00
	int	10h

	.EXIT

INSTALL     ENDP



;*****************************************************************************
;***									   ***
;***			SUBROUTINES					   ***
;***									   ***
;*****************************************************************************

EXEC_COM proc	 near
	push	es
	mov	ax,ds
	mov	es,ax
	mov	cs:stk_seg,ss
	mov	cs:stk_ptr,sp
	mov	bx,offset parm_blk
	mov	al,0
	mov	ah,4BH
	int	21H			;call child process
	mov	ss,cs:stk_seg
	mov	sp,cs:stk_ptr
	jnc	exec_ok
	mov	ah,59H
	mov	bx,0
	int	21H
	call	ERRHAND
	mov	error,1
	mov	ah,4DH		;get return code
	mov	al,0
	int	21H
	mov	retcode,ax
exec_ok:
	pop	es
	ret
EXEC_COM endp

;-----------------------------------------------------------------------------

BLUE proc    near
	push	es
	mov	ax,ds
	mov	es,ax
	mov	cs:stk_seg,ss
	mov	cs:stk_ptr,sp
	mov	dx,offset pgm_file1
	mov	bx,offset parm_blk
	mov	word ptr parm_blk[02],offset cmd_line1
	mov	al,0
	mov	ah,4BH
	int	21H			;call child process
	mov	ss,cs:stk_seg
	mov	sp,cs:stk_ptr
	mov	ah,4DH		;get return code
	mov	al,0
	int	21H
	mov	retcode,ax
	xor	dx,dx
	pop	es
	ret
BLUE endp

;-----------------------------------------------------------------------------

CLS proc    near
	push	es
	mov	ax,ds
	mov	es,ax
	mov	cs:stk_seg,ss
	mov	cs:stk_ptr,sp
	mov	dx,offset pgm_file2
	mov	bx,offset parm_blk
	mov	word ptr parm_blk[02],offset cmd_line1
	mov	al,0
	mov	ah,4BH
	int	21H			;call child process
	mov	ss,cs:stk_seg
	mov	sp,cs:stk_ptr
	mov	ah,4DH		;get return code
	mov	al,0
	int	21H
	mov	retcode,ax
	xor	dx,dx
	pop	es
	ret
CLS endp

;-----------------------------------------------------------------------------

NOPATH PROC    NEAR

	CALL   BORDER
	MAKEBOX  8,10,14,64    ;startline,startcol,stopline,stopcol
	calc_addr 10,20
	mov   si,offset NOPATHSTR
	mov   cx,43
	w_f_scr 		    ;write field to screen
	calc_addr 12,24
	mov   si,offset anykey
	mov   cx,25
	w_f_scr

	CALL	BEEP

	calc_addr 12,48
	cursor
	CALL	GETKEY		;READ CHARACTER FROM KEYBOARD

		RET

NOPATH	      ENDP

;-----------------------------------------------------------------------------

BEEP PROC    NEAR
	push	dx
	push	cx
	push	ax
	mov	cx,1000    ;freq in hertz
	mov	dx,250	   ;delay in milliseconds
	call	freq
	call	toneset
	call	toneon
	mov	cx,dx
	call	delay
	call	toneoff
	pop	ax
	pop	cx
	pop	dx
		RET
BEEP ENDP

;------------------------------------------------------------------------------

freq	proc	near
	push	dx
	push	ax
	mov	dx,12H
	mov	ax,34deH
	div	cx
	mov	cx,ax
	pop	ax
	pop	dx
	ret
freq	endp

;------------------------------------------------------------------------------

toneset proc	near
	push	ax
	mov	al,cl
	out	42H,al
	mov	al,ch
	out	42H,al
	pop	ax
	ret
toneset endp

;------------------------------------------------------------------------------

toneon	proc	near
	push	ax
	in	al,61H
	or	al,3
	out	61H,al
	pop	ax
	ret
toneon	endp

;------------------------------------------------------------------------------

toneoff proc	near
	push	ax
	in	al,61H
	and	al,11111100b
	out	61H,al
	pop	ax
	ret
toneoff endp

;------------------------------------------------------------------------------

delay	proc	near
	push	cx
delay1:
	push	cx
	mov	cx,260
delay2:
	loop	delay2
	pop	cx
	loop	delay1
	pop	cx
	ret
delay	endp

;------------------------------------------------------------------------------

copycmd PROC	NEAR
	PUBLIC	copycmd

     push	es		;;save segment
     mov	ax,ds		;;make di point to code instead of screen
     mov	es,ax

     mov	di,offset sourcename	;copy source as parameter
     mov	ax,0
     mov	cx,35
     cld
     repne	scasb
     mov	ax,35
     sub	ax,cx
     xchg	ax,cx
     mov	bx,cx			;save length
     dec	bx

     mov	si,offset sourcename	;copy source as parameter
     mov	di,offset zipcmdstr
     rep	movsb
     dec	di

     push	di
     mov	di,offset zip_string
     mov	ax,0
     mov	cx,20
     cld
     repne	scasb
     mov	ax,20
     sub	ax,cx
     xchg	ax,cx
     add	bx,cx			;save length
     dec	bx
     pop	di

     mov	si,offset zip_string
     rep	movsb
     dec	di

     pop	es

		RET
copycmd ENDP

;------------------------------------------------------------------------------

unzipcmd PROC	 NEAR
	PUBLIC	unzipcmd

     push	es		;;save segment
     mov	ax,ds		;;make di point to code instead of screen
     mov	es,ax

     mov	di,offset destname    ;get length of destination
     mov	ax,0
     mov	cx,35
     cld
     repne	scasb
     mov	ax,35
     sub	ax,cx
     xchg	ax,cx
     mov	bx,cx			;save length
     dec	bx

     mov	di,offset zipcmdstr
     mov	si,offset destname    ;copy as parameter
     rep	movsb
     dec	di
     mov	al,5ch
     mov	es:[di],al
     inc	di

     push	di
     mov	di,offset zip_string
     mov	ax,0
     mov	cx,20
     cld
     repnz	cmpsb
     mov	ax,20
     sub	ax,cx
     xchg	ax,cx
     dec	cx
     add	bx,cx
     pop	di

     mov	si,offset zip_string
     rep	movsb

     pop	es

		RET
unzipcmd ENDP

;------------------------------------------------------------------------------

unzip_exe PROC	  NEAR
	PUBLIC	unzip_exe

	push	es		;;save segment
	mov	ax,ds		;;make di point to code instead of screen
	mov	es,ax
	xor	bx,bx

	mov	di,dx		      ;get length of destination
	mov	ax,0
	mov	cx,35
	cld
	repne	scasb
	mov	ax,35
	sub	ax,cx
	xchg	ax,cx
	mov	bx,cx			;save length
	dec	bx
	push	cx

	mov	di,offset cmd_line2
	inc	di
	mov	al,20h
	mov	es:[di],al
	inc	bx
	inc	di
	mov	al,2dh		 ;-
	mov	es:[di],al
	inc	di
	inc	bx
	mov	al,4fh		 ;O
	mov	es:[di],al
	inc	di
	inc	bx
	mov	al,20h
	mov	es:[di],al
	inc	bx
	inc	di

     push	di
     push	bx
     mov	di,offset sourcename	;get length of destination
     mov	ax,0
     mov	cx,35
     cld
     repne	scasb
     mov	ax,35
     sub	ax,cx
     xchg	ax,cx
     dec	cx
     pop	bx
     add	bx,cx
     pop	di

     mov	si,offset sourcename	;copy as parameter
     rep	movsb

	mov	si,dx		   ;copy as parameter
	pop	cx
	rep	movsb
	dec	di
	inc	bx
	mov	al,20h
	mov	es:[di],al
	inc	di

     push	di
     push	bx
     mov	di,offset destname    ;get length of destination
     mov	ax,0
     mov	cx,35
     cld
     repne	scasb
     mov	ax,35
     sub	ax,cx
     xchg	ax,cx
     dec	cx
     pop	bx
     add	bx,cx
     pop	di

     mov	si,offset destname    ;copy as parameter
     rep	movsb

     mov     al,20h
     mov     es:[di],al
     inc     di
     inc     bx

     mov	si,offset extexe      ;copy as parameter
     mov	cx,5
     rep	movsb
     add	bx,5

	mov	al,0dh
	mov	es:[di],al
	mov	di,offset cmd_line2
	mov	es:[di],bl	    ;record length of string

	pop	   es
		RET
unzip_exe ENDP

;------------------------------------------------------------------------------

unzip_com PROC	  NEAR
	PUBLIC	unzip_com

	push	es		;;save segment
	mov	ax,ds		;;make di point to code instead of screen
	mov	es,ax
	xor	bx,bx

	mov	di,dx		      ;get length of destination
	mov	ax,0
	mov	cx,35
	cld
	repne	scasb
	mov	ax,35
	sub	ax,cx
	xchg	ax,cx
	mov	bx,cx			;save length
	dec	bx
	push	cx

	mov	di,offset cmd_line2
	inc	di
	mov	al,20h
	mov	es:[di],al
	inc	bx
	inc	di
	mov	al,2dh		 ;-
	mov	es:[di],al
	inc	di
	inc	bx
	mov	al,4fh		 ;O
	mov	es:[di],al
	inc	di
	inc	bx
	mov	al,20h
	mov	es:[di],al
	inc	bx
	inc	di

     push	di
     push	bx
     mov	di,offset sourcename	;get length of destination
     mov	ax,0
     mov	cx,35
     cld
     repne	scasb
     mov	ax,35
     sub	ax,cx
     xchg	ax,cx
     dec	cx
     pop	bx
     add	bx,cx
     pop	di

     mov	si,offset sourcename	;copy as parameter
     rep	movsb

	mov	si,dx		   ;copy as parameter
	pop	cx
	rep	movsb
	dec	di
	inc	bx
	mov	al,20h
	mov	es:[di],al
	inc	di

     push	di
     push	bx
     mov	di,offset destname    ;get length of destination
     mov	ax,0
     mov	cx,35
     cld
     repne	scasb
     mov	ax,35
     sub	ax,cx
     xchg	ax,cx
     dec	cx
     pop	bx
     add	bx,cx
     pop	di

     mov	si,offset destname    ;copy as parameter
     rep	movsb

     mov     al,20h
     mov     es:[di],al
     inc     di
     inc     bx

     mov	si,offset extcom      ;copy as parameter
     mov	cx,5
     rep	movsb
     add	bx,5

	mov	al,0dh
	mov	es:[di],al
	mov	di,offset cmd_line2
	mov	es:[di],bl	    ;record length of string

	pop	   es
		RET
unzip_com ENDP

;------------------------------------------------------------------------------

unzip_mdm PROC	  NEAR
	PUBLIC	unzip_mdm

	push	es		;;save segment
	mov	ax,ds		;;make di point to code instead of screen
	mov	es,ax
	xor	bx,bx

	mov	di,dx		      ;get length of destination
	mov	ax,0
	mov	cx,35
	cld
	repne	scasb
	mov	ax,35
	sub	ax,cx
	xchg	ax,cx
	mov	bx,cx			;save length
	dec	bx
	push	cx

	mov	di,offset cmd_line2
	inc	di
	mov	al,20h
	mov	es:[di],al
	inc	bx
	inc	di
	mov	al,2dh		 ;-
	mov	es:[di],al
	inc	di
	inc	bx
	mov	al,4fh		 ;O
	mov	es:[di],al
	inc	di
	inc	bx
	mov	al,20h
	mov	es:[di],al
	inc	bx
	inc	di

     push	di
     push	bx
     mov	di,offset sourcename	;get length of destination
     mov	ax,0
     mov	cx,35
     cld
     repne	scasb
     mov	ax,35
     sub	ax,cx
     xchg	ax,cx
     dec	cx
     pop	bx
     add	bx,cx
     pop	di

     mov	si,offset sourcename	;copy as parameter
     rep	movsb

	mov	si,dx		   ;copy as parameter
	pop	cx
	rep	movsb
	dec	di
	inc	bx
	mov	al,20h
	mov	es:[di],al
	inc	di

     push	di
     push	bx
     mov	di,offset destname    ;get length of destination
     mov	ax,0
     mov	cx,35
     cld
     repne	scasb
     mov	ax,35
     sub	ax,cx
     xchg	ax,cx
     dec	cx
     pop	bx
     add	bx,cx
     pop	di

     mov	si,offset destname    ;copy as parameter
     rep	movsb

     mov     al,20h
     mov     es:[di],al
     inc     di
     inc     bx

     mov	si,offset extmdm      ;copy as parameter
     mov	cx,5
     rep	movsb
     add	bx,5

	mov	al,0dh
	mov	es:[di],al
	mov	di,offset cmd_line2
	mov	es:[di],bl	    ;record length of string

	pop	   es
		RET
unzip_mdm ENDP

;------------------------------------------------------------------------------

unzip_key PROC	  NEAR
	PUBLIC	unzip_key

	push	es		;;save segment
	mov	ax,ds		;;make di point to code instead of screen
	mov	es,ax
	xor	bx,bx

	mov	di,dx		      ;get length of destination
	mov	ax,0
	mov	cx,35
	cld
	repne	scasb
	mov	ax,35
	sub	ax,cx
	xchg	ax,cx
	mov	bx,cx			;save length
	dec	bx
	push	cx

	mov	di,offset cmd_line2
	inc	di
	mov	al,20h
	mov	es:[di],al
	inc	bx
	inc	di
	mov	al,2dh		 ;-
	mov	es:[di],al
	inc	di
	inc	bx
	mov	al,4fh		 ;O
	mov	es:[di],al
	inc	di
	inc	bx
	mov	al,20h
	mov	es:[di],al
	inc	bx
	inc	di

     push	di
     push	bx
     mov	di,offset sourcename	;get length of destination
     mov	ax,0
     mov	cx,35
     cld
     repne	scasb
     mov	ax,35
     sub	ax,cx
     xchg	ax,cx
     dec	cx
     pop	bx
     add	bx,cx
     pop	di

     mov	si,offset sourcename	;copy as parameter
     rep	movsb

	mov	si,dx		   ;copy as parameter
	pop	cx
	rep	movsb
	dec	di
	inc	bx
	mov	al,20h
	mov	es:[di],al
	inc	di

     push	di
     push	bx
     mov	di,offset destname    ;get length of destination
     mov	ax,0
     mov	cx,35
     cld
     repne	scasb
     mov	ax,35
     sub	ax,cx
     xchg	ax,cx
     dec	cx
     pop	bx
     add	bx,cx
     pop	di

     mov	si,offset destname    ;copy as parameter
     rep	movsb

     mov     al,20h
     mov     es:[di],al
     inc     di
     inc     bx

     mov	si,offset extkey      ;copy as parameter
     mov	cx,5
     rep	movsb
     add	bx,5

	mov	al,0dh
	mov	es:[di],al
	mov	di,offset cmd_line2
	mov	es:[di],bl	    ;record length of string

	pop	   es
		RET
unzip_key ENDP

;------------------------------------------------------------------------------

unzip_doc PROC	  NEAR
	PUBLIC	unzip_doc

	push	es		;;save segment
	mov	ax,ds		;;make di point to code instead of screen
	mov	es,ax
	xor	bx,bx

	mov	di,dx		      ;get length of destination
	mov	ax,0
	mov	cx,35
	cld
	repne	scasb
	mov	ax,35
	sub	ax,cx
	xchg	ax,cx
	mov	bx,cx			;save length
	dec	bx
	push	cx

	mov	di,offset cmd_line3
	inc	di
	mov	al,20h
	mov	es:[di],al
	inc	bx
	inc	di
	mov	al,2dh		 ;-
	mov	es:[di],al
	inc	di
	inc	bx
	mov	al,4fh		 ;O
	mov	es:[di],al
	inc	di
	inc	bx
	mov	al,20h
	mov	es:[di],al
	inc	bx
	inc	di

     push	di
     push	bx
     mov	di,offset sourcename	;get length of destination
     mov	ax,0
     mov	cx,35
     cld
     repne	scasb
     mov	ax,35
     sub	ax,cx
     xchg	ax,cx
     dec	cx
     pop	bx
     add	bx,cx
     pop	di

     mov	si,offset sourcename	;copy as parameter
     rep	movsb

	mov	si,dx		   ;copy as parameter
	pop	cx
	rep	movsb
	dec	di
	inc	bx
	mov	al,20h
	mov	es:[di],al
	inc	di

     push	di
     push	bx
     mov	di,offset destname    ;get length of destination
     mov	ax,0
     mov	cx,35
     cld
     repne	scasb
     mov	ax,35
     sub	ax,cx
     xchg	ax,cx
     dec	cx
     pop	bx
     add	bx,cx
     pop	di

     mov	si,offset destname    ;copy as parameter
     rep	movsb
     mov	si,offset todocdir    ;copy as parameter
     mov	cx,9
     rep	movsb
     add	bx,9

     mov     al,20h
     mov     es:[di],al
     inc     di
     inc     bx

     mov	si,offset extdoc      ;copy as parameter
     mov	cx,5
     rep	movsb
     add	bx,5

	mov	al,0dh
	mov	es:[di],al
	mov	di,offset cmd_line3
	mov	es:[di],bl	    ;record length of string

	pop	   es
		RET
unzip_doc ENDP

;------------------------------------------------------------------------------

unzip_new PROC	  NEAR
	PUBLIC	unzip_new

	push	es		;;save segment
	mov	ax,ds		;;make di point to code instead of screen
	mov	es,ax
	xor	bx,bx

	mov	di,dx		      ;get length of destination
	mov	ax,0
	mov	cx,35
	cld
	repne	scasb
	mov	ax,35
	sub	ax,cx
	xchg	ax,cx
	mov	bx,cx			;save length
	dec	bx
	push	cx

	mov	di,offset cmd_line3
	inc	di
	mov	al,20h
	mov	es:[di],al
	inc	bx
	inc	di
	mov	al,2dh		 ;-
	mov	es:[di],al
	inc	di
	inc	bx
	mov	al,4fh		 ;O
	mov	es:[di],al
	inc	di
	inc	bx
	mov	al,20h
	mov	es:[di],al
	inc	bx
	inc	di

     push	di
     push	bx
     mov	di,offset sourcename	;get length of destination
     mov	ax,0
     mov	cx,35
     cld
     repne	scasb
     mov	ax,35
     sub	ax,cx
     xchg	ax,cx
     dec	cx
     pop	bx
     add	bx,cx
     pop	di

     mov	si,offset sourcename	;copy as parameter
     rep	movsb

	mov	si,dx		   ;copy as parameter
	pop	cx
	rep	movsb
	dec	di
	inc	bx
	mov	al,20h
	mov	es:[di],al
	inc	di

     push	di
     push	bx
     mov	di,offset destname    ;get length of destination
     mov	ax,0
     mov	cx,35
     cld
     repne	scasb
     mov	ax,35
     sub	ax,cx
     xchg	ax,cx
     dec	cx
     pop	bx
     add	bx,cx
     pop	di

     mov	si,offset destname    ;copy as parameter
     rep	movsb
     mov	si,offset todocdir    ;copy as parameter
     mov	cx,9
     rep	movsb
     add	bx,9

     mov     al,20h
     mov     es:[di],al
     inc     di
     inc     bx

     mov	si,offset extnew      ;copy as parameter
     mov	cx,5
     rep	movsb
     add	bx,5

	mov	al,0dh
	mov	es:[di],al
	mov	di,offset cmd_line3
	mov	es:[di],bl	    ;record length of string

	pop	   es
		RET
unzip_new ENDP

;------------------------------------------------------------------------------

unzip_me  PROC	  NEAR
	PUBLIC	unzip_me

	push	es		;;save segment
	mov	ax,ds		;;make di point to code instead of screen
	mov	es,ax
	xor	bx,bx

	mov	di,dx		      ;get length of destination
	mov	ax,0
	mov	cx,35
	cld
	repne	scasb
	mov	ax,35
	sub	ax,cx
	xchg	ax,cx
	mov	bx,cx			;save length
	dec	bx
	push	cx

	mov	di,offset cmd_line3
	inc	di
	mov	al,20h
	mov	es:[di],al
	inc	bx
	inc	di
	mov	al,2dh		 ;-
	mov	es:[di],al
	inc	di
	inc	bx
	mov	al,4fh		 ;O
	mov	es:[di],al
	inc	di
	inc	bx
	mov	al,20h
	mov	es:[di],al
	inc	bx
	inc	di

     push	di
     push	bx
     mov	di,offset sourcename	;get length of destination
     mov	ax,0
     mov	cx,35
     cld
     repne	scasb
     mov	ax,35
     sub	ax,cx
     xchg	ax,cx
     dec	cx
     pop	bx
     add	bx,cx
     pop	di

     mov	si,offset sourcename	;copy as parameter
     rep	movsb

	mov	si,dx		   ;copy as parameter
	pop	cx
	rep	movsb
	dec	di
	inc	bx
	mov	al,20h
	mov	es:[di],al
	inc	di

     push	di
     push	bx
     mov	di,offset destname    ;get length of destination
     mov	ax,0
     mov	cx,35
     cld
     repne	scasb
     mov	ax,35
     sub	ax,cx
     xchg	ax,cx
     dec	cx
     pop	bx
     add	bx,cx
     pop	di

     mov	si,offset destname    ;copy as parameter
     rep	movsb
     mov	si,offset todocdir    ;copy as parameter
     mov	cx,9
     rep	movsb
     add	bx,9

     mov     al,20h
     mov     es:[di],al
     inc     di
     inc     bx

     mov	si,offset extme       ;copy as parameter
     mov	cx,5
     rep	movsb
     add	bx,5

	mov	al,0dh
	mov	es:[di],al
	mov	di,offset cmd_line3
	mov	es:[di],bl	    ;record length of string

	pop	   es
		RET
unzip_me  ENDP

;------------------------------------------------------------------------------

unzip_slc PROC	  NEAR
	PUBLIC	unzip_slc

	push	es		;;save segment
	mov	ax,ds		;;make di point to code instead of screen
	mov	es,ax
	xor	bx,bx

	mov	di,dx		      ;get length of destination
	mov	ax,0
	mov	cx,35
	cld
	repne	scasb
	mov	ax,35
	sub	ax,cx
	xchg	ax,cx
	mov	bx,cx			;save length
	dec	bx
	push	cx

	mov	di,offset cmd_line3
	inc	di
	mov	al,20h
	mov	es:[di],al
	inc	bx
	inc	di
	mov	al,2dh		 ;-
	mov	es:[di],al
	inc	di
	inc	bx
	mov	al,4fh		 ;O
	mov	es:[di],al
	inc	di
	inc	bx
	mov	al,20h
	mov	es:[di],al
	inc	bx
	inc	di

     push	di
     push	bx
     mov	di,offset sourcename	;get length of destination
     mov	ax,0
     mov	cx,35
     cld
     repne	scasb
     mov	ax,35
     sub	ax,cx
     xchg	ax,cx
     dec	cx
     pop	bx
     add	bx,cx
     pop	di

     mov	si,offset sourcename	;copy as parameter
     rep	movsb

	mov	si,dx		   ;copy as parameter
	pop	cx
	rep	movsb
	dec	di
	inc	bx
	mov	al,20h
	mov	es:[di],al
	inc	di

     push	di
     push	bx
     mov	di,offset destname    ;get length of destination
     mov	ax,0
     mov	cx,35
     cld
     repne	scasb
     mov	ax,35
     sub	ax,cx
     xchg	ax,cx
     dec	cx
     pop	bx
     add	bx,cx
     pop	di

     mov	si,offset destname    ;copy as parameter
     rep	movsb
     mov	si,offset toslcdir    ;copy as parameter
     mov	cx,7
     rep	movsb
     add	bx,7

     mov     al,20h
     mov     es:[di],al
     inc     di
     inc     bx

     mov	si,offset extslc
     mov	cx,5
     rep	movsb
     add	bx,5

	mov	al,0dh
	mov	es:[di],al
	mov	di,offset cmd_line3
	mov	es:[di],bl	    ;record length of string

	pop	   es
		RET
unzip_slc ENDP

;------------------------------------------------------------------------------

unzip_slt PROC	  NEAR
	PUBLIC	unzip_slt

	push	es		;;save segment
	mov	ax,ds		;;make di point to code instead of screen
	mov	es,ax
	xor	bx,bx

	mov	di,dx		      ;get length of destination
	mov	ax,0
	mov	cx,35
	cld
	repne	scasb
	mov	ax,35
	sub	ax,cx
	xchg	ax,cx
	mov	bx,cx			;save length
	dec	bx
	push	cx

	mov	di,offset cmd_line3
	inc	di
	mov	al,20h
	mov	es:[di],al
	inc	bx
	inc	di
	mov	al,2dh		 ;-
	mov	es:[di],al
	inc	di
	inc	bx
	mov	al,4fh		 ;O
	mov	es:[di],al
	inc	di
	inc	bx
	mov	al,20h
	mov	es:[di],al
	inc	bx
	inc	di

     push	di
     push	bx
     mov	di,offset sourcename	;get length of destination
     mov	ax,0
     mov	cx,35
     cld
     repne	scasb
     mov	ax,35
     sub	ax,cx
     xchg	ax,cx
     dec	cx
     pop	bx
     add	bx,cx
     pop	di

     mov	si,offset sourcename	;copy as parameter
     rep	movsb

	mov	si,dx		   ;copy as parameter
	pop	cx
	rep	movsb
	dec	di
	inc	bx
	mov	al,20h
	mov	es:[di],al
	inc	di

     push	di
     push	bx
     mov	di,offset destname    ;get length of destination
     mov	ax,0
     mov	cx,35
     cld
     repne	scasb
     mov	ax,35
     sub	ax,cx
     xchg	ax,cx
     dec	cx
     pop	bx
     add	bx,cx
     pop	di

     mov	si,offset destname    ;copy as parameter
     rep	movsb
     mov	si,offset toslcdir    ;copy as parameter
     mov	cx,7
     rep	movsb
     add	bx,7

     mov     al,20h
     mov     es:[di],al
     inc     di
     inc     bx

     mov	si,offset extslt
     mov	cx,5
     rep	movsb
     add	bx,5

	mov	al,0dh
	mov	es:[di],al
	mov	di,offset cmd_line3
	mov	es:[di],bl	    ;record length of string

	pop	   es
		RET
unzip_slt ENDP

;------------------------------------------------------------------------------
	END
