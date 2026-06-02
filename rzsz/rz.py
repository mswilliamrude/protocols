#!/usr/bin/env python3

"""
rz.py - A ZMODEM file receiver.
"""

import sys
import argparse
import os
import termios
from pathlib import Path

# Add the current directory to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modem.protocol.zmodem import ZMODEM

def getc(size, timeout=1):
    import select
    import sys
    import os
    
    # If using sys.stdin.buffer, select() will wait on the FD even if the buffer has data.
    # To fix this, we read directly from the raw file descriptor.
    fd = sys.stdin.fileno()
    
    # Wait for data
    r, _, _ = select.select([fd], [], [], timeout)
    if r:
        b = os.read(fd, size)
        if b:
            return b
    return b''

def putc(data, timeout=1):
    import os, sys
    os.write(sys.stdout.fileno(), data)


def main():
    parser = argparse.ArgumentParser(
        description="Receive files with ZMODEM protocol (rz)."
    )
    parser.add_argument(
        '--directory',
        type=str,
        default='.',
        help="The directory to save received files into. Defaults to current directory."
    )
    
    parser.add_argument(
        '--request',
        type=str,
        help="Request the local wrapper to upload a specific file."
    )
    
    parser.add_argument(
        '--debug',
        action='store_true',
        help="Enable debug logging"
    )
    
    parser.add_argument(
        '-c', '--compress',
        action='store_true',
        help="Advertise inline ZLIB compression support and use it if sender agrees"
    )
    
    parser.add_argument(
        '-e', '--escape',
        action='store_true',
        help="Escape all control characters (safe for sudo/use_pty wrappers)"
    )
    
    parser.add_argument(
        '--zdle',
        type=str,
        help="Override ZDLE byte (hex string, e.g. 1d). Useful if Bastion/SSH strips 0x18."
    )
    
    parser.add_argument(
        'command',
        nargs=argparse.REMAINDER,
        help="Optional command to run in a PTY wrapper (e.g. ssh user@host)"
    )
    
    args = parser.parse_args()
    
    if args.zdle:
        import modem.const
        global ZDLE
        modem.const.ZDLE = int(args.zdle, 16)
        ZDLE = int(args.zdle, 16)
    
    import logging
    log_level = logging.DEBUG if args.debug else logging.ERROR
    logging.basicConfig(level=log_level, format='RZ: %(asctime)s [%(levelname)s] %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p', force=True)
    
    if args.debug:
        # In debug mode, log to a file so it doesn't mess up the terminal
        file_handler = logging.FileHandler('/tmp/pyzmodem_rz_debug.log')
        file_handler.setFormatter(logging.Formatter('RZ: %(asctime)s [%(levelname)s] %(message)s'))
        # Clear existing handlers from root logger
        logging.getLogger().handlers = []
        logging.getLogger().addHandler(file_handler)
        sys.stderr.write("\r\n[PyZMODEM] Debug logging enabled. See /tmp/pyzmodem_rz_debug.log on local machine.\r\n")
    
    if args.command:
        # PTY Wrapper mode
        import pty
        import select
        import re
        import signal
        import fcntl
        import struct
        
        # Remove '--' if it's the first argument in command
        cmd = args.command
        if cmd[0] == '--':
            cmd = cmd[1:]
            
        pid, master_fd = pty.fork()
        if pid == 0:
            # Child
            os.execvp(cmd[0], cmd)
            
        # Parent
        fd = sys.stdin.fileno()
        
        def set_winsize(signum, frame):
            try:
                # Get the window size from the actual terminal (stdin)
                winsize = fcntl.ioctl(fd, termios.TIOCGWINSZ, b'0000')
                # Propagate the window size to the pseudo-terminal (master_fd)
                fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
            except OSError:
                pass

        # Set initial window size
        set_winsize(None, None)
        # Catch window resize events
        signal.signal(signal.SIGWINCH, set_winsize)

        try:
            old_settings = termios.tcgetattr(fd)
        except termios.error:
            old_settings = None
        try:
            import tty
            tty.setraw(fd)
            
            snoop_buffer = bytearray()
            stdout_fd = sys.stdout.fileno()
            
            while True:
                try:
                    r, _, _ = select.select([fd, master_fd], [], [])
                except KeyboardInterrupt:
                    # In environments like MSYS2/Git Bash, Ctrl+C may still raise
                    # KeyboardInterrupt even in raw mode. Forward it to the child.
                    try:
                        os.write(master_fd, b'\x03')
                    except OSError:
                        pass
                    continue
                
                if fd in r:
                    try:
                        data = os.read(fd, 4096)
                    except OSError:
                        break
                    if not data:
                        break
                    os.write(master_fd, data)
                    
                if master_fd in r:
                    try:
                        data = os.read(master_fd, 4096)
                    except OSError:
                        break
                    if not data:
                        break
                        
                    if args.debug:
                        logging.debug(f"PTY READ: {repr(data)}")
                    
                    snoop_buffer.extend(data)
                    if len(snoop_buffer) > 4096:
                        snoop_buffer = snoop_buffer[-4096:]
                        
                    # Check for ZMODEM signature
                    # lsz sends "rz\r**\x18B00"
                    idx = -1
                    is_upload = False
                    upload_filename = ""
                    
                    import modem.const
                    zdle_char = bytes([modem.const.ZDLE])
                    zrqinit_sig = b'**' + zdle_char + b'B00'
                    zrinit_sig  = b'**' + zdle_char + b'B01'
                    
                    req_pattern = b'rz-request:([^\r\n]+)\r*\n.*?\\*\\*' + re.escape(zdle_char) + b'B0[01]'
                    req_match = re.search(req_pattern, snoop_buffer, re.DOTALL)
                    
                    if req_match:
                        is_upload = True
                        upload_filename = req_match.group(1).decode('utf-8')
                        # Find where this started in data
                        req_str = b'rz-request:' + req_match.group(1)
                        if req_str in data:
                            idx = data.find(req_str)
                        else:
                            # It straddled a chunk boundary, just use the end
                            idx_00 = data.find(zrqinit_sig)
                            idx_01 = data.find(zrinit_sig)
                            idx = max(idx_00, idx_01)
                            if idx == -1:
                                idx = 0
                    elif zrqinit_sig in snoop_buffer or zrinit_sig in snoop_buffer:
                        if zrqinit_sig in data:
                            idx = data.find(zrqinit_sig)
                            # Truncate snoop buffer up to idx
                            snoop_buffer = snoop_buffer[:snoop_buffer.find(zrqinit_sig) + len(zrqinit_sig)]
                            if args.debug:
                                logging.debug(f"Found **\\x{modem.const.ZDLE:02x}B00 at index {idx}")
                        elif zrinit_sig in data:
                            idx = data.find(zrinit_sig)
                            # Truncate snoop buffer up to idx
                            snoop_buffer = snoop_buffer[:snoop_buffer.find(zrinit_sig) + len(zrinit_sig)]
                            if args.debug:
                                logging.debug(f"Found **\\x{modem.const.ZDLE:02x}B01 at index {idx}")
                        else:
                            idx = 0
                            
                    if idx != -1:
                        # We found it! Now we need to start intercepting.
                        
                        # First, if the signature was split across a chunk boundary, 
                        # ensure we pass the correct initial buffer to the protocol.
                        sig_str = zrinit_sig if (is_upload or zrinit_sig in snoop_buffer) else zrqinit_sig
                        
                        if not is_upload:
                            sig_idx = snoop_buffer.find(zrqinit_sig)
                            if sig_idx != -1:
                                zmodem_buffer = snoop_buffer[sig_idx:]
                            else:
                                zmodem_buffer = data[idx:]
                        else:
                            # Skip the request string and get to the **\x1dB01
                            zmodem_buffer = snoop_buffer[snoop_buffer.find(zrinit_sig):]
                            
                        def wrapper_getc(size, timeout=1):
                            nonlocal zmodem_buffer
                            if zmodem_buffer:
                                chunk = zmodem_buffer[:size]
                                zmodem_buffer = zmodem_buffer[size:]
                                return chunk
                            rr, _, _ = select.select([master_fd], [], [], timeout)
                            if rr:
                                try:
                                    return os.read(master_fd, size)
                                except OSError:
                                    return b''
                            return b''
                            
                        def wrapper_putc(d, timeout=1):
                            try:
                                os.write(master_fd, d)
                            except OSError:
                                pass
                                
                        if is_upload:
                            # If it's a relative path and not in the current dir, check the --directory fallback
                            upload_path = upload_filename
                            if not os.path.isabs(upload_path) and not os.path.exists(upload_path):
                                potential_path = os.path.join(args.directory, upload_filename)
                                if os.path.exists(potential_path):
                                    upload_path = potential_path
                                    
                            sys.stderr.write(f"\r\n\033[K[PyZMODEM] Local wrapper requested to upload {upload_path}...\r\n")
                            sys.stderr.write("[PyZMODEM] Tip: Press Ctrl+C to abort the transfer.\r\n")
                            
                            def upload_progress(filename, total_size, uploaded):
                                if total_size > 0:
                                    pct = int((uploaded / total_size) * 100)
                                    sys.stderr.write(f"\r[PyZMODEM] Sending {filename}: {pct}% ({uploaded}/{total_size} bytes)\033[K")
                                else:
                                    sys.stderr.write(f"\r[PyZMODEM] Sending {filename}: {uploaded} bytes\033[K")
                                sys.stderr.flush()

                            # Temporarily restore normal terminal mode so Ctrl+C works to interrupt
                            if old_settings is not None:
                                try:
                                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                                except termios.error:
                                    pass
                            z = ZMODEM(wrapper_getc, wrapper_putc, progress_callback=upload_progress, compress=args.compress, escape_all=args.escape)
                            try:
                                success = z.send([upload_path], overwrite=True)
                            except KeyboardInterrupt:
                                sys.stderr.write(f"\r\n[PyZMODEM] Transfer interrupted by user.\r\n")
                                # Send 5 CAN bytes to tell remote to abort
                                try:
                                    wrapper_putc(bytes([ZDLE]) * 5, 1)
                                except Exception:
                                    pass
                                success = False
                            except Exception as e:
                                sys.stderr.write(f"\r\n[PyZMODEM] Transfer failed with error: {e}\r\n")
                                try:
                                    wrapper_putc(bytes([ZDLE]) * 5, 1)
                                except Exception:
                                    pass
                                success = False
                            finally:
                                # Put back into raw mode
                                try:
                                    tty.setraw(fd)
                                except termios.error:
                                    pass
                                
                            if success:
                                sys.stderr.write(f"\r\n[PyZMODEM] Successfully uploaded {upload_path}.\r\n")
                                flush_needed = False
                            else:
                                sys.stderr.write(f"\r\n[PyZMODEM] Upload failed.\r\n")
                                flush_needed = True
                                
                            # If we were interrupted on the remote side, flush garbage out of the pipe
                            if flush_needed:
                                while True:
                                    try:
                                        rr, _, _ = select.select([master_fd], [], [], 0.1)
                                    except KeyboardInterrupt:
                                        continue
                                    if not rr:
                                        break
                                    try:
                                        os.read(master_fd, 4096)
                                    except OSError:
                                        break
                        else:
                            def progress(filename, total_size, downloaded):
                                if total_size > 0:
                                    pct = int((downloaded / total_size) * 100)
                                    sys.stderr.write(f"\r[PyZMODEM] Receiving {filename}: {pct}% ({downloaded}/{total_size} bytes)\033[K")
                                else:
                                    sys.stderr.write(f"\r[PyZMODEM] Receiving {filename}: {downloaded} bytes\033[K")
                                sys.stderr.flush()
    
                            # Temporarily restore normal terminal mode so Ctrl+C works to interrupt
                            if old_settings is not None:
                                try:
                                    termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                                except termios.error:
                                    pass
                            sys.stderr.write("[PyZMODEM] Tip: Press Ctrl+C to abort the transfer.\r\n")
                            z = ZMODEM(wrapper_getc, wrapper_putc, progress_callback=progress, compress=args.compress, escape_all=args.escape)
                            try:
                                count = z.recv(args.directory)
                            except KeyboardInterrupt:
                                sys.stderr.write(f"\r\n[PyZMODEM] Transfer interrupted by user.\r\n")
                                # Send 5 CAN bytes to tell remote to abort
                                try:
                                    wrapper_putc(bytes([ZDLE]) * 5, 1)
                                except Exception:
                                    pass
                                count = 0
                            except Exception as e:
                                sys.stderr.write(f"\r\n[PyZMODEM] Transfer failed with error: {e}\r\n")
                                try:
                                    wrapper_putc(bytes([ZDLE]) * 5, 1)
                                except Exception:
                                    pass
                                count = 0
                            finally:
                                # Put back into raw mode
                                try:
                                    tty.setraw(fd)
                                except termios.error:
                                    pass
                            
                            if count:
                                sys.stderr.write(f"\r\n[PyZMODEM] Successfully received {count} file(s).\r\n")
                                flush_needed = False
                            else:
                                sys.stderr.write("\r\n[PyZMODEM] Transfer failed.\r\n")
                                flush_needed = True
                                
                            # If we were interrupted or failed, flush any garbage out of the pipe
                            if flush_needed:
                                while True:
                                    try:
                                        rr, _, _ = select.select([master_fd], [], [], 0.1)
                                    except KeyboardInterrupt:
                                        continue
                                    if not rr:
                                        break
                                    try:
                                        os.read(master_fd, 4096)
                                    except OSError:
                                        break
                        
                        # Transfer finished, resume pass-through
                        snoop_buffer.clear()
                    else:
                        os.write(stdout_fd, data)
                        
        finally:
            try:
                if old_settings:
                    termios.tcsetattr(fd, termios.TCSANOW, old_settings)
            except termios.error:
                pass
            
    else:
        # Standalone mode
        fd = sys.stdin.fileno()
        try:
            old_settings = termios.tcgetattr(fd)
        except termios.error:
            old_settings = None
        
        if args.request:
            # Emit our special request signature before starting ZMODEM receiver
            sys.stdout.write(f"rz-request:{args.request}\r\n")
            sys.stdout.flush()
            
        print("\r\n[PyZMODEM] Tip: Press Ctrl+X 5 times to abort the transfer at any time.\r\n", file=sys.stderr)
            
        try:
            # Set raw mode
            import tty
            try:
                tty.setraw(fd)
            except termios.error:
                pass
            
            z = ZMODEM(getc, putc, compress=args.compress, escape_all=args.escape)
            
            # Start receiver loop
            # The recv() method in xyzmodem returns the number of files received.
            try:
                count = z.recv(args.directory)
            except KeyboardInterrupt:
                print("\r\n[PyZMODEM] Transfer interrupted by user.\r\n", file=sys.stderr)
                try:
                    putc(bytes([ZDLE]) * 5, 1)
                except Exception:
                    pass
                count = 0
            except Exception as e:
                print(f"\r\n[PyZMODEM] Transfer failed with error: {e}\r\n", file=sys.stderr)
                try:
                    putc(bytes([ZDLE]) * 5, 1)
                except Exception:
                    pass
                count = 0
            
            if count:
                sys.stderr.write(f"\r\nReceived {count} files.\r\n")
            else:
                sys.stderr.write("\r\nTransfer failed or no files received.\r\n")
        finally:
            try:
                if old_settings:
                    termios.tcsetattr(fd, termios.TCSANOW, old_settings)
            except termios.error:
                pass

if __name__ == "__main__":
    main()
