#!/usr/bin/env python3
import sys
import argparse
import logging
from transport import StdioTransport
from protocol.hslink import HSLinkSession

def main():
    parser = argparse.ArgumentParser(description="Receive files over HS/Link")
    parser.add_argument("--directory", default=".", help="Directory to save received files")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()
    
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
        
    transport = StdioTransport()
    session = HSLinkSession(transport)
    session.recv_dir = args.directory
    
    print("HS/Link receiver initialized", file=sys.stderr)
    session.loop()
    
if __name__ == '__main__':
    main()
