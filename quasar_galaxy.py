"""Quasar 은하(오브) 위젯 모음.

기존 quasar_desktop.py 의 OrbWidget / BlackHoleOverlay 를 대체합니다.

- OrbWidget       : 케플러식 차등 회전 원반 + 나선팔 + 중력 렌즈 링 + 양극 제트
- BlackHoleOverlay: 보낸 메시지가 글자 단위로 소용돌이치며 블랙홀에 빨려 들어가는 효과
"""

from __future__ import annotations

import math
import random
import time
from functools import lru_cache
from typing import Any

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QLabel, QWidget

# 메시지가 블랙홀에 빨려 들어가는 시간(ms). 오브와 오버레이가 같은 값을 공유합니다.
SWALLOW_MS = 1300
GALAXY_TRANSITION_INTERVAL_MS = 33
GALAXY_EXPANDED_INTERVAL_MS = 33

# 원반 색: 중심(고온) → 바깥(저온)
DISK_STOPS = (
    (0.0, (218, 228, 255)),
    (0.22, (116, 157, 255)),
    (0.58, (119, 105, 242)),
    (1.0, (78, 64, 166)),
)
CANVAS_BACKGROUND_STOPS = (
    (0.0, "#0b1022"),
    (0.52, "#070a16"),
    (1.0, "#0a091b"),
)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _smoothstep(t: float) -> float:
    t = _clamp(t)
    return t * t * (3 - 2 * t)


def _mix(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _gradient_color(stops: tuple, t: float) -> tuple[float, float, float]:
    t = _clamp(t)
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            k = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return tuple(_mix(c0[i], c1[i], k) for i in range(3))  # type: ignore[return-value]
    return stops[-1][1]


def _rgba(rgb: tuple, alpha: float) -> QColor:
    return QColor(int(rgb[0]), int(rgb[1]), int(rgb[2]), int(_clamp(alpha, 0, 255)))


@lru_cache(maxsize=2048)
def _particle_pen(rgb: tuple, alpha: int, width: float) -> QPen:
    return QPen(
        _rgba(rgb, alpha),
        width,
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap,
    )


class OrbWidget(QWidget):
    clicked = Signal()

    PARTICLE_COUNT = 160
    BASE_PARTICLE_COUNT = 110
    EXPANDED_PARTICLE_COUNT = 100
    INNER = 27.0  # 원반 안쪽 반지름
    OUTER = 64.0  # 원반 바깥 반지름
    HOLE = 19.0  # 사건의 지평선 반지름
    TILT = 0.34  # 원반 기울기 (작을수록 옆에서 본 모습)
    def __init__(self) -> None:
        super().__init__()
        self.active = False
        self.clock = 0.0  # 실제 경과 시간
        self.spin = 0.0  # 회전 누적량 (빨려 들어갈 때 빨라짐)
        self.jet_power = 0.0  # 0=대기, 1=답변 생성 중
        self.flare = 0.0  # 메시지를 삼킨 직후의 폭발적 밝기
        self.pull = 0.0  # 블랙홀이 원반을 끌어당기는 정도
        self.transparent_card = False
        self.particle_count = self.BASE_PARTICLE_COUNT
        self.particle_size_scale = 1.0
        self.stage = "idle"
        self.stage_elapsed = 0.0
        self._last = time.perf_counter()
        self.setMinimumSize(120, 210)
        self.setMaximumWidth(144)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("눌러서 은하 화면 열기")

        rng = random.Random(11)
        self.particles: list[tuple] = []
        for _ in range(self.PARTICLE_COUNT):
            t = rng.random() ** 1.3  # 안쪽에 더 촘촘하게
            radius = self.INNER + t * (self.OUTER - self.INNER)
            omega = 1.7 * (self.INNER / radius) ** 1.5  # 케플러 차등 회전
            clump = rng.random() < 0.04
            size = rng.uniform(1.8, 2.6) if clump else rng.uniform(0.9, 1.5)
            bright = rng.uniform(0.5, 0.9) * (1 - 0.45 * t * t) * (0.65 if clump else 1.0)
            lift = rng.uniform(0, 45)
            rgb = tuple(min(255, c + lift) for c in _gradient_color(DISK_STOPS, t))
            self.particles.append(
                (radius, rng.uniform(0, math.tau), omega, size, bright, rgb)
            )
        self.stars = [
            (rng.random(), rng.random(), rng.uniform(0.5, 1.0), rng.uniform(0, math.tau))
            for _ in range(10)
        ]
        self._particle_tail_offsets = [
            (math.cos(omega * 0.14), math.sin(omega * 0.14))
            for _, _, omega, _, _, _ in self.particles
        ]
        self._cached_card_size: tuple[int, int] | None = None
        self._cached_card_path: QPainterPath | None = None
        self._cached_sky_brush: QBrush | None = None

        self.timer = QTimer(self)
        self.timer.setInterval(24)
        self.timer.timeout.connect(self._advance_animation)

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    # ------------------------------------------------------------------ API
    def set_active(self, active: bool) -> None:
        self.active = active

    def start_submission(self) -> None:
        self.stage = "suction"
        self.stage_elapsed = 0.0

    def hole_center(self) -> QPoint:
        """블랙홀 중심 (위젯 좌표). 오버레이의 도착점으로 쓰세요."""
        return QPoint(self.width() // 2, self.height() // 2)

    def disk_emission_point(self) -> QPoint:
        """A point on the visible near side of the accretion disk."""
        return QPoint(
            round(self.width() / 2 + self.OUTER * 0.62),
            round(self.height() / 2 + self.OUTER * self.TILT * 0.55),
        )

    def showEvent(self, event: Any) -> None:
        self._last = time.perf_counter()
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event: Any) -> None:
        self.timer.stop()
        super().hideEvent(event)

    # ------------------------------------------------------------ animation
    def _advance_animation(self) -> None:
        now = time.perf_counter()
        dt = min(0.05, now - self._last)
        self._last = now
        self.clock += dt

        if self.stage == "suction":
            self.stage_elapsed += dt * 1000
            progress = min(1.0, self.stage_elapsed / SWALLOW_MS)
            self.pull = _smoothstep(progress)
            if progress >= 1.0:  # 메시지를 다 삼킴 → 제트 폭발
                self.stage = "idle"
                self.flare = 1.0
        else:
            self.pull = max(0.0, self.pull - dt * 2.2)
        self.flare = max(0.0, self.flare - dt / 1.1)

        target = 1.0 if self.active else 0.0
        rate = 3.0 if target > self.jet_power else 1.6
        self.jet_power += (target - self.jet_power) * (1 - math.exp(-rate * dt))

        self.spin += dt * (1.0 + 2.2 * self.pull + 0.7 * self.jet_power + 1.5 * self.flare)
        self.update()

    # -------------------------------------------------------------- drawing
    def paintEvent(self, _event: Any) -> None:
        w, h = self.width(), self.height()
        self._ensure_static_paint_resources(w, h)
        cx, cy = w / 2, h / 2
        s = min(w / 144.0, h / 210.0)
        hole = self.HOLE * s * (1 + 0.1 * self.pull)
        power = _clamp(self.jet_power * 0.85 + self.flare * 0.6)
        energy = 0.8 + 0.3 * self.jet_power + 0.55 * self.flare + 0.35 * self.pull

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 우주 배경 카드 (밝은 패널 위에서도 빛이 살아나도록)
        painter.setClipPath(self._cached_card_path)
        if not self.transparent_card:
            painter.fillRect(self.rect(), self._cached_sky_brush)

        painter.setPen(Qt.PenStyle.NoPen)
        for fx, fy, size, phase in self.stars:
            twinkle = 0.55 + 0.45 * math.sin(self.clock * 1.6 + phase)
            painter.setBrush(QColor(200, 215, 255, int(40 + 110 * twinkle)))
            painter.drawEllipse(QPointF(fx * w, fy * h), size * s, size * s)

        plus = QPainter.CompositionMode.CompositionMode_Plus
        normal = QPainter.CompositionMode.CompositionMode_SourceOver

        back, front = self._build_disk(cx, cy, s, energy)

        painter.setCompositionMode(plus)
        self._draw_jets(painter, cx, cy, s, hole, power)
        self._draw_glow(painter, cx, cy, s, False, energy)  # 블랙홀 뒤쪽 원반
        self._draw_batch(painter, back)
        self._draw_halo(painter, cx, cy, s, hole, energy)

        painter.setCompositionMode(normal)  # 사건의 지평선
        horizon = QRadialGradient(QPointF(cx - hole * 0.25, cy - hole * 0.3), hole * 1.4)
        horizon.setColorAt(0.0, QColor(10, 11, 18))
        horizon.setColorAt(0.7, QColor(2, 3, 8))
        horizon.setColorAt(1.0, QColor(0, 0, 0))
        painter.setPen(QPen(_rgba((255, 220, 170), 70 * energy), 1.0))
        painter.setBrush(QBrush(horizon))
        painter.drawEllipse(QPointF(cx, cy), hole, hole)

        painter.setCompositionMode(plus)  # 블랙홀 앞을 가로지르는 원반
        self._draw_glow(painter, cx, cy, s, True, energy)
        self._draw_batch(painter, front)
        painter.end()

    def _ensure_static_paint_resources(self, width: int, height: int) -> None:
        size = (width, height)
        if self._cached_card_size == size:
            return
        card = QPainterPath()
        card.addRoundedRect(QRectF(0, 0, width, height), 18, 18)
        sky = QLinearGradient(0, 0, 0, height)
        sky.setColorAt(0.0, QColor(9, 12, 28))
        sky.setColorAt(0.5, QColor(4, 6, 15))
        sky.setColorAt(1.0, QColor(10, 9, 27))
        self._cached_card_size = size
        self._cached_card_path = card
        self._cached_sky_brush = QBrush(sky)

    def _build_disk(self, cx: float, cy: float, s: float, energy: float):
        back: list[tuple] = []
        front: list[tuple] = []
        squeeze = 1 - 0.22 * self.pull
        tilt = self.TILT
        spin = self.spin

        for index in range(self.particle_count):
            radius, a0, omega, size, bright, rgb = self.particles[index]
            tail_cos, tail_sin = self._particle_tail_offsets[index]
            r = radius * squeeze * s
            a = a0 + omega * spin
            ca, sa = math.cos(a), math.sin(a)
            alpha = 175 * bright * energy * (1 + 0.6 * ca)  # 도플러 빔: 다가오는 쪽이 더 밝음
            item = (
                cx + (ca * tail_cos + sa * tail_sin) * r,
                cy + (sa * tail_cos - ca * tail_sin) * r * tilt,
                cx + ca * r,
                cy + sa * r * tilt,
                rgb,
                alpha,
                size * s * self.particle_size_scale,
            )
            (front if sa > 0 else back).append(item)

        return back, front

    @staticmethod
    def _draw_batch(painter: QPainter, batch: list[tuple]) -> None:
        for x1, y1, x2, y2, rgb, alpha, width in batch:
            painter.setPen(_particle_pen(rgb, int(_clamp(alpha, 0, 255)), width))
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def _draw_glow(
        self, painter: QPainter, cx: float, cy: float, s: float, front: bool, energy: float
    ) -> None:
        squeeze = 1 - 0.22 * self.pull
        inner = self.INNER * squeeze * s
        outer = self.OUTER * s
        ry = outer * self.TILT
        painter.save()
        if front:
            clip = QRectF(cx - outer - 2, cy, outer * 2 + 4, ry + 4)
        else:
            clip = QRectF(cx - outer - 2, cy - ry - 4, outer * 2 + 4, ry + 4)
        painter.setClipRect(clip, Qt.ClipOperation.IntersectClip)
        painter.translate(cx, cy)
        painter.scale(1.0, self.TILT)

        k = inner / outer
        grad = QRadialGradient(QPointF(0, 0), outer)
        grad.setColorAt(0.0, _rgba(DISK_STOPS[0][1], 0))
        grad.setColorAt(k * 0.85, _rgba(DISK_STOPS[0][1], 0))
        grad.setColorAt(k, _rgba(DISK_STOPS[0][1], 190 * energy))
        grad.setColorAt(k + (1 - k) * 0.30, _rgba(DISK_STOPS[1][1], 105 * energy))
        grad.setColorAt(k + (1 - k) * 0.65, _rgba(DISK_STOPS[2][1], 50 * energy))
        grad.setColorAt(1.0, _rgba(DISK_STOPS[3][1], 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(grad))
        painter.drawEllipse(QPointF(0, 0), outer, outer)
        painter.restore()

    def _draw_halo(
        self, painter: QPainter, cx: float, cy: float, s: float, hole: float, energy: float
    ) -> None:
        reach = self.OUTER * s
        k = hole / reach
        halo = QRadialGradient(QPointF(cx, cy), reach)
        halo.setColorAt(0.0, _rgba((116, 157, 255), 0))
        halo.setColorAt(k * 0.9, _rgba((116, 157, 255), 34 * energy))
        halo.setColorAt(k, _rgba((116, 157, 255), 72 * energy))
        halo.setColorAt(k + (1 - k) * 0.4, _rgba((137, 113, 255), 24 * energy))
        halo.setColorAt(1.0, _rgba((137, 113, 255), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(halo))
        painter.drawEllipse(QPointF(cx, cy), reach, reach)

    def _draw_jets(
        self,
        painter: QPainter,
        cx: float,
        cy: float,
        s: float,
        hole: float,
        power: float,
    ) -> None:
        max_len = max(24.0, self.height() / 2 - hole - 3)
        flare = min(1.0, self.flare)
        length = max_len * (0.30 + 0.70 * power)
        bright = 0.30 + 0.70 * power + 0.30 * flare
        root = (2.2 + 2.2 * power) * s
        mid = (3.6 + 7.5 * power + 4.0 * flare) * s * (0.96 + 0.04 * math.sin(self.clock * 11))
        for direction in (-1, 1):
            base_y = cy + direction * hole * 0.8
            mid_y = cy + direction * (hole + length * 0.42)
            tip_y = cy + direction * (hole + length)

            grad = QLinearGradient(cx, base_y, cx, tip_y)
            grad.setColorAt(0.0, _rgba((190, 205, 255), 210 * bright))
            grad.setColorAt(0.45, _rgba((115, 150, 255), 125 * bright))
            grad.setColorAt(1.0, _rgba((125, 110, 255), 0))
            path = QPainterPath()
            path.moveTo(cx - root, base_y)
            path.quadTo(cx - mid, mid_y, cx - 0.7 * s, tip_y)
            path.lineTo(cx + 0.7 * s, tip_y)
            path.quadTo(cx + mid, mid_y, cx + root, base_y)
            path.closeSubpath()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(grad))
            painter.drawPath(path)

            core = QLinearGradient(cx, cy + direction * hole, cx, tip_y)
            core.setColorAt(0.0, _rgba((222, 231, 255), 235 * bright))
            core.setColorAt(0.7, _rgba((160, 185, 255), 125 * bright))
            core.setColorAt(1.0, _rgba((150, 170, 255), 0))
            painter.setPen(
                QPen(
                    QBrush(core),
                    (1.0 + 1.5 * power) * s,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                )
            )
            painter.drawLine(
                QPointF(cx, cy + direction * hole * 0.8), QPointF(cx, tip_y)
            )


class _GalaxyOrbitLayer(QWidget):
    def __init__(self, transition: GalaxyTransitionOverlay) -> None:
        super().__init__(transition)
        self.transition = transition
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, _event: Any) -> None:
        transition = self.transition
        progress = _smoothstep(transition.progress)
        if progress <= 0:
            return

        orb_rect = transition.orb.geometry()
        cx, cy = orb_rect.center().x(), orb_rect.center().y()
        scale = min(orb_rect.width() / 144.0, orb_rect.height() / 210.0)
        outer = OrbWidget.OUTER * scale
        energy = 0.8 + 0.3 * transition.orb.jet_power + 0.55 * transition.orb.flare
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_Plus
        )
        hole_radius = OrbWidget.HOLE * scale
        glow_radius = (OrbWidget.HOLE + OrbWidget.INNER * 0.25) * scale
        hole_edge = hole_radius / glow_radius
        disk_glow = QRadialGradient(QPointF(cx, cy), glow_radius)
        disk_glow.setColorAt(0.0, _rgba((239, 244, 255), 0))
        disk_glow.setColorAt(
            hole_edge * 0.94,
            _rgba((239, 244, 255), 0),
        )
        disk_glow.setColorAt(
            min(1.0, hole_edge + 0.08),
            _rgba((174, 112, 255), 78 * energy * progress),
        )
        disk_glow.setColorAt(
            min(1.0, hole_edge + 0.2),
            _rgba((132, 91, 232), 38 * energy * progress),
        )
        disk_glow.setColorAt(1.0, _rgba((126, 137, 255), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(disk_glow))
        painter.drawEllipse(QPointF(cx, cy), glow_radius, glow_radius)
        painter.setPen(Qt.PenStyle.NoPen)
        for phase, radius, speed, size, brightness in transition.orbiting_stars:
            angle = phase + transition.clock * speed
            orbit_radius = outer * radius
            tail_angle = angle - speed * 0.14
            x1 = cx + math.cos(tail_angle) * orbit_radius
            y1 = cy + math.sin(tail_angle) * orbit_radius * OrbWidget.TILT
            x2 = cx + math.cos(angle) * orbit_radius
            y2 = cy + math.sin(angle) * orbit_radius * OrbWidget.TILT
            twinkle = 0.62 + 0.38 * math.sin(transition.clock * 2.4 + phase)
            t = (radius - 1.05) / (1.88 - 1.05)
            rgb = _gradient_color(DISK_STOPS, t)
            alpha = (
                175
                * brightness
                * twinkle
                * energy
                * progress
                * (1 + 0.6 * math.cos(angle))
            )
            painter.setPen(
                QPen(
                    _rgba(rgb, alpha),
                    size * scale,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                )
            )
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        for fx, fy, angle, delay, hue, size in transition.fragments:
            fragment_progress = _clamp((progress - delay) / (1 - delay))
            if fragment_progress <= 0:
                continue
            travel = _smoothstep(fragment_progress)
            orbit_radius = outer * (1.12 + hue * 0.72)
            target_x = cx + math.cos(angle + transition.clock * 0.5) * orbit_radius
            target_y = cy + math.sin(angle + transition.clock * 0.5) * orbit_radius * 0.48
            x = _mix(fx * self.width(), target_x, travel)
            y = _mix(fy * self.height(), target_y, travel)
            fragment_size = size * (1 - travel * 0.72)
            color = QColor(143, 91, 232, int(155 * (1 - travel * 0.35) * progress))
            painter.setBrush(color)
            painter.drawRoundedRect(
                QRectF(
                    x - fragment_size * 1.1,
                    y - fragment_size * 0.55,
                    fragment_size * 2.2,
                    fragment_size * 1.1,
                ),
                fragment_size * 0.55,
                fragment_size * 0.55,
            )
            if fragment_progress > 0.82:
                painter.setBrush(QColor(196, 160, 255, int(175 * progress)))
                painter.drawEllipse(QPointF(x, y), 0.85, 0.85)

        painter.setPen(QColor(178, 186, 225, int(190 * progress)))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        painter.drawText(
            QRectF(0, self.height() - 54, self.width(), 24),
            Qt.AlignmentFlag.AlignCenter,
            "QUASAR  ·  은하를 눌러 돌아가기  ·  ESC",
        )
        painter.end()


class GalaxyTransitionOverlay(QWidget):
    expanded_changed = Signal(bool)

    def __init__(
        self,
        parent: QWidget,
        source_orb: OrbWidget,
        source_layout: Any,
    ) -> None:
        super().__init__(parent)
        self.source_orb = source_orb
        self.source_layout = source_layout
        self.progress = 0.0
        self.clock = 0.0
        self._animation_origin = 0.0
        self._animation_target = 0.0
        self._animation_started = 0.0
        self._animation_complete = False
        self._source_geometry = QRectF()
        self._duration = 1.45
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.orb = source_orb
        orbit_rng = random.Random(17)
        self.orbiting_stars = []
        for _ in range(76):
            radius = orbit_rng.uniform(1.05, 1.88)
            disk_radius = OrbWidget.OUTER * radius
            speed = 1.7 * (OrbWidget.INNER / disk_radius) ** 1.5
            self.orbiting_stars.append(
                (
                    orbit_rng.random() * math.tau,
                    radius,
                    speed,
                    orbit_rng.uniform(0.9, 1.5),
                    orbit_rng.uniform(0.5, 0.9),
                )
            )
        rng = random.Random(29)
        self.fragments = [
            (
                rng.uniform(0.2, 0.88),
                rng.uniform(0.12, 0.86),
                rng.random() * math.tau,
                rng.uniform(0, 0.24),
                rng.random(),
                rng.uniform(1.5, 3),
            )
            for _ in range(48)
        ]
        self.orbit_layer = _GalaxyOrbitLayer(self)
        self.conversation_status = QLabel(
            "은하 대화를 준비하고 있습니다...",
            self,
        )
        self.conversation_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.conversation_status.setWordWrap(True)
        self.conversation_status.setStyleSheet(
            "color: #e7eaff; background: rgba(7, 10, 22, 185);"
            "border: 1px solid rgba(155, 145, 255, 90);"
            "border-radius: 12px; padding: 10px 18px;"
            "font-size: 13px; font-weight: 600;"
        )
        self.timer = QTimer(self)
        self.timer.setInterval(GALAXY_TRANSITION_INTERVAL_MS)
        self.timer.timeout.connect(self._advance)
        self.hide()

    def set_conversation_status(self, status: str) -> None:
        self.conversation_status.setText(status)

    def resizeEvent(self, event: Any) -> None:
        self.conversation_status.setGeometry(
            32,
            28,
            max(1, self.width() - 64),
            54,
        )
        self.orbit_layer.setGeometry(self.rect())
        super().resizeEvent(event)

    def toggle(self) -> None:
        if not self.isVisible():
            self.setGeometry(self.parentWidget().rect())
            source_point = self.source_orb.mapTo(self.parentWidget(), QPoint(0, 0))
            self._source_geometry = QRectF(
                source_point.x(),
                source_point.y(),
                self.source_orb.width(),
                self.source_orb.height(),
            )
            self.source_layout.removeWidget(self.source_orb)
            self.source_orb.setParent(self)
            self.source_orb.setMinimumSize(1, 1)
            self.source_orb.setMaximumWidth(420)
            self.source_orb.transparent_card = True
            self.source_orb.particle_count = OrbWidget.BASE_PARTICLE_COUNT
            self.source_orb.particle_size_scale = 1.0
            self.orb.setGeometry(self._source_geometry.toRect())
            self.orb.show()
            self.progress = 0.0
            self._animation_origin = 0.0
            self._animation_target = 1.0
            self._animation_complete = False
            self.show()
            self.raise_()
            self.setFocus(Qt.FocusReason.OtherFocusReason)
            self.orbit_layer.setGeometry(self.rect())
        else:
            self._animation_origin = self.progress
            self._animation_target = 0.0 if self._animation_target > 0.5 else 1.0
            self._animation_complete = False

        self.expanded_changed.emit(self._animation_target > 0.5)
        self.orb.timer.setInterval(GALAXY_TRANSITION_INTERVAL_MS)
        self.orb.raise_()
        self.orbit_layer.raise_()
        self.conversation_status.raise_()
        self._animation_started = time.perf_counter()
        self.timer.setInterval(GALAXY_TRANSITION_INTERVAL_MS)
        self.timer.start()

    def _advance(self) -> None:
        self.clock += self.timer.interval() / 1000
        if self._animation_complete:
            if self._animation_target > 0.5:
                self.timer.setInterval(GALAXY_EXPANDED_INTERVAL_MS)
                self.update()
                self.orbit_layer.update()
            return

        elapsed = (time.perf_counter() - self._animation_started) / self._duration
        amount = _smoothstep(elapsed)
        self.progress = _mix(
            self._animation_origin,
            self._animation_target,
            amount,
        )
        self.orb.particle_count = round(
            _mix(
                OrbWidget.BASE_PARTICLE_COUNT,
                OrbWidget.EXPANDED_PARTICLE_COUNT,
                self.progress,
            )
        )
        self.orb.particle_size_scale = 1.0

        target_scale = min(
            380 / 144.0,
            (self.width() - 80) / 144.0,
            (self.height() - 80) / 210.0,
        )
        target_width = 144.0 * target_scale
        target_height = 210.0 * target_scale
        target_rect = QRectF(
            (self.width() - target_width) / 2,
            (self.height() - target_height) / 2,
            target_width,
            target_height,
        )
        start = self._source_geometry
        scale = _mix(
            start.width() / 144.0,
            target_scale,
            self.progress,
        )
        center_x = _mix(
            start.center().x(),
            target_rect.center().x(),
            self.progress,
        )
        center_y = _mix(
            start.center().y(),
            target_rect.center().y(),
            self.progress,
        )
        self.orb.setGeometry(
            QRectF(
                center_x - 72 * scale,
                center_y - 105 * scale,
                144 * scale,
                210 * scale,
            ).toRect()
        )
        self.update()
        self.orbit_layer.update()

        if elapsed >= 1.0 and not self._animation_complete:
            self._animation_complete = True
            self.progress = self._animation_target
            if self.progress == 0.0:
                self.timer.stop()
                self.orb.timer.setInterval(24)
                self.orb.particle_count = OrbWidget.BASE_PARTICLE_COUNT
                self.orb.particle_size_scale = 1.0
                self.orb.setParent(self.source_layout.parentWidget())
                self.orb.transparent_card = False
                self.orb.setMinimumSize(120, 210)
                self.orb.setMaximumWidth(144)
                self.source_layout.addWidget(
                    self.orb,
                    0,
                    Qt.AlignmentFlag.AlignHCenter,
                )
                self.orb.show()
                self.hide()

    def paintEvent(self, _event: Any) -> None:
        progress = _smoothstep(self.progress)
        if progress <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        background = QLinearGradient(0, 0, self.width(), self.height())
        for position, color in CANVAS_BACKGROUND_STOPS:
            background.setColorAt(position, QColor(color))
        painter.fillRect(self.rect(), QBrush(background))
        painter.setOpacity(progress)

        painter.setPen(Qt.PenStyle.NoPen)
        for fx, fy, size, phase in self.source_orb.stars:
            twinkle = 0.55 + 0.45 * math.sin(self.clock * 1.8 + phase)
            painter.setBrush(QColor(192, 207, 255, int(90 * twinkle * progress)))
            painter.drawEllipse(
                QPointF(fx * self.width(), fy * self.height()),
                size,
                size,
            )
        painter.setOpacity(1.0)
        painter.end()

    def keyPressEvent(self, event: Any) -> None:
        if event.key() == Qt.Key.Key_Escape:
            if self._animation_target > 0.5:
                self.toggle()
            event.accept()
            return
        super().keyPressEvent(event)


class BlackHoleOverlay(QWidget):
    """보낸 메시지가 글자 단위로 블랙홀을 향해 소용돌이치며 빨려 들어가는 효과."""

    animation_finished = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.glyphs: list[tuple[str, float, float]] = []  # (글자, 중심 x 오프셋, 폭)
        self.start_point = (0.0, 0.0)
        self.end_point = (0.0, 0.0)
        self.outgoing = False
        self.progress = 1.0
        self._t0 = 0.0
        self.font = QFont("Segoe UI", 11, QFont.Weight.DemiBold)
        metrics = QFontMetrics(self.font)
        self.baseline = (metrics.ascent() - metrics.descent()) / 2
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._advance)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

    def start(
        self,
        text: str,
        start_point: tuple[float, float],
        end_point: tuple[float, float],
        outgoing: bool = False,
    ) -> None:
        text = " ".join(text.split())
        if len(text) > 24:
            text = text[:23] + "…"
        metrics = QFontMetrics(self.font)
        x = -metrics.horizontalAdvance(text) / 2
        self.glyphs = []
        for ch in text:
            advance = metrics.horizontalAdvance(ch)
            if not ch.isspace():
                self.glyphs.append((ch, x + advance / 2, advance))
            x += advance
        self.outgoing = outgoing
        self.start_point = end_point if outgoing else start_point
        self.end_point = start_point if outgoing else end_point
        self.progress = 0.0
        self._t0 = time.perf_counter()
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.timer.start()
        self.update()

    def _advance(self) -> None:
        self.progress = (time.perf_counter() - self._t0) * 1000 / SWALLOW_MS
        if self.progress >= 1.0:
            self.timer.stop()
            self.hide()
            self.animation_finished.emit()
            return
        self.update()

    def _position(self, offset: float, p: float) -> tuple[float, float]:
        """Return a character's spiral position, from the text line into the core."""
        end_x, end_y = self.end_point
        rel_x = self.start_point[0] + offset - end_x
        rel_y = self.start_point[1] - end_y
        radius = math.hypot(rel_x, rel_y)
        theta = math.atan2(rel_y, rel_x)
        pull = p**1.9  # 점점 빨라지며 끌려 들어감
        radius *= 1 - pull
        theta += 2.4 * pull**1.2  # 중심에 가까울수록 더 크게 휘감김
        return end_x + math.cos(theta) * radius, end_y + math.sin(theta) * radius

    def paintEvent(self, _event: Any) -> None:
        if not self.glyphs:
            return
        progress = _clamp(self.progress)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(self.font)

        # Light flashes at the destination for incoming text and at the disk for replies.
        glow = math.sin(math.pi * progress) ** 1.5
        glow_x, glow_y = self.end_point
        halo = QRadialGradient(QPointF(glow_x, glow_y), 38)
        glow_color = QColor(147, 132, 255, int(140 * glow)) if self.outgoing else QColor(255, 196, 120, int(130 * glow))
        halo.setColorAt(0.0, glow_color)
        halo.setColorAt(1.0, QColor(glow_color.red(), glow_color.green(), glow_color.blue(), 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(halo))
        painter.drawEllipse(QPointF(glow_x, glow_y), 38, 38)

        count = len(self.glyphs)
        for index, (ch, offset, advance) in enumerate(self.glyphs):
            delay = 0.4 * index / max(1, count - 1)  # 앞글자부터 차례로 빨려 들어감
            p = _clamp((progress - delay) / 0.6)
            if p >= 1.0:
                continue
            path_progress = 1 - p if self.outgoing else p
            e = path_progress**1.9
            x, y = self._position(offset, path_progress)
            next_progress = (
                max(0.0, path_progress - 0.02)
                if self.outgoing
                else min(1.0, path_progress + 0.02)
            )
            nx, ny = self._position(offset, next_progress)
            angle = math.degrees(math.atan2(ny - y, nx - x)) if (nx, ny) != (x, y) else 0.0
            if self.outgoing:
                fade = _smoothstep(p / 0.2) * (1 - _smoothstep((p - 0.92) / 0.08))
                color = QColor(181, 190, 255)
            else:
                fade = 1 - _smoothstep((p - 0.75) / 0.25)
                warm = _smoothstep(p * 1.4)
                color = QColor(
                    int(_mix(54, 255, warm)),
                    int(_mix(125, 176, warm)),
                    int(_mix(113, 86, warm)),
                )

            if p > 0.03:  # 빛 꼬리
                painter.setPen(Qt.PenStyle.NoPen)
                for j in range(1, 5):
                    trail_progress = (
                        min(1.0, path_progress + j * 0.035)
                        if self.outgoing
                        else max(0.0, path_progress - j * 0.035)
                    )
                    gx, gy = self._position(offset, trail_progress)
                    painter.setBrush(
                        _rgba(
                            (146, 132, 255) if self.outgoing else (255, 170, 80),
                            255 * fade * (0.42 - j * 0.09) * min(1.0, p * 6),
                        )
                    )
                    size = 2.8 - j * 0.45
                    painter.drawEllipse(QPointF(gx, gy), size, size)

            painter.save()
            painter.setOpacity(fade)
            painter.translate(x, y)
            # 이동 방향으로만 늘어나고(스파게티화) 글자는 똑바로 유지한 채 회전하며 축소
            painter.rotate(angle)
            painter.scale(1 + 2.2 * e, max(0.1, 1 - 0.8 * e))
            painter.rotate(-angle + 240 * e)
            shrink_progress = e if self.outgoing else e**1.5
            shrink = 1 - 0.75 * shrink_progress
            painter.scale(shrink, shrink)
            painter.setPen(color)
            painter.drawText(QPointF(-advance / 2, self.baseline), ch)
            painter.restore()
        painter.end()
