from sqlalchemy.orm import Session
from mobile.db import models

def get_stations(db: Session, skip: int = 0, limit: int = 100):
    """Recupera la lista delle stazioni sismiche dal database."""
    return db.query(models.Station).offset(skip).limit(limit).all()

def get_events(db: Session, skip: int = 0, limit: int = 100):
    """Recupera la lista degli eventi sismici dal database, ordinati dal più recente."""
    return db.query(models.SeismicEvent)\
             .order_by(models.SeismicEvent.origin_time.desc())\
             .offset(skip).limit(limit).all()