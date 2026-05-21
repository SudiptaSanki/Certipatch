import csv
import io
import os
import re
import shutil
import sys
import threading
import time
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.dirname(BASE_DIR)
ROOT_DIR = os.path.dirname(BACKEND_DIR)
FRONTEND_DIR = os.path.join(ROOT_DIR, "frontend")
CERT_DIR = os.path.join(BACKEND_DIR, "data", "certificates")
TEST_CERT_DIR = os.path.join(BACKEND_DIR, "data", "test_data", "certificates")

os.makedirs(CERT_DIR, exist_ok=True)
os.makedirs(TEST_CERT_DIR, exist_ok=True)
os.makedirs(FRONTEND_DIR, exist_ok=True)

sys.path.insert(0, BACKEND_DIR)
from core.mailer import send_certificate
from database import CertificateJob, DEFAULT_DAILY_LIMIT, EmailAccount, EmailSettings, SessionLocal, init_db

_favicon_path = os.path.join(ROOT_DIR, "CertiPatch_logo.png")


app = FastAPI(title="Certipatch v3", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

is_sending = False
send_lock = threading.Lock()


class AccountIn(BaseModel):
    email: str
    password: str
    daily_limit: Optional[int] = None


class SettingsIn(BaseModel):
    subject: str
    body_template: str


class TestSendIn(BaseModel):
    to_email: str
    account_id: int


# --- FAVICON ---
@app.get("/favicon.png", include_in_schema=False)
def favicon_png():
    if not os.path.isfile(_favicon_path):
        raise HTTPException(status_code=404, detail="favicon not found")
    return FileResponse(_favicon_path, media_type="image/png")


@app.get("/favicon.ico", include_in_schema=False)
def favicon_ico():
    if not os.path.isfile(_favicon_path):
        raise HTTPException(status_code=404, detail="favicon not found")
    return FileResponse(_favicon_path, media_type="image/png")


@app.on_event("startup")
def on_startup():
    init_db()
    _copy_logo()
    _generate_test_data()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _copy_logo():
    src = os.path.join(ROOT_DIR, "CertiPatch_logo.png")
    dst = os.path.join(FRONTEND_DIR, "certipatch_logo.png")
    if os.path.exists(src):
        shutil.copy(src, dst)


def _generate_test_data():
    pdf_path = os.path.join(TEST_CERT_DIR, "test_certificate.pdf")
    os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
    if not os.path.exists(pdf_path):
        _write_test_pdf(pdf_path, "Test User")


def _write_test_pdf(path: str, name: str):
    stream = (
        "BT /F1 22 Tf 40 800 Td (CertiPatch v3 - Test Certificate) Tj "
        "0 -60 Td /F1 16 Tf (This certifies that) Tj "
        f"0 -40 Td /F1 20 Tf ({name}) Tj "
        "0 -40 Td /F1 13 Tf (has completed the Certipatch test run.) Tj "
        "0 -30 Td (This is a dummy certificate generated for testing purposes.) Tj ET"
    )
    encoded_stream = stream.encode("latin-1")
    objects = [
        "1 0 obj\n<</Type /Catalog /Pages 2 0 R>>\nendobj\n",
        "2 0 obj\n<</Type /Pages /Kids [3 0 R] /Count 1>>\nendobj\n",
        "3 0 obj\n<</Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources <</Font <</F1 5 0 R>>>>>>\nendobj\n",
        f"4 0 obj\n<</Length {len(encoded_stream)}>>\nstream\n{stream}\nendstream\nendobj\n",
        "5 0 obj\n<</Type /Font /Subtype /Type1 /BaseFont /Helvetica>>\nendobj\n",
    ]
    header = b"%PDF-1.4\n"
    body = b""
    offsets = []
    for obj in objects:
        offsets.append(len(header) + len(body))
        body += obj.encode("latin-1")

    xref_position = len(header) + len(body)
    xref = "xref\n0 6\n0000000000 65535 f \n" + "".join(f"{x:010d} 00000 n \n" for x in offsets)
    trailer = f"trailer\n<</Size 6 /Root 1 0 R>>\nstartxref\n{xref_position}\n%%EOF\n"
    with open(path, "wb") as f:
        f.write(header + body + xref.encode("latin-1") + trailer.encode("latin-1"))


def _clear_certificate_files():
    if not os.path.exists(CERT_DIR):
        return 0

    removed = 0
    for filename in os.listdir(CERT_DIR):
        path = os.path.join(CERT_DIR, filename)
        if os.path.isfile(path) and filename.lower().endswith(".pdf"):
            os.remove(path)
            removed += 1
    return removed


def _safe_filename(filename: str) -> str:
    return os.path.basename((filename or "").replace("\\", "/")).strip()


def _file_key(filename: str) -> str:
    base = os.path.splitext(_safe_filename(filename))[0]
    return re.sub(r"[^a-z0-9]+", "", base.lower())


def _pdf_inventory() -> tuple[dict[str, str], list[str]]:
    files = []
    if os.path.exists(CERT_DIR):
        files = sorted(f for f in os.listdir(CERT_DIR) if f.lower().endswith(".pdf"))

    lookup = {}
    for filename in files:
        lookup[filename.lower()] = filename
        lookup[_file_key(filename)] = filename
    return lookup, files


def _parse_rows(content: bytes, filename: str) -> tuple[list[str], list[dict]]:
    lower_name = filename.lower()
    if lower_name.endswith(".csv"):
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        return reader.fieldnames or [], list(reader)

    if lower_name.endswith((".xlsx", ".xls")):
        import openpyxl

        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
        sheet = workbook.active
        raw_rows = list(sheet.iter_rows(values_only=True))
        if not raw_rows:
            return [], []

        headers = [str(cell).strip() if cell is not None else "" for cell in raw_rows[0]]
        rows = []
        for row in raw_rows[1:]:
            rows.append({
                headers[i]: (str(value).strip() if value is not None else "")
                for i, value in enumerate(row)
                if i < len(headers)
            })
        return headers, rows

    raise HTTPException(400, "Unsupported file type. Use .csv or .xlsx")


def _detect_columns(headers: list[str]) -> dict:
    result = {"name_col": None, "email_col": None, "file_col": None}
    for header in headers:
        normalized = header.lower().strip()
        if any(key in normalized for key in ["email", "e-mail", "mail"]) and not result["email_col"]:
            result["email_col"] = header
        elif any(key in normalized for key in ["name", "student", "recipient", "full"]) and not result["name_col"]:
            result["name_col"] = header
        elif any(key in normalized for key in ["file", "cert", "pdf", "attach", "document"]) and not result["file_col"]:
            result["file_col"] = header
    return result


def _cell(row: dict, column: str) -> str:
    if not column:
        return ""
    value = row.get(column, "")
    return "" if value is None else str(value).strip()


def _match_pdf(raw_file: str, name: str, lookup: dict[str, str]) -> tuple[Optional[str], str]:
    candidates = []
    raw_file = (raw_file or "").strip()
    name = (name or "").strip()

    if raw_file:
        base = _safe_filename(raw_file)
        candidates.extend([base, base if base.lower().endswith(".pdf") else f"{base}.pdf"])
    if name:
        candidates.extend([name, f"{name}.pdf"])

    for candidate in candidates:
        exact = lookup.get(_safe_filename(candidate).lower())
        if exact:
            return exact, candidate

        normalized = lookup.get(_file_key(candidate))
        if normalized:
            return normalized, candidate

    return None, raw_file or name


def _validated_jobs(rows: list[dict], name_col: str, email_col: str, file_col: str) -> tuple[list[dict], dict]:
    lookup, available_pdfs = _pdf_inventory()
    prepared = []
    missing_email = []
    missing_pdf = []
    used_pdfs = set()

    for index, row in enumerate(rows, start=2):
        name = _cell(row, name_col)
        email = _cell(row, email_col)
        raw_file = _cell(row, file_col)

        if not email:
            missing_email.append({"row": index, "name": name, "expected": raw_file or name})
            continue

        matched_file, expected = _match_pdf(raw_file, name, lookup)
        if not matched_file:
            missing_pdf.append({"row": index, "name": name, "email": email, "expected": expected})
            continue

        prepared.append({"name": name, "email": email, "certificate_file": matched_file})
        used_pdfs.add(matched_file)

    return prepared, {
        "missing_email": missing_email,
        "missing_pdf": missing_pdf,
        "unused_pdfs": [f for f in available_pdfs if f not in used_pdfs],
        "available_pdfs": available_pdfs,
    }


@app.get("/api/accounts")
def list_accounts(db: Session = Depends(get_db)):
    accounts = db.query(EmailAccount).order_by(EmailAccount.id.asc()).all()
    return [{
        "id": account.id,
        "email": account.email,
        "daily_limit": account.daily_limit or DEFAULT_DAILY_LIMIT,
        "daily_sent": account.daily_sent,
        "last_reset_date": account.last_reset_date,
        "at_limit": account.is_at_limit,
    } for account in accounts]


@app.post("/api/accounts")
def add_account(data: AccountIn, db: Session = Depends(get_db)):
    email = data.email.strip()
    password = data.password.replace(" ", "").strip()
    if db.query(EmailAccount).filter(EmailAccount.email == email).first():
        raise HTTPException(400, "Account already exists")

    limit = data.daily_limit if data.daily_limit and data.daily_limit > 0 else DEFAULT_DAILY_LIMIT
    account = EmailAccount(
        email=email, password=password,
        daily_limit=limit, last_reset_date=date.today().isoformat(),
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return {"message": "Account added", "id": account.id}


@app.put("/api/accounts/{account_id}")
def update_account(account_id: int, data: AccountIn, db: Session = Depends(get_db)):
    account = db.query(EmailAccount).filter(EmailAccount.id == account_id).first()
    if not account:
        raise HTTPException(404, "Account not found")

    account.email = data.email.strip()
    account.password = data.password.replace(" ", "").strip()
    db.commit()
    return {"message": "Account updated"}


@app.delete("/api/accounts/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    account = db.query(EmailAccount).filter(EmailAccount.id == account_id).first()
    if not account:
        raise HTTPException(404, "Account not found")

    db.delete(account)
    db.commit()
    return {"message": "Account removed"}


@app.get("/api/settings")
def get_settings(db: Session = Depends(get_db)):
    settings = db.query(EmailSettings).first()
    return {"subject": settings.subject, "body_template": settings.body_template}


@app.put("/api/settings")
def update_settings(data: SettingsIn, db: Session = Depends(get_db)):
    settings = db.query(EmailSettings).first()
    if not settings:
        settings = EmailSettings()
        db.add(settings)

    settings.subject = data.subject
    settings.body_template = data.body_template
    db.commit()
    return {"message": "Settings saved"}


@app.post("/api/upload/preview")
async def preview_file(file: UploadFile = File(...)):
    content = await file.read()
    try:
        headers, rows = _parse_rows(content, file.filename)
    except ImportError:
        raise HTTPException(400, "openpyxl not installed. Run: pip install openpyxl")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"Failed to parse file: {exc}")

    return {
        "headers": headers,
        "detected": _detect_columns(headers),
        "preview": rows[:5],
        "filename": file.filename,
    }


@app.post("/api/upload/load")
async def load_file(
    file: UploadFile = File(...),
    name_col: str = Form("Name"),
    email_col: str = Form("Email"),
    file_col: str = Form(""),
    replace_queue: bool = Form(False),
    db: Session = Depends(get_db),
):
    content = await file.read()
    try:
        headers, rows = _parse_rows(content, file.filename)
    except ImportError:
        raise HTTPException(400, "openpyxl not installed. Run: pip install openpyxl")
    except Exception as exc:
        raise HTTPException(400, f"Parse error: {exc}")

    if email_col not in headers:
        raise HTTPException(400, {"message": "Selected email column was not found in the uploaded file."})
    if name_col and name_col not in headers:
        raise HTTPException(400, {"message": "Selected name column was not found in the uploaded file."})
    if file_col and file_col not in headers:
        raise HTTPException(400, {"message": "Selected PDF file column was not found in the uploaded file."})
    if not rows:
        raise HTTPException(400, {"message": "No recipient rows were found in the uploaded file."})

    prepared_jobs, match_report = _validated_jobs(rows, name_col, email_col, file_col)
    if match_report["missing_email"] or match_report["missing_pdf"]:
        raise HTTPException(400, {
            "message": "Data unmatched. Fix the CSV/Excel rows or select the correct PDF folder before loading.",
            "unmatched": match_report,
        })

    if replace_queue:
        db.query(CertificateJob).delete()
        db.flush()

    added = 0
    skipped = 0
    seen = set()
    for job in prepared_jobs:
        email_key = job["email"].lower()
        if email_key in seen:
            skipped += 1
            continue
        seen.add(email_key)

        if db.query(CertificateJob).filter(CertificateJob.email == job["email"]).first():
            skipped += 1
            continue

        db.add(CertificateJob(
            name=job["name"],
            email=job["email"],
            certificate_file=job["certificate_file"],
            status="Pending",
            account_used=None,
            error_message=None,
        ))
        added += 1

    db.commit()
    return {
        "added": added,
        "skipped": skipped,
        "total_in_queue": db.query(CertificateJob).count(),
        "matched_pdfs": len(prepared_jobs),
        "unused_pdfs": match_report["unused_pdfs"],
    }


@app.post("/api/upload/certificates")
async def upload_certs(files: list[UploadFile] = File(...), replace: bool = Form(False)):
    if replace:
        _clear_certificate_files()

    saved = []
    for upload in files:
        if not upload.filename.lower().endswith(".pdf"):
            continue

        filename = _safe_filename(upload.filename)
        if not filename:
            continue

        with open(os.path.join(CERT_DIR, filename), "wb") as output:
            output.write(await upload.read())
        saved.append(filename)

    return {"saved": saved, "count": len(saved)}


@app.get("/api/upload/certificates")
def list_certs():
    if not os.path.exists(CERT_DIR):
        return {"files": []}
    return {"files": sorted(f for f in os.listdir(CERT_DIR) if f.lower().endswith(".pdf"))}


@app.post("/api/test-send")
def test_send(data: TestSendIn, db: Session = Depends(get_db)):
    account = db.query(EmailAccount).filter(EmailAccount.id == data.account_id).first()
    if not account:
        raise HTTPException(404, "Account not found")

    settings = db.query(EmailSettings).first()
    test_pdf = os.path.join(TEST_CERT_DIR, "test_certificate.pdf")
    if not os.path.exists(test_pdf):
        _write_test_pdf(test_pdf, "Test User")

    success, message = send_certificate(
        sender_email=account.email,
        sender_password=account.password,
        recipient_email=data.to_email,
        recipient_name="Test User",
        subject=f"[TEST] {settings.subject}",
        body_text=settings.body_template + "\n\n[This is a test email sent from Certipatch]",
        attachment_path=test_pdf,
    )
    return {"success": success, "message": message}


def _available_accounts(db: Session):
    accounts = db.query(EmailAccount).order_by(EmailAccount.id.asc()).all()
    ready = []
    for account in accounts:
        account.check_and_reset()
        if not account.is_at_limit:
            ready.append(account)
    return ready


def _batch_quotas(accounts: list[EmailAccount], pending_count: int) -> dict[int, int]:
    if not accounts or pending_count <= 0:
        return {}

    base = pending_count // len(accounts)
    remainder = pending_count % len(accounts)
    return {
        account.id: base + (1 if index < remainder else 0)
        for index, account in enumerate(accounts)
    }


def _pick_batch_account(db: Session, quotas: dict[int, int], attempts: dict[int, int]):
    for account in _available_accounts(db):
        if attempts.get(account.id, 0) < quotas.get(account.id, 0):
            return account
    return None


def _process_queue():
    global is_sending
    db = SessionLocal()
    try:
        settings = db.query(EmailSettings).first()
        subject = settings.subject if settings else "Your Certificate"
        body_template = settings.body_template if settings else "Hello {name},"
        pending_count = db.query(CertificateJob).filter(CertificateJob.status == "Pending").count()
        accounts = _available_accounts(db)
        quotas = _batch_quotas(accounts, pending_count)
        attempts_by_account = {account.id: 0 for account in accounts}

        if pending_count and not accounts:
            print("[WARN] No sender accounts are available. Engine paused.")
            is_sending = False
            return
        if pending_count:
            plan = ", ".join(f"{account.email}: {quotas.get(account.id, 0)}" for account in accounts)
            print(f"[ENGINE] Batch distribution plan: {plan}")

        while is_sending:
            db.expire_all()
            job = db.query(CertificateJob).filter(CertificateJob.status == "Pending").order_by(CertificateJob.id.asc()).first()
            if not job:
                print("[ENGINE] No pending jobs found. Stopping.")
                is_sending = False
                break

            account = _pick_batch_account(db, quotas, attempts_by_account)
            if not account:
                print("[WARN] No sender account has remaining quota for this batch. Engine paused.")
                is_sending = False
                break
            attempts_by_account[account.id] = attempts_by_account.get(account.id, 0) + 1

            cert_path = os.path.join(CERT_DIR, job.certificate_file)
            print(f"[ENGINE] Sending to {job.email} using {account.email}, cert: {cert_path}")

            success, message = send_certificate(
                sender_email=account.email,
                sender_password=account.password,
                recipient_email=job.email,
                recipient_name=job.name or "Participant",
                subject=subject,
                body_text=body_template,
                attachment_path=cert_path,
            )

            job.status = "Sent" if success else "Failed"
            job.account_used = account.email
            job.error_message = None if success else message
            if success:
                job.sent_at = datetime.utcnow()
                account.daily_sent += 1
                account.last_reset_date = date.today().isoformat()
            db.commit()

            print(f"[ENGINE] Result for {job.email}: {job.status} - {message}")
            time.sleep(1)
    except Exception as exc:
        print(f"[ENGINE ERROR] {exc}")
        is_sending = False
    finally:
        db.close()


@app.post("/api/engine/start")
def engine_start(background_tasks: BackgroundTasks):
    global is_sending
    with send_lock:
        if is_sending:
            return {"message": "Engine already running"}
        is_sending = True
    background_tasks.add_task(_process_queue)
    return {"message": "Engine started"}


@app.post("/api/engine/pause")
def engine_pause():
    global is_sending
    is_sending = False
    return {"message": "Engine pausing after current email"}


@app.get("/api/engine/status")
def engine_status(db: Session = Depends(get_db)):
    total = db.query(CertificateJob).count()
    pending = db.query(CertificateJob).filter(CertificateJob.status == "Pending").count()
    sent = db.query(CertificateJob).filter(CertificateJob.status == "Sent").count()
    failed = db.query(CertificateJob).filter(CertificateJob.status == "Failed").count()

    # Rolling-window stats (ported from V2)
    window_hours = 24
    sent_in_window = 0
    try:
        since = datetime.utcnow() - timedelta(hours=window_hours)
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
        "is_sending": is_sending,
        "total": total,
        "pending": pending,
        "sent": sent,
        "failed": failed,
        "sent_in_window": sent_in_window,
        "window_hours": window_hours,
    }


@app.get("/api/jobs/quota")
def get_quota(db: Session = Depends(get_db)):
    """Per-account rolling-window quota breakdown (ported from V2)."""
    window_hours = 24
    accounts = db.query(EmailAccount).order_by(EmailAccount.id.asc()).all()
    since = datetime.utcnow() - timedelta(hours=window_hours)

    per_out = []
    total_cap = 0
    remaining_sum = 0
    used_total = 0

    for acc in accounts:
        acc.check_and_reset()
        lim = acc.daily_limit if acc.daily_limit and acc.daily_limit > 0 else DEFAULT_DAILY_LIMIT
        total_cap += lim

        used = (
            db.query(CertificateJob)
            .filter(
                CertificateJob.status == "Sent",
                CertificateJob.account_used == acc.email,
                func.coalesce(CertificateJob.sent_at, CertificateJob.timestamp) >= since,
            )
            .count()
        )
        used_total += used
        rem = max(0, lim - used)
        remaining_sum += rem
        per_out.append({
            "email": acc.email,
            "limit": lim,
            "used_in_window": used,
            "remaining": rem,
        })

    return {
        "accounts": len(accounts),
        "total": total_cap,
        "remaining": remaining_sum,
        "used_in_window": used_total,
        "window_hours": window_hours,
        "per_account": per_out,
    }


@app.get("/api/jobs")
def list_jobs(db: Session = Depends(get_db)):
    jobs = db.query(CertificateJob).order_by(CertificateJob.id.desc()).limit(200).all()
    return [{
        "id": job.id,
        "name": job.name,
        "email": job.email,
        "certificate_file": job.certificate_file,
        "status": job.status,
        "account_used": job.account_used,
        "error_message": job.error_message,
        "timestamp": job.timestamp.isoformat() if job.timestamp else None,
    } for job in jobs]


@app.post("/api/jobs/clear-completed")
def clear_completed(db: Session = Depends(get_db)):
    """Remove Sent and Failed rows so a new campaign can start clean; Pending jobs are kept."""
    q = db.query(CertificateJob).filter(
        CertificateJob.status.in_(("Sent", "Failed"))
    )
    deleted = q.delete(synchronize_session=False)
    db.commit()
    return {"message": f"Removed {deleted} completed job(s).", "deleted": deleted}


@app.delete("/api/jobs")
def clear_jobs(db: Session = Depends(get_db)):
    db.query(CertificateJob).delete()
    db.commit()
    return {"message": "All jobs cleared"}


@app.post("/api/session/reset")
def reset_transient_session(db: Session = Depends(get_db)):
    if is_sending:
        return {"message": "Engine is running. Temporary files were not cleared.", "cleared": False}

    db.query(CertificateJob).delete()
    db.commit()
    removed_pdfs = _clear_certificate_files()
    return {
        "message": "Temporary queue and selected PDF folder cleared.",
        "cleared": True,
        "removed_pdfs": removed_pdfs,
    }


if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def serve_index():
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"status": "Certipatch v3 API running. Frontend not found."}
