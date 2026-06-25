"""Unit tests for Roman APT population tools."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

# Add parent directory to path so we can import from tools/ and helpers/
sys.path.insert(0, str(Path(__file__).parent.parent))

from helpers.rcs_apt_helper import (
    format_flight_lines,
    generate_output,
    read_review_table,
    lampstate_for_visit,
)


class TestRcsAptHelper(unittest.TestCase):
    """Tests for rcs_apt_helper functions."""

    def test_format_flight_lines_single_led(self):
        """Test formatting a single LED exposure line."""
        lines = format_flight_lines(
            Nexp=2,
            start_exp=0,
            nres=13,
            LED1="LED16",
            flux1=500.0,
            precharge_duration1=30.0,
            precharge_flux1=100.0,
        )
        self.assertEqual(len(lines), 2)
        self.assertIn("LED16=500", lines[0])
        self.assertIn("pre=30,100", lines[0])
        # Second exposure should not have precharge
        self.assertIn("LED16=500", lines[1])
        self.assertNotIn("pre=", lines[1])

    def test_format_flight_lines_dual_led(self):
        """Test formatting dual LED exposure lines."""
        lines = format_flight_lines(
            Nexp=1,
            start_exp=0,
            nres=13,
            LED1="LED12",
            flux1=250.0,
            LED2="LED22",
            flux2=250.0,
        )
        self.assertEqual(len(lines), 1)
        self.assertIn("LED12=250", lines[0])
        self.assertIn("LED22=250", lines[0])

    def test_format_flight_lines_no_precharge_nan(self):
        """Test that NaN precharge values are skipped."""
        lines = format_flight_lines(
            Nexp=1,
            start_exp=0,
            nres=13,
            LED1="LED16",
            flux1=500.0,
            precharge_duration1=np.nan,
            precharge_flux1=np.nan,
        )
        self.assertNotIn("pre=", lines[0])

    def test_format_flight_lines_start_exp_offset(self):
        """Test that start_exp parameter correctly offsets exposure numbering."""
        lines = format_flight_lines(
            Nexp=2, start_exp=5, nres=13, LED1="LED16", flux1=500.0
        )
        self.assertIn("6, R=13", lines[0])
        self.assertIn("7, R=13", lines[1])

    def test_read_review_table_csv(self):
        """Test reading a CSV review table."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", delete=False
        ) as f:
            # Write header with all required columns
            f.write(
                "VISIT_NUMBER,NEXP,RESULTANTS_PER_EXPOSURE,MA_TABLE,"
                "SRCS_LEDB1,SRCS_LEDB1_FLUX,SRCS_LEDB1_PRECHARGE_DURATION,SRCS_LEDB1_PRECHARGE_FLUX,"
                "SRCS_LEDB2,SRCS_LEDB2_FLUX,SRCS_LEDB2_PRECHARGE_DURATION,SRCS_LEDB2_PRECHARGE_FLUX,"
                "WFI_SRCS_LEDB1_CLEANUP,WFI_SRCS_LEDB2_CLEANUP\n"
            )
            f.write(
                "1,10,13,MA_TABLE_REV_G,LED16,500,30,100,,,,,NO,NO\n"
            )
            f.write(
                "1,10,13,MA_TABLE_REV_G,LED16,500,30,100,,,,,NO,NO\n"
            )
            temp_path = f.name

        try:
            df = read_review_table(temp_path)
            self.assertEqual(len(df), 2)
            self.assertEqual(df["VISIT_NUMBER"].iloc[0], 1)
            self.assertEqual(df["NEXP"].iloc[0], 10)
        finally:
            os.unlink(temp_path)

    def test_lampstate_for_visit_dark(self):
        """Test that dark visits generate lines without LEDs."""
        dark_rows = pd.DataFrame(
            {
                "NEXP": [2],
                "RESULTANTS_PER_EXPOSURE": [13],
                "SRCS_LEDB1": [np.nan],
                "SRCS_LEDB1_FLUX": [np.nan],
                "SRCS_LEDB1_PRECHARGE_DURATION": [np.nan],
                "SRCS_LEDB1_PRECHARGE_FLUX": [np.nan],
                "SRCS_LEDB2": [np.nan],
                "SRCS_LEDB2_FLUX": [np.nan],
                "SRCS_LEDB2_PRECHARGE_DURATION": [np.nan],
                "SRCS_LEDB2_PRECHARGE_FLUX": [np.nan],
                "WFI_SRCS_LEDB1_CLEANUP": ["NO"],
                "WFI_SRCS_LEDB2_CLEANUP": ["NO"],
            }
        )
        lines, next_exp = lampstate_for_visit(dark_rows, start_next_exp=0)
        # Dark visits generate exposure lines without any LED information
        self.assertEqual(len(lines), 2)
        # Lines should just have R= (resultants) but no LED
        self.assertIn("R=13", lines[0])
        self.assertNotIn("LED", lines[0])

    def test_lampstate_for_visit_single_led(self):
        """Test LampState formatting for single LED."""
        lit_rows = pd.DataFrame(
            {
                "NEXP": [2],
                "RESULTANTS_PER_EXPOSURE": [13],
                "SRCS_LEDB1": ["LED16"],
                "SRCS_LEDB1_FLUX": [500.0],
                "SRCS_LEDB1_PRECHARGE_DURATION": [30.0],
                "SRCS_LEDB1_PRECHARGE_FLUX": [100.0],
                "SRCS_LEDB2": [np.nan],
                "SRCS_LEDB2_FLUX": [np.nan],
                "SRCS_LEDB2_PRECHARGE_DURATION": [np.nan],
                "SRCS_LEDB2_PRECHARGE_FLUX": [np.nan],
                "WFI_SRCS_LEDB1_CLEANUP": ["NO"],
                "WFI_SRCS_LEDB2_CLEANUP": ["NO"],
            }
        )
        lines, next_exp = lampstate_for_visit(lit_rows, start_next_exp=0)
        self.assertGreater(len(lines), 0)
        # Join all lines to search for LED and flux
        lampstate_text = "\n".join(lines)
        self.assertIn("LED16", lampstate_text)
        self.assertIn("500", lampstate_text)


class TestPopulateDarkcalApt(unittest.TestCase):
    """Tests for populate_darkcal_apt.py functions."""

    def test_imports(self):
        """Test that populate_darkcal_apt can be imported."""
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
            import populate_darkcal_apt

            self.assertTrue(hasattr(populate_darkcal_apt, "build_observation"))
        except ImportError as e:
            self.fail(f"Failed to import populate_darkcal_apt: {e}")


class TestPopulateLoloApt(unittest.TestCase):
    """Tests for populate_lolo_apt.py functions."""

    def test_imports(self):
        """Test that populate_lolo_apt can be imported."""
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
            import populate_lolo_apt

            self.assertTrue(hasattr(populate_lolo_apt, "main"))
        except ImportError as e:
            self.fail(f"Failed to import populate_lolo_apt: {e}")


class TestGenerateDarkcalGopup(unittest.TestCase):
    """Tests for generate_darkcal_gopup.py functions."""

    def test_imports(self):
        """Test that generate_darkcal_gopup can be imported."""
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
            import generate_darkcal_gopup

            self.assertTrue(hasattr(generate_darkcal_gopup, "main"))
        except ImportError as e:
            self.fail(f"Failed to import generate_darkcal_gopup: {e}")


class TestDiagnoseCsv(unittest.TestCase):
    """Tests for diagnose_csv.py functions."""

    def test_imports(self):
        """Test that diagnose_csv can be imported."""
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
            import diagnose_csv

            self.assertTrue(hasattr(diagnose_csv, "main"))
        except ImportError as e:
            self.fail(f"Failed to import diagnose_csv: {e}")


class TestExampleData(unittest.TestCase):
    """Tests that verify example data exists and is readable."""

    def test_cfa_examples_exist(self):
        """Test that CFA example files exist."""
        example_dir = Path(__file__).parent.parent / "examples" / "cfa"
        self.assertTrue(
            example_dir.exists(), f"CFA examples directory not found: {example_dir}"
        )
        # Check for at least one example file
        csv_files = list(example_dir.glob("data/*.csv"))
        self.assertTrue(
            len(csv_files) > 0, "No CSV files in examples/cfa/data/"
        )

    def test_lolo_examples_exist(self):
        """Test that LOLO example files exist."""
        example_dir = Path(__file__).parent.parent / "examples" / "lolo"
        self.assertTrue(
            example_dir.exists(), f"LOLO examples directory not found: {example_dir}"
        )
        csv_files = list(example_dir.glob("*.csv"))
        self.assertTrue(len(csv_files) > 0, "No CSV files in examples/lolo/")

    def test_seed_files_exist(self):
        """Test that seed APT files exist."""
        seeds_dir = Path(__file__).parent.parent / "seeds"
        self.assertTrue(seeds_dir.exists(), f"Seeds directory not found: {seeds_dir}")
        apt_files = list(seeds_dir.glob("*.apt"))
        self.assertTrue(len(apt_files) > 0, "No APT files in seeds/")


if __name__ == "__main__":
    unittest.main()
