import os
from dotenv import load_dotenv

load_dotenv()

ACCOUNTS = [
    {
        "email": "your_email@example.com",
        "host": "imap.example.com",
        "port": 993,
        "username": os.getenv("EMAIL_USERNAME"),
        "password": os.getenv("EMAIL_PASSWORD"),
    },
    # Add more accounts here
]
