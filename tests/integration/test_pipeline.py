"""
Integration tests for pipeline workflows.
"""

import pytest
import subprocess
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent


class TestPipelineOrchestrator:
    """Integration tests for run_pipeline.py."""

    def test_run_pipeline_help(self):
        """Test that run_pipeline.py shows help."""
        cmd = [sys.executable, str(PROJECT_ROOT / "run_pipeline.py"), "--help"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0
        assert "Pipeline" in result.stdout or "Pipeline" in result.stderr

    def test_run_pipeline_dry_run(self):
        """Test run_pipeline.py with dry-run flag."""
        examples_dir = PROJECT_ROOT / "examples" / "mobile_devices"
        
        # Check if example files exist
        if not (examples_dir / "scoperte_automatiche.csv.gz").exists():
            pytest.skip("Example data files not found")
        if not (examples_dir / "stations.csv").exists():
            pytest.skip("Example data files not found")
        
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "run_pipeline.py"),
            "--run-name", "test_integration",
            "--delta-csv", str(examples_dir / "scoperte_automatiche.csv.gz"),
            "--stations-csv", str(examples_dir / "stations.csv"),
            "--start-phase", "2",
            "--dry-run"
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0

    def test_run_pipeline_invalid_input(self):
        """Test run_pipeline.py with invalid input."""
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "run_pipeline.py"),
            "--run-name", "test_invalid",
            "--delta-csv", "/nonexistent/file.csv"
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert result.returncode != 0


class TestMobilePipeline:
    """Integration tests for mobile_analysis_pipeline.py."""

    def test_mobile_pipeline_help(self):
        """Test that mobile_analysis_pipeline.py shows help."""
        cmd = [sys.executable, str(PROJECT_ROOT / "mobile" / "mobile_analysis_pipeline.py"), "--help"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0
        assert "Pipeline" in result.stdout or "Pipeline" in result.stderr

    def test_mobile_pipeline_dry_run(self):
        """Test mobile_analysis_pipeline.py with dry-run flag."""
        examples_dir = PROJECT_ROOT / "examples" / "mobile_devices"
        
        # Check if example files exist
        if not (examples_dir / "scoperte_automatiche.csv.gz").exists():
            pytest.skip("Example data files not found")
        if not (examples_dir / "stations.csv").exists():
            pytest.skip("Example data files not found")
        
        output_dir = PROJECT_ROOT / "runs" / "mobile_test_integration"
        
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "mobile" / "mobile_analysis_pipeline.py"),
            "--input-csv", str(examples_dir / "scoperte_automatiche.csv.gz"),
            "--stations-csv", str(examples_dir / "stations.csv"),
            "--output-dir", str(output_dir),
            "--dry-run"
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0


class TestWorkflow:
    """End-to-end workflow tests."""

    def test_workflow_test_script(self):
        """Test the workflow test script."""
        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "test_workflow.py"),
            "--validation-only",
            "--verbose"
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        # Should complete (return code 0 or 1 depending on test results)
        assert result.returncode in [0, 1]


class TestScriptImports:
    """Test that all scripts can be imported."""

    def test_import_run_pipeline(self):
        """Test importing run_pipeline."""
        sys.path.insert(0, str(PROJECT_ROOT))
        import run_pipeline
        assert run_pipeline is not None

    def test_import_mobile_pipeline(self):
        """Test importing mobile_analysis_pipeline."""
        sys.path.insert(0, str(PROJECT_ROOT / "mobile"))
        import mobile_analysis_pipeline
        assert mobile_analysis_pipeline is not None

    def test_import_all_scripts(self):
        """Test importing all scripts from scripts directory."""
        scripts_dir = PROJECT_ROOT / "scripts"
        sys.path.insert(0, str(scripts_dir))
        
        scripts_to_test = [
            "test_workflow",
            "encrypt_credentials",
            "export_all_stations",
            "export_station_csv",
            "analyze_delta_map",
            "analyze_trace",
            "compute_station_stats",
            "select_stations_spatial",
            "download_cf_waveforms",
            "attach_coords_to_deltas",
            "compute_mseed_deltas",
            "export_missing_stations",
            "invert_station_locations",
            "prepare_science_deltas",
            "view_delta_maps"
        ]
        
        for script in scripts_to_test:
            try:
                __import__(script)
            except ImportError as e:
                # Some scripts may have optional dependencies
                if "No module named" not in str(e):
                    raise
