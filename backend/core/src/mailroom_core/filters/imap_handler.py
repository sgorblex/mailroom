import imapclient
import pyzmail

def process_mailbox(account):
    with imapclient.IMAPClient(account["host"], port=account["port"], ssl=True) as client:
        client.login(account["username"], account["password"])
        client.select_folder('INBOX', readonly=False)

        messages = client.search(['UNSEEN', 'FROM', 'alerts@example.com'])

        for uid in messages:
            raw_message = client.fetch([uid], ['BODY[]', 'FLAGS'])[uid][b'BODY[]']
            message = pyzmail.PyzMessage.factory(raw_message)
            subject = message.get_subject()
            print(f"[{account['email']}] Processing: {subject}")

            client.add_flags(uid, [b'\\Seen'])
            client.move([uid], 'Processed')

        client.logout()
