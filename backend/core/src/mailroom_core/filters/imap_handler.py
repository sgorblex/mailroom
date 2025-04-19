import logging
from custom_filters import FILTERS

logger = logging.getLogger("imap-thingy")

def process_filters(dry_run=False):
    for filter in FILTERS:
        filter.apply(dry_run=dry_run)
