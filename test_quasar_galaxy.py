import math
import types
import unittest
from unittest.mock import Mock

from quasar_galaxy import GalaxyTransitionOverlay, OrbWidget


class GalaxyTransitionPerformanceTests(unittest.TestCase):
    def test_settled_open_overlay_only_updates_animated_layers(self) -> None:
        overlay = types.SimpleNamespace(
            _animation_complete=True,
            _animation_target=1.0,
            clock=0.0,
            timer=types.SimpleNamespace(
                interval=lambda: 33,
                setInterval=Mock(),
            ),
            update=Mock(),
            orbit_layer=types.SimpleNamespace(update=Mock()),
        )

        GalaxyTransitionOverlay._advance(overlay)

        self.assertAlmostEqual(overlay.clock, 0.033)
        overlay.timer.setInterval.assert_called_once_with(33)
        overlay.update.assert_called_once_with()
        overlay.orbit_layer.update.assert_called_once_with()

    def test_settled_closed_overlay_does_not_schedule_redraws(self) -> None:
        overlay = types.SimpleNamespace(
            _animation_complete=True,
            _animation_target=0.0,
            clock=0.0,
            timer=types.SimpleNamespace(interval=lambda: 16),
            update=Mock(),
            orbit_layer=types.SimpleNamespace(update=Mock()),
        )

        GalaxyTransitionOverlay._advance(overlay)

        self.assertAlmostEqual(overlay.clock, 0.016)
        overlay.update.assert_not_called()
        overlay.orbit_layer.update.assert_not_called()

    def test_precomputed_particle_tails_preserve_disk_geometry(self) -> None:
        particles = [
            (27.0, 0.3, 0.08, 1.2, 0.7, (120.0, 130.0, 140.0)),
            (48.0, 2.0, 0.2, 1.0, 0.5, (80.0, 90.0, 100.0)),
        ]
        widget = types.SimpleNamespace(
            particles=particles,
            _particle_tail_offsets=[
                (math.cos(item[2] * 0.14), math.sin(item[2] * 0.14))
                for item in particles
            ],
            particle_count=len(particles),
            particle_size_scale=1.0,
            pull=0.15,
            spin=1.7,
            INNER=OrbWidget.INNER,
            TILT=OrbWidget.TILT,
        )
        actual = OrbWidget._build_disk(widget, 72.0, 105.0, 1.0, 0.9)
        expected_back = []
        expected_front = []
        squeeze = 1 - 0.22 * widget.pull
        for radius, angle, omega, size, bright, rgb in particles:
            r = radius * squeeze
            angle += omega * widget.spin
            cos_angle, sin_angle = math.cos(angle), math.sin(angle)
            tail_angle = angle - omega * 0.14
            item = (
                72.0 + math.cos(tail_angle) * r,
                105.0 + math.sin(tail_angle) * r * widget.TILT,
                72.0 + cos_angle * r,
                105.0 + sin_angle * r * widget.TILT,
                rgb,
                175 * bright * 0.9 * (1 + 0.6 * cos_angle),
                size,
            )
            (expected_front if sin_angle > 0 else expected_back).append(item)

        for actual_batch, expected_batch in zip(actual, (expected_back, expected_front)):
            self.assertEqual(len(actual_batch), len(expected_batch))
            for actual_particle, expected_particle in zip(actual_batch, expected_batch):
                for actual_value, expected_value in zip(
                    actual_particle[:4],
                    expected_particle[:4],
                ):
                    self.assertAlmostEqual(actual_value, expected_value, places=12)
                self.assertEqual(actual_particle[4], expected_particle[4])
                self.assertAlmostEqual(actual_particle[5], expected_particle[5], places=12)
                self.assertEqual(actual_particle[6], expected_particle[6])

    def test_static_orb_paint_resources_are_reused_until_resize(self) -> None:
        widget = types.SimpleNamespace(
            _cached_card_size=None,
            _cached_card_path=None,
            _cached_sky_brush=None,
        )

        OrbWidget._ensure_static_paint_resources(widget, 144, 210)
        card = widget._cached_card_path
        brush = widget._cached_sky_brush
        OrbWidget._ensure_static_paint_resources(widget, 144, 210)

        self.assertIs(widget._cached_card_path, card)
        self.assertIs(widget._cached_sky_brush, brush)

        OrbWidget._ensure_static_paint_resources(widget, 180, 260)

        self.assertEqual(widget._cached_card_size, (180, 260))
        self.assertIsNot(widget._cached_card_path, card)
        self.assertIsNot(widget._cached_sky_brush, brush)


if __name__ == "__main__":
    unittest.main()
