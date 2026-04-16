# ============================================================
# config.py — Configurações do Painel Interativo Telbra-Ex
# Altere os valores aqui para ajustar o comportamento do sistema
# ============================================================

# --- Câmera ---
CAMERA_INDEX = 0          # Índice da câmera USB (0 = câmera padrão)
CAMERA_WIDTH = 1280       # Largura da captura em pixels
CAMERA_HEIGHT = 720       # Altura da captura em pixels

# --- Detecção de Gesto ---
MIN_HAND_SIZE = 0.03      # Tamanho mínimo da mão (fração da largura do frame)
WAVE_ALTERNATIONS = 2     # Número mínimo de alternâncias esq↔dir para detectar aceno
WAVE_WINDOW_SECONDS = 1.5 # Janela de tempo para detectar o aceno (segundos)
WAVE_MIN_MOVEMENT = 0.03  # Movimento mínimo entre alternâncias (fração do frame)

# --- Cooldown ---
COOLDOWN_SECONDS = 0.03    # Tempo de espera após disparar o vídeo (segundos)

import os

# --- Caminhos de Vídeo ---
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

# Garantir que os nomes das pastas correspondam ao sistema de arquivos (case-sensitive em alguns casos)
LOOP_FOLDER = os.path.join(ROOT_DIR, "Videos", "loop")
PRESENTATION_VIDEO = os.path.join(ROOT_DIR, "Videos", "apresentacao")

# --- Exibição (OSD do mpv) ---
OVERLAY_TEXT = "Olhe para a câmera e acene!"  # Texto sobreposto no modo idle
OVERLAY_FONT_SCALE = 1.0  # Escala da fonte OSD no mpv
OVERLAY_ALPHA = 0.6       # Transparência do fundo do texto (reservado)

# --- MPV (Player de Vídeo) ---
MPV_PATH = r"C:\mpv\mpv.exe"  # Certifique-se que o mpv.exe está nesta pasta exata
