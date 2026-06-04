from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional

# --- STATIONS ---
class StationBase(BaseModel):
    code: str
    network: str
    latitude: float
    longitude: float
    elevation: float

class Station(StationBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)

# --- SEISMIC EVENTS ---
class SeismicEventBase(BaseModel):
    event_id: str
    origin_time: Optional[datetime]
    latitude: float
    longitude: float
    depth: Optional[float]
    magnitude: Optional[float]

class SeismicEvent(SeismicEventBase):
    id: int
    
    model_config = ConfigDict(from_attributes=True)