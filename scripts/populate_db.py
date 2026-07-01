#!/usr/bin/env python3
"""
Script per popolare il database PostgreSQL/PostGIS dai file CSV.

Utilizzo:
    python scripts/populate_db.py \
        --stations-csv examples/mobile_devices/stations.csv \
        --deltas-csv examples/mobile_devices/scoperte_automatiche.csv.gz \
        --clear
"""
import argparse
import sys
from pathlib import Path
import pandas as pd
from sqlalchemy.orm import Session
from geoalchemy2.elements import WKTElement

# Aggiungi la root del progetto al PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mobile.db.database import SessionLocal, engine, Base
from mobile.db.models import Station, SeismicEvent, EventDelta
from scripts.utils import setup_logger

logger = setup_logger("db_populate")

def parse_args():
    parser = argparse.ArgumentParser(description="Popola il database spaziale PostGIS.")
    parser.add_argument("--stations-csv", type=Path, required=True, help="Percorso al file CSV stazioni")
    parser.add_argument("--deltas-csv", type=Path, required=True, help="Percorso al file CSV deltas/eventi")
    parser.add_argument("--clear", action="store_true", help="Svuota le tabelle prima di importare")
    return parser.parse_args()

def populate_database(stations_path: Path, deltas_path: Path, clear: bool):
    # Crea le tabelle nel database (se non esistono già)
    logger.info("Verifica e creazione tabelle nel database...")
    Base.metadata.create_all(bind=engine)
    
    db: Session = SessionLocal()
    
    try:
        if clear:
            logger.warning("Svuotamento delle tabelle in corso...")
            db.query(EventDelta).delete()
            db.query(SeismicEvent).delete()
            db.query(Station).delete()
            db.commit()

        # 1. CARICAMENTO STAZIONI
        logger.info(f"Caricamento stazioni da {stations_path}...")
        df_stations = pd.read_csv(stations_path)
        
        # Fill dei valori mancanti per evitare errori SQL
        df_stations = df_stations.fillna({'elevation': 0.0, 'network': 'IV'})
        
        stations_added = 0
        for _, row in df_stations.iterrows():
            # Crea la geometria Point per PostGIS (SRID 4326)
            geom = WKTElement(f'POINT({row["longitude"]} {row["latitude"]})', srid=4326)
            
            station = Station(
                code=str(row['station']).strip().upper(),
                network=str(row['network']).strip().upper(),
                latitude=float(row['latitude']),
                longitude=float(row['longitude']),
                elevation=float(row['elevation']),
                geom=geom
            )
            db.add(station)
            stations_added += 1
            
        db.commit()
        logger.info(f"✅ Inserite {stations_added} stazioni nel database.")

        # Mappa Codice -> ID Database per le stazioni
        station_map = {st.code: st.id for st in db.query(Station).all()}

        # 2. CARICAMENTO EVENTI E DELTAS
        logger.info(f"Caricamento delta ed eventi da {deltas_path}...")
        df_deltas = pd.read_csv(deltas_path)
        
        if 'arrival_iso' in df_deltas.columns:
            df_deltas['arrival_iso'] = pd.to_datetime(df_deltas['arrival_iso'], errors='coerce')
            
        # Calcoliamo gli epicentri "al volo" aggregando i dati (come in mappa_epicentrici.py)
        logger.info("Calcolo epicentri eventi in corso...")
        df_merged = pd.merge(df_deltas, df_stations, on='station', how='left')
        
        events_df = df_merged.groupby('event_id').agg({
            'latitude': 'mean',
            'longitude': 'mean',
            'arrival_iso': 'min'
        }).reset_index()
        
        events_df = events_df.dropna(subset=['latitude', 'longitude'])

        events_added = 0
        for _, row in events_df.iterrows():
            geom = WKTElement(f'POINT({row["longitude"]} {row["latitude"]})', srid=4326)
            
            event = SeismicEvent(
                event_id=str(row['event_id']),
                origin_time=row['arrival_iso'] if not pd.isna(row['arrival_iso']) else None,
                latitude=float(row['latitude']),
                longitude=float(row['longitude']),
                geom=geom
            )
            db.add(event)
            events_added += 1
            
        db.commit()
        logger.info(f"✅ Inseriti {events_added} eventi sismici unici nel database.")

        # Mappa Event_ID stringa -> ID Database
        event_map = {ev.event_id: ev.id for ev in db.query(SeismicEvent).all()}

        # 3. INSERIMENTO DELTAS (Associazione Stazione-Evento)
        logger.info("Creazione relazioni delta spaziali in corso...")
        deltas_added = 0
        delta_objects = []
        
        for _, row in df_deltas.iterrows():
            st_id = station_map.get(str(row['station']).strip().upper())
            ev_id = event_map.get(str(row['event_id']))
            
            if st_id and ev_id:
                delta = EventDelta(
                    event_id=ev_id,
                    station_id=st_id,
                    delta_seconds=float(row['delta_seconds']),
                    pick_time=row['arrival_iso'] if 'arrival_iso' in row and not pd.isna(row['arrival_iso']) else None,
                    phase=row.get('phase', 'P')
                )
                delta_objects.append(delta)
                deltas_added += 1
                
                # Eseguiamo commit parziali per non saturare la RAM
                if len(delta_objects) >= 5000:
                    db.bulk_save_objects(delta_objects)
                    db.commit()
                    delta_objects = []
                    
        # Salva i rimanenti
        if delta_objects:
            db.bulk_save_objects(delta_objects)
            db.commit()
            
        logger.info(f"✅ Inseriti {deltas_added} records di delta relazionali.")
        logger.info("🎉 Popolamento del database completato con successo!")

    except Exception as e:
        db.rollback()
        logger.error(f"Errore durante l'importazione: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    args = parse_args()
    populate_database(args.stations_csv, args.deltas_csv, args.clear)