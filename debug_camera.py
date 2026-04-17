# ============================================================
# debug_camera.py - Visualizador de debug para calibracao
# Rode com: python debug_camera.py
# Feche com: tecla Q ou ESC
# ============================================================

import time

import cv2
import mediapipe as mp
import numpy as np

from config import (
    CAMERA_HEIGHT,
    CAMERA_INDEX,
    CAMERA_WIDTH,
    MAX_NUM_HANDS,
    MIN_HAND_SIZE,
    WAVE_ALTERNATIONS,
    WAVE_MIN_MOVEMENT,
    WAVE_WINDOW_SECONDS,
)
from detector import WaveDetector

# --- Layout ---
GRAPH_W = 340
GRAPH_H = 120
HISTORY_SECS = 3.0
DETECT_FLASH = 0.8

# --- Cores BGR ---
C_GREEN = (0, 220, 80)
C_RED = (0, 60, 220)
C_YELLOW = (0, 200, 220)
C_WHITE = (255, 255, 255)
C_BLACK = (0, 0, 0)
C_GRAY = (60, 60, 60)
C_PANEL = (30, 30, 30)
C_ORANGE = (0, 140, 255)
C_CYAN = (255, 255, 0)


def _text(img, txt, pos, scale=0.55, color=C_WHITE, thickness=1):
    cv2.putText(
        img,
        txt,
        pos,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        C_BLACK,
        thickness + 2,
        cv2.LINE_AA,
    )
    cv2.putText(
        img,
        txt,
        pos,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color,
        thickness,
        cv2.LINE_AA,
    )


def _draw_landmarks(frame, hand_landmarks, hand_width_ratio, h, w, is_active=False):
    """Desenha conexoes e bbox da mao detectada."""
    mp_drawing = mp.solutions.drawing_utils
    mp_hands = mp.solutions.hands

    ok_size = hand_width_ratio >= MIN_HAND_SIZE
    color = C_CYAN if is_active else (C_GREEN if ok_size else C_RED)

    style = mp_drawing.DrawingSpec(color=color, thickness=2, circle_radius=0)
    mp_drawing.draw_landmarks(frame, hand_landmarks, mp_hands.HAND_CONNECTIONS, style, style)

    xs = [lm.x for lm in hand_landmarks.landmark]
    ys = [lm.y for lm in hand_landmarks.landmark]
    x1, x2 = int(min(xs) * w), int(max(xs) * w)
    y1, y2 = int(min(ys) * h), int(max(ys) * h)
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

    state = "ACTIVE" if is_active else ("OK" if ok_size else f"< {MIN_HAND_SIZE}")
    label = f"size={hand_width_ratio:.3f} {state}"
    _text(frame, label, (x1, max(y1 - 8, 14)), scale=0.48, color=color)


def _draw_panel(panel, detector, detected_at):
    """Preenche painel lateral com grafico e metricas do detector oficial."""
    panel[:] = C_PANEL

    now = time.time()
    ph = panel.shape[0]

    _text(panel, "DEBUG - Wave Detector", (10, 24), scale=0.58, color=C_WHITE, thickness=1)

    gy = 44
    graph = panel[gy : gy + GRAPH_H, 8 : 8 + GRAPH_W - 16]
    graph[:] = (18, 18, 18)
    cv2.rectangle(panel, (8, gy), (8 + GRAPH_W - 16, gy + GRAPH_H), C_GRAY, 1)

    _text(panel, "Posicao horizontal da mao lider", (10, gy - 5), scale=0.42, color=C_GRAY)

    gw = GRAPH_W - 16
    for frac in (0.25, 0.5, 0.75):
        gx = int(frac * gw)
        cv2.line(graph, (gx, 0), (gx, GRAPH_H), (45, 45, 45), 1)

    cutoff = now - HISTORY_SECS
    pts = [(ts, x) for ts, x in detector._wrist_history if ts >= cutoff]
    if len(pts) >= 2:
        coords = []
        for ts, x in pts:
            px = int((ts - cutoff) / HISTORY_SECS * gw)
            py = int((1.0 - x) * (GRAPH_H - 4)) + 2
            coords.append((px, py))
        for i in range(len(coords) - 1):
            cv2.line(graph, coords[i], coords[i + 1], C_YELLOW, 2)
        cv2.circle(graph, coords[-1], 4, C_WHITE, -1)

    my = gy + GRAPH_H + 18

    def metric(label, value, color=C_WHITE):
        nonlocal my
        _text(panel, f"{label}: {value}", (12, my), scale=0.50, color=color)
        my += 22

    alt_color = C_GREEN if detector._alternations >= WAVE_ALTERNATIONS else C_WHITE
    metric("Alternancias", f"{detector._alternations} / {WAVE_ALTERNATIONS}", alt_color)
    metric("Maos candidatas", detector._candidate_count)
    metric("Score atual", f"{detector._active_score:.3f}" if detector._active_wrist else "-", C_CYAN)
    metric("Estado", detector._last_selection_reason)

    my += 4
    secs_since = now - detected_at if detected_at else 999
    if secs_since < DETECT_FLASH:
        alpha = 1.0 - secs_since / DETECT_FLASH
        green = (0, int(200 * alpha), int(80 * alpha))
        cv2.rectangle(panel, (8, my), (GRAPH_W - 8, my + 30), green, -1)
        _text(panel, "ACENO DETECTADO!", (18, my + 20), scale=0.60, color=C_WHITE, thickness=1)
    else:
        cv2.rectangle(panel, (8, my), (GRAPH_W - 8, my + 30), (40, 40, 40), -1)
        _text(panel, "aguardando gesto...", (18, my + 20), scale=0.55, color=C_GRAY)
    my += 42

    my += 6
    _text(panel, "config.py", (12, my), scale=0.45, color=C_ORANGE)
    my += 18
    metric("MAX_NUM_HANDS", f"{MAX_NUM_HANDS}", C_GRAY)
    metric("MIN_HAND_SIZE", f"{MIN_HAND_SIZE}", C_GRAY)
    metric("WAVE_MIN_MOV", f"{WAVE_MIN_MOVEMENT}", C_GRAY)
    metric("WAVE_WINDOW", f"{WAVE_WINDOW_SECONDS}", C_GRAY)

    _text(panel, "Q / ESC fechar", (12, ph - 12), scale=0.42, color=C_GRAY)


def _find_active_index(hand_landmarks_list, detector):
    if detector._active_wrist is None or not hand_landmarks_list:
        return -1

    ax, ay = detector._active_wrist
    best_idx = -1
    best_dist = 999.0

    for idx, hl in enumerate(hand_landmarks_list):
        wrist = hl.landmark[0]
        dx = wrist.x - ax
        dy = wrist.y - ay
        dist = (dx * dx + dy * dy) ** 0.5
        if dist < best_dist:
            best_dist = dist
            best_idx = idx

    return best_idx


def main():
    mp_hands_mod = mp.solutions.hands
    hands = mp_hands_mod.Hands(
        static_image_mode=False,
        max_num_hands=MAX_NUM_HANDS,
        min_detection_confidence=0.6,
        min_tracking_confidence=0.3,
    )

    detector = WaveDetector()

    backend = cv2.CAP_DSHOW
    cap = cv2.VideoCapture(CAMERA_INDEX, backend)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    if not cap.isOpened():
        print(f"[ERRO] Nao foi possivel abrir camera {CAMERA_INDEX}")
        return

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"Camera aberta: {w}x{h}  |  Q ou ESC para fechar")

    detected_at = None

    panel_h = max(h, 560)
    panel = np.zeros((panel_h, GRAPH_W, 3), dtype=np.uint8)

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[AVISO] Falha ao capturar frame")
            continue

        if detector.process_frame(frame):
            detected_at = time.time()

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = hands.process(rgb)

        hand_list = results.multi_hand_landmarks or []
        active_idx = _find_active_index(hand_list, detector)

        for idx, hl in enumerate(hand_list):
            xs = [lm.x for lm in hl.landmark]
            hand_width_ratio = max(xs) - min(xs)
            _draw_landmarks(frame, hl, hand_width_ratio, h, w, is_active=(idx == active_idx))

        has_candidates = detector._candidate_count > 0
        status = "MAO LIDER" if detector._active_wrist else ("MAOS DETECTADAS" if has_candidates else "sem mao")
        color = C_CYAN if detector._active_wrist else (C_GREEN if has_candidates else C_RED)
        _text(frame, status, (10, 28), scale=0.65, color=color, thickness=1)
        _text(frame, f"candidatas={detector._candidate_count}", (10, 54), scale=0.55, color=C_WHITE)
        _text(frame, f"motivo={detector._last_selection_reason}", (10, 80), scale=0.50, color=C_GRAY)

        if detected_at and (time.time() - detected_at) < DETECT_FLASH:
            overlay = frame.copy()
            cv2.rectangle(overlay, (0, 0), (w, h), C_GREEN, -1)
            cv2.addWeighted(overlay, 0.18, frame, 0.82, 0, frame)
            _text(frame, "ACENO DETECTADO!", (w // 2 - 130, h // 2), scale=1.1, color=C_WHITE, thickness=2)

        _draw_panel(panel, detector, detected_at)

        if frame.shape[0] != panel_h:
            frame = cv2.resize(frame, (int(w * panel_h / h), panel_h))

        combined = np.hstack([frame, panel])
        cv2.imshow("Debug - Wave Detector (Q para fechar)", combined)

        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), ord("Q"), 27):
            break

    cap.release()
    hands.close()
    detector.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
