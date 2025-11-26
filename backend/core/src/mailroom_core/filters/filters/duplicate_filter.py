"""Filter for detecting and removing duplicate emails."""

import logging
from collections import defaultdict

import mailparser
from imapclient import IMAPClient

from imap_thingy.accounts import EMailAccount
from imap_thingy.filters.criterion_filter import CriterionFilter, FilterCriterion, ParsedMail, trash

logger = logging.getLogger("imap-thingy")


def _get_duplicate_key(msg: ParsedMail) -> str:
    """Generate a unique key for identifying duplicate emails.

    Args:
        msg: Parsed email message.

    Returns:
        A string that uniquely identifies the email.

    """
    message_id = getattr(msg, "message_id", None)
    if message_id:
        return f"msgid:{message_id}"

    subject = msg.subject or ""
    from_addr = ""
    if msg.from_:
        from_addr = msg.from_[0][1] if len(msg.from_[0]) > 1 else str(msg.from_[0][0])

    date = str(getattr(msg, "date", ""))

    return f"fallback:{subject}|{from_addr}|{date}"


class DuplicateCriterion(FilterCriterion):
    """Criterion that selects duplicate emails (keeping the first one in each group).

    Duplicates are identified by:
    1. Message-ID header (primary method, most reliable)
    2. Subject + From + Date (fallback if Message-ID is missing)

    This criterion returns message IDs for duplicates that should be removed,
    keeping the first email in each duplicate group.
    """

    def __init__(self) -> None:
        """Initialize a duplicate criterion.

        No IMAP query optimization is possible - we need to see all messages
        to identify duplicates.

        """
        super().__init__(lambda msg: False, imap_query=None)

    def filter(self, connection: IMAPClient) -> list[int]:
        """Filter messages to find duplicates.

        Args:
            connection: IMAP connection to use for filtering.

        Returns:
            List of message IDs for duplicate emails (excluding the first one in each group).

        """
        logger.info("Scanning for duplicate emails...")

        logger.info("Fetching mail with IMAP query ['ALL']")
        msg_ids = connection.search(["ALL"])
        fetched = connection.fetch(msg_ids, ["BODY.PEEK[]"])

        messages: list[tuple[int, ParsedMail]] = []
        for msgid, data in fetched.items():
            msg = mailparser.parse_from_bytes(data[b"BODY[]"])
            messages.append((msgid, msg))

        if not messages:
            logger.info("No emails found in folder")
            return []

        duplicate_groups: dict[str, list[tuple[int, ParsedMail]]] = defaultdict(list)
        for msgid, msg in messages:
            key = _get_duplicate_key(msg)
            duplicate_groups[key].append((msgid, msg))

        duplicates_to_remove: list[int] = []

        for key, group in duplicate_groups.items():
            if len(group) > 1:
                group.sort(key=lambda x: x[0])
                keep_msgid = group[0][0]
                duplicate_msgids = [msgid for msgid, _ in group[1:]]

                duplicates_to_remove.extend(duplicate_msgids)

                logger.info(f"Found {len(group)} duplicates (key: {key if len(key) <= 50 else key[:50] + '...'}). Keeping message {keep_msgid}, marking {duplicate_msgids} for removal")

        if duplicates_to_remove:
            logger.info(f"Found {len(duplicates_to_remove)} duplicate emails to remove")
        else:
            logger.info("No duplicate emails found")

        return duplicates_to_remove


def duplicates() -> DuplicateCriterion:
    """Create a criterion that selects duplicate emails.

    Returns a FilterCriterion that identifies duplicate emails based on
    Message-ID (primary) or Subject+From+Date (fallback). For each group
    of duplicates, the first email is kept and the rest are selected.

    Returns:
        A criterion that selects duplicate emails.

    """
    return DuplicateCriterion()


class DuplicateFilter(CriterionFilter):
    """Filter that removes duplicate emails, keeping only one copy.

    Duplicates are identified by:
    1. Message-ID header (primary method, most reliable)
    2. Subject + From + Date (fallback if Message-ID is missing)

    For each group of duplicates, the first email encountered is kept,
    and the rest are moved to trash.
    """

    def __init__(self, account: EMailAccount, base_folder: str = "INBOX") -> None:
        """Initialize a duplicate filter.

        Args:
            account: Email account to filter.
            base_folder: Source folder to search in (default: "INBOX").

        """
        super().__init__(account, duplicates(), trash(), base_folder=base_folder)
