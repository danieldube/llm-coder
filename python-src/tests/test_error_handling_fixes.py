#!/usr/bin/env python3
"""
Test to validate that bare except clauses have been fixed
"""
import unittest
import tempfile
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent))

from llm_coding.cli import wait_for_ssh_connection, get_systemd_service_info
from llm_coding.runtime import down, _try_acquire_lock

class TestFixedBareExceptClauses(unittest.TestCase):
    
    def test_wait_for_ssh_connection_handles_exceptions(self):
        """Test that wait_for_ssh_connection properly handles exceptions"""
        # This would previously use bare except:, now it catches Exception
        # We'll test it doesn't crash with any exception
        result = wait_for_ssh_connection("localhost", 22, timeout=1)
        self.assertIsInstance(result, bool)  # Should return boolean
        
    def test_get_systemd_service_info_handles_exceptions(self):
        """Test that get_systemd_service_info properly handles exceptions"""
        # This would previously use bare except:, now it catches Exception  
        # Mock the subprocess call to simulate an error
        with patch('llm_coding.cli.run_command') as mock_run:
            mock_run.side_effect = Exception("Simulated subprocess error")
            result = get_systemd_service_info("nonexistent.service")
            self.assertEqual(result, ("unknown", ""))
    
    def test_try_acquire_lock_with_timeout(self):
        """Test the new lock acquisition with timeout"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            lock_path = Path(tmp_dir) / "test.lock"
            
            # Test with valid path and timeout
            result = _try_acquire_lock(lock_path, timeout_seconds=1)
            self.assertIsNotNone(result)
            result.close()  # Release the lock
            
            # Test with a non-existent directory (should fail gracefully)
            bad_path = Path("/non/existent/path/test.lock")
            result = _try_acquire_lock(bad_path, timeout_seconds=1)
            self.assertIsNone(result)

if __name__ == '__main__':
    unittest.main()