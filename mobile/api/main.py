from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from typing import List

from mobile.db.database import get_db
from mobile.api import schemas, crud

app = FastAPI(
    title="Pipeline Sismologica Geospaziale API",
    description="API REST ad alte prestazioni per l'interrogazione dei dati sismici.",
    version="1.0.0"
)

@app.get("/", tags=["Root"])
def read_root():
    return {"message": "Benvenuto nell'API della Pipeline Sismologica Geospaziale! Visita /docs per la documentazione."}

@app.get("/stations/", response_model=List[schemas.Station], tags=["Stations"])
def read_stations(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """Restituisce l'elenco delle stazioni sismiche."""
    stations = crud.get_stations(db, skip=skip, limit=limit)
    return stations

@app.get("/events/", response_model=List[schemas.SeismicEvent], tags=["Events"])
def read_events(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """Restituisce l'elenco degli eventi sismici."""
    events = crud.get_events(db, skip=skip, limit=limit)
    return events