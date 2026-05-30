#!/usr/bin/env python3

"""
rz.py - A ZMODEM file receiver.
"""

import sys
import argparse
import os
import termios
from pathlib import Path

# Add the parent directory to the Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from rzsz.modem.protocol.zmodem import ZMODEM

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
    
    args = parser.parse_args()
    
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
        
        # Start receiver loop
        # The recv() method in xyzmodem returns the number of files received.
        count = z.recv(args.directory)
        
        if count:
            print(f"\nReceived {count} files.", file=sys.stderr)
        else:
            print("\nTransfer failed or no files received.", file=sys.stderr)
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old_settings)

if __name__ == "__main__":
    main()
