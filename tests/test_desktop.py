"""Tray lifecycle and edge geometry, without manipulating the user's desktop."""

import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock, patch

from quick_access.desktop import (DesktopIntegration, WindowsTray, WindowMotion, edge_position,
                                  ball_position, ball_frame, restore_message_name, request_restore)


class DesktopTests(unittest.TestCase):
    def controller(self):
        value = DesktopIntegration.__new__(DesktopIntegration)
        value.root = Mock()
        value.root.state.return_value = "normal"
        value.root.attributes.return_value = 1.0
        value.root.grab_current.return_value = None
        value.app = SimpleNamespace(clipboard_panel=None)
        value.tray = SimpleNamespace(available=True, close=Mock(), process_events=Mock())
        value.side, value.collapsed = "left", False
        value.expanded_rect = (0, 50, 800, 650)
        value.work_area = Mock(return_value=(0, 0, 1920, 1040))
        value.move = Mock()
        value.rect = Mock(return_value=value.expanded_rect)
        value._clipboard_visible = False
        value.ball_enabled = True
        value._poll_job = value._drag_job = value._unmap_job = None
        value._grace_until = 0
        value._outside_since = None
        value.ball_size = 44
        value.ball_position = (0, 0)
        value._ball_side = None
        value._ball_tuck_job = None
        value._ball_drag_origin = None
        value._ball_offset = (0, 0)
        value._ball_exposure = 1.0
        value.ball_canvas = Mock()
        value.enabled = Mock()
        value.enabled.get.return_value = True
        value.user = Mock()
        value.user.GetAsyncKeyState.return_value = 0
        value.motion = Mock()
        value.motion.closed = False
        value.motion.active.return_value = False

        def finish_motion(name, start, target, apply, on_complete=None, **options):
            apply(target)
            if on_complete:
                on_complete()
        value.motion.start.side_effect = finish_motion
        return value

    def test_snap_left_right_and_keep_window_inside_monitor_work_area(self):
        work = (0, 0, 1920, 1040)
        self.assertEqual(edge_position((12, 500, 812, 1100), work), ("left", (0, 440, 800, 1040)))
        self.assertEqual(edge_position((1110, -15, 1910, 585), work), ("right", (1120, 0, 1920, 600)))
        self.assertIsNone(edge_position((300, 100, 1100, 700), work))
        self.assertIsNone(edge_position((0, 0, 1920, 1040), work))
        self.assertEqual(edge_position((-1910, 20, -1110, 620), (-1920, 0, 0, 1080))[0], "left")

    def test_collapsing_hides_window_without_a_strip_and_restores_exact_position(self):
        value = self.controller()
        value.collapse()
        value.root.withdraw.assert_called_once()
        value.move.assert_called_with((0, 50, 800, 650))
        self.assertTrue(value.collapsed)
        value.expand()
        value.move.assert_called_with((0, 50, 800, 650))
        self.assertFalse(value.collapsed)
        value.side, value.expanded_rect = "right", (1120, 50, 1920, 650)
        value.collapse()
        value.move.assert_called_with((1120, 50, 1920, 650))

    def test_top_snap_chooses_nearest_edge_and_respects_other_monitors(self):
        self.assertEqual(edge_position((400, 12, 1200, 612), (0, 0, 1920, 1040)),
                         ("top", (400, 0, 1200, 600)))
        self.assertEqual(edge_position((18, 4, 818, 604), (0, 0, 1920, 1040))[0], "top")
        self.assertEqual(edge_position((-1600, -1068, -800, -468), (-1920, -1080, 0, 0)),
                         ("top", (-1600, -1080, -800, -480)))
        self.assertIsNone(edge_position((400, 100, 1200, 700), (0, 0, 1920, 1040)))

    def test_top_collapse_keeps_ball_at_top_and_uses_original_monitor(self):
        value = self.controller()
        value.side, value.expanded_rect = "top", (400, 0, 1200, 600)
        value.rect.return_value = value.expanded_rect
        value._show_ball = Mock()
        value.collapse()
        value.root.withdraw.assert_called_once()
        value._show_ball.assert_called_once_with((778, 0), work=(0, 0, 1920, 1040))
        value.expand()
        self.assertFalse(value.collapsed)
        value.move.assert_called_with((400, 0, 1200, 600))
        value.root.attributes.assert_any_call("-alpha", 1.0)

    def test_close_to_tray_hides_and_show_restores_instead_of_destroying(self):
        value = self.controller()
        value.hide()
        value.root.withdraw.assert_called_once()
        value.root.destroy.assert_not_called()
        value.show()
        value.root.deiconify.assert_called_once()
        value.root.lift.assert_called_once()

    def test_ball_toggle_persists_updates_both_menus_and_hides_or_restores_ball(self):
        value = self.controller()
        value.app.workspace = Mock()
        value.app.update_floating_ball_button = Mock()
        value.menu = Mock()
        value.ball_menu_index = 2
        value.tab = Mock()
        value.toggle_floating_ball()
        self.assertFalse(value.ball_enabled)
        value.app.workspace.set_floating_ball.assert_called_once_with(False)
        value.tab.withdraw.assert_called_once()
        value.menu.entryconfigure.assert_called_once_with(2, label="打开悬浮球")
        value.app.update_floating_ball_button.assert_called_once_with(False)

        value.root.state.return_value = "normal"
        value.toggle_floating_ball()
        self.assertTrue(value.ball_enabled)
        value.app.workspace.set_floating_ball.assert_called_with(True)
        value.menu.entryconfigure.assert_called_with(2, label="关闭悬浮球")
        value.app.update_floating_ball_button.assert_called_with(True)
        value.root.deiconify.assert_not_called()

    def test_disabling_ball_cannot_leave_an_edge_collapsed_main_window_hidden(self):
        value = self.controller()
        value.ball_enabled = False
        value.side = "left"
        value.collapse()
        value.motion.start.assert_not_called()
        self.assertFalse(value.collapsed)

    def test_clipboard_stays_open_when_main_is_closed_or_edge_collapsed(self):
        value = self.controller()
        panel = Mock()
        panel.state.return_value = "normal"
        panel.hide.return_value = True
        value.app = SimpleNamespace(clipboard_panel=panel, open_clipboard=Mock())
        value.hide()
        panel.hide.assert_not_called()
        value.show()
        value.app.open_clipboard.assert_not_called()
        value.side = "left"
        value.collapse()
        panel.hide.assert_not_called()

    def test_unsaved_clipboard_stays_available_when_main_is_hidden(self):
        value = self.controller()
        value.app.clipboard_panel = Mock()
        value.app.clipboard_panel.state.return_value = "normal"
        value.app.clipboard_panel.hide.return_value = False
        value.hide()
        value.root.withdraw.assert_called_once()
        value.app.clipboard_panel.hide.assert_not_called()

    def test_explicit_exit_cancels_timers_and_removes_tray_icon(self):
        value = self.controller()
        value._poll_job, value._drag_job = "poll", "drag"
        value.close()
        value.root.after_cancel.assert_any_call("poll")
        value.root.after_cancel.assert_any_call("drag")
        value.tray.close.assert_called_once()
        value.motion.close.assert_called_once()

    def test_restore_messages_are_scoped_to_data_directory(self):
        self.assertEqual(restore_message_name("C:/Data"), restore_message_name("c:/data"))
        self.assertNotEqual(restore_message_name("C:/Data"), restore_message_name("C:/Other"))

    def test_restore_targets_the_hidden_tray_window_directly(self):
        user = Mock()
        user.FindWindowW.return_value = 77
        user.RegisterWindowMessageW.return_value = 0xC001
        user.PostMessageW.return_value = True
        with patch("quick_access.desktop.ctypes.WinDLL", return_value=user):
            self.assertTrue(request_restore("C:/Data"))
        user.FindWindowW.assert_called_once_with(restore_message_name("C:/Data"), None)
        user.PostMessageW.assert_called_once_with(77, 0xC001, 0, 0)

    def test_native_callbacks_only_queue_and_never_reenter_tk(self):
        tray = WindowsTray.__new__(WindowsTray)
        tray.events = deque()
        tray.root, tray.show, tray._popup = Mock(), Mock(), Mock()
        tray.callback_message, tray.restore_message = 10, 11
        tray._message(77, 11, 0, 0)
        tray._message(77, 10, 0, 0x0202)
        tray._message(77, 10, 0, 0x0205)
        tray.root.after.assert_not_called()
        tray.show.assert_not_called()
        self.assertEqual(list(tray.events), ["show", "show", "menu"])
        tray.process_events()
        self.assertEqual(tray.show.call_count, 2)
        tray._popup.assert_called_once()

    def test_ball_snaps_to_nearest_edge_and_stays_above_taskbar(self):
        work = (0, 0, 1920, 1040)
        self.assertEqual(ball_position(1400, 1200, 44, work), (1876, 996))
        self.assertEqual(ball_position(150, -20, 44, work), (150, 0))
        self.assertEqual(ball_position(900, 70, 44, work), (900, 0))
        self.assertEqual(ball_position(500, 300, 44, work, False), (500, 300))
        self.assertEqual(ball_position(-1850, 500, 44, (-1920, 0, 0, 1080)), (-1920, 500))

    def test_resizing_ball_keeps_right_edge_and_does_not_open_main_window(self):
        value = self.controller()
        value.ball_size_var = Mock()
        value.ball_size_var.get.return_value = 88
        value.app.workspace = Mock()
        value.tab = Mock()
        value.tab.state.return_value = "normal"
        value.ball_position = (1876, 400)
        value._ball_side = "right"
        value._ball_hwnd = Mock(return_value=77)
        value._draw_ball = Mock()
        value._show_ball = Mock()
        value.resize_ball()
        value.app.workspace.set_ball_size.assert_called_once_with(88)
        self.assertEqual(value.ball_size, 88)
        self.assertEqual(value.ball_position, (1832, 400))
        value._show_ball.assert_called_once()
        value.root.deiconify.assert_not_called()

    def test_close_shows_ball_and_click_restores_main_without_hover_activation(self):
        value = self.controller()
        value._show_ball = Mock()
        value.hide()
        value._show_ball.assert_called_once()
        value.tab = Mock()
        value._ball_drag_origin = (10, 10, 0, 0)
        value._ball_dragged = False
        value._ball_release()
        value.root.deiconify.assert_called_once()
        value.tab.withdraw.assert_called_once()

    def test_minimizing_main_window_switches_to_the_floating_ball(self):
        value = self.controller()
        value.root.state.return_value = "iconic"
        value._show_ball = Mock()
        value._sync_hidden_ball()
        value.root.withdraw.assert_not_called()
        value._show_ball.assert_called_once()

    def test_taskbar_restore_hides_ball_without_forcing_another_restore(self):
        value = self.controller()
        value.tab = Mock()
        value._sync_hidden_ball()
        value.tab.withdraw.assert_called_once()
        value.root.deiconify.assert_not_called()

    def test_unmap_event_is_deferred_and_ignored_for_child_windows(self):
        value = self.controller()
        value.root.after_idle.side_effect = lambda callback: "unmap-job"
        value._main_unmapped(SimpleNamespace(widget=Mock()))
        value.root.after_idle.assert_not_called()
        value._main_unmapped(SimpleNamespace(widget=value.root))
        value.root.after_idle.assert_called_once_with(value._sync_hidden_ball)
        self.assertEqual(value._unmap_job, "unmap-job")

    def test_dragging_ball_does_not_open_main_and_saves_snapped_position(self):
        value = self.controller()
        value._ball_drag_origin = (10, 10, 0, 0)
        value._ball_dragged = True
        value.ball_size = 44
        value._ball_hwnd = Mock(return_value=77)
        value.enabled = Mock()
        value.enabled.get.return_value = True
        value.user = Mock()
        value._move_ball = Mock()
        value._ball_release()
        value.root.deiconify.assert_not_called()
        self.assertEqual(value.ball_position, (0, 0))
        value._move_ball.assert_called_once_with(0, 0)

    def test_half_ball_is_cropped_inside_each_edge_including_negative_monitors(self):
        self.assertEqual(ball_frame((0, 300), 44, "left", 0.5), ((0, 300, 22, 44), (-22, 0)))
        self.assertEqual(ball_frame((1876, 300), 44, "right", 0.5), ((1898, 300, 22, 44), (0, 0)))
        self.assertEqual(ball_frame((-900, -1080), 44, "top", 0.5),
                         ((-900, -1080, 44, 22), (0, -22)))
        self.assertEqual(ball_frame((0, 300), 44, "left", 1.0), ((0, 300, 44, 44), (0, 0)))
        self.assertEqual(ball_frame((500, 300), 44, None, 0.5), ((500, 300, 44, 44), (0, 0)))

    def test_hover_only_reveals_ball_and_drag_interrupts_reveal(self):
        value = self.controller()
        value._ball_side, value._ball_exposure = "left", 0.5
        value._ball_hwnd = Mock(return_value=77)
        value._ball_enter()
        self.assertEqual(value._ball_exposure, 1.0)
        value.root.deiconify.assert_not_called()
        value._ball_press()
        value.motion.cancel.assert_any_call("reveal")
        self.assertIsNone(value._ball_side)

    def test_main_window_waits_after_pointer_leaves_and_resets_on_return(self):
        value = self.controller()
        position = [1000, 800]
        def cursor(pointer):
            pointer._obj.x, pointer._obj.y = position
            return True
        value.user.GetCursorPos.side_effect = cursor
        with patch("quick_access.desktop.time.monotonic", return_value=10):
            value._poll()
        self.assertFalse(value.collapsed)
        with patch("quick_access.desktop.time.monotonic", return_value=10.4):
            value._poll()
        self.assertFalse(value.collapsed)
        position[:] = [100, 100]
        with patch("quick_access.desktop.time.monotonic", return_value=10.5):
            value._poll()
        self.assertIsNone(value._outside_since)
        position[:] = [1000, 800]
        with patch("quick_access.desktop.time.monotonic", return_value=11):
            value._poll()
        self.assertFalse(value.collapsed)
        with patch("quick_access.desktop.time.monotonic", return_value=11.7):
            value._poll()
        self.assertTrue(value.collapsed)

    def test_animation_configure_events_do_not_start_another_snap(self):
        value = self.controller()
        value.motion.active.return_value = True
        value.user.GetAsyncKeyState.return_value = 0x8000
        value._configured(SimpleNamespace(widget=value.root))
        value.root.after.assert_not_called()
        self.assertEqual(value.side, "left")


class FakeClock:
    def __init__(self):
        self.now, self.counter = 0.0, 0
        self.jobs = {}

    def after(self, milliseconds, callback):
        self.counter += 1
        self.jobs[self.counter] = (self.now + milliseconds / 1000, callback)
        return self.counter

    def after_cancel(self, job):
        self.jobs.pop(job, None)

    def advance(self, seconds):
        end = self.now + seconds
        while self.jobs:
            job = min(self.jobs, key=lambda key: self.jobs[key][0])
            due, callback = self.jobs[job]
            if due > end:
                break
            self.now = due
            self.jobs.pop(job)
            callback()
        self.now = end


class MotionTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.motion = WindowMotion(self.clock)
        self.timer = patch("quick_access.desktop.time.monotonic", side_effect=lambda: self.clock.now)
        self.timer.start()
        self.addCleanup(self.timer.stop)

    def test_motion_has_intermediate_frames_no_overshoot_and_exact_final_state(self):
        frames, done = [], Mock()
        self.motion.start("ball", (0, 1.0), (400, 0.0), frames.append, done)
        self.clock.advance(0.1)
        self.assertGreater(len(frames), 3)
        self.assertTrue(0 < frames[-1][0] < 400)
        self.assertTrue(self.motion.active("ball"))
        self.clock.advance(0.2)
        self.assertEqual(frames[-1], (400, 0.0))
        self.assertEqual(sorted(frame[0] for frame in frames), [frame[0] for frame in frames])
        self.assertTrue(all(0 <= frame[1] <= 1 for frame in frames))
        self.assertFalse(self.motion.active("ball"))
        done.assert_called_once()

    def test_new_drag_cancels_pending_frames_and_the_old_completion(self):
        frames, done = [], Mock()
        self.motion.start("ball", (0,), (400,), frames.append, done)
        stale = next(iter(self.clock.jobs.values()))[1]
        self.clock.advance(0.05)
        self.motion.cancel("ball")
        count = len(frames)
        stale()
        self.clock.advance(1)
        self.assertEqual(len(frames), count)
        done.assert_not_called()

    def test_replacing_reveal_and_quitting_cancels_all_animation_work(self):
        first_done, second_done = Mock(), Mock()
        self.motion.start("reveal", (0.5,), (1.0,), Mock(), first_done)
        self.clock.advance(0.05)
        self.motion.start("reveal", (0.8,), (0.5,), Mock(), second_done)
        self.motion.start("main", (0,), (100,), Mock())
        self.motion.close()
        self.clock.advance(1)
        first_done.assert_not_called()
        second_done.assert_not_called()
        self.assertFalse(self.clock.jobs)
        self.motion.start("ball", (0,), (100,), Mock())
        self.assertFalse(self.clock.jobs)
