# ============================================================
# main.py — Painel Interativo Telbra-Ex
# Sistema de Detecção por Gesto e Vídeo
# ============================================================
"""
Ponto de entrada principal do sistema.

Arquitetura:
  - Thread principal: máquina de estados (IDLE ↔ PRESENTING)
    gerencia a reprodução de vídeos de forma sequencial e bloqueante
  - Thread da câmera: monitora a câmera em segundo plano e sinaliza
    quando um aceno é detectado, interrompendo o player via stop()

Fluxo resumido:
  IDLE  → reproduz vídeos em loop → câmera detecta aceno → stop() → PRESENTING
  PRESENTING → reproduz apresentação → cooldown → reset → IDLE
"""

import glob
import logging
import os
import random
import signal
import sys
import threading
import time

import cv2

from config import (
    CAMERA_HEIGHT,
    CAMERA_INDEX,
    CAMERA_WIDTH,
    COOLDOWN_SECONDS,
    LOOP_FOLDER,
    OVERLAY_TEXT,
    PRESENTATION_VIDEO,
)
from detector import WaveDetector
from player import VideoPlayer, get_video_duration

# ------------------------------------------------------------------
# Configuração de logging (arquivo + console)
# ------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("painel.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Estados do sistema
# ------------------------------------------------------------------
IDLE = "IDLE"
PRESENTING = "PRESENTING"


class PainelController:
    """
    Controlador principal do Painel Interativo.

    Coordena a câmera, o detector de gestos e o player de vídeo
    através de threads e eventos thread-safe.
    """

    def __init__(self):
        """Inicializa todos os componentes e estruturas de controle."""
        self.state = IDLE
        self.player = VideoPlayer()
        self.detector = WaveDetector()

        # --- Eventos de controle (thread-safe) ---
        self._wave_detected = threading.Event()  # sinalizado quando aceno é encontrado
        self._shutdown = threading.Event()        # sinalizado para encerrar tudo

        # Timestamp até o qual o cooldown está ativo
        self._cooldown_until = 0.0
        self._camera_thread = None  # threading.Thread ou None

    # ------------------------------------------------------------------
    # Loop principal
    # ------------------------------------------------------------------

    def run(self):
        """
        Inicia e executa o loop principal do sistema.

        Bloqueia até Ctrl+C ou sinal de shutdown. Ao sair,
        garante limpeza de todos os recursos.
        """
        logger.info("=" * 60)
        logger.info("  Painel Interativo Telbra-Ex — Sistema iniciando")
        logger.info(f"  Loop: '{LOOP_FOLDER}'")
        logger.info(f"  Apresentação: '{PRESENTATION_VIDEO}'")
        logger.info(f"  Câmera: índice {CAMERA_INDEX}")
        logger.info("=" * 60)

        # Iniciar thread da câmera em background
        self._camera_thread = threading.Thread(
            target=self._camera_worker,
            daemon=True,
            name="CameraThread",
        )
        self._camera_thread.start()

        if not self.player.start():
            logger.error("[Main] Falha ao iniciar o player mpv. Encerrando...")
            self._shutdown.set()
            return

        try:
            while not self._shutdown.is_set():
                if self.state == IDLE:
                    self._run_idle_cycle()
                elif self.state == PRESENTING:
                    self._run_presentation()
        except KeyboardInterrupt:
            logger.info("Interrompido pelo usuário (Ctrl+C)")
        finally:
            self._cleanup()

    # ------------------------------------------------------------------
    # Ciclos de estado
    # ------------------------------------------------------------------

    def _run_idle_cycle(self):
        """
        Executa o modo IDLE com uma única instância do mpv em loop contínuo.

        Passa todos os vídeos de uma vez para o mpv com --loop-playlist=inf,
        eliminando o flash de tela entre vídeos. Bloqueia até que um aceno
        seja detectado (stop() interrompe o mpv) ou o sistema encerre.
        """
        videos = _get_loop_videos()

        if not videos:
            abs_loop = os.path.abspath(LOOP_FOLDER)
            logger.error(
                f"[IDLE] Nenhum vídeo encontrado em: {abs_loop}"
            )
            logger.error(
                "Verifique se a pasta existe e contém arquivos .mp4, .avi, .mkv, .mov ou .wmv"
            )
            self._shutdown.wait(timeout=3)
            return

        random.shuffle(videos)
        nomes = [os.path.basename(v) for v in videos]
        logger.info(f"[IDLE] Ciclo iniciado — {len(videos)} vídeo(s) em ordem aleatória")
        logger.info(f"[IDLE] ▶ {os.path.basename(videos[0])}")
        logger.info(f"[IDLE] Playlist: {nomes}")

        # Uma única instância mpv para todos os vídeos — sem flash entre eles
        if not self.player.play_loop(videos, osd_text=OVERLAY_TEXT):
            logger.error("[IDLE] Falha ao iniciar o loop de reprodução do mpv")
            if self.player.ensure_started() and self.player.play_loop(videos, osd_text=OVERLAY_TEXT):
                logger.warning("[IDLE] Loop recuperado após reiniciar o mpv")
            else:
                logger.error("[IDLE] Não foi possível recuperar a reprodução do loop")
                self._shutdown.set()
                return

        # Bloqueia o ciclo idle até um aceno ou encerramento
        while not self._shutdown.is_set() and not self._wave_detected.wait(timeout=0.2):
            continue

        if self._shutdown.is_set():
            return

        self._wave_detected.clear()
        self.state = PRESENTING
        logger.info("[IDLE] → Transicionando para PRESENTING")

    def _run_presentation(self):
        """
        Reproduz o vídeo de apresentação e retorna ao modo IDLE.

        Após o término, aplica o cooldown configurado antes de
        reativar a detecção de gestos.
        """
        logger.info("=" * 50)
        logger.info("  [PRESENTING] Iniciando vídeo de apresentação")
        logger.info("=" * 50)

        if not os.path.exists(PRESENTATION_VIDEO):
            logger.error(
                f"[PRESENTING] Arquivo não encontrado: '{PRESENTATION_VIDEO}'\n"
                f"  → Coloque o vídeo em: {os.path.abspath(PRESENTATION_VIDEO)}"
            )
            time.sleep(2)
            self.state = IDLE
            return

        if not self.player.ensure_started():
            logger.error("[PRESENTING] Não foi possível iniciar o player mpv. Retornando ao modo IDLE")
            self.state = IDLE
            return

        # Limpar detecção de aceno ANTES de iniciar a apresentação,
        # para que nenhum aceno residual dispare uma segunda transição
        self._wave_detected.clear()

        if not self.player.play_presentation(PRESENTATION_VIDEO):
            logger.error("[PRESENTING] Não foi possível reproduzir o vídeo de apresentação. Tentando reiniciar o mpv...")
            if self.player.ensure_started() and self.player.play_presentation(PRESENTATION_VIDEO):
                logger.warning("[PRESENTING] Reprodução recuperada após reiniciar o mpv")
            else:
                logger.error("[PRESENTING] Não foi possível recuperar a reprodução da apresentação. Retornando ao modo IDLE")
                self.state = IDLE
                return

        logger.info("[PRESENTING] Aguardando término da reprodução via IPC...")
        
        # Aguarda o mpv avisar que o vídeo acabou (máximo 120s por segurança)
        if not self.player.wait_for_playback(timeout=120):
            logger.warning("[PRESENTING] Timeout aguardando fim do vídeo")

        logger.info(
            f"[PRESENTING] Vídeo finalizado. "
            f"Cooldown de {COOLDOWN_SECONDS:.1f}s ativo..."
        )

        # Aplicar cooldown (câmera ignora gestos durante este período)
        self._cooldown_until = time.time() + COOLDOWN_SECONDS
        self._shutdown.wait(timeout=COOLDOWN_SECONDS)  # aguarda ou encerra se shutdown

        # Resetar estado e retornar ao loop
        self.detector.reset()
        self._wave_detected.clear()  # limpa qualquer aceno captado durante o cooldown
        self.state = IDLE

        logger.info("[PRESENTING] → Retornando ao modo IDLE")

    # ------------------------------------------------------------------
    # Thread da câmera
    # ------------------------------------------------------------------

    def _camera_worker(self):
        """
        Thread dedicada à captura de frames e detecção de acenos.

        Executa continuamente em segundo plano:
          - Reconecta automaticamente se câmera desconectar
          - Pausa a detecção durante PRESENTING e cooldown
          - Ao detectar aceno: sinaliza _wave_detected e para o player
        """
        logger.info("[Câmera] Thread iniciada")
        cap = None  # cv2.VideoCapture ou None

        while not self._shutdown.is_set():

            # --- Garantir que a câmera está conectada ---
            if cap is None or not cap.isOpened():
                cap = self._open_camera()
                if cap is None:
                    # Aguardar antes de tentar reconectar
                    self._shutdown.wait(timeout=3)
                    continue

            # --- Pausar durante apresentação ---
            if self.state == PRESENTING:
                time.sleep(0.05)
                continue

            # --- Pausar durante cooldown ---
            if time.time() < self._cooldown_until:
                time.sleep(0.05)
                continue

            # --- Capturar frame ---
            ret, frame = cap.read()

            if not ret:
                logger.warning("[Câmera] Falha ao capturar frame — reconectando...")
                cap.release()
                cap = None
                time.sleep(1)
                continue

            # --- Processar detecção ---
            try:
                if self.detector.process_frame(frame):
                    logger.info("[Câmera] ★★★ ACENO DETECTADO! ★★★")
                    # Ativar cooldown imediatamente para evitar re-disparo
                    self._cooldown_until = time.time() + COOLDOWN_SECONDS
                    self._wave_detected.set()
                    self.detector.reset()
                    # O player será substituído pelo play_presentation do loop principal.
                    # Evitamos um stop() extra para não criar condições de corrida no mpv.

            except Exception as exc:
                logger.error(f"[Câmera] Erro durante detecção: {exc}")

        # --- Encerrar câmera ao sair ---
        if cap and cap.isOpened():
            cap.release()
        logger.info("[Câmera] Thread encerrada")

    @staticmethod
    def _open_camera():
        """
        Tenta abrir a câmera configurada.

        Usa CAP_DSHOW no Windows para menor latência na inicialização.

        Returns:
            Objeto VideoCapture aberto e configurado, ou None em falha.
        """
        logger.info(f"[Câmera] Conectando à câmera {CAMERA_INDEX}...")

        # CAP_DSHOW = DirectShow — inicialização mais rápida no Windows
        backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
        cap = cv2.VideoCapture(CAMERA_INDEX, backend)

        if not cap.isOpened():
            logger.error(
                f"[Câmera] Não foi possível abrir câmera {CAMERA_INDEX}. "
                "Verifique a conexão USB."
            )
            return None

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Minimizar latência do buffer

        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        logger.info(f"[Câmera] Conectada! Resolução: {w}x{h}")
        return cap

    # ------------------------------------------------------------------
    # Encerramento
    # ------------------------------------------------------------------

    def _cleanup(self):
        """
        Encerra todos os recursos do sistema de forma limpa.

        Chamado automaticamente ao sair do loop principal.
        Garante que threads e processos mpv sejam encerrados sem deixar
        processos órfãos.
        """
        logger.info("Encerrando sistema...")
        self._shutdown.set()

        # Parar o player (encerra processo mpv se ativo)
        self.player.cleanup()

        # Aguardar thread da câmera encerrar
        if self._camera_thread and self._camera_thread.is_alive():
            logger.debug("Aguardando thread da câmera...")
            self._camera_thread.join(timeout=5)

        # Liberar recursos do MediaPipe
        self.detector.release()

        logger.info("=" * 60)
        logger.info("  Sistema encerrado com sucesso")
        logger.info("=" * 60)


# ------------------------------------------------------------------
# Funções auxiliares
# ------------------------------------------------------------------

def _get_loop_videos():
    """
    Retorna a lista de vídeos disponíveis na pasta loop.

    Suporta os formatos: .mp4, .avi, .mkv, .mov, .wmv

    Returns:
        Lista de caminhos de arquivo ordenada alfabeticamente.
    """
    extensions = ["*.mp4", "*.avi", "*.mkv", "*.mov", "*.wmv"]
    videos = []
    for ext in extensions:
        videos.extend(glob.glob(os.path.join(LOOP_FOLDER, ext)))
    return sorted(videos)


# ------------------------------------------------------------------
# Ponto de entrada
# ------------------------------------------------------------------

def main():
    """
    Inicializa e executa o Painel Interativo.

    Registra handlers para SIGINT e SIGTERM para garantir encerramento
    limpo mesmo ao fechar a janela do terminal.
    """
    controller = PainelController()

    def _signal_handler(sig, frame):
        """Handler para encerramento gracioso via sinais do SO."""
        logger.info(f"Sinal {sig} recebido — encerrando...")
        controller._shutdown.set()
        controller.player.cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    controller.run()


if __name__ == "__main__":
    main()
