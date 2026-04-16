# ============================================================
# player.py — Controle do mpv via processo persistente + stdin
# ============================================================
"""
O mpv é iniciado UMA única vez com --idle=yes e --input-ipc-server.
Fica aberto sem vídeo e recebe comandos via named pipe IPC, de modo que
a janela permanece visível e as transições são suaves.
"""

import ctypes
import ctypes.wintypes
import json
import logging
import msvcrt
import os
import subprocess
import threading
import time

from config import MPV_PATH, OVERLAY_FONT_SCALE

logger = logging.getLogger(__name__)

_WIN_NO_CONSOLE = 0x08000000  # CREATE_NO_WINDOW on Windows

# Windows API direto — usado para PeekNamedPipe e ReadFile sem passar pelo
# MSVCRT, cujo lock per-fd serializa read e write no mesmo handle e causa
# bloqueios de até 20-30s quando a thread leitora espera dados do mpv.
_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)


class VideoPlayer:
    """
    Controla um processo mpv persistente via pipe IPC.

    A janela do mpv fica aberta indefinidamente. Trocas de vídeo são
    feitas por JSON IPC via named pipe no Windows.
    """

    def __init__(self):
        """Inicializa o player sem processo ativo."""
        self._process = None
        self._ipc = None         # FileIO — mantém o handle vivo
        self._ipc_fd = -1        # C fd para os.write (escrita)
        self._ipc_win_handle = 0 # Windows HANDLE para PeekNamedPipe/ReadFile (leitura)
        self._pipe_path = None
        self._ipc_reader = None
        self._ipc_reader_stop = threading.Event()
        self._playback_finished = threading.Event()
        self._lock = threading.Lock() # Protege escrita simultânea no pipe
        # Flag: True apenas quando aguardamos o fim do vídeo de apresentação.
        # Impede que eventos end-file dos vídeos em loop acionem _playback_finished.
        self._awaiting_presentation = False

    # ------------------------------------------------------------------
    # Ciclo de vida do processo
    # ------------------------------------------------------------------

    def start(self):
        """
        Inicia o processo mpv persistente em tela cheia no modo idle.

        Deve ser chamado uma única vez no início do programa.
        mpv aguarda comandos via stdin sem exibir nenhum vídeo ainda.
        """
        if self._is_alive():
            return

        # Pipe único com timestamp para evitar conflitos em reinicializações rápidas
        self._pipe_path = rf"\\.\pipe\mpv_pipe_{os.getpid()}_{int(time.time())}"

        cmd = [
            MPV_PATH,
            "--idle=yes",            # fica aberto sem arquivo
            "--force-window=yes",    # exibe janela mesmo sem vídeo
            "--fullscreen",          # tela cheia
            "--ontop",               # manter janela visível
            "--vo=gpu,d3d11,direct3d", # Ordem de preferência de drivers
            "--hwdec=auto-safe",     # Aceleração de hardware segura
            "--msg-level=all=warn",  # Menos logs inúteis
            "--no-input-default-bindings",
            f"--input-ipc-server={self._pipe_path}",
            "--log-file=mpv.log",
            f"--osd-font-size={int(42 * OVERLAY_FONT_SCALE)}",
            "--osd-color=#FFFFFF",
            "--osd-border-color=#000000",
            "--osd-border-size=2",
            "--osd-align-y=bottom",
            "--osd-margin-y=30",
        ]

        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_WIN_NO_CONSOLE,
            )
            # Aguarda a conexão IPC ser estabelecida de forma ativa (sem sleep fixo)
            if not self._connect_ipc(timeout=5.0):
                self.cleanup()
                return False

            if not self._is_alive():
                logger.error("[Player] mpv morreu imediatamente após iniciar")
                self._process = None
                return False
            logger.info("[Player] mpv iniciado em modo persistente")
            return True
        except FileNotFoundError:
            logger.error(
                f"[Player] mpv nao encontrado em '{MPV_PATH}'. "
                "Baixe em https://mpv.io e adicione ao PATH do Windows."
            )
        except Exception as exc:
            logger.error(f"[Player] Erro ao iniciar mpv: {exc}")
        return False

    def ensure_started(self):
        """Garante que o mpv esteja em execução e pronto para receber comandos."""
        if self._is_alive():
            self._ensure_ipc()
            return self._ipc is not None

        self.cleanup()
        return self.start()

    # ------------------------------------------------------------------
    # Controle de reprodução
    # ------------------------------------------------------------------

    def play_loop(self, videos, osd_text=None):
        """
        Carrega uma lista de vídeos em loop contínuo no mpv já aberto.

        Não-bloqueante: apenas envia comandos, retorna imediatamente.
        A troca de vídeo é instantânea, sem fechar a janela.

        Args:
            videos: lista de caminhos de vídeo
            osd_text: texto sobreposto (OSD) ou None

        Returns:
            True se o loop foi iniciado com sucesso, False caso contrário.
        """
        if not videos:
            return False

        if not self.ensure_started():
            logger.error("[Player] Não foi possível iniciar o mpv para o loop")
            return False

        # Não aguardamos fim de vídeo no modo loop — desabilitar flag
        self._awaiting_presentation = False

        # Limpar playlist e carregar o primeiro vídeo imediatamente
        # O modo 'replace' já limpa a playlist anterior automaticamente
        if not self._cmd(["loadfile", _safe_path(videos[0]), "replace"]):
            return False

        # Adicionar os demais vídeos à playlist
        for v in videos[1:]:
            if not self._cmd(["loadfile", _safe_path(v), "append"]):
                logger.warning(f"[Player] Falha ao adicionar vídeo à playlist: {v}")
                continue

        # Configurar loop infinito
        if not self._cmd(["set_property", "loop-playlist", "inf"]):
            return False

        # Garantir que o player não fique pausado
        if not self._cmd(["set_property", "pause", False]):
            return False

        # Exibir ou ocultar o texto OSD
        if osd_text:
            if not self._cmd(["set_property", "osd-msg3", osd_text]):
                return False
            if not self._cmd(["set_property", "osd-level", 3]):
                return False
        else:
            if not self._cmd(["set_property", "osd-level", 1]):
                return False

        logger.info(f"[Player] Loop iniciado: {len(videos)} video(s)")
        return True

    def play_presentation(self, path):
        """
        Troca imediatamente para o vídeo de apresentação.

        Não-bloqueante: apenas envia comando, retorna imediatamente.
        O mpv troca o conteúdo na mesma janela aberta.

        Args:
            path: caminho do vídeo de apresentação

        Returns:
            True se o vídeo foi iniciado com sucesso, False caso contrário.
        """
        if not self.ensure_started():
            logger.error("[Player] Não foi possível iniciar o mpv para apresentação")
            return False

        safe = _safe_path(path)

        # Garantir que não há flag residual de apresentação anterior
        self._awaiting_presentation = False
        # Limpar evento ANTES de enviar loadfile, para não perder o sinal
        self._playback_finished.clear()

        if not self._cmd(["set_property", "osd-level", 1]):
            return False
        if not self._cmd(["set_property", "loop-file", "no"]):
            return False
        if not self._cmd(["set_property", "loop-playlist", "no"]):
            return False
        if not self._cmd(["loadfile", safe, "replace"]):
            return False
        if not self._cmd(["set_property", "pause", False]):
            return False

        # Habilitar captura do evento end-file somente agora,
        # após todos os comandos serem enviados
        self._awaiting_presentation = True

        logger.info(f"[Player] Apresentacao: {os.path.basename(path)}")
        return True

    def play(self, path, osd_text=None):
        """Alias para reproduzir um único vídeo de apresentação."""
        return self.play_presentation(path)

    def stop(self):
        """Interrompe a reprodução atual sem encerrar o processo mpv."""
        if not self._is_alive():
            return
        self._cmd(["stop"])
        logger.info("[Player] Reprodução interrompida")

    def wait_for_playback(self, timeout=60):
        """
        Bloqueia a execução até que o vídeo atual termine ou o timeout ocorra.
        Retorna True se o vídeo terminou, False em caso de timeout.

        Nota: NÃO chamar clear() aqui — já foi feito em play_presentation,
        antes do loadfile, para evitar perder o sinal de fim de vídeo.
        """
        result = self._playback_finished.wait(timeout=timeout)
        self._awaiting_presentation = False
        return result

    def cleanup(self):
        """Encerra o processo mpv de forma limpa."""
        self._stop_ipc_reader()
        if self._is_alive():
            try:
                self._cmd("quit")
                self._process.wait(timeout=2)
            except Exception:
                pass
            try:
                self._process.kill()
            except Exception:
                pass
        if self._ipc is not None:
            try:
                self._ipc.close()
            except Exception:
                pass
            self._ipc = None
        self._ipc_fd = -1
        self._ipc_win_handle = 0
        self._process = None
        logger.info("[Player] Encerrado com sucesso")

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _cmd(self, command):
        """
        Envia um comando JSON ao mpv via named pipe IPC.

        Args:
            command: lista de strings que representa o comando mpv.

        Returns:
            True se o comando foi enviado, False caso contrário.
        """
        if not self._is_alive():
            logger.warning("[Player] mpv não está ativo ao enviar comando")
            return False

        if self._ipc_fd < 0:
            self._ensure_ipc()
            if self._ipc_fd < 0:
                logger.error("[Player] não há conexão IPC com o mpv")
                return False

        with self._lock:
            try:
                if isinstance(command, str):
                    command = command.split()

                payload = (json.dumps({"command": command}, ensure_ascii=False) + "\n").encode("utf-8")
                # os.write usa o fd bruto — sem o lock interno do FileIO,
                # permitindo que a thread leitora bloqueie em os.read() em paralelo.
                os.write(self._ipc_fd, payload)
                return True
            except OSError as exc:
                logger.error("[Player] Erro de comunicação (Pipe): %s", exc)
                # NÃO zerar _process — o mpv pode ainda estar vivo.
                self._ipc_fd = -1
                try:
                    if self._ipc is not None:
                        self._ipc.close()
                except Exception:
                    pass
                self._ipc = None
                return False
            except Exception as exc:
                logger.error(f"[Player] Erro ao enviar comando '{command}': {exc}")
                return False

    def _connect_ipc(self, timeout=5.0):
        """
        Tenta conectar ao named pipe do mpv para enviar comandos JSON.

        O mpv cria o pipe após iniciar. Este método aguarda até o pipe
        aparecer ou até o timeout expirar.
        """
        start = time.time()
        if self._ipc is not None:
            self._stop_ipc_reader()
            self._ipc = None

        while time.time() - start < timeout and self._is_alive():
            try:
                # Abre o pipe e extrai o fd bruto imediatamente.
                # O objeto FileIO (_ipc) é mantido apenas para gerenciar o lifetime do handle;
                # todo I/O real é feito via os.read/os.write no fd bruto (_ipc_fd).
                ipc = open(self._pipe_path, "r+b", buffering=0)
                self._ipc = ipc
                self._ipc_fd = ipc.fileno()
                # Windows HANDLE para chamadas diretas sem passar pelo MSVCRT
                self._ipc_win_handle = msvcrt.get_osfhandle(self._ipc_fd)
                logger.debug("[Player] Conectado ao pipe IPC do mpv")
                self._start_ipc_reader()
                return True
            except FileNotFoundError:
                time.sleep(0.1)
            except Exception:
                time.sleep(0.1)

        self._ipc = None
        self._ipc_fd = -1
        return False

    def _ensure_ipc(self):
        """Garante que há uma conexão IPC ativa com o mpv."""
        if self._ipc is None and self._is_alive():
            self._connect_ipc()

    def _start_ipc_reader(self):
        """Inicia a thread que consome respostas do pipe IPC do mpv."""
        if self._ipc_reader and self._ipc_reader.is_alive():
            return

        self._ipc_reader_stop.clear()
        self._ipc_reader = threading.Thread(
            target=self._ipc_reader_loop,
            daemon=True,
            name="mpv-ipc-reader",
        )
        self._ipc_reader.start()

    def _stop_ipc_reader(self):
        """Para a thread de leitura do pipe IPC e fecha o handle."""
        if self._ipc_reader is None:
            return

        self._ipc_reader_stop.set()
        # Fechar o FileIO para desbloquear o os.read() pendente na thread leitora
        try:
            if self._ipc is not None:
                self._ipc.close()
        except Exception:
            pass

        self._ipc_reader.join(timeout=1)
        self._ipc_reader = None
        self._ipc = None
        self._ipc_fd = -1
        self._ipc_win_handle = 0

    def _ipc_reader_loop(self):
        """
        Consome eventos do mpv via PeekNamedPipe + ReadFile direto.

        Problema anterior: os.read(fd) bloqueava segurando o lock per-fd do MSVCRT
        (C runtime do Windows). Qualquer os.write(fd) em _cmd ficava esperando o
        lock, causando delays iguais à duração restante do vídeo em loop (20-30s).

        Solução: PeekNamedPipe verifica se há dados sem bloquear. ReadFile é chamado
        via ctypes, sem passar pelo MSVCRT, então não há conflito de lock com os.write.
        """
        handle = ctypes.c_void_p(self._ipc_win_handle)
        bytes_avail = ctypes.wintypes.DWORD(0)
        read_buf = ctypes.create_string_buffer(4096)
        bytes_read = ctypes.wintypes.DWORD(0)
        buf = b""

        while not self._ipc_reader_stop.is_set() and self._ipc_win_handle:
            # Verifica bytes disponíveis sem bloquear
            bytes_avail.value = 0
            ok = _KERNEL32.PeekNamedPipe(
                handle, None, 0, None, ctypes.byref(bytes_avail), None
            )
            if not ok:
                if not self._ipc_reader_stop.is_set():
                    logger.debug("[Player] Pipe fechou (PeekNamedPipe err=%d)", ctypes.get_last_error())
                break

            if bytes_avail.value == 0:
                time.sleep(0.01)  # nenhum dado — aguarda 10ms e tenta novamente
                continue

            # Há dados: lê sem risco de bloquear (ReadFile direto, sem lock MSVCRT)
            bytes_read.value = 0
            ok = _KERNEL32.ReadFile(
                handle, read_buf, min(bytes_avail.value, 4096),
                ctypes.byref(bytes_read), None
            )
            if not ok or bytes_read.value == 0:
                break

            buf += read_buf.raw[: bytes_read.value]
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line:
                    continue
                try:
                    data = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue

                if data.get("event") == "end-file" and data.get("reason") == "eof":
                    if self._awaiting_presentation:
                        logger.debug("[Player] EOF apresentação detectado")
                        self._playback_finished.set()
                    else:
                        logger.debug("[Player] EOF do loop ignorado")

        # Saída inesperada: fechar e zerar para que _ensure_ipc reconecte
        if not self._ipc_reader_stop.is_set():
            try:
                if self._ipc is not None:
                    self._ipc.close()
            except Exception:
                pass
            self._ipc = None
            self._ipc_fd = -1
            self._ipc_win_handle = 0

        logger.debug("[Player] IPC reader encerrado")

    def _is_alive(self):
        """Retorna True se o processo mpv está rodando."""
        return self._process is not None and self._process.poll() is None


# ------------------------------------------------------------------
# Utilitário de caminho
# ------------------------------------------------------------------

def _safe_path(path):
    """
    Converte caminho para formato absoluto com barras normais.

    No Windows, o mpv via IPC JSON prefere barras normais (/) para evitar 
    problemas de escape de caracteres em strings de comando.
    """
    return os.path.abspath(path).replace("\\", "/")


def get_video_duration(path):
    """
    Retorna a duração de um vídeo em segundos usando OpenCV.

    Usado para aguardar o término do vídeo de apresentação
    sem precisar de comunicação bidirecional com o mpv.

    Args:
        path: caminho do arquivo de vídeo

    Returns:
        Duração em segundos, ou 60.0 como fallback se não for possível medir.
    """
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        count = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        cap.release()
        if fps > 0 and count > 0:
            duration = count / fps
            logger.info(
                f"[Player] Duracao de '{os.path.basename(path)}': {duration:.1f}s"
            )
            return duration
    except Exception as exc:
        logger.warning(f"[Player] Nao foi possivel medir duracao: {exc}")
    return 60.0  # fallback conservador
