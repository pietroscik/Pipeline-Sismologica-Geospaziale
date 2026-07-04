import argparse
import subprocess
import sys
from pathlib import Path

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover
    def tqdm(iterable, **kwargs):
        return iterable
    tqdm.write = print  # type: ignore[attr-defined]

def is_valid_run_dir(p: Path) -> bool:
    return p.is_dir() and (
        (p / "processed").exists()
        or (p / "interim").exists()
        or (p / "selected_stations.txt").exists()
    )

def main():
    parser = argparse.ArgumentParser(description="Itera su tutte le run e lancia ingestione DB.")
    parser.add_argument("--runs-dir", default="runs", help="Cartella runs")
    parser.add_argument("--source-type", default="mseed", help="Valore --source-type")
    parser.add_argument("--python-exe", default=sys.executable, help="Python da usare")
    parser.add_argument("--ingest-script", default="ingest_runs_to_db.py", help="Script ingestione")
    parser.add_argument("--stop-on-error", action="store_true", help="Interrompe al primo errore")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    runs_dir = (project_root / args.runs_dir).resolve()
    ingest_script = (project_root / args.ingest_script).resolve()

    run_dirs = sorted([p for p in runs_dir.iterdir() if is_valid_run_dir(p)])
    if not run_dirs:
        print("Nessuna run valida trovata.")
        return 0

    failures = []
    for run_dir in tqdm(run_dirs, desc="Ingesting runs", unit="run"):
        run_id = run_dir.name
        run_name = f"Analisi Pipeline: {run_id}"
        tqdm.write(f"\n[INGEST] {run_id}")

        cmd = [
            str(args.python_exe),
            str(ingest_script),
            "--run-id", run_id,
            "--run-name", run_name,
            "--run-dir", str(run_dir),
            "--source-type", args.source_type,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            failures.append(run_id)
            tqdm.write(f"[ERROR] Ingestione fallita per {run_id} (exit code: {result.returncode})")
            if result.stdout:
                tqdm.write("--- STDOUT ---\n" + result.stdout)
            if result.stderr:
                tqdm.write("--- STDERR ---\n" + result.stderr)
            if args.stop_on_error:
                break
        else:
            tqdm.write(f"[OK] {run_id}")

    print("\n=== RIEPILOGO ===")
    print(f"Totale run: {len(run_dirs)}")
    print(f"Fallite: {len(failures)}")
    if failures:
        print("Elenco fallite:", ", ".join(failures))
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())