#!/usr/bin/env python3

"""
rz.py - A skeleton for a ZMODEM file receiver.
"""

import sys
import argparse
import os
from pathlib import Path

def receive_files(stream, download_dir: Path):
    """
    Main function to handle the ZMODEM receive process.
    This is a placeholder and does not implement the protocol.
    """
    print("ZMODEM receive utility (skeleton)", file=sys.stderr)
    print(f"Ready to receive files into: {download_dir.resolve()}", file=sys.stderr)
    
    # In a real implementation, this is where the ZMODEM
    # state machine would start, waiting for the sender's
    # initial ZRINIT header.
    
    # This outer loop would handle one full session (including multiple files).
    # After ZFIN is received, this function would return.
    try:
        # Loop to read from the serial stream
        while True:
            # Reading byte by byte is necessary for protocol handling
            char = stream.read(1)
            
            # TODO: Implement ZMODEM state machine here.
            # This would involve:
            # 1. Waiting for ZRINIT to start a session.
            # 2. Receiving ZFILE packets (getting filename).
            #    - Open a file in `download_dir`.
            # 3. Receiving ZDATA packets and writing to the file.
            # 4. Acknowledging packets (ZACK).
            # 5. Receiving ZEOF to close the file.
            # 6. Receiving ZFIN to end the session.
            
            # A ZFIN packet would signal the end of the session,
            # causing this function to `return`.
            if not char:
                print("Stream ended. Exiting current receive session.", file=sys.stderr)
                break # End of stream
            
            pass

    except KeyboardInterrupt:
        print("\nReceive cancelled by user.", file=sys.stderr)
        # We should break here to allow the main loop to decide whether to continue.
        raise
    except Exception as e:
        print(f"\nAn error occurred: {e}", file=sys.stderr)

def main():
    """Parse arguments and start the receiver."""
    parser = argparse.ArgumentParser(
        description="Receive files with a skeleton ZMODEM protocol (rz)."
    )
    parser.add_argument(
        '--stay-active',
        action='store_true',
        help="Do not exit after a transfer session is complete."
    )
    parser.add_argument(
        '--directory',
        type=str,
        default='.',
        help="The directory to save received files into. Defaults to current directory."
    )
    
    args = parser.parse_args()
    
    download_dir = Path(args.directory)
    
    # Create the directory if it doesn't exist
    try:
        download_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"Error: Could not create directory '{download_dir}': {e}", file=sys.stderr)
        sys.exit(1)

    # ZMODEM traditionally uses stdin/stdout for the serial communication channel
    # We need to use the underlying binary buffer.
    binary_stream_in = sys.stdin.buffer

    active = True
    while active:
        try:
            receive_files(binary_stream_in, download_dir)
            
            # If stay_active is False, we run once and exit the loop.
            if not args.stay_active:
                active = False
            else:
                print("\nTransfer complete. Waiting for next session...", file=sys.stderr)

        except KeyboardInterrupt:
            print("\nExiting program.", file=sys.stderr)
            active = False
        except Exception as e:
            print(f"A critical error occurred: {e}", file=sys.stderr)
            active = False

if __name__ == "__main__":
    main()
