import os
import base64
import pickle
from dotenv import load_dotenv
from email.message import EmailMessage
from googleapiclient.discovery import build

# Load from your specific file
load_dotenv("keys.env")

def get_gmail_service():
    if not os.path.exists('token.pickle'):
        print("❌ ERROR: token.pickle not found. Run gdrive_setup.py first!")
        return None
    
    with open('token.pickle', 'rb') as token:
        creds = pickle.load(token)
    
    # Connect to the Gmail API
    return build('gmail', 'v1', credentials=creds)

def create_test_draft():
    service = get_gmail_service()
    if not service: return

    # 1. Create the Email content
    message = EmailMessage()
    message.set_content("This is a test draft for LH2 Holdings outreach. If you see this, the Gmail API is working!")
    message['To'] = "test@example.com" # You can put your own email here
    message['Subject'] = "LH2 Automation Test Draft"

    # 2. Encode the message for the Google API
    encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    create_message = {'message': {'raw': encoded_message}}

    try:
        # 3. Send the draft request to Google
        draft = service.users().drafts().create(userId="me", body=create_message).execute()
        print(f"✅ Success! Draft created. ID: {draft['id']}")
        print("👉 Go check the 'Drafts' folder in your Gmail inbox.")
    except Exception as e:
        print(f"❌ Gmail Connection Failed: {e}")
        if "insufficient permissions" in str(e).lower():
            print("Tip: You likely forgot to check the 'Gmail Compose' box during the Google login.")

if __name__ == "__main__":
    create_test_draft()