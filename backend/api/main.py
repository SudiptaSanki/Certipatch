from fastapi import FastAPI, Depends, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from database import SessionLocal, init_db, CertificateJob
import csv
import os
import sys
import time
from datetime import datetime, timedelta

# Ensure Python can find the 'core' folder from inside the 'api' folder
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(base_dir)

from core.mailer import send_certificate
from core.rate_limiter import AccountManager

# Initialize the API
app = FastAPI(title="Certipatch API", version="1.0")

_api_dir = os.path.dirname(os.path.abspath(__file__))
_favicon_path = os.path.join(_api_dir, "favicon.png")


@app.get("/favicon.png", include_in_schema=False)
def favicon_png():
    if not os.path.isfile(_favicon_path):
        raise HTTPException(status_code=404, detail="favicon not found")
    return FileResponse(_favicon_path, media_type="image/png")


@app.get("/favicon.ico", include_in_schema=False)
def favicon_ico():
    """Browsers often request /favicon.ico by default; same asset as favicon.png."""
    if not os.path.isfile(_favicon_path):
        raise HTTPException(status_code=404, detail="favicon not found")
    return FileResponse(_favicon_path, media_type="image/png")


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
            job.sent_at = datetime.utcnow()
        else:
            job.status = "Failed"
            job.error_message = msg
            
        job.account_used = sender_email
        db.commit()
        
        # 1-second delay to protect against rate limits
        time.sleep(1) 
        
    db.close()


# --- API ENDPOINTS ---

@app.get("/", response_class=HTMLResponse)
def read_root():
    with open(os.path.join(os.path.dirname(__file__), "dashboard.html"), "r", encoding="utf-8") as f:
        return f.read()

@app.get("/status")
def get_status():
    return {"status": "Certipatch Backend is Online", "is_sending": is_sending}

def _window_start_utc(hours: int) -> datetime:
    return datetime.utcnow() - timedelta(hours=hours)


@app.get("/jobs/stats")
def get_job_stats(db: Session = Depends(get_db)):
    """Returns the current real-time stats of the queue."""
    sent_all = db.query(CertificateJob).filter(CertificateJob.status == "Sent").count()
    config_path = os.path.join(base_dir, "config", "settings.json")
    window_hours = 24
    sent_in_window = 0
    try:
        manager = AccountManager(config_path)
        window_hours = manager.get_rolling_window_hours()
        since = _window_start_utc(window_hours)
        sent_in_window = (
            db.query(CertificateJob)
            .filter(
                CertificateJob.status == "Sent",
                func.coalesce(CertificateJob.sent_at, CertificateJob.timestamp) >= since,
            )
            .count()
        )
    except Exception:
        pass
    return {
        "total": db.query(CertificateJob).count(),
        "pending": db.query(CertificateJob).filter(CertificateJob.status == "Pending").count(),
        "sent": sent_all,
        "failed": db.query(CertificateJob).filter(CertificateJob.status == "Failed").count(),
        "sent_in_window": sent_in_window,
        "window_hours": window_hours,
    }

@app.get("/jobs/list")
def get_jobs_list(db: Session = Depends(get_db)):
    """Returns the 50 most recent jobs for the dashboard."""
    jobs = db.query(CertificateJob).order_by(CertificateJob.id.desc()).limit(50).all()
    return [{"id": j.id, "name": j.name, "email": j.email, "certificate_file": j.certificate_file, "status": j.status, "account_used": j.account_used} for j in jobs]

@app.get("/jobs/quota")
def get_quota(db: Session = Depends(get_db)):
    """Remaining send capacity per rolling window (per Google-style daily SMTP caps)."""
    config_path = os.path.join(base_dir, "config", "settings.json")
    try:
        manager = AccountManager(config_path)
        pairs = list(manager.iter_accounts_with_limits())
        window_hours = manager.get_rolling_window_hours()
    except Exception as e:
        return {
            "accounts": 0,
            "total": 0,
            "remaining": 0,
            "used_in_window": 0,
            "window_hours": 24,
            "per_account": [],
            "note": str(e),
        }

    since = _window_start_utc(window_hours)
    total_cap = sum(lim for _, lim in pairs)
    per_out = []
    remaining_sum = 0
    used_total = 0

    for email, lim in pairs:
        used = (
            db.query(CertificateJob)
            .filter(
                CertificateJob.status == "Sent",
                CertificateJob.account_used == email,
                func.coalesce(CertificateJob.sent_at, CertificateJob.timestamp) >= since,
            )
            .count()
        )
        used_total += used
        rem = max(0, lim - used)
        remaining_sum += rem
        per_out.append(
            {"email": email, "limit": lim, "used_in_window": used, "remaining": rem}
        )

    return {
        "accounts": len(pairs),
        "total": total_cap,
        "remaining": remaining_sum,
        "used_in_window": used_total,
        "window_hours": window_hours,
        "per_account": per_out,
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


@app.post("/jobs/clear-completed")
def clear_completed(db: Session = Depends(get_db)):
    """Remove Sent and Failed rows so a new campaign can start clean; Pending jobs are kept."""
    q = db.query(CertificateJob).filter(
        CertificateJob.status.in_(("Sent", "Failed"))
    )
    deleted = q.delete(synchronize_session=False)
    db.commit()
    return {"message": f"Removed {deleted} completed job(s).", "deleted": deleted}


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