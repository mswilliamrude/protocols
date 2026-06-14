PAGE  64,132  ; (CTRL-OH)   IBM PC PRINTER CONDENSED MODE
TITLE	ERRHAND

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

.model small

IF1
INCLUDE 	ASSEMBLY.MAC
ENDIF


ERRORSEGMENT	    SEGMENT  PUBLIC 'CODE'

ERRHAND PROC	FAR

	ASSUME	CS:ERRORSEGMENT
PUBLIC	ERRHAND

	JMP	START

cr	equ	0DH
lf	equ	0AH
eof	equ	1AH

;--------------------------- messages ------------------------------

err_1	db	'invalid function number',              cr,lf,'$'
err_2	db	'file not found',                       cr,lf,'$'
err_3	db	'path not found',                       cr,lf,'$'
err_4	db	'to many open files (no handles left)', cr,lf,'$'
err_5	db	'access denied',                        cr,lf,'$'
err_6	db	'invalid handle',                       cr,lf,'$'
err_7	db	'memory control blocks destroyed',      cr,lf,'$'
err_8	db	'insufficient memory',                  cr,lf,'$'
err_9	db	'invalid memory block address',         cr,lf,'$'
err_10	db	'invalid environment',                  cr,lf,'$'
err_11	db	'invalid format',                       cr,lf,'$'
err_12	db	'invalid access code',                  cr,lf,'$'
err_13	db	'invalid data',                         cr,lf,'$'
err_14	db	'invalid error message NOT USED',       cr,lf,'$'
err_15	db	'invalid drive was specified',          cr,lf,'$'
err_16	db	'attempt to remove current directory',  cr,lf,'$'
err_17	db	'not same device',                      cr,lf,'$'
err_18	db	'no matching or no more wildcard files', cr,lf,'$'
err_19	db	'attempt to write on write procted disk',cr,lf,'$'
err_20	db	'unknown unit',                          cr,lf,'$'
err_21	db	'drive not ready',                       cr,lf,'$'
err_22	db	'end of file mark not found in file - ',cr,lf,'$'
err_23	db	'data error (crc)',                     cr,lf,'$'
err_24	db	'bad request structure length',         cr,lf,'$'
err_25	db	'seek error',                           cr,lf,'$'
err_26	db	'unknown media type',                   cr,lf,'$'
err_27	db	'sector not found',                     cr,lf,'$'
err_28	db	'printer out of paper',                 cr,lf,'$'
err_29	db	'write fault',                          cr,lf,'$'
err_30	db	'read fault',                           cr,lf,'$'
err_31	db	'general failure',                      cr,lf,'$'
err_32	db	'sharing violation',                    cr,lf,'$'
err_33	db	'lock violation',                       cr,lf,'$'
err_34	db	'invalid disk change',                  cr,lf,'$'
err_35	db	'fcb unavailable',                      cr,lf,'$'
err_36	db	'sharing buffer overflow',              cr,lf,'$'
err_37	db	'reserved',                             cr,lf,'$'
err_38	db	'reserved',                             cr,lf,'$'
err_39	db	'reserved',                             cr,lf,'$'
err_40	db	'reserved',                             cr,lf,'$'
err_41	db	'reserved',                             cr,lf,'$'
err_42	db	'reserved',                             cr,lf,'$'
err_43	db	'reserved',                             cr,lf,'$'
err_44	db	'reserved',                             cr,lf,'$'
err_45	db	'reserved',                             cr,lf,'$'
err_46	db	'reserved',                             cr,lf,'$'
err_47	db	'reserved',                             cr,lf,'$'
err_48	db	'reserved',                             cr,lf,'$'
err_49	db	'reserved',                             cr,lf,'$'
err_50	db	'network request not supported',        cr,lf,'$'
err_51	db	'remote computer not listening',        cr,lf,'$'
err_52	db	'duplicate name on network',            cr,lf,'$'
err_53	db	'network name not found',               cr,lf,'$'
err_54	db	'network busy',                         cr,lf,'$'
err_55	db	'network device no longer exist',       cr,lf,'$'
err_56	db	'net BIOS command limit exceeded',      cr,lf,'$'
err_57	db	'network adapter hardware error',       cr,lf,'$'
err_58	db	'incorrect response from network',      cr,lf,'$'
err_59	db	'unexpected network error',             cr,lf,'$'
err_60	db	'incompatiable remote adapter',         cr,lf,'$'
err_61	db	'print que full',                       cr,lf,'$'
err_62	db	'not enough space for print file',      cr,lf,'$'
err_63	db	'print file was deleted',               cr,lf,'$'
err_64	db	'network name was deleted',             cr,lf,'$'
err_65	db	'access denied',                        cr,lf,'$'
err_66	db	'network device type incorrect',        cr,lf,'$'
err_67	db	'network name not found',               cr,lf,'$'
err_68	db	'network name limit exceeded',          cr,lf,'$'
err_69	db	'net BIOS session limit exceeded',      cr,lf,'$'
err_70	db	'temporarily paused',                   cr,lf,'$'
err_71	db	'network request not accepted',         cr,lf,'$'
err_72	db	'print or disk redirection is paused',  cr,lf,'$'
err_73	db	'reserved',                             cr,lf,'$'
err_74	db	'reserved',                             cr,lf,'$'
err_75	db	'reserved',                             cr,lf,'$'
err_76	db	'reserved',                             cr,lf,'$'
err_77	db	'reserved',                             cr,lf,'$'
err_78	db	'reserved',                             cr,lf,'$'
err_79	db	'reserved',                             cr,lf,'$'
err_80	db	'file exists',                          cr,lf,'$'
err_81	db	'resevered',                            cr,lf,'$'
err_82	db	'cannot make directory entry',          cr,lf,'$'
err_83	db	'fail on interrupt 24',                 cr,lf,'$'
err_84	db	'too many redirections',                cr,lf,'$'
err_85	db	'duplicate redirection',                cr,lf,'$'
err_86	db	'invalid password',                     cr,lf,'$'
err_87	db	'network data fault',                   cr,lf,'$'
err_88	db	'seek error',                           cr,lf,'$'

START:
	push	ax

	EQUIP_CHK		  ;macro determines if color or mono
	MOV	ES,AX		  ;screen is connected
	CMP	AX,0B000H
	JNE	COLOR

SCREEN	SEGMENT  AT   0B000H	  ;Monochrome screen
SCREEN	ENDS
	JMP	SHORT SET_SCREEN
;
COLOR:
SCREEN	SEGMENT  AT   0B800H	  ;Color screen
SCREEN	ENDS
;
SET_SCREEN:
	ASSUME	ES:SCREEN

	clr
	MAKEBOX  6,12,17,66    ;startline,startcol,stopline,stopcol
	calc_addr 12,15
	cursor

	pop	ax
	push	ds
	push	ax
	mov	ax,cs
	mov	ds,ax
	pop	ax

	cmp	al,0
	jne	_01
	jmp	err_exit
_01:
	cmp	al,1
	jne	cmp_2
	mov	dx,offset cs:err_1
	jmp	print_msg
cmp_2:	cmp	al,2
	jne	cmp_3
	mov	dx,offset cs:err_2
	jmp	print_msg
cmp_3:	cmp	al,3
	jne	cmp_4
	mov	dx,offset cs:err_3
	jmp	print_msg
cmp_4:	cmp	al,4
	jne	cmp_5
	mov	dx,offset cs:err_4
	jmp	print_msg
cmp_5:	cmp	al,5
	jne	cmp_6
	mov	dx,offset cs:err_5
	jmp	print_msg
cmp_6:	cmp	al,6
	jne	cmp_7
	mov	dx,offset cs:err_6
	jmp	print_msg
cmp_7:	cmp	al,7
	jne	cmp_8
	mov	dx,offset cs:err_7
	jmp	print_msg
cmp_8:	cmp	al,8
	jne	cmp_9
	mov	dx,offset cs:err_8
	jmp	print_msg
cmp_9:	cmp	al,9
	jne	cmp_10
	mov	dx,offset cs:err_9
	jmp	print_msg
cmp_10: cmp	al,10
	jne	cmp_11
	mov	dx,offset cs:err_10
	jmp	print_msg
cmp_11: cmp	al,11
	jne	cmp_12
	mov	dx,offset cs:err_11
	jmp	print_msg
cmp_12: cmp	al,12
	jne	cmp_13
	mov	dx,offset cs:err_12
	jmp	print_msg
cmp_13: cmp	al,13
	jne	cmp_14
	mov	dx,offset cs:err_13
	jmp	print_msg
cmp_14: cmp	al,14
	jne	cmp_15
	mov	dx,offset cs:err_14
	jmp	print_msg
cmp_15: cmp	al,15
	jne	cmp_16
	mov	dx,offset cs:err_15
	jmp	print_msg
cmp_16: cmp	al,16
	jne	cmp_17
	mov	dx,offset cs:err_16
	jmp	print_msg
cmp_17: cmp	al,17
	jne	cmp_18
	mov	dx,offset cs:err_17
	jmp	print_msg
cmp_18: cmp	al,18
	jne	cmp_19
	mov	dx,offset cs:err_18
	jmp	print_msg
cmp_19: cmp	al,19
	jne	cmp_20
	mov	dx,offset cs:err_19
	jmp	print_msg
cmp_20: cmp	al,20
	jne	cmp_21
	mov	dx,offset cs:err_20
	jmp	print_msg
cmp_21: cmp	al,21
	jne	cmp_22
	mov	dx,offset cs:err_21
	jmp	print_msg
cmp_22: cmp	al,22
	jne	cmp_23
	mov	dx,offset cs:err_22
	jmp	print_msg
cmp_23: cmp	al,23
	jne	cmp_24
	mov	dx,offset cs:err_23
	jmp	print_msg
cmp_24: cmp	al,24
	jne	cmp_25
	mov	dx,offset cs:err_24
	jmp	print_msg
cmp_25: cmp	al,25
	jne	cmp_26
	mov	dx,offset cs:err_25
	jmp	print_msg
cmp_26: cmp	al,26
	jne	cmp_27
	mov	dx,offset cs:err_26
	jmp	print_msg
cmp_27: cmp	al,27
	jne	cmp_28
	mov	dx,offset cs:err_27
	jmp	print_msg
cmp_28: cmp	al,28
	jne	cmp_29
	mov	dx,offset cs:err_28
	jmp	print_msg
cmp_29: cmp	al,29
	jne	cmp_30
	mov	dx,offset cs:err_29
	jmp	print_msg
cmp_30: cmp	al,30
	jne	cmp_31
	mov	dx,offset cs:err_30
	jmp	print_msg
cmp_31: cmp	al,31
	jne	cmp_32
	mov	dx,offset cs:err_31
	jmp	print_msg
cmp_32: cmp	al,32
	jne	cmp_33
	mov	dx,offset cs:err_32
	jmp	print_msg
cmp_33: cmp	al,33
	jne	cmp_34
	mov	dx,offset cs:err_33
	jmp	print_msg
cmp_34: cmp	al,34
	jne	cmp_35
	mov	dx,offset cs:err_34
	jmp	print_msg
cmp_35: cmp	al,35
	jne	cmp_36
	mov	dx,offset cs:err_35
	jmp	print_msg
cmp_36: cmp	al,36
	jne	cmp_37
	mov	dx,offset cs:err_36
	jmp	print_msg
cmp_37: cmp	al,37
	jne	cmp_38
	mov	dx,offset cs:err_37
	jmp	print_msg
cmp_38: cmp	al,38
	jne	cmp_39
	mov	dx,offset cs:err_38
	jmp	print_msg
cmp_39: cmp	al,39
	jne	cmp_40
	mov	dx,offset cs:err_39
	jmp	print_msg
cmp_40: cmp	al,40
	jne	cmp_41
	mov	dx,offset cs:err_40
	jmp	print_msg
cmp_41: cmp	al,41
	jne	cmp_42
	mov	dx,offset cs:err_41
	jmp	print_msg
cmp_42: cmp	al,42
	jne	cmp_43
	mov	dx,offset cs:err_42
	jmp	print_msg
cmp_43: cmp	al,43
	jne	cmp_44
	mov	dx,offset cs:err_43
	jmp	print_msg
cmp_44: cmp	al,44
	jne	cmp_45
	mov	dx,offset cs:err_44
	jmp	print_msg
cmp_45: cmp	al,45
	jne	cmp_46
	mov	dx,offset cs:err_45
	jmp	print_msg
cmp_46: cmp	al,46
	jne	cmp_47
	mov	dx,offset cs:err_47
	jmp	print_msg
cmp_47: cmp	al,47
	jne	cmp_48
	mov	dx,offset cs:err_47
	jmp	print_msg
cmp_48: cmp	al,48
	jne	cmp_49
	mov	dx,offset cs:err_48
	jmp	print_msg
cmp_49: cmp	al,49
	jne	cmp_50
	mov	dx,offset cs:err_49
	jmp	print_msg
cmp_50: cmp	al,50
	jne	cmp_51
	mov	dx,offset cs:err_50
	jmp	print_msg
cmp_51: cmp	al,51
	jne	cmp_52
	mov	dx,offset cs:err_51
	jmp	print_msg
cmp_52: cmp	al,52
	jne	cmp_53
	mov	dx,offset cs:err_52
	jmp	print_msg
cmp_53: cmp	al,53
	jne	cmp_54
	mov	dx,offset cs:err_53
	jmp	print_msg
cmp_54: cmp	al,54
	jne	cmp_55
	mov	dx,offset cs:err_54
	jmp	print_msg
cmp_55: cmp	al,55
	jne	cmp_56
	mov	dx,offset cs:err_55
	jmp	print_msg
cmp_56: cmp	al,56
	jne	cmp_57
	mov	dx,offset cs:err_56
	jmp	print_msg
cmp_57: cmp	al,57
	jne	cmp_58
	mov	dx,offset cs:err_57
	jmp	print_msg
cmp_58: cmp	al,58
	jne	cmp_59
	mov	dx,offset cs:err_58
	jmp	print_msg
cmp_59: cmp	al,59
	jne	cmp_60
	mov	dx,offset cs:err_59
	jmp	print_msg
cmp_60: cmp	al,60
	jne	cmp_61
	mov	dx,offset cs:err_60
	jmp	print_msg
cmp_61: cmp	al,61
	jne	cmp_62
	mov	dx,offset cs:err_61
	jmp	print_msg
cmp_62: cmp	al,62
	jne	cmp_63
	mov	dx,offset cs:err_62
	jmp	print_msg
cmp_63: cmp	al,63
	jne	cmp_64
	mov	dx,offset cs:err_63
	jmp	print_msg
cmp_64: cmp	al,64
	jne	cmp_65
	mov	dx,offset cs:err_64
	jmp	print_msg
cmp_65: cmp	al,65
	jne	cmp_66
	mov	dx,offset cs:err_65
	jmp	print_msg
cmp_66: cmp	al,66
	jne	cmp_67
	mov	dx,offset cs:err_66
	jmp	print_msg
cmp_67: cmp	al,67
	jne	cmp_68
	mov	dx,offset cs:err_67
	jmp	print_msg
cmp_68: cmp	al,68
	jne	cmp_69
	mov	dx,offset cs:err_68
	jmp	print_msg
cmp_69: cmp	al,69
	jne	cmp_70
	mov	dx,offset cs:err_69
	jmp	print_msg
cmp_70: cmp	al,70
	jne	cmp_71
	mov	dx,offset cs:err_70
	jmp	print_msg
cmp_71: cmp	al,71
	jne	cmp_72
	mov	dx,offset cs:err_71
	jmp	print_msg
cmp_72: cmp	al,72
	jne	cmp_73
	mov	dx,offset cs:err_72
	jmp	print_msg
cmp_73: cmp	al,73
	jne	cmp_74
	mov	dx,offset cs:err_73
	jmp	print_msg
cmp_74: cmp	al,74
	jne	cmp_75
	mov	dx,offset cs:err_74
	jmp	print_msg
cmp_75: cmp	al,75
	jne	cmp_76
	mov	dx,offset cs:err_75
	jmp	short print_msg
cmp_76: cmp	al,76
	jne	cmp_77
	mov	dx,offset cs:err_76
	jmp	short print_msg
cmp_77: cmp	al,77
	jne	cmp_78
	mov	dx,offset cs:err_77
	jmp	short print_msg
cmp_78: cmp	al,78
	jne	cmp_79
	mov	dx,offset cs:err_78
	jmp	short print_msg
cmp_79: cmp	al,79
	jne	cmp_80
	mov	dx,offset cs:err_79
	jmp	short print_msg
cmp_80: cmp	al,80
	jne	cmp_81
	mov	dx,offset cs:err_80
	jmp	short print_msg
cmp_81: cmp	al,81
	jne	cmp_82
	mov	dx,offset cs:err_81
	jmp	short print_msg
cmp_82: cmp	al,82
	jne	cmp_83
	mov	dx,offset cs:err_82
	jmp	short print_msg
cmp_83: cmp	al,83
	jne	cmp_84
	mov	dx,offset cs:err_83
	jmp	short print_msg
cmp_84: cmp	al,84
	jne	cmp_85
	mov	dx,offset cs:err_84
	jmp	short print_msg
cmp_85: cmp	al,85
	jne	cmp_86
	mov	dx,offset cs:err_85
	jmp	short print_msg
cmp_86: cmp	al,86
	jne	cmp_87
	mov	dx,offset cs:err_86
	jmp	short print_msg
cmp_87: cmp	al,87
	jne	cmp_88
	mov	dx,offset cs:err_87
	jmp	short print_msg
cmp_88: cmp	al,88
	jne	err_EXIT
	mov	dx,offset cs:err_88
	jmp	short print_msg

print_msg:
	xor	ax,ax
	mov	ah,09H
	int	21H

err_EXIT:
	pop	ds
	RET

ERRHAND   ENDP

;-------------------------------------------------------------


ERRORSEGMENT	    ENDS
	END	ERRHAND
