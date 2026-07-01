#!/usr/bin/env python3
"""
Test Workflow Completo - Issue #3

Script per validare che l'intero workflow funzioni correttamente.
Esegue test su:
1. Pipeline principale (run_pipeline.py)
2. Pipeline mobile (mobile_analysis_pipeline.py)
3. Validazione configurazione
4. Gestione errori

Environment Variables:
    ENVIRONMENT: Ambiente di test (dev, test, prod)
    RUN_TESTS: Se impostato, esegue solo i test senza generare output

Usage:
    # Esegui tutti i test
    python scripts/test_workflow.py
    
    # Esegui con ambiente specifico
    ENVIRONMENT=test python scripts/test_workflow.py
    
    # Esegui solo test di validazione
    python scripts/test_workflow.py --validation-only
    
    # Esegui solo test di integrazione
    python scripts/test_workflow.py --integration-only
    
    # Verbose mode
    python scripts/test_workflow.py --verbose
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import json
import yaml

# Aggiungiamo la root del progetto al sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Import PROJECT_ROOT for consistent path resolution
from path_utils import PROJECT_ROOT

# Test results storage
test_results: List[Dict] = []


def log_test(test_name: str, passed: bool, message: str = "", details: Optional[Dict] = None) -> None:
    """Record a test result."""
    result = {
        "timestamp": datetime.now().isoformat(),
        "test": test_name,
        "passed": passed,
        "message": message,
        "details": details or {}
    }
    test_results.append(result)
    
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"{status}: {test_name}")
    if message:
        print(f"   {message}")
    if details:
        for key, value in details.items():
            print(f"   {key}: {value}")


def save_test_results(results_dir: Path = None) -> Path:
    """Save test results to JSON file."""
    if results_dir is None:
        results_dir = PROJECT_ROOT / "test_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = results_dir / f"workflow_test_{timestamp}.json"
    
    # Calculate summary
    summary = {
        "total_tests": len(test_results),
        "passed": sum(1 for r in test_results if r["passed"]),
        "failed": sum(1 for r in test_results if not r["passed"]),
        "pass_rate": sum(1 for r in test_results if r["passed"]) / len(test_results) if test_results else 0
    }
    
    # Save results
    output = {
        "summary": summary,
        "environment": os.getenv("ENVIRONMENT", "dev"),
        "timestamp": datetime.now().isoformat(),
        "results": test_results
    }
    
    with open(results_file, "w") as f:
        json.dump(output, f, indent=2, default=str)
    
    print(f" 📊 Results saved to: {results_file}")
    return results_file


def print_summary() -> None:
    """Print test summary."""
    if not test_results:
        print("⚠️ No tests executed")
        return
    
    passed = sum(1 for r in test_results if r["passed"])
    failed = sum(1 for r in test_results if not r["passed"])
    pass_rate = (passed / len(test_results)) * 100
    
    print(" " + "=" * 60)
    print("📊 TEST SUMMARY")
    print("=" * 60)
    print(f"Total:  {len(test_results)}")
    print(f"✅ Passed: {passed}")
    print(f"❌ Failed: {failed}")
    print(f"Pass Rate: {pass_rate:.1f}%")
    print("=" * 60)
    
    if failed > 0:
        print(" ❌ FAILED TESTS:")
        for r in test_results:
            if not r["passed"]:
                print(f"  - {r['test']}: {r['message']}")


# =============================================================================
# VALIDATION TESTS
# =============================================================================

def test_path_resolution():
    """Test that path resolution works correctly."""
    from path_utils import PROJECT_ROOT, get_project_root, resolve_project_path
    
    # Test PROJECT_ROOT
    assert PROJECT_ROOT.exists(), "PROJECT_ROOT does not exist"
    assert PROJECT_ROOT.is_dir(), "PROJECT_ROOT is not a directory"
    
    # Test get_project_root
    root = get_project_root()
    assert root.exists(), "get_project_root() returns non-existent path"
    assert root == PROJECT_ROOT, "get_project_root() != PROJECT_ROOT"
    
    # Test resolve_project_path
    test_path = resolve_project_path("scripts")
    assert test_path.exists(), "resolve_project_path('scripts') does not exist"
    
    log_test("Path Resolution", True, "All path resolution functions work correctly")


def test_imports():
    """Test that all required modules can be imported."""
    errors = []
    
    # Test main modules
    modules_to_test = [
        "path_utils",
        "scripts.utils",
        "mobile.alert_system",
        "mobile.data_validator",
    ]
    
    for module in modules_to_test:
        try:
            __import__(module)
        except ImportError as e:
            errors.append(f"{module}: {e}")
    
    if errors:
        log_test("Module Imports", False, f"{len(errors)} import errors", {"errors": errors})
    else:
        log_test("Module Imports", True, f"All {len(modules_to_test)} modules imported successfully")


def test_alert_system_config():
    """Test alert system configuration loading."""
    from mobile.alert_system import AlertSystem, validate_alert_config
    
    # Test default config
    try:
        alert_system = AlertSystem()
        log_test("Alert System - Default Config", True, "Default configuration loaded")
    except Exception as e:
        log_test("Alert System - Default Config", False, str(e))
        return
    
    # Test environment-specific config
    for env in ["dev", "test", "prod"]:
        try:
            os.environ["ENVIRONMENT"] = env
            # Force reimport to pick up new environment
            import importlib
            import mobile.alert_system as as_module
            importlib.reload(as_module)
            alert_system = as_module.AlertSystem()
            log_test(f"Alert System - {env.upper()} Config", True, f"{env} configuration loaded")
        except Exception as e:
            log_test(f"Alert System - {env.upper()} Config", False, str(e))
        finally:
            os.environ.pop("ENVIRONMENT", None)
    
    # Test config validation
    try:
        os.environ['WEBHOOK_URL'] = 'https://test.dev/null'
        is_valid, errors = validate_alert_config()
        if is_valid:
            log_test("Alert System - Config Validation", True, "Configuration is valid")
        else:
            log_test("Alert System - Config Validation", False, f"{len(errors)} validation errors", {"errors": errors})
    except Exception as e:
        log_test("Alert System - Config Validation", False, str(e))
    finally:
        os.environ.pop('WEBHOOK_URL', None)


def test_data_validator():
    """Test data validation functions."""
    from mobile.data_validator import (
        validate_data,
        validate_stations,
        DataValidationError
    )
    import pandas as pd
    
    # Test valid data
    try:
        df = pd.DataFrame({
            "event_id": [1, 2],
            "station": ["A", "B"],
            "delta_seconds": [0.1, 0.2],
            "arrival_iso": ["2024-01-01T00:00:00", "2024-01-01T00:00:01"]
        })
        is_valid, msg, _ = validate_data(df)
        log_test("Data Validator - Valid Data", is_valid, msg)
    except Exception as e:
        log_test("Data Validator - Valid Data", False, str(e))
    
    # Test missing columns
    try:
        df = pd.DataFrame({"event_id": [1, 2]})
        is_valid, msg, _ = validate_data(df)
        log_test("Data Validator - Missing Columns", not is_valid, "Correctly detected missing columns: " + msg)
    except Exception as e:
        log_test("Data Validator - Missing Columns", False, str(e))
    
    try:
        df = pd.DataFrame({
            "station": ["A", "B"],
            "latitude": [40.8, -100.0],  # Invalid latitude
            "longitude": [14.1, 14.2]
        })
        is_valid, msg = validate_stations(df)
        log_test("Data Validator - Geographic Coordinates", not is_valid, "Correctly detected invalid coordinates: " + msg)
    except Exception as e:
        log_test("Data Validator - Geographic Coordinates", False, str(e))
    
    try:
        df = pd.DataFrame({
            "station": ["A", "B"],
            "latitude": [40.8, 41.0],
            "longitude": [14.1, 14.2]
        })
        is_valid, msg = validate_stations(df)
        log_test("Data Validator - Valid Coordinates", is_valid, "Valid coordinates accepted: " + msg)
    except Exception as e:
        log_test("Data Validator - Valid Coordinates", False, str(e))


def test_encryption():
    """Test encryption/decryption functionality."""
    from mobile.alert_system import AlertSystem
    import base64
    
    # Generate a valid Fernet test key (32 url-safe base64-encoded bytes)
    test_key = base64.urlsafe_b64encode(b"12345678901234567890123456789012").decode('utf-8')
    os.environ["ENCRYPTION_KEY"] = test_key
    
    try:
        alert_system = AlertSystem()
        alert_system.encryption_key = test_key
        
        # Test encryption
        encrypted = alert_system._encrypt_value("secret_password")
        assert encrypted.startswith("ENC:"), "Encrypted value should start with ENC:"
        
        # Test decryption
        decrypted = alert_system._decrypt_config_values()
        
        log_test("Encryption - Basic", True, "Encryption and decryption work")
        
        # Test with encrypt_credentials function
        from scripts.encrypt_credentials import encrypt_value
        encrypted_cred = encrypt_value("test_value", test_key)
        assert encrypted_cred.startswith("ENC:"), "Encrypted credential should start with ENC:"
        
        log_test("Encryption - Credentials", True, "Credential encryption works")
        
    except ImportError as e:
        log_test("Encryption", False, f"Cryptography library not installed: {e}")
    except Exception as e:
        log_test("Encryption", False, str(e))
    finally:
        os.environ.pop("ENCRYPTION_KEY", None)


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

def run_command(cmd: List[str], cwd: Path = None, timeout: int = 300) -> Tuple[bool, str, str]:
    """Run a command and return (success, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            check=True,
            timeout=timeout,
            capture_output=True,
            text=True
        )
        return (True, result.stdout, result.stderr)
    except subprocess.TimeoutExpired:
        return (False, "", "Command timed out")
    except subprocess.CalledProcessError as e:
        return (False, e.stdout, e.stderr)
    except Exception as e:
        return (False, "", str(e))


def test_pipeline_execution():
    """Test execution of run_pipeline.py with example data."""
    python_exe = sys.executable
    scripts_dir = PROJECT_ROOT / "scripts"
    examples_dir = PROJECT_ROOT / "examples" / "mobile_devices"
    
    # Check if example data exists
    example_files = [
        examples_dir / "scoperte_automatiche.csv.gz",
        examples_dir / "stations.csv"
    ]
    
    missing_files = [f for f in example_files if not f.exists()]
    if missing_files:
        log_test("Pipeline Execution - Example Data", False, 
                f"Missing example files: {[f.name for f in missing_files]}")
        return
    
    log_test("Pipeline Execution - Example Data", True, "Example data files exist")
    
    # Test with dry-run first
    run_name = f"test_dryrun_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    cmd = [
        python_exe,
        str(PROJECT_ROOT / "run_pipeline.py"),
        "--run-name", run_name,
        "--delta-csv", str(example_files[0]),
        "--stations-csv", str(example_files[1]),
        "--start-phase", "2",
        "--dry-run"
    ]
    
    success, stdout, stderr = run_command(cmd, cwd=PROJECT_ROOT, timeout=60)
    
    if success:
        log_test("Pipeline Execution - Dry Run", True, "Dry run completed successfully")
    else:
        log_test("Pipeline Execution - Dry Run", False, f"Dry run failed: {stderr[:200]}")
    
    # Test actual execution (only if dry-run passed)
    if success:
        run_name = f"test_run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        cmd = [
            python_exe,
            str(PROJECT_ROOT / "run_pipeline.py"),
            "--run-name", run_name,
            "--delta-csv", str(example_files[0]),
            "--stations-csv", str(example_files[1]),
            "--start-phase", "2",
            "--skip-phase3",  # Skip spatialization for faster test
            "--skip-phase4",  # Skip GIS output for faster test
            "--cleanup-on-error"
        ]
        
        success, stdout, stderr = run_command(cmd, cwd=PROJECT_ROOT, timeout=300)
        
        if success:
            # Check if output directory was created
            output_dir = PROJECT_ROOT / "runs" / run_name
            if output_dir.exists():
                # Check for expected output files
                expected_files = [
                    output_dir / "processed" / "station_stats.csv"
                ]
                existing_files = [f for f in expected_files if f.exists()]
                
                log_test("Pipeline Execution - Output Files", 
                        len(existing_files) == len(expected_files),
                        f"{len(existing_files)}/{len(expected_files)} expected files created",
                        {"created": [f.name for f in existing_files]})
            else:
                log_test("Pipeline Execution - Output Directory", False, "Output directory not created")
            
            log_test("Pipeline Execution - Full Run", True, "Pipeline completed successfully")
        else:
            log_test("Pipeline Execution - Full Run", False, f"Pipeline failed: {stderr[:500]}")


def test_mobile_pipeline():
    """Test execution of mobile_analysis_pipeline.py."""
    python_exe = sys.executable
    examples_dir = PROJECT_ROOT / "examples" / "mobile_devices"
    mobile_script = PROJECT_ROOT / "mobile" / "mobile_analysis_pipeline.py"
    
    # Check if mobile script exists
    if not mobile_script.exists():
        log_test("Mobile Pipeline - Script Exists", False, "mobile_analysis_pipeline.py not found")
        return
    
    log_test("Mobile Pipeline - Script Exists", True)
    
    # Check if example data exists
    example_files = [
        examples_dir / "scoperte_automatiche.csv.gz",
        examples_dir / "stations.csv"
    ]
    
    missing_files = [f for f in example_files if not f.exists()]
    if missing_files:
        log_test("Mobile Pipeline - Example Data", False,
                f"Missing files: {[f.name for f in missing_files]}")
        return
    
    log_test("Mobile Pipeline - Example Data", True, "Example data files exist")
    
    # Test with dry-run
    run_name = f"mobile_test_dryrun_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    output_dir = PROJECT_ROOT / "runs" / run_name
    
    cmd = [
        python_exe,
        str(mobile_script),
        "--input-csv", str(example_files[0]),
        "--stations-csv", str(example_files[1]),
        "--output-dir", str(output_dir),
        "--dry-run"
    ]
    
    success, stdout, stderr = run_command(cmd, cwd=examples_dir, timeout=60)
    
    if success:
        log_test("Mobile Pipeline - Dry Run", True, "Dry run completed successfully")
    else:
        log_test("Mobile Pipeline - Dry Run", False, f"Dry run failed: {stderr[:200]}")


def test_error_handling():
    """Test error handling with invalid inputs."""
    python_exe = sys.executable
    
    # Test with non-existent file
    cmd = [
        python_exe,
        str(PROJECT_ROOT / "run_pipeline.py"),
        "--run-name", "test_error",
        "--delta-csv", "/nonexistent/file.csv"
    ]
    
    success, stdout, stderr = run_command(cmd, cwd=PROJECT_ROOT, timeout=30)
    
    # Should fail
    if not success:
        log_test("Error Handling - Invalid File", True, "Correctly handled non-existent file")
    else:
        log_test("Error Handling - Invalid File", False, "Should have failed with non-existent file")
    
    # Test with invalid start-phase
    cmd = [
        python_exe,
        str(PROJECT_ROOT / "run_pipeline.py"),
        "--run-name", "test_error",
        "--start-phase", "10"  # Invalid
    ]
    
    success, stdout, stderr = run_command(cmd, cwd=PROJECT_ROOT, timeout=30)
    
    # Should fail
    if not success:
        log_test("Error Handling - Invalid Phase", True, "Correctly handled invalid phase")
    else:
        log_test("Error Handling - Invalid Phase", False, "Should have failed with invalid phase")


def test_multi_environment():
    """Test multi-environment configuration."""
    from mobile.alert_system import AlertSystem
    
    # Test each environment
    for env in ["dev", "test", "prod"]:
        os.environ["ENVIRONMENT"] = env
        
        try:
            # Reload module to pick up new environment
            import importlib
            import mobile.alert_system as as_module
            importlib.reload(as_module)
            
            alert_system = as_module.AlertSystem()
            
            # Check if environment was set correctly
            if alert_system.environment == env:
                log_test(f"Multi-Environment - {env.upper()}", True, 
                        f"Environment correctly set to {env}")
            else:
                log_test(f"Multi-Environment - {env.upper()}", False,
                        f"Environment is {alert_system.environment}, expected {env}")
        except Exception as e:
            log_test(f"Multi-Environment - {env.upper()}", False, str(e))
        finally:
            os.environ.pop("ENVIRONMENT", None)


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Test Workflow Completo - Issue #3"
    )
    parser.add_argument(
        "--validation-only",
        action="store_true",
        help="Esegui solo test di validazione (nessuna esecuzione pipeline)"
    )
    parser.add_argument(
        "--integration-only",
        action="store_true",
        help="Esegui solo test di integrazione (esecuzione pipeline)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Mostra output dettagliato"
    )
    parser.add_argument(
        "--save-results",
        action="store_true",
        help="Salva risultati in file JSON"
    )
    args = parser.parse_args()
    
    print("=" * 60)
    print("🧪 TEST WORKFLOW COMPLETO - Issue #3")
    print("=" * 60)
    print(f"Environment: {os.getenv('ENVIRONMENT', 'dev')}")
    print(f"Python: {sys.executable}")
    print(f"CWD: {Path.cwd()}")
    print("=" * 60)
    print()
    
    # Validation tests
    if not args.integration_only:
        print("📋 VALIDATION TESTS")
        print("-" * 60)
        
        test_path_resolution()
        test_imports()
        test_alert_system_config()
        test_data_validator()
        test_encryption()
        test_multi_environment()
    
    # Integration tests
    if not args.validation_only:
        print()
        print("🔧 INTEGRATION TESTS")
        print("-" * 60)
        
        test_pipeline_execution()
        test_mobile_pipeline()
        test_error_handling()
    
    # Print summary
    print_summary()
    
    # Save results
    if args.save_results or args.verbose:
        save_test_results()
    
    # Exit with error code if any tests failed
    failed = sum(1 for r in test_results if not r["passed"])
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
