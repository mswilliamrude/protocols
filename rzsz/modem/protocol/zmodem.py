import datetime
import os
import time
from modem.base import Modem
from modem import const
from modem.tools import log


class ZMODEM(Modem):
    '''
    ZMODEM protocol implementation, expects an object to read from and an
    object to write to.
    '''
    
    def __init__(self, getc, putc, progress_callback=None, compress=False, escape_all=False):
        super().__init__(getc, putc)
        self.progress_callback = progress_callback
        self.compress_enabled = compress
        self.escape_all = escape_all


    def send(self, files, retry=16, timeout=60, overwrite=False):
        '''
        Send one or more files using ZMODEM protocol.
        files is a list of file paths.
        '''
        import os
        
        # 1. Send ZRQINIT
        # Many terminal emulators (SecureCRT, iTerm2, etc.) require "rz\r" before the ZRQINIT 
        # signature to auto-trigger the local ZMODEM receiver.
        self.putc(b'rz\r', timeout)
        self._send_hex_header([const.ZRQINIT, 0, 0, 0, 0], timeout)
        
        # 2. Wait for ZRINIT
        kind, header = None, None
        peer_zf1 = 0
        while True:
            res = self._recv_header(timeout)
            if res is const.TIMEOUT:
                log.error("Timeout waiting for ZRINIT")
                return False
            if res and res[0] == const.ZRINIT:
                header = res
                peer_zf1 = header[const.ZP2]
                peer_zf0 = header[const.ZP3]
                if peer_zf0 & const.ZF0_ESCCTL:
                    log.info("Receiver requested control character escaping (ESCCTL)")
                    self.escape_all = True
                break
                
        can_zlib = (peer_zf1 & const.ZF1_CANZLIB) != 0 and self.compress_enabled
        if can_zlib:
            log.info("Receiver supports inline ZLIB compression")
        
        # 3. For each file
        sent_count = 0
        for filepath in files:
            try:
                size = os.path.getsize(filepath)
                filename = os.path.basename(filepath)
            except Exception as e:
                log.error("Cannot read file %s: %s", filepath, e)
                import sys
                print(f"\r\n[ERROR] Cannot read file {filepath}: {e}\r\n", file=sys.stderr)
                continue
            
            # Send ZFILE header (Hex is fine, or BIN)
            zf1 = const.ZF1_ZMCLOB if overwrite else 0
            zf3 = const.ZF3_ZLIB if can_zlib else 0
            # Note: _send_hex_header expects [type, f3, f2, f1, f0] order because ZFILE header is ZP0=ZF3...ZP3=ZF0
            # Wait, no. Standard says ZF3 is at index 0 (which is zfile_header[1] since 0 is frame type).
            # zfile_header[1] = ZF3, [2] = ZF2, [3] = ZF1, [4] = ZF0.
            self._send_hex_header([const.ZFILE, zf3, 0, zf1, 0], timeout)
            
            # Send ZFILE data
            # Format: filename \x00 filesize \x00
            file_info = filename.encode('utf-8') + b'\x00' + str(size).encode('utf-8') + b' 0 0 0\x00'
            self._send_16_data(file_info, const.ZCRCW, timeout)
            
            # Wait for ZRPOS
            offset = 0
            while True:
                res = self._recv_header(timeout)
                if res is const.TIMEOUT:
                    log.error("Timeout waiting for ZRPOS")
                    return False
                if res and res[0] == const.ZRPOS:
                    offset = res[const.ZP0] | (res[const.ZP1] << 8) | (res[const.ZP2] << 16) | (res[const.ZP3] << 24)
                    break
                elif res and res[0] == const.ZSKIP:
                    offset = -1
                    break
                    
            if offset == -1:
                log.info("Receiver skipped file")
                continue
                
            # Send ZDATA header
            self._send_hex_header([const.ZDATA, offset & 0xff, (offset >> 8) & 0xff, (offset >> 16) & 0xff, (offset >> 24) & 0xff], timeout)
            
            # Stream data
            import zlib
            total_sent = offset
            with open(filepath, 'rb') as f:
                f.seek(offset)
                compressor = zlib.compressobj() if can_zlib else None
                
                while True:
                    chunk = f.read(4096 if can_zlib else 1024)
                    if not chunk:
                        if compressor:
                            comp_chunk = compressor.flush()
                            if comp_chunk:
                                for i in range(0, len(comp_chunk), 1024):
                                    sub = comp_chunk[i:i+1024]
                                    self._send_16_data(sub, const.ZCRCG, timeout)
                        self._send_16_data(b'', const.ZCRCE, timeout)
                        break
                    
                    if compressor:
                        comp_chunk = compressor.compress(chunk)
                        if comp_chunk:
                            for i in range(0, len(comp_chunk), 1024):
                                sub = comp_chunk[i:i+1024]
                                self._send_16_data(sub, const.ZCRCG, timeout)
                    else:
                        self._send_16_data(chunk, const.ZCRCG, timeout)
                        
                    total_sent += len(chunk)
                    if self.progress_callback:
                        self.progress_callback(filename, size, total_sent)
                        
                    # Non-blocking check for receiver abort (CAN) or other interruptions
                    char = self.getc(1, 0)
                    if char:
                        if char == bytes([const.ZDLE]):
                            self._can_count = getattr(self, '_can_count', 0) + 1
                            if self._can_count >= 5:
                                log.info("Received 5 consecutive CAN (Ctrl+X), aborting")
                                raise KeyboardInterrupt("Transfer cancelled by peer")
                        else:
                            self._can_count = 0
            
            # Send ZEOF
            self._send_hex_header([const.ZEOF, size & 0xff, (size >> 8) & 0xff, (size >> 16) & 0xff, (size >> 24) & 0xff], timeout)
            
            # Wait for ZRINIT
            while True:
                res = self._recv_header(timeout)
                if res is const.TIMEOUT:
                    return False
                if res and res[0] == const.ZRINIT:
                    break
                    
            sent_count += 1

        # Send ZFIN
        self._send_hex_header([const.ZFIN, 0, 0, 0, 0], timeout)
        
        # Wait for ZFIN
        while True:
            res = self._recv_header(timeout)
            if res is const.TIMEOUT:
                break
            if res and res[0] == const.ZFIN:
                break
        
        # Send OO
        self.putc(b'O', timeout)
        self.putc(b'O', timeout)
        return sent_count > 0

    def _send_16_data(self, data, frameend, timeout):
        mine = 0
        for byte in data:
            char = byte if isinstance(byte, int) else ord(byte)
            mine = self.calc_crc16(chr(char), mine)
            self._send(char, timeout)
        
        self.putc(bytes([const.ZDLE]), timeout)
        self.putc(bytes([frameend]), timeout)
        mine = self.calc_crc16(chr(frameend), mine)
        
        self._send(mine >> 8, timeout)
        self._send(mine & 0xff, timeout)
    def recv(self, basedir, retry=16, timeout=60, delay=1):
        '''
        Receive some files via the ZMODEM protocol and place them under
        ``basedir``::

            >>> print modem.recv(basedir)
            3

        Returns the number of files received on success or ``None`` in case of
        failure.

        N.B.: currently there are no control on the existence of files, so they
        will be silently overwritten.
        '''
        
        # Loop until we established a connection, we expect to receive a
        # different packet than ZRQINIT
        kind = const.TIMEOUT
        header = None
        while kind in [const.TIMEOUT, const.ZRQINIT]:
            self._send_zrinit(timeout)
            res = self._recv_header(timeout)
            if res is const.TIMEOUT or res is False:
                kind = const.TIMEOUT
            else:
                header = res
                kind = res[0]

        log.info('ZMODEM connection established')

        file_count = 0
        # Receive files
        while kind != const.ZFIN:
            if kind == const.ZFILE:
                if self._recv_file(header, basedir, timeout, retry) is not False:
                    file_count += 1
                kind = const.TIMEOUT
            elif kind == const.ZFIN:
                continue
            else:
                log.info('Did not get a file offer? Sending position header')
                self._send_pos_header(const.ZCOMPL, 0, timeout)
                kind = const.TIMEOUT

            while kind is const.TIMEOUT:
                self._send_zrinit(timeout)
                res = self._recv_header(timeout)
                if res is const.TIMEOUT or res is False:
                    kind = const.TIMEOUT
                else:
                    header = res
                    kind = res[0]

        # Acknowledge the ZFIN
        log.info('Received ZFIN, done receiving files')
        self._send_hex_header([const.ZFIN, 0, 0, 0, 0], timeout)

        # Wait for the over and out sequence
        kind = self._recv(timeout)
        while kind not in [ord('O'), const.TIMEOUT]:
            kind = self._recv(timeout)

        if kind is not const.TIMEOUT:
            # We got the first 'O', wait for the second 'O'
            kind = self._recv(timeout)
            while kind not in [ord('O'), const.TIMEOUT]:
                kind = self._recv(timeout)

        return file_count

    def _recv(self, timeout):
        # Outer loop
        while True:
            while True:
                char = self._recv_raw(timeout)
                if char is const.TIMEOUT:
                    return const.TIMEOUT

                if char == const.ZDLE:
                    break
                elif char in [0x11, 0x91, 0x13, 0x93]:
                    continue
                else:
                    # Regular character
                    return char

            # ZDLE encoded sequence or session abort
            char = self._recv_raw(timeout)
            if char is const.TIMEOUT:
                return const.TIMEOUT

            if char in [0x11, 0x91, 0x13, 0x93, const.ZDLE]:
                # Drop
                continue

            # Special cases
            if char in [const.ZCRCE, const.ZCRCG, const.ZCRCQ, const.ZCRCW]:
                return char | const.ZDLEESC
            elif char == const.ZRUB0:
                return 0x7f
            elif char == const.ZRUB1:
                return 0xff
            else:
                # Escape sequence
                return char ^ 0x40

    def _recv_raw(self, timeout):
        char = self.getc(1, timeout)
        if char == b'':
            return const.TIMEOUT
        if char is not const.TIMEOUT:
            char_val = ord(char)
            if char_val == 0x18:
                self._can_count = getattr(self, '_can_count', 0) + 1
                if self._can_count >= 5:
                    log.info("Received 5 consecutive CAN (Ctrl+X), aborting")
                    raise KeyboardInterrupt("Transfer cancelled by peer")
            else:
                self._can_count = 0
                
            return char_val
        return char

    def _recv_data(self, ack_file_pos, timeout, ack=True):
        # zack_header = [const.ZACK, 0, 0, 0, 0]
        pos = ack_file_pos

        if self._recv_bits == 16:
            sub_frame_kind, data = self._recv_16_data(timeout)
        elif self._recv_bits == 32:
            sub_frame_kind, data = self._recv_32_data(timeout)
        else:
            raise TypeError('Invalid _recv_bits size')

        # Update file positions
        if sub_frame_kind is const.TIMEOUT:
            return const.TIMEOUT, None
        else:
            pos += len(data)

        # Frame continues non-stop
        if sub_frame_kind == const.ZCRCG:
            return const.FRAMEOK, data
        # Frame ends
        elif sub_frame_kind == const.ZCRCE:
            return const.ENDOFFRAME, data
        # Frame continues; ZACK expected
        elif sub_frame_kind == const.ZCRCQ:
            if ack: self._send_pos_header(const.ZACK, pos, timeout)
            return const.FRAMEOK, data
        # Frame ends; ZACK expected
        elif sub_frame_kind == const.ZCRCW:
            if ack: self._send_pos_header(const.ZACK, pos, timeout)
            return const.ENDOFFRAME, data
        else:
            return False, data

    def _recv_16_data(self, timeout):
        char = 0
        data = bytearray()
        mine = 0
        log.debug("Entering _recv_16_data")
        while char < 0x100:
            char = self._recv(timeout)
            if char is const.TIMEOUT:
                log.debug("_recv_16_data timeout!")
                return const.TIMEOUT, b''
            elif char < 0x100:
                mine = self.calc_crc16(bytes([char & 0xff]), mine)
                data.append(char)

        # Calculate our crc, unescape the sub_frame_kind
        sub_frame_kind = char ^ const.ZDLEESC
        mine = self.calc_crc16(bytes([sub_frame_kind]), mine)

        # Read their crc
        rcrc = self._recv(timeout) << 0x08
        rcrc |= self._recv(timeout)

        log.debug('My CRC16 = %08x, theirs = %08x' % (mine, rcrc))
        if mine != rcrc:
            log.error('Invalid CRC16')
            return timeout, b''
        else:
            return sub_frame_kind, bytes(data)

    def _recv_32_data(self, timeout):
        mine = 0
        data = bytearray()
        while True:
            char = self._recv(timeout)
            if char is const.TIMEOUT:
                return const.TIMEOUT, b''
            elif char < 0x100:
                mine = self.calc_crc32(bytes([char & 0xff]), mine)
                data.append(char)
            else:
                break

        # Calculate our crc, unescape the sub_frame_kind
        sub_frame_kind = char ^ const.ZDLEESC
        mine = self.calc_crc32(bytes([sub_frame_kind]), mine)

        # Read their crc
        rcrc = self._recv(timeout) << 0x00
        rcrc |= self._recv(timeout) << 0x08
        rcrc |= self._recv(timeout) << 0x10
        rcrc |= self._recv(timeout) << 0x18

        log.debug('My CRC32 = %08x, theirs = %08x' % (mine, rcrc))
        if mine != rcrc:
            log.error('Invalid CRC32')
            return timeout, b''
        else:
            return sub_frame_kind, bytes(data)

    def _recv_header(self, timeout, errors=10):
        header_length = 0
        error_count = 0
        char = None
        while header_length == 0:
            # Frist ZPAD
            while char != const.ZPAD:
                char = self._recv_raw(timeout)
                if char is const.TIMEOUT:
                    return const.TIMEOUT

            # Second ZPAD
            char = self._recv_raw(timeout)
            if char == const.ZPAD:
                # Get raw character
                char = self._recv_raw(timeout)
                if char is const.TIMEOUT:
                    return const.TIMEOUT

            # Spurious ZPAD check
            if char != const.ZDLE:
                continue

            # Read header style
            char = self._recv_raw(timeout)
            if char is const.TIMEOUT:
                return const.TIMEOUT

            if char == const.ZBIN:
                header_length, header = self._recv_bin16_header(timeout)
                self._recv_bits = 16
            elif char == const.ZHEX:
                header_length, header = self._recv_hex_header(timeout)
                self._recv_bits = 16
            elif char == const.ZBIN32:
                header_length, header = self._recv_bin32_header(timeout)
                self._recv_bits = 32
            else:
                error_count += 1
                if error_count > errors:
                    return const.TIMEOUT
                continue

        # We received a valid header
        # if header[0] == const.ZDATA:
        #     ack_file_pos = \
        #         header[const.ZP0] | \
        #         header[const.ZP1] << 0x08 | \
        #         header[const.ZP2] << 0x10 | \
        #         header[const.ZP3] << 0x20

        # elif header[0] == const.ZFILE:
        #     # ack_file_pos = 0
        #     pass

        return header

    def _recv_bin16_header(self, timeout):
        '''
        Recieve a header with 16 bit CRC.
        '''
        header = []
        mine = 0
        for x in range(0, 5):
            char = self._recv(timeout)
            if char is const.TIMEOUT:
                return 0, False
            else:
                mine = self.calc_crc16(chr(char), mine)
                header.append(char)

        rcrc = self._recv(timeout) << 0x08
        rcrc |= self._recv(timeout)

        if mine != rcrc:
            log.error('Invalid CRC16 in header')
            return 0, False
        else:
            return 5, header

    def _recv_bin32_header(self, timeout):
        header = []
        mine = 0

        for x in range(0, 5):
            char = self._recv(timeout)
            if char is const.TIMEOUT:
                return 0, False
            else:
                mine = self.calc_crc32(bytes([char]), mine)
                header.append(char)

        # Read their crc
        rcrc = self._recv(timeout) << 0x00
        rcrc |= self._recv(timeout) << 0x08
        rcrc |= self._recv(timeout) << 0x10
        rcrc |= self._recv(timeout) << 0x18

        log.debug('My CRC32 = %08x, theirs = %08x' % (mine, rcrc))
        if mine != rcrc:
            log.error('Invalid CRC32 in header')
            return 0, False
        else:
            return 5, header

    def _recv_hex_header(self, timeout):
        '''
        Receive a header with HEX encoding.
        '''
        header = []
        mine = 0
        for x in range(0, 5):
            char = self._recv_hex(timeout)
            if char is const.TIMEOUT:
                return 0, False
            mine = self.calc_crc16(chr(char), mine)
            header.append(char)

        # Read their crc
        char = self._recv_hex(timeout)
        if char is const.TIMEOUT:
            return 0, False
        rcrc = char << 0x08
        char = self._recv_hex(timeout)
        if char is const.TIMEOUT:
            return 0, False
        rcrc |= char

        log.debug('My CRC = %04x, theirs = %04x' % (mine, rcrc))
        if mine != rcrc:
            log.error('Invalid CRC16 in receiving HEX header')
            return 0, False

        # Read to see if we receive a carriage return
        char = self.getc(1, timeout)
        if char == b'\r' or char == b'\x8d' or char == b'\n' or char == b'\x8a':
            # Expect a second one (which we discard)
            self.getc(1, timeout)
            
        # Many senders (including us) append XON after \r\n, optionally consume it
        # Actually it's safer to just do a quick non-blocking read
        char = self.getc(1, 0.1)
        if char != b'\x11' and char != b'':
            # It wasn't XON, but we consumed it. In a robust implementation we'd unget it,
            # but we can just ignore it or log it.
            pass

        return 5, header

    def _recv_hex(self, timeout):
        n1 = self._recv_hex_nibble(timeout)
        if n1 is const.TIMEOUT:
            return const.TIMEOUT
        n0 = self._recv_hex_nibble(timeout)
        if n0 is const.TIMEOUT:
            return const.TIMEOUT
        return (n1 << 0x04) | n0

    def _recv_hex_nibble(self, timeout):
        char = self.getc(1, timeout)
        if char is const.TIMEOUT:
            return const.TIMEOUT

        if isinstance(char, bytes) and len(char) > 0:
            char = char[0]
        elif isinstance(char, str) and len(char) > 0:
            char = ord(char[0])
        else:
            return const.TIMEOUT
            
        if char > 57: # '9'
            if char < 97 or char > 102: # 'a' to 'f'
                if char >= 65 and char <= 70: # 'A' to 'F'
                    return char - 65 + 10
                # Illegal character
                return const.TIMEOUT
            return char - 97 + 10
        else:
            if char < 48: # '0'
                # Illegal character
                return const.TIMEOUT
            return char - 48

    def _recv_file(self, zfile_header, basedir, timeout, retry):
        import zlib
        log.info('Abort to receive a file in %s' % (basedir,))
        pos = 0

        is_zlib = (zfile_header[const.ZP0] & const.ZF3_ZLIB) != 0 if len(zfile_header) > const.ZP0 else False
        if is_zlib:
            log.info("ZLIB inline decompression enabled for this file")

        force_overwrite = (zfile_header[const.ZP2] & const.ZF1_ZMCLOB) != 0 if len(zfile_header) > const.ZP2 else False
        if force_overwrite:
            log.info("Sender requested forced overwrite for this file")

        # Read the data subpacket containing the file information
        kind, data = self._recv_data(pos, timeout, ack=False)
        pos += len(data)
        if kind not in [const.FRAMEOK, const.ENDOFFRAME]:
            if kind is not const.TIMEOUT:
                # File info metadata corrupted
                self._send_znak(pos, timeout)
            return False

        # We got the file name
        part = data.split(b'\x00')
        filename = part[0].decode('utf-8', 'replace')
        filepath = os.path.join(basedir, os.path.basename(filename))
        
        file_size_on_disk = 0
        if os.path.exists(filepath) and not force_overwrite:
            file_size_on_disk = os.path.getsize(filepath)
            fp = open(filepath, 'ab')
            log.info('File exists, resuming from offset %d' % file_size_on_disk)
        else:
            if force_overwrite and os.path.exists(filepath):
                log.info('File exists, but overwrite requested')
            fp = open(filepath, 'wb')
            
        part = part[1].split(b' ')
        log.info('Meta %r' % (part,))
        size = int(part[0])
        # Date is octal (!?)
        if len(part) > 1 and part[1]:
            date = datetime.datetime.fromtimestamp(int(part[1], 8))
        else:
            date = datetime.datetime.now()
        # We ignore mode and serial number, whatever, dude :-)

        log.info('Receiving file "%s" with size %d, mtime %s' %
                 (filename, size, date))

        # Receive contents
        start = time.time()
        kind = None
        total_size = file_size_on_disk
        
        # Send initial ZRPOS
        self._send_pos_header(const.ZRPOS, file_size_on_disk, timeout)
        
        while True:
            header = self._recv_header(timeout)
            if header is const.TIMEOUT or header is False:
                break
            kind = header[0]
            
            if kind == const.ZDATA:
                decompressor = zlib.decompressobj() if is_zlib else None
                # Read data subpackets
                frame_kind = const.FRAMEOK
                while frame_kind == const.FRAMEOK:
                    frame_kind, chunk = self._recv_data(fp.tell(), timeout)
                    if frame_kind in [const.ENDOFFRAME, const.FRAMEOK]:
                        if decompressor:
                            try:
                                chunk = decompressor.decompress(chunk)
                            except zlib.error as e:
                                log.error(f"Zlib decompression failed: {e}")
                                self._send_pos_header(const.ZRPOS, fp.tell(), timeout)
                                break
                        fp.write(chunk)
                        total_size += len(chunk)
                        if self.progress_callback:
                            self.progress_callback(filename, size, total_size)
            elif kind == const.ZEOF:
                # File EOF reached
                break
            elif kind == const.ZNAK:
                # Resend ZRPOS? Or wait?
                pass
            else:
                log.info(f"Unexpected header during file transfer: {kind}")
                pass

        # End of file
        speed = (total_size / (time.time() - start))
        log.info('Receiving file "%s" done at %.02f bps' % (filename, speed))

        # Truncate to exact size specified in ZFILE header to strip any trailing ZMODEM frame padding
        fp.truncate(size)
        fp.close()
        
        # Update file metadata
        mtime = time.mktime(date.timetuple())
        os.utime(filepath, (mtime, mtime))

    def _recv_file_data(self, pos, fp, timeout):
        self._send_pos_header(const.ZRPOS, pos, timeout)
        kind = 0
        dpos = -1
        while dpos != pos:
            while kind != const.ZDATA:
                if kind is const.TIMEOUT:
                    return const.TIMEOUT, 0
                else:
                    header = self._recv_header(timeout)
                    if header is const.TIMEOUT:
                        return const.TIMEOUT, 0
                    kind = header[0]

            # Read until we are at the correct block
            dpos = \
                header[const.ZP0] | \
                header[const.ZP1] << 0x08 | \
                header[const.ZP2] << 0x10 | \
                header[const.ZP3] << 0x18

        # TODO: stream to file handle directly
        kind = const.FRAMEOK
        size = 0
        while kind == const.FRAMEOK:
            kind, chunk = self._recv_data(pos, timeout)
            if kind in [const.ENDOFFRAME, const.FRAMEOK]:
                fp.write(chunk)
                size += len(chunk)

        return kind, size

    def _send(self, char, timeout, esc=True):
        if char == const.ZDLE:
            self._send_esc(char, timeout)
        elif char in [0x10, 0x90, 0x11, 0x91, 0x13, 0x93]:
            self._send_esc(char, timeout)
        elif char in [0x0d, 0x0a]:
            # ALWAYS escape CR and LF to survive PTY translations (ONLCR, ICRNL, IGNCR)
            self._send_esc(char, timeout)
        elif getattr(self, 'escape_all', False) and (char < 0x20 or char == 0x7f):
            self._send_esc(char, timeout)
        elif char in [0x8d] or not esc:
            self.putc(bytes([char]), timeout)
        else:
            self.putc(bytes([char]), timeout)

    def _send_esc(self, char, timeout):
        self.putc(bytes([const.ZDLE]), timeout)
        if char == 0x7f:
            self.putc(bytes([const.ZRUB0]), timeout)
        elif char == 0xff:
            self.putc(bytes([const.ZRUB1]), timeout)
        else:
            self.putc(bytes([char ^ 0x40]), timeout)

    def _send_znak(self, pos, timeout):
        self._send_pos_header(const.ZNAK, pos, timeout)

    def _send_pos_header(self, kind, pos, timeout):
        header = []
        header.append(kind)
        header.append(pos & 0xff)
        header.append((pos >> 0x08) & 0xff)
        header.append((pos >> 0x10) & 0xff)
        header.append((pos >> 0x20) & 0xff)
        self._send_hex_header(header, timeout)

    def _send_hex(self, char, timeout):
        char = char & 0xff
        self._send_hex_nibble(char >> 0x04, timeout)
        self._send_hex_nibble(char >> 0x00, timeout)

    def _send_hex_nibble(self, nibble, timeout):
        nibble &= 0x0f
        self.putc(('%x' % nibble).encode('ascii'), timeout)

    def _send_hex_header(self, header, timeout):
        log.debug(f'Sending hex header: {header}')
        buf = bytearray([const.ZPAD, const.ZPAD, const.ZDLE, const.ZHEX])
        mine = 0

        # Update CRC
        for char in header:
            mine = self.calc_crc16(chr(char), mine)
            buf.extend(('%x' % (char >> 0x04)).encode('ascii'))
            buf.extend(('%x' % (char & 0x0f)).encode('ascii'))

        # Transmit the CRC
        crc1 = mine >> 0x08
        buf.extend(('%x' % (crc1 >> 0x04)).encode('ascii'))
        buf.extend(('%x' % (crc1 & 0x0f)).encode('ascii'))
        
        crc2 = mine & 0xff
        buf.extend(('%x' % (crc2 >> 0x04)).encode('ascii'))
        buf.extend(('%x' % (crc2 & 0x0f)).encode('ascii'))

        buf.extend(b'\r\n')
        buf.extend(const.XON)
            
        self.putc(bytes(buf), timeout)

    def _send_zrinit(self, timeout):
        log.debug('Sending ZRINIT header')
        zf1 = const.ZF1_CANZLIB if self.compress_enabled else 0
        header = [const.ZRINIT, 0, 0, zf1, 4 | const.ZF0_CANFDX |
                  const.ZF0_CANOVIO | const.ZF0_CANFC32 | const.ZF0_ESCCTL]
        self._send_hex_header(header, timeout)
