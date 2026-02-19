import os
import pickle
import io
from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# 1. LOAD ENVIRONMENT VARIABLES
# This tells the script to look specifically for 'keys.env' in your folder
load_dotenv("keys.env")

# These will now correctly pull from keys.env instead of returning 'None'
CONTEXT_FOLDER_ID = os.getenv("CONTEXT_FOLDER_ID")
SCOPES = [
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/gmail.compose'
]

def get_gdrive_service():
    """Handles the Google OAuth 2.0 handshake."""
    creds = None
    # token.pickle stores your login session so you don't log in every time
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
            
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Looks for credentials.json in your LH2_Automation folder
            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)

    return build('drive', 'v3', credentials=creds)

def test_folder_connection(service):
    """Verifies the script can actually see your Context folder."""
    if not CONTEXT_FOLDER_ID:
        print("❌ ERROR: CONTEXT_FOLDER_ID not found in keys.env")
        return

    print(f"Checking Folder ID: {CONTEXT_FOLDER_ID}...")
    try:
        results = service.files().list(
            q=f"'{CONTEXT_FOLDER_ID}' in parents", 
            fields="files(id, name)"
        ).execute()
        
        files = results.get('files', [])
        if not files:
            print("No files found in the Context folder.")
        else:
            print("✅ Success! Found these files in your Context folder:")
            for file in files:
                print(f"- {file['name']} ({file['id']})")
                
    except Exception as e:
        print(f"❌ Connection Failed: {e}")

if __name__ == "__main__":
    # Ensure token.pickle is fresh if you just added Gmail scopes
    print("Starting Google Drive Handshake...")
    drive_service = get_gdrive_service()
    print("Google Drive Connection Authenticated!")
    
    # Run the test to find your files
    test_folder_connection(drive_service)