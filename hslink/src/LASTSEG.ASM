

EXTRN	ERRHAND:FAR

EXTRN	ENVIORMENT_SEG:WORD
EXTRN	ERROR:BYTE

INCLUDE ZSEG.CMN

lastsegs segment para public  'code'
	assume	cs:lastsegs

public	lastseg,mem_size

lastseg  proc	 far

;#------------------------ reduce memory allocation --------------------------

mem_size:
	push	es
	mov	ax,ENVIORMENT_SEG
	mov	es,ax
	mov	bx,seg zseg		;get end of program
	sub	bx,ax			;subract start from end
	mov	ax,4A00H		;request memory size modification
	int	21H			;to release unneeded memory
	jnc	mem_rel_ok

	mov	al,23
	call	ERRHAND
	mov	error,1

mem_rel_ok:
	pop	es
	ret
lastseg  endp
lastsegs ends

	end	lastseg
