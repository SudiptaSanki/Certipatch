from fastapi import FastAPI, Depends, BackgroundTasks
from sqlalchemy.orm import Session
from database import SessionLocal, init_db, CertificateJob
import csv
import os
import sys
import time

# Ensure Python can find the 'core' folder from inside the 'api' folder
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(base_dir)

from core.mailer import send_certificate
from core.rate_limiter import AccountManager

# Initialize the API
app = FastAPI(title="Certipatch API", version="1.0")

# Global variable to control the Play/Pause state
is_sending = False

@app.on_event("startup")
def on_startup():
    init_db()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- BACKGROUND ENGINE ---
def process_email_queue():
    """This runs in the background. It reads the DB and sends emails until paused or finished."""
    global is_sending
    
    config_path = os.path.join(base_dir, 'config', 'settings.json')
    cert_dir = os.path.join(base_dir, 'data', 'certificates')
    
    # Connect to the database inside the thread
    db = SessionLocal()
    
    try:
        manager = AccountManager(config_path)
        subject, body_template = manager.get_template()
    except Exception as e:
        print(f"[ERROR] Config failed: {e}")
        is_sending = False
        db.close()
        return

    while is_sending:
        # Find the next pending job
        job = db.query(CertificateJob).filter(CertificateJob.status == "Pending").first()
        
        if not job:
            print("[INFO] Queue is empty or all jobs processed!")
            is_sending = False
            break
            
        cert_path = os.path.join(cert_dir, job.certificate_file)
        sender_email, sender_password = manager.get_next_account()
        
        # Send the email using your Phase 1 engine
        success, msg = send_certificate(
            sender_email=sender_email,
            sender_password=sender_password,
            recipient_email=job.email,
            recipient_name=job.name,
            subject=subject,
            body_text=body_template,
            attachment_path=cert_path
        )
        
        # Update the database securely
        if success:
            job.status = "Sent"
        else:
            job.status = "Failed"
            job.error_message = msg
            
        job.account_used = sender_email
        db.commit()
        
        # 1-second delay to protect against rate limits
        time.sleep(1) 
        
    db.close()


# --- API ENDPOINTS ---

@app.get("/")
def read_root():
    return {"status": "Certipatch Backend is Online", "is_sending": is_sending}

@app.get("/jobs/stats")
def get_job_stats(db: Session = Depends(get_db)):
    """Returns the current real-time stats of the queue."""
    return {
        "total": db.query(CertificateJob).count(),
        "pending": db.query(CertificateJob).filter(CertificateJob.status == "Pending").count(),
        "sent": db.query(CertificateJob).filter(CertificateJob.status == "Sent").count(),
        "failed": db.query(CertificateJob).filter(CertificateJob.status == "Failed").count()
    }

@app.post("/jobs/load-csv")
def load_csv(db: Session = Depends(get_db)):
    """Reads Contact_List.csv and adds new users to the database Queue."""
    csv_path = os.path.join(base_dir, 'data', 'Contact_List.csv')
    if not os.path.exists(csv_path):
        return {"error": "Contact_List.csv not found in the data folder."}
        
    added = 0
    with open(csv_path, mode='r', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        for row in reader:
            # Check if email already exists in DB to prevent double-adding
            exists = db.query(CertificateJob).filter(CertificateJob.email == row['Email']).first()
            if not exists:
                new_job = CertificateJob(
                    name=row['Name'],
                    email=row['Email'],
                    certificate_file=row['Certificate_File']
                )
                db.add(new_job)
                added += 1
    db.commit()
    return {"message": f"Successfully added {added} new records to the database."}

@app.post("/engine/start")
def start_engine(background_tasks: BackgroundTasks):
    """Hits PLAY on the email engine."""
    global is_sending
    if is_sending:
        return {"message": "Engine is already running."}
    
    is_sending = True
    background_tasks.add_task(process_email_queue)
    return {"message": "Engine started! Emails are now dispatching."}

@app.post("/engine/pause")
def pause_engine():
    """Hits PAUSE on the email engine."""
    global is_sending
    is_sending = False
    return {"message": "Engine paused. The current email will finish, then it will stop."}