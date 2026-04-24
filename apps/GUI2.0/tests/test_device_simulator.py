from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from device_simulations.device_simulator import DeviceSimulatorService, SimulatorConfig, parse_args


class DeviceSimulatorTests(unittest.TestCase):
    def test_uncapped_flag_forces_all_rate_caps_off(self) -> None:
        service = DeviceSimulatorService(SimulatorConfig(uncapped=True))

        self.assertTrue(service._uncapped(2000.0))
        self.assertTrue(service._uncapped(1000.0))

    def test_nonpositive_rate_is_treated_as_uncapped(self) -> None:
        service = DeviceSimulatorService(SimulatorConfig())

        self.assertTrue(service._uncapped(0.0))
        self.assertTrue(service._uncapped(-1.0))
        self.assertFalse(service._uncapped(1.0))

    def test_parse_args_accepts_uncapped(self) -> None:
        with patch.object(sys, "argv", ["device_simulator.py", "--uncapped"]):
            args = parse_args()

        self.assertTrue(args.uncapped)


if __name__ == "__main__":
    unittest.main()
