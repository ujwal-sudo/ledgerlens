from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .models import Base


def init_db(db_path: str = "./ledgerlens.db"):
    """Initialize the SQLite database, creating all tables."""
    engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return engine


def get_session(db_path: str = "./ledgerlens.db"):
    """Get a new database session."""
    engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        pool_pre_ping=True,
        connect_args={"check_same_thread": False},
    )
    Session = sessionmaker(bind=engine)
    return Session()