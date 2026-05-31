#!/usr/bin/env python3
import sys
import argparse
import logging
from protocol.hslink import HSLink

def main():
    parser = argparse.ArgumentParser(description="Receive files using the HS/Link protocol")
    parser.add_argument("--directory", default=".", help="Directory to save received files")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()
    
    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
        
    print("HS/Link receiver initialized", file=sys.stderr)
    
if __name__ == '__main__':
    main()
