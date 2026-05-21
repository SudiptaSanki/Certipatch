from datetime import date, datetime
import json
import os

from sqlalchemy import Column, DateTime, Integer, String, create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

# Consumer Gmail (@gmail.com) is commonly limited to about 500 recipients per
# rolling 24 hours for SMTP/app sending; Google Workspace limits are higher.
DEFAULT_DAILY_LIMIT = 500


DB_PATH = os.path.join(os.path.dirname(__file__), "data", "certipatch.db")
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config", "settings.json")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class EmailAccount(Base):
    __tablename__ = "email_accounts"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    password = Column(String)
    daily_limit = Column(Integer, default=DEFAULT_DAILY_LIMIT)
    daily_sent = Column(Integer, default=0)
    last_reset_date = Column(String, default="")

    def check_and_reset(self):
        today = date.today().isoformat()
        if self.last_reset_date != today:
            self.daily_sent = 0
            self.last_reset_date = today

    @property
    def is_at_limit(self):
        limit = self.daily_limit if self.daily_limit and self.daily_limit > 0 else DEFAULT_DAILY_LIMIT
        return self.daily_sent >= limit


class EmailSettings(Base):
    __tablename__ = "email_settings"

    id = Column(Integer, primary_key=True, default=1)
    subject = Column(String, default="Your Certificate of Participation")
    body_template = Column(
        String,
        default=(
            "Hello {name},\n\n"
            "Thank you for participating. Please find your certificate attached.\n\n"
            "Best regards,\nThe Team"
        ),
    )


class CertificateJob(Base):
    __tablename__ = "certificate_jobs"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    email = Column(String, index=True)
    certificate_file = Column(String)
    status = Column(String, default="Pending")
    account_used = Column(String, nullable=True)
    error_message = Column(String, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    # Set when status becomes Sent; used for rolling send-limit calculations
    sent_at = Column(DateTime, nullable=True)


def _load_config_defaults():
    if not os.path.exists(CONFIG_PATH):
        return {}, [], {}

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception:
        return {}, [], {}

    sending_limits = config.get("sending_limits", {})
    return config.get("email_settings", {}), config.get("accounts", []), sending_limits


def _migrate_schema():
    """Add columns introduced after the initial release (SQLite has no ALTER IF NOT EXISTS)."""
    try:
        insp = inspect(engine)
        # --- certificate_jobs ---
        if insp.has_table("certificate_jobs"):
            cols = {c["name"] for c in insp.get_columns("certificate_jobs")}
            if "sent_at" not in cols:
                with engine.begin() as conn:
                    conn.execute(text("ALTER TABLE certificate_jobs ADD COLUMN sent_at DATETIME"))
        # --- email_accounts ---
        if insp.has_table("email_accounts"):
            cols = {c["name"] for c in insp.get_columns("email_accounts")}
            if "daily_limit" not in cols:
                with engine.begin() as conn:
                    conn.execute(
                        text(f"ALTER TABLE email_accounts ADD COLUMN daily_limit INTEGER DEFAULT {DEFAULT_DAILY_LIMIT}")
                    )
    except Exception as e:
        print(f"[WARN] schema migration skipped: {e}")


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate_schema()
    db = SessionLocal()
    try:
        email_settings, accounts, sending_limits = _load_config_defaults()

        # Resolve the default daily limit from config or fall back to the constant
        cfg_default_limit = DEFAULT_DAILY_LIMIT
        if sending_limits:
            raw = sending_limits.get(
                "smtp_daily_per_account_default",
                sending_limits.get("gmail_free_smtp_daily_per_account", DEFAULT_DAILY_LIMIT),
            )
            try:
                cfg_default_limit = max(1, min(int(raw), 50_000))
            except (TypeError, ValueError):
                pass

        if not db.query(EmailSettings).first():
            db.add(EmailSettings(
                subject=email_settings.get("subject", "Your Certificate of Participation"),
                body_template=email_settings.get(
                    "body_template",
                    "Hello {name},\n\nThank you for participating. Please find your certificate attached.\n\nBest regards,\nThe Team",
                ),
            ))

        if not db.query(EmailAccount).first():
            today = date.today().isoformat()
            for item in accounts:
                email = (item.get("email") or "").strip()
                password = (item.get("password") or "").replace(" ", "").strip()
                if email and password:
                    # Per-account limit from config, or the resolved global default
                    raw_limit = item.get("daily_send_limit", cfg_default_limit)
                    try:
                        per_limit = max(1, min(int(raw_limit), 50_000))
                    except (TypeError, ValueError):
                        per_limit = cfg_default_limit
                    db.add(EmailAccount(
                        email=email, password=password,
                        daily_limit=per_limit, last_reset_date=today,
                    ))

        db.commit()
    finally:
        db.close()
