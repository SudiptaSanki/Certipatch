import csv
import os
import sys
import time

# Ensure Python can find the 'core' folder
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.mailer import send_certificate
from core.rate_limiter import AccountManager

def main():
    # Setup absolute paths based on script location
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(base_dir, 'config', 'settings.json')
    csv_path = os.path.join(base_dir, 'data', 'Contact_List.csv')
    cert_dir = os.path.join(base_dir, 'data', 'certificates')

    print("=== Certipatch Engine Initializing ===")
    
    # 1. Load Accounts & Templates
    try:
        manager = AccountManager(config_path)
        subject, body_template = manager.get_template()
        print(f"[OK] Loaded {len(manager.accounts)} sending accounts.")
    except Exception as e:
        print(f"[ERROR] Configuration Error: {e}")
        return

    # 2. Check Data
    if not os.path.exists(csv_path):
        print(f"[ERROR] Could not find CSV at {csv_path}")
        return

    success_count = 0
    fail_count = 0

    # 3. Read CSV and Send Emails
    print("Starting dispatch...\n")
    with open(csv_path, mode='r', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        
        # Ensure your CSV has exactly these column headers
        required_cols = ['Name', 'Email', 'Certificate_File']
        if not all(col in reader.fieldnames for col in required_cols):
            print(f"[ERROR] CSV format error. Required columns: {required_cols}")
            return

        for row in reader:
            name = row['Name']
            recipient_email = row['Email']
            cert_filename = row['Certificate_File']
            cert_path = os.path.join(cert_dir, cert_filename)

            # Get the next sender account (Round Robin)
            sender_email, sender_password = manager.get_next_account()

            print(f"Sending to {recipient_email} (via {sender_email})...")
            
            success, msg = send_certificate(
                sender_email=sender_email,
                sender_password=sender_password,
                recipient_email=recipient_email,
                recipient_name=name,
                subject=subject,
                body_text=body_template,
                attachment_path=cert_path
            )

            if success:
                print(f"  [+] Success: {name}")
                success_count += 1
            else:
                print(f"  [-] Failed: {name} - {msg}")
                fail_count += 1
            
            # A 1-second delay prevents Google from flagging the script as a spam bot
            time.sleep(1)

    print("\n=== Dispatch Complete ===")
    print(f"Successfully sent: {success_count} | Failed: {fail_count}")

if __name__ == "__main__":
    main()