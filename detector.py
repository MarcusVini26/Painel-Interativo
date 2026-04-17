# ============================================================
# detector.py - Deteccao do gesto de aceno com MediaPipe Hands
# ============================================================

import logging
import math
import time
from collections import deque

import cv2
import mediapipe as mp

from config import (
    MAX_NUM_HANDS,
    MIN_HAND_SIZE,
    WAVE_ALTERNATIONS,
    WAVE_MIN_MOVEMENT,
    WAVE_WINDOW_SECONDS,
)

logger = logging.getLogger(__name__)


class WaveDetector:
    """
    Detecta aceno horizontal em paralelo para varias maos.

    Regra atual: dispara quando a primeira mao completar um aceno valido,
    independentemente de proximidade.
    """

    # Track e considerado perdido apos esse tempo sem match.
    _NO_HAND_GRACE = 0.45
    # Distancia maxima entre punhos normalizados para associar ao mesmo track.
    _TRACK_MATCH_MAX_DIST = 0.40

    def __init__(self):
        self._mp_hands = mp.solutions.hands
        self._hands = self._mp_hands.Hands(
            static_image_mode=False,
            max_num_hands=MAX_NUM_HANDS,
            min_detection_confidence=0.6,
            min_tracking_confidence=0.3,
        )

        # Historico para debug do movimento horizontal da mao lider.
        self._wrist_history = deque()

        # Estado exposto para debug_camera.
        self._alternations = 0
        self._last_hand_seen = 0.0
        self._active_wrist = None
        self._active_score = 0.0
        self._candidate_count = 0
        self._last_selection_reason = "none"

        # Tracks de maos em paralelo.
        self._tracks = {}
        self._next_track_id = 1

        # Rate limit de logs por evento.
        self._last_event_log = {}

    # ------------------------------------------------------------------
    # Interface publica
    # ------------------------------------------------------------------

    def process_frame(self, frame):
        """Analisa um frame e retorna True quando detectar aceno."""
        now = time.time()
        self._purge_old_history(now)

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self._hands.process(rgb)

        candidates = self._extract_candidates(results)
        self._candidate_count = len(candidates)

        matches = self._match_candidates_to_tracks(candidates, now)
        self._cleanup_stale_tracks(now)

        if not matches:
            self._active_wrist = None
            self._active_score = 0.0
            self._alternations = 0
            self._last_selection_reason = "none"
            return False

        self._last_hand_seen = now

        detections = []
        leader = None

        for track, cand in matches:
            if self._update_track_state(track, cand["wrist_x"], now):
                detections.append((track, cand))

            if leader is None:
                leader = (track, cand)
            else:
                l_track, l_cand = leader
                # Lider de acompanhamento para debug:
                # prioriza quem esta mais avancado no aceno e,
                # em empate, quem iniciou antes (sem vies por proximidade).
                if (
                    track["alternations"] > l_track["alternations"]
                    or (
                        track["alternations"] == l_track["alternations"]
                        and (
                            track["started_at"] < l_track["started_at"]
                            or (
                                track["started_at"] == l_track["started_at"]
                                and track["id"] < l_track["id"]
                            )
                        )
                    )
                ):
                    leader = (track, cand)

        if detections:
            winner_track, winner_cand = self._pick_detection_winner(detections)
            self._active_wrist = (winner_cand["wrist_x"], winner_cand["wrist_y"])
            self._active_score = winner_cand["score"]
            self._alternations = winner_track["alternations"]
            self._wrist_history.append((now, winner_cand["wrist_x"]))
            self._last_selection_reason = "wave_first_valid"
            self._log_event(
                "WAVE_DETECTED",
                (
                    "WAVE_DETECTED: track=%s alternancias=%s score=%.3f"
                    % (winner_track["id"], winner_track["alternations"], winner_cand["score"])
                ),
            )
            return True

        leader_track, leader_cand = leader
        self._active_wrist = (leader_cand["wrist_x"], leader_cand["wrist_y"])
        self._active_score = leader_cand["score"]
        self._alternations = leader_track["alternations"]
        self._wrist_history.append((now, leader_cand["wrist_x"]))
        self._last_selection_reason = "tracking"
        return False

    def reset(self):
        """Limpa historico e estado interno."""
        self._wrist_history.clear()
        self._alternations = 0
        self._last_hand_seen = 0.0
        self._active_wrist = None
        self._active_score = 0.0
        self._candidate_count = 0
        self._last_selection_reason = "reset"
        self._tracks.clear()
        logger.debug("Detector resetado")

    def release(self):
        """Libera recursos do MediaPipe."""
        self._hands.close()
        logger.debug("Recursos do MediaPipe liberados")

    # ------------------------------------------------------------------
    # Internos: candidatos e tracking
    # ------------------------------------------------------------------

    def _extract_candidates(self, results):
        candidates = []
        if not results.multi_hand_landmarks:
            return candidates

        for hand_landmarks in results.multi_hand_landmarks:
            xs = [lm.x for lm in hand_landmarks.landmark]
            size_ratio = max(xs) - min(xs)
            if size_ratio < MIN_HAND_SIZE:
                continue

            wrist = hand_landmarks.landmark[0]
            candidates.append(
                {
                    "wrist_x": wrist.x,
                    "wrist_y": wrist.y,
                    "score": size_ratio,
                }
            )

        return candidates

    def _match_candidates_to_tracks(self, candidates, now):
        # Somente tracks recentes participam do matching.
        viable_track_ids = [
            tid for tid, tr in self._tracks.items() if now - tr["last_seen"] <= self._NO_HAND_GRACE
        ]
        unused_tracks = set(viable_track_ids)
        unmatched_candidates = list(candidates)
        matches = []

        # Association global por menor distancia para reduzir troca de identidade
        # quando existem duas ou mais maos simultaneas.
        pair_distances = []
        for tid in viable_track_ids:
            tr = self._tracks[tid]
            for cand in candidates:
                dx = cand["wrist_x"] - tr["wrist_x"]
                dy = cand["wrist_y"] - tr["wrist_y"]
                dist = math.hypot(dx, dy)
                if dist <= self._TRACK_MATCH_MAX_DIST:
                    pair_distances.append((dist, tid, cand))

        pair_distances.sort(key=lambda item: item[0])

        matched_candidate_ids = set()
        for _, tid, cand in pair_distances:
            cand_id = id(cand)
            if tid not in unused_tracks or cand_id in matched_candidate_ids:
                continue

            unused_tracks.remove(tid)
            matched_candidate_ids.add(cand_id)
            tr = self._tracks[tid]
            tr["wrist_x"] = cand["wrist_x"]
            tr["wrist_y"] = cand["wrist_y"]
            tr["score"] = cand["score"]
            tr["last_seen"] = now
            matches.append((tr, cand))

        unmatched_candidates = [cand for cand in candidates if id(cand) not in matched_candidate_ids]
        for cand in unmatched_candidates:
            tr = self._create_track(cand, now)
            matches.append((tr, cand))
            self._log_event("TRACK_CREATED", f"TRACK_CREATED: id={tr['id']}")

        return matches

    def _create_track(self, cand, now):
        tid = self._next_track_id
        self._next_track_id += 1

        track = {
            "id": tid,
            "wrist_x": cand["wrist_x"],
            "wrist_y": cand["wrist_y"],
            "score": cand["score"],
            "last_seen": now,
            "last_extreme": None,
            "last_direction": None,
            "alternations": 0,
            "started_at": now,
            "completed_at": None,
        }
        self._tracks[tid] = track
        return track

    def _cleanup_stale_tracks(self, now):
        stale = [tid for tid, tr in self._tracks.items() if now - tr["last_seen"] > self._NO_HAND_GRACE]
        for tid in stale:
            self._tracks.pop(tid, None)
            self._log_event("TRACK_LOST", f"TRACK_LOST: id={tid}")

    # ------------------------------------------------------------------
    # Internos: maquina de aceno por track
    # ------------------------------------------------------------------

    def _update_track_state(self, track, wrist_x, now):
        if track["last_extreme"] is None:
            track["last_extreme"] = wrist_x
            return False

        delta = wrist_x - track["last_extreme"]
        if abs(delta) < WAVE_MIN_MOVEMENT:
            return False

        direction = "r" if delta > 0 else "l"

        if track["last_direction"] is not None and direction != track["last_direction"]:
            track["alternations"] += 1

        track["last_direction"] = direction
        track["last_extreme"] = wrist_x

        if track["alternations"] >= WAVE_ALTERNATIONS:
            track["completed_at"] = now
            return True

        return False

    @staticmethod
    def _pick_detection_winner(detections):
        # Se mais de uma completar no mesmo frame, escolhe a que iniciou antes.
        return min(
            detections,
            key=lambda item: (
                item[0]["completed_at"] or 0.0,
                item[0]["started_at"],
                item[0]["id"],
            ),
        )

    def _log_event(self, event_name, message):
        now = time.time()
        last = self._last_event_log.get(event_name, 0.0)
        if now - last >= 1.0:
            logger.info(message)
            self._last_event_log[event_name] = now

    def _purge_old_history(self, now):
        cutoff = now - WAVE_WINDOW_SECONDS
        while self._wrist_history and self._wrist_history[0][0] < cutoff:
            self._wrist_history.popleft()
