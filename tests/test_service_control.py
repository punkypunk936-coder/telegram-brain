import unittest
from types import SimpleNamespace
from unittest.mock import patch

from service_control import process_alive


class ServiceControlTests(unittest.TestCase):
    @patch("service_control.subprocess.run")
    @patch("service_control.os.kill")
    def test_zombie_process_is_not_alive(self, kill, run):
        run.return_value = SimpleNamespace(stdout="Z    \n")

        self.assertFalse(process_alive(1234))
        kill.assert_called_once_with(1234, 0)

    @patch("service_control.subprocess.run")
    @patch("service_control.os.kill")
    def test_running_process_is_alive(self, _kill, run):
        run.return_value = SimpleNamespace(stdout="S    \n")

        self.assertTrue(process_alive(1234))


if __name__ == "__main__":
    unittest.main()
