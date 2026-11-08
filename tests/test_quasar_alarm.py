import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
from typing import Callable
from unittest.mock import Mock, patch

import quasar_cli


class AlarmTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_notification_callback = (
            quasar_cli.alarm_notification_callback
        )

    def tearDown(self) -> None:
        quasar_cli.alarm_notification_callback = (
            self.original_notification_callback
        )

    def test_set_alarm_notifies_when_its_timer_fires(self) -> None:
        class FakeTimer:
            def __init__(
                self,
                delay: float,
                function: Callable[..., None],
                args: list[str],
            ) -> None:
                self.delay = delay
                self.function = function
                self.args = args
                self.daemon = False
                self.started = False
                timers.append(self)

            def start(self) -> None:
                self.started = True

        timers: list[FakeTimer] = []
        notification = Mock()
        quasar_cli.alarm_notification_callback = notification
        target_time = (datetime.now() + timedelta(minutes=3)).strftime("%H:%M")

        with (
            patch.object(quasar_cli.threading, "Timer", FakeTimer),
            patch.object(quasar_cli.platform, "system", return_value="Windows"),
            patch.object(quasar_cli, "winsound", create=True) as winsound,
            patch.object(quasar_cli.time, "sleep"),
            redirect_stdout(StringIO()),
        ):
            result = quasar_cli.set_alarm(target_time, "테스트 알람")
            self.assertIn("알림이 울리도록 설정했습니다", result)
            self.assertEqual(len(timers), 1)
            timer = timers[0]
            self.assertTrue(timer.started)
            self.assertTrue(timer.daemon)
            timer.function(*timer.args)

        self.assertEqual(winsound.Beep.call_count, 3)
        notification.assert_called_once_with("테스트 알람")

    def test_alarm_notifies_even_if_system_beep_fails(self) -> None:
        notification = Mock()
        quasar_cli.alarm_notification_callback = notification
        stderr = StringIO()

        with (
            patch.object(quasar_cli.platform, "system", return_value="Windows"),
            patch.object(
                quasar_cli,
                "winsound",
                create=True,
            ) as winsound,
            patch.object(quasar_cli.time, "sleep"),
            redirect_stdout(StringIO()),
            redirect_stderr(stderr),
        ):
            winsound.Beep.side_effect = RuntimeError("audio device unavailable")
            quasar_cli._trigger_alarm("테스트 알람")

        notification.assert_called_once_with("테스트 알람")
        self.assertIn("알람 소리 재생 실패", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
