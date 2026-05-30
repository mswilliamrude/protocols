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
    parser.add_argument("files", nargs='+', help="The file(s) to send.")
    args = parser.parse_args()

    import logging
    logging.basicConfig(level=logging.DEBUG, format='SZ: %(asctime)s [%(levelname)s] %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p')
    
    # Save tty state
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    
    try:
        # Set raw mode
        tty = termios.tcgetattr(fd)
        tty[3] = tty[3] & ~termios.ICANON & ~termios.ECHO & ~termios.ISIG
        tty[0] = tty[0] & ~termios.ICRNL & ~termios.INLCR
        tty[1] = tty[1] & ~termios.OPOST
        termios.tcsetattr(fd, termios.TCSANOW, tty)
        
        z = ZMODEM(getc, putc)
        success = z.send(args.files)
        if success:
            print("\nTransfer complete.", file=sys.stderr)
        else:
            print("\nTransfer failed.", file=sys.stderr)
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old_settings)

if __name__ == "__main__":
    main()
