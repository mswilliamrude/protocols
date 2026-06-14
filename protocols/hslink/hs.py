#!/usr/bin/env python3
import sys
import argparse
import logging
from transport import StdioTransport
from protocol.hslink import HSLinkSession

def main():
    parser = argparse.ArgumentParser(description="HS/Link Bidirectional Transfer (True Client)")
    parser.add_argument("--send", nargs='+', help="Files to send", default=[])
    parser.add_argument("--recv-dir", default=".", help="Directory to save received files")
    parser.add_argument("--chat", help="Send an M2M chat message", type=str)
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()
    
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    
    transport = StdioTransport()
    session = HSLinkSession(transport)
    session.recv_dir = args.recv_dir
    
    if args.send:
        session.add_files(args.send)
        
    if args.chat:
        session.send_chat(args.chat.encode('utf-8'))
        
    if not args.send and not args.chat:
        print("HS/Link standing by to receive...", file=sys.stderr)
    else:
        print("HS/Link bidirectional transfer initiated", file=sys.stderr)
        
    session.loop()
    
if __name__ == '__main__':
    main()
