# ============================================================
# detector.py — Detecção do gesto de aceno com MediaPipe Hands
# ============================================================

import logging
import time
from collections import deque

import cv2
import mediapipe as mp

from config import (
    MIN_HAND_SIZE,
    WAVE_ALTERNATIONS,
    WAVE_MIN_MOVEMENT,
    WAVE_WINDOW_SECONDS,
)

logger = logging.getLogger(__name__)


class WaveDetector:
    """
    Detecta o gesto de aceno horizontal a partir de frames BGR.

    Usa uma máquina de estados incremental: cada alternância é contada
    no exato frame em que acontece, sem reprocessar o histórico inteiro.
    Isso garante detecção correta tanto para acenos lentos quanto rápidos.
    """

    # Tempo máximo sem mão antes de zerar o estado (segundos).
    # Evita reset por frames isolados de perda de tracking em movimentos rápidos.
    _NO_HAND_GRACE = 0.20

    def __init__(self):
        self._mp_hands = mp.solutions.hands
        self._hands = self._mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=1,
            min_detection_confidence=0.6,
            min_tracking_confidence=0.3,  # mais tolerante em movimentos rápidos
        )
        # Histórico para o debug_camera (visualização do gráfico)
        self._wrist_history = deque()

        # Estado incremental da detecção
        self._last_extreme   = None
        self._last_direction = None
        self._alternations   = 0
        self._last_hand_seen = 0.0    # timestamp da última mão detectada

    # ------------------------------------------------------------------
    # Interface pública
    # ------------------------------------------------------------------

    def process_frame(self, frame):
        """
        Analisa um frame e retorna True se o gesto de aceno for detectado.
        """
        self._purge_old_history()

        rgb     = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self._hands.process(rgb)

        if not results.multi_hand_landmarks:
            # Grace period: só reseta após mão ausente por _NO_HAND_GRACE segundos.
            # Evita zerar alternâncias por 1-2 frames de tracking perdido
            # durante movimentos rápidos ou amplos.
            if time.time() - self._last_hand_seen > self._NO_HAND_GRACE:
                self._reset_wave_state()
            return False

        hand_landmarks   = results.multi_hand_landmarks[0]
        xs               = [lm.x for lm in hand_landmarks.landmark]
        hand_width_ratio = max(xs) - min(xs)

        if hand_width_ratio < MIN_HAND_SIZE:
            return False

        self._last_hand_seen = time.time()
        wrist_x = hand_landmarks.landmark[0].x
        self._wrist_history.append((self._last_hand_seen, wrist_x))

        return self._update_state(wrist_x)

    def reset(self):
        """Limpa histórico e estado. Chamar após detecção ou ao sair do cooldown."""
        self._wrist_history.clear()
        self._reset_wave_state()
        self._last_hand_seen = 0.0
        logger.debug("Detector resetado")

    def release(self):
        """Libera recursos do MediaPipe."""
        self._hands.close()
        logger.debug("Recursos do MediaPipe liberados")

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _reset_wave_state(self):
        """Zera a máquina de estados sem tocar no histórico."""
        self._last_extreme   = None
        self._last_direction = None
        self._alternations   = 0

    def _update_state(self, wrist_x):
        """
        Atualiza a máquina de estados com a nova posição do pulso.

        Conta alternâncias incrementalmente — não reprocessa o histórico.
        Retorna True quando WAVE_ALTERNATIONS é atingido.
        """
        if self._last_extreme is None:
            self._last_extreme = wrist_x
            return False

        delta = wrist_x - self._last_extreme

        if abs(delta) < WAVE_MIN_MOVEMENT:
            return False

        direction = "r" if delta > 0 else "l"

        if self._last_direction is not None and direction != self._last_direction:
            self._alternations += 1

        self._last_direction = direction
        self._last_extreme   = wrist_x

        if self._alternations >= WAVE_ALTERNATIONS:
            logger.info(
                f"Aceno detectado! Alternâncias={self._alternations} "
                f"(mín={WAVE_ALTERNATIONS})"
            )
            self._wrist_history.clear()
            self._reset_wave_state()
            return True

        return False

    def _purge_old_history(self):
        """
        Remove entradas antigas do histórico. Se a janela expirar completamente
        (sem mão por WAVE_WINDOW_SECONDS), zera também o estado incremental.
        """
        cutoff = time.time() - WAVE_WINDOW_SECONDS
        while self._wrist_history and self._wrist_history[0][0] < cutoff:
            self._wrist_history.popleft()

        if not self._wrist_history:
            self._reset_wave_state()
