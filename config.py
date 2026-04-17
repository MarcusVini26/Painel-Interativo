# ============================================================
# config.py - Configuracoes do Painel Interativo Telbra-Ex
# Altere os valores aqui para ajustar o comportamento do sistema
# ============================================================

# --- Camera ---
CAMERA_INDEX = 0          # Indice da camera USB (0 = camera padrao)
CAMERA_WIDTH = 1280       # Largura da captura em pixels
CAMERA_HEIGHT = 720       # Altura da captura em pixels

# --- Deteccao de Gesto ---
MIN_HAND_SIZE = 0.03      # Tamanho minimo da mao (fracao da largura do frame)
WAVE_ALTERNATIONS = 2     # Numero minimo de alternancias esq<->dir para detectar aceno
WAVE_WINDOW_SECONDS = 1.5 # Janela de tempo para detectar o aceno (segundos)
WAVE_MIN_MOVEMENT = 0.035 # Movimento minimo entre alternancias (fracao do frame)
MAX_NUM_HANDS = 4         # Quantidade maxima de maos analisadas por frame

# --- Cooldown ---
COOLDOWN_SECONDS = 0.03    # Tempo de espera apos disparar o video (segundos)

import os

# --- Caminhos de Video ---
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

# Garantir que os nomes das pastas correspondam ao sistema de arquivos
LOOP_FOLDER = os.path.join(ROOT_DIR, "Videos", "loop")
PRESENTATION_VIDEO = os.path.join(ROOT_DIR, "Videos", "apresentacao")

# --- Exibicao (OSD do mpv) ---
OVERLAY_TEXT = "Olhe para a camera e acene!"  # Texto sobreposto no modo idle
OVERLAY_FONT_SCALE = 1.0  # Escala da fonte OSD no mpv
OVERLAY_ALPHA = 0.6       # Transparencia do fundo do texto (reservado)

# --- MPV (Player de Video) ---
MPV_PATH = r"C:\mpv\mpv.exe"  # Certifique-se que o mpv.exe esta nesta pasta exata
