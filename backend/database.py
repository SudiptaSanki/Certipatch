from sqlalchemy import create_engine, Column, Integer, String, DateTime, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
import os

# Connect to a local SQLite file inside your data folder
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "certipatch.db")
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

class CertificateJob(Base):
    __tablename__ = "certificate_jobs"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    email = Column(String, index=True)
    certificate_file = Column(String)
    
    # Tracking columns
    status = Column(String, default="Pending") # Pending, Sent, Failed
    account_used = Column(String, nullable=True)
    error_message = Column(String, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    # Set when status becomes Sent; used for rolling send limits per account
    sent_at = Column(DateTime, nullable=True)

def _migrate_schema():
    """Add columns introduced after first release (SQLite has no ALTER IF NOT EXISTS)."""
    try:
        inspector = inspect(engine)
        if not inspector.has_table("certificate_jobs"):
            return
        cols = {c["name"] for c in inspector.get_columns("certificate_jobs")}
        if "sent_at" not in cols:
            with engine.begin() as conn:
                conn.execute(
                    text("ALTER TABLE certificate_jobs ADD COLUMN sent_at DATETIME")
                )
    except Exception as e:
        print(f"[WARN] certificate_jobs migrate skipped: {e}")

def init_db():
    """Creates the database tables if they don't exist."""
    Base.metadata.create_all(bind=engine)
    _migrate_schema()