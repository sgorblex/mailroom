import argparse
import logging

from imap_thingy import logout_all
from .imap_handler import process_filters

def main():
    parser = argparse.ArgumentParser(description="imap-thingy: automate email filtering via IMAP")
    parser.add_argument("--dry-run", action="store_true", help="Print actions instead of executing them")
    parser.add_argument("--log", type=str, default="INFO", help="Logging level (DEBUG, INFO, WARNING, ERROR)")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log.upper(), None), format='%(asctime)s [%(levelname)s] %(message)s')

    process_filters(dry_run=args.dry_run)
    logout_all()
