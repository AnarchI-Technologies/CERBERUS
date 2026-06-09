import os
from dotenv import load_dotenv
from dotenv import set_key
from agentmail import AgentMail

# Load settings from .env or the current terminal environment.
load_dotenv()
api_key = os.getenv("AGENTMAIL_API_KEY")
test_to = os.getenv("AGENTMAIL_TEST_TO", "")

if not api_key:
    raise RuntimeError("Set AGENTMAIL_API_KEY in .env or in your terminal environment.")

# Initialize the client.
client = AgentMail(api_key=api_key)

# Create/reuse Hellion's inbox. client_id makes retries idempotent.
print("Creating inbox...")
inbox = client.inboxes.create(
    request={
        "username": "hellion-meet-your-molty-maker",
        "domain": "agentmail.to",
        "display_name": "Hellion-Meet-Your-Molty-Maker",
        "client_id": "cerberus-hellion-primary-inbox-v1",
        "metadata": {"agent": "Hellion-Meet-Your-Molty-Maker", "system": "cerberus"},
    }
)
print("Inbox created successfully!")
print(inbox)
set_key(".env", "AGENTMAIL_INBOX_ID", inbox.inbox_id)
if "@" in inbox.inbox_id:
    set_key(".env", "AGENTMAIL_EMAIL", inbox.inbox_id)
print("Saved AGENTMAIL_INBOX_ID to .env.")

# Send an email only when you provide a real recipient.
if test_to:
    client.inboxes.messages.send(
        inbox.inbox_id,
        to=test_to,
        subject="Hello from AgentMail!",
        text="This is my first email sent with the AgentMail API.",
    )
    print(f"Test email sent to {test_to}.")
else:
    print("Skipping send. Set AGENTMAIL_TEST_TO to send a test email.")
