#!/usr/bin/env python3

"""
sz.py - A skeleton for a ZMODEM file sender.
"""

import sys
import argparse
import os
import termios
import select

# Import our patched modem module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modem.protocol.zmodem import ZMODEM

def getc(size, timeout=1):
    r, _, _ = select.select([sys.stdin.fileno()], [], [], timeout)
    if r:
        return os.read(sys.stdin.fileno(), size)
    return b''

def putc(data, timeout=1):
    if isinstance(data, str):
        data = data.encode('latin1')
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()

def main():
    parser = argparse.ArgumentParser(
        description="Send files with a skeleton ZMODEM protocol (sz)."
    )
    parser.add_argument("files", nargs='*', help="The file(s) to send.")
    parser.add_argument("--request", type=str, help="Request the remote wrapper to upload a file to you.")
    parser.add_argument("--directory", type=str, default=".", help="Directory to save requested files into.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    parser.add_argument("-c", "--compress", action="store_true", help="Force inline ZLIB compression if supported by receiver.")
    args = parser.parse_args()

    import logging
    log_level = logging.DEBUG if args.debug else logging.ERROR
    logging.basicConfig(level=log_level, format='SZ: %(asctime)s [%(levelname)s] %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p', force=True)
    
    if args.debug:
        # In debug mode, log to a file so it doesn't mess up the terminal
        file_handler = logging.FileHandler('/tmp/pyzmodem_sz_debug.log')
        file_handler.setFormatter(logging.Formatter('SZ: %(asctime)s [%(levelname)s] %(message)s'))
        # Clear existing handlers from root logger
        logging.getLogger().handlers = []
        logging.getLogger().addHandler(file_handler)
        print("\r\n[PyZMODEM] Debug logging enabled. See /tmp/pyzmodem_sz_debug.log on remote machine.\r\n", file=sys.stderr)
    
    print("\r\n[PyZMODEM] Tip: Press Ctrl+X 5 times to abort the transfer at any time.\r\n", file=sys.stderr)
    
    if not args.files and not args.request:
        parser.error("You must specify either files to send, or a file to --request")

    # Save tty state
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    
    if args.request:
        # Emit our special request signature to tell the local wrapper to send us a file
        sys.stdout.write(f"rz-request:{args.request}\r\n")
        sys.stdout.flush()
    
    try:
        # Set raw mode
        import tty
        try:
            tty.setraw(fd)
        except termios.error:
            pass
        
        z = ZMODEM(getc, putc, compress=args.compress)
        
        if args.request:
            # We are receiving the file we just requested
            try:
                count = z.recv(args.directory)
            except KeyboardInterrupt:
                print("\r\n[PyZMODEM] Transfer interrupted by user.\r\n", file=sys.stderr)
                try:
                    putc(b'\x18\x18\x18\x18\x18', 1)
                except Exception:
                    pass
                count = 0
            
            if count:
                print(f"\r\nReceived {count} files.\r\n", file=sys.stderr)
            else:
                print("\r\nTransfer failed or no files received.\r\n", file=sys.stderr)
        else:
            # Normal send mode
            try:
                success = z.send(args.files)
            except KeyboardInterrupt:
                print("\r\n[PyZMODEM] Transfer interrupted by user.\r\n", file=sys.stderr)
                try:
                    putc(b'\x18\x18\x18\x18\x18', 1)
                except Exception:
                    pass
                success = False

            if success:
                print("\r\nTransfer complete.\r\n", file=sys.stderr)
            else:
                print("\r\nTransfer failed.\r\n", file=sys.stderr)
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except termios.error:
            pass

if __name__ == "__main__":
    main()
