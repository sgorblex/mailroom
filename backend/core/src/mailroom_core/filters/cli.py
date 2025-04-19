import argparse
from .config import ACCOUNTS
from .imap_handler import process_mailbox

def main():
    parser = argparse.ArgumentParser(description="Email automation tool.")
    parser.add_argument(
        "--account",
        type=str,
        help="Process only one specific account by email",
    )

    args = parser.parse_args()

    if args.account:
        acc = next((a for a in ACCOUNTS if a["email"] == args.account), None)
        if not acc:
            print("Account not found.")
            return
        process_mailbox(acc)
    else:
        for acc in ACCOUNTS:
            process_mailbox(acc)
