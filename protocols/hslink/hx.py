#!/usr/bin/env python3
import sys
import argparse
import logging
from transport import StdioTransport
from protocol.hslink import HSLinkSession

def main():
    parser = argparse.ArgumentParser(description="Send files over HS/Link")
    parser.add_argument("files", nargs='*', help="Files to send")
    parser.add_argument("--chat", help="Send an M2M chat message and exit", type=str)
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()
    
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    
    transport = StdioTransport()
    session = HSLinkSession(transport)
    
    if args.files:
        session.add_files(args.files)
        
    if args.chat:
        session.send_chat(args.chat.encode('utf-8'))
        # Give the OS pipe a moment to flush the outbound buffer
        transport.idle(0.1)
        return

    print("HS/Link sender initialized", file=sys.stderr)
    session.loop()
    
if __name__ == '__main__':
    main()
