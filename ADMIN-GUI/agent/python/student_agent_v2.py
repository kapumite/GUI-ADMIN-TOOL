# =============================================================================
#  student_agent_v2.py — Агент управления компьютерным классом (без Telegram)
#
#  Подключается к Flask-серверу teacher_gui.py.
#  Запуск: python student_agent_v2.py
#
#  Зависимости: requests, psutil, Pillow
#  Стандартные библиотеки: os, time, sys, logging, platform,
#                           zipfile, socket, subprocess, threading, tkinter
#
#  Правила антивирусной совместимости:
#   - Только subprocess.run / Popen, никакого os.system
#   - Нет PyInstaller / скрытых запусков
#   - Консоль всегда открыта с русскими логами
#   - Используются только стандартные и учебные библиотеки
# =============================================================================

import os
import sys
import time
import socket
import logging
import platform
import zipfile
import subprocess
import threading
import base64
import io
from datetime import datetime
from pathlib import Path

# --- Сторонние библиотеки ---
try:
    import requests
    import psutil
    from PIL import ImageGrab
except ImportError as e:
    print(f"[ОШИБКА] Отсутствует зависимость: {e}")
    print("Запустите: pip install requests psutil Pillow")
    input("Нажмите Enter для выхода...")
    sys.exit(1)

# --- tkinter (уведомления) ---
try:
    import tkinter as tk
    TKINTER_OK = True
except ImportError:
    TKINTER_OK = False
    print("[ПРЕДУПРЕЖДЕНИЕ] tkinter недоступен — уведомления на экране отключены.")

# --- Импорт конфигурации ---
try:
    from config import (
        MATERIALS_FOLDER,
        PROJECT_FOLDER,
        AGENT_POLL_INTERVAL,
        TEACHER_IP,
        FLASK_PORT,
        COMMAND_POLL_INTERVAL,
    )
except ImportError:
    print("[ОШИБКА] Файл config.py не найден рядом со скриптом.")
    print("Убедитесь, что config.py содержит TEACHER_IP, FLASK_PORT, MATERIALS_FOLDER, PROJECT_FOLDER.")
    input("Нажмите Enter для выхода...")
    sys.exit(1)

# =============================================================================
#  Настройка логирования
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("agent.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("Агент")

# =============================================================================
#  Константы и пути
# =============================================================================

HOSTNAME    = socket.gethostname()
LOCAL_IP    = socket.gethostbyname(HOSTNAME)
OS_INFO     = f"{platform.system()} {platform.release()} ({platform.version()[:40]})"

# Рабочий стол (Windows / Linux / macOS)
if platform.system() == "Windows":
    DESKTOP_PATH   = Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop"
    DOCUMENTS_PATH = Path(os.environ.get("USERPROFILE", Path.home())) / "Documents"
else:
    DESKTOP_PATH   = Path.home() / "Рабочий стол"
    DOCUMENTS_PATH = Path.home() / "Документы"
    if not DESKTOP_PATH.exists():
        DESKTOP_PATH = Path.home() / "Desktop"
    if not DOCUMENTS_PATH.exists():
        DOCUMENTS_PATH = Path.home() / "Documents"

MATERIALS_PATH = DESKTOP_PATH / MATERIALS_FOLDER
PROJECT_PATH   = DOCUMENTS_PATH / PROJECT_FOLDER

# URL-адреса Flask-сервера преподавателя
_SERVER        = f"http://{TEACHER_IP}:{FLASK_PORT}"
URL_REGISTER   = f"{_SERVER}/register"
URL_GET_CMD    = f"{_SERVER}/get_command/{HOSTNAME}"
URL_POST_LOG   = f"{_SERVER}/post_log"
URL_GET_FILE   = f"{_SERVER}/get_file/{HOSTNAME}"
URL_UPLOAD_WORKS = f"{_SERVER}/upload_works"

# =============================================================================
#  Вспомогательные утилиты
# =============================================================================

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _ensure_dir(path: Path) -> bool:
    """Создать папку, если не существует. Вернуть True при успехе."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True
    except PermissionError:
        log.error("[ОШИБКА] Нет прав для создания папки: %s", path)
        return False


def _post_log(msg_type: str, data: str):
    """
    Отправить результат или сообщение об ошибке на Flask-сервер.
    Используется учителем для логирования.
    """
    try:
        resp = requests.post(
            URL_POST_LOG,
            json={"type": msg_type, "hostname": HOSTNAME, "data": data},
            timeout=10,
            proxies={"http": None, "https": None}
        )
        resp.raise_for_status()
    except Exception as exc:
        log.error("[%s] [ОШИБКА] Не удалось отправить лог (%s): %s", _ts(), msg_type, exc)


def _send_error(error_text: str):
    """Отправить сообщение об ошибке."""
    _post_log("ERROR", error_text)


# =============================================================================
#  Уведомление на экране студента (tkinter)
# =============================================================================

def _show_notification(message: str, duration_ms: int = 3000):
    """
    Показать всплывающее уведомление студенту на N миллисекунд.
    Запускается в отдельном потоке, чтобы не блокировать агент.
    """
    if not TKINTER_OK:
        return

    def _run():
        try:
            root = tk.Tk()
            root.title("Уведомление")
            root.attributes("-topmost", True)     # поверх всех окон
            root.resizable(False, False)
            root.overrideredirect(False)           # оставляем заголовок

            # Цветовое оформление
            BG   = "#1a1a2e"
            FG   = "#e0e0e0"
            ACCENT = "#4a90d9"

            root.configure(bg=BG)

            # Иконка + текст
            frame = tk.Frame(root, bg=BG, padx=20, pady=15)
            frame.pack(fill="both", expand=True)

            tk.Label(
                frame,
                text="📡  Система управления классом",
                bg=BG, fg=ACCENT,
                font=("Segoe UI", 10, "bold"),
            ).pack(anchor="w")

            tk.Label(
                frame,
                text=message,
                bg=BG, fg=FG,
                font=("Segoe UI", 11),
                wraplength=360,
                justify="left",
            ).pack(anchor="w", pady=(8, 0))

            # Разместить в правом нижнем углу экрана
            screen_w = root.winfo_screenwidth()
            screen_h = root.winfo_screenheight()
            root.update_idletasks()
            w = root.winfo_width()
            h = root.winfo_height()
            x = screen_w - w - 20
            y = screen_h - h - 60
            root.geometry(f"+{x}+{y}")

            root.after(duration_ms, root.destroy)
            root.mainloop()
        except Exception as exc:
            log.debug("[УВЕДОМЛЕНИЕ] Ошибка tkinter: %s", exc)

    threading.Thread(target=_run, daemon=True, name="Уведомление").start()


# =============================================================================
#  Наблюдение за рабочим столом (реального времени)
# =============================================================================

_observation_active = False
_observation_lock = threading.Lock()
_observation_thread = None

def _observation_loop():
    """Отправляет скриншоты каждые ~0.5 секунды, пока активно."""
    global _observation_active
    log.info("[НАБЛЮДЕНИЕ] Запущен поток захвата экрана.")
    while True:
        with _observation_lock:
            if not _observation_active:
                break

        try:
            # Захват экрана
            img = ImageGrab.grab()
            # Масштабируем до ширины 800 пикселей (сохраняя пропорции)
            w, h = img.size
            new_w = 800
            new_h = int(h * new_w / w)
            img = img.resize((new_w, new_h))
            # Кодируем в JPEG с качеством 40 для уменьшения размера
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=40)
            jpeg_bytes = buf.getvalue()
            # Проверяем, что размер не превышает ~200 КБ (ограничение для лога)
            if len(jpeg_bytes) > 200 * 1024:
                log.warning("[НАБЛЮДЕНИЕ] Кадр слишком большой (%d байт), пропускаем.", len(jpeg_bytes))
            else:
                b64_data = base64.b64encode(jpeg_bytes).decode("ascii")
                _post_log("FRAME", b64_data)   # тип FRAME для teacher_gui
        except Exception as exc:
            log.error("[НАБЛЮДЕНИЕ] Ошибка при захвате кадра: %s", exc)

        time.sleep(0.5)   # 2 кадра в секунду

    log.info("[НАБЛЮДЕНИЕ] Поток захвата экрана остановлен.")


def _start_observation():
    global _observation_active, _observation_thread
    with _observation_lock:
        if _observation_active:
            return
        _observation_active = True
    _observation_thread = threading.Thread(target=_observation_loop, daemon=True, name="Observation")
    _observation_thread.start()
    log.info("[КОМАНДА] Наблюдение запущено учителем.")
    _show_notification("🔴 Наблюдение рабочего стола активно", duration_ms=3500)


def _stop_observation():
    global _observation_active
    with _observation_lock:
        _observation_active = False
    log.info("[КОМАНДА] Наблюдение остановлено учителем.")
    _show_notification("🟢 Наблюдение завершено", duration_ms=2500)


# =============================================================================
#  Команды агента (без Telegram)
# =============================================================================

def cmd_sysinfo():
    """Собрать и отправить информацию о системе."""
    log.info("[%s] [КОМАНДА] Информация о системе", _ts())
    try:
        cpu_pct   = psutil.cpu_percent(interval=1)
        mem       = psutil.virtual_memory()
        disk      = psutil.disk_usage(str(Path.home().anchor))

        mem_total = mem.total  / (1024 ** 3)
        mem_used  = mem.used   / (1024 ** 3)
        mem_pct   = mem.percent

        disk_total = disk.total / (1024 ** 3)
        disk_used  = disk.used  / (1024 ** 3)
        disk_pct   = disk.percent

        # Процессы с наибольшей нагрузкой на CPU
        top_procs = []
        for proc in sorted(
            psutil.process_iter(["pid", "name", "cpu_percent"]),
            key=lambda p: p.info["cpu_percent"] or 0,
            reverse=True,
        )[:5]:
            top_procs.append(
                f"  • {proc.info['name'][:20]:<20} CPU: {proc.info['cpu_percent']:.1f}%"
            )
        top_text = "\n".join(top_procs) if top_procs else "  (нет данных)"

        info = (
            f"💻 ОС:      {OS_INFO}\n"
            f"🔵 CPU:     {cpu_pct:.1f}%\n"
            f"🟡 ОЗУ:     {mem_used:.1f} / {mem_total:.1f} ГБ  ({mem_pct:.1f}%)\n"
            f"🟠 Диск:    {disk_used:.1f} / {disk_total:.1f} ГБ  ({disk_pct:.1f}%)\n"
            f"🌐 IP:      {LOCAL_IP}\n\n"
            f"📋 Топ процессы:\n{top_text}"
        )
        _post_log("SYSINFO", info)
        log.info("[%s] [ИНФО] Информация о системе отправлена.", _ts())
    except Exception as exc:
        log.error("[%s] [ОШИБКА] Не удалось собрать инфо о системе: %s", _ts(), exc)
        _send_error(f"Не удалось собрать информацию о системе: {exc}")


def cmd_collect_works():
    """Заархивировать папку проекта и загрузить на сервер через /upload_works."""
    log.info("[%s] [КОМАНДА] Сбор работ", _ts())

    if not PROJECT_PATH.exists():
        msg = f"Папка проекта не найдена: {PROJECT_PATH}"
        log.warning("[%s] [ПРЕДУПРЕЖДЕНИЕ] %s", _ts(), msg)
        _send_error(msg)
        return

    archive_name = f"works_{HOSTNAME}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    archive_path = Path(archive_name)

    try:
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in PROJECT_PATH.rglob("*"):
                if file_path.is_file():
                    zf.write(file_path, file_path.relative_to(PROJECT_PATH))
        log.info("[%s] [ИНФО] Архив создан: %s", _ts(), archive_path)
    except Exception as exc:
        log.error("[%s] [ОШИБКА] Не удалось создать архив: %s", _ts(), exc)
        _send_error(f"Не удалось создать архив: {exc}")
        return

    # Загрузка на сервер (multipart/form-data)
    try:
        with open(archive_path, "rb") as f:
            resp = requests.post(
                URL_UPLOAD_WORKS,
                files={"file": (archive_name, f, "application/zip")},
                data={"hostname": HOSTNAME},
                timeout=30,
                proxies={"http": None, "https": None}
            )
            resp.raise_for_status()
        log.info("[%s] [ИНФО] Архив работ отправлен на сервер.", _ts())
        _post_log("FILE_UPLOADED", f"Архив {archive_name} загружен успешно.")
    except Exception as exc:
        log.error("[%s] [ОШИБКА] Ошибка при загрузке архива: %s", _ts(), exc)
        _send_error(f"Не удалось отправить архив: {exc}")
    finally:
        try:
            archive_path.unlink(missing_ok=True)
        except Exception:
            pass


def cmd_receive_file(file_name: str):
    """
    Скачать учебный файл с Flask-сервера преподавателя,
    сохранить в папку Учебные_материалы и открыть стандартным приложением.
    """
    log.info("[%s] [КОМАНДА] Получение файла: %s", _ts(), file_name)
    _show_notification(f"📂  Получен новый материал:\n{file_name}", duration_ms=4000)

    if not _ensure_dir(MATERIALS_PATH):
        _send_error(f"Не удалось создать папку: {MATERIALS_PATH}")
        return

    save_path = MATERIALS_PATH / file_name

    try:
        response = requests.get(URL_GET_FILE, timeout=60, stream=True)
        response.raise_for_status()

        with open(save_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        log.info("[%s] [ИНФО] Файл сохранён: %s", _ts(), save_path)
    except Exception as exc:
        log.error("[%s] [ОШИБКА] Не удалось скачать файл: %s", _ts(), exc)
        _send_error(f"Не удалось скачать файл '{file_name}': {exc}")
        return

    # Открыть файл стандартным приложением
    try:
        if platform.system() == "Windows":
            subprocess.run(["start", "", str(save_path)], shell=True, check=False)
        elif platform.system() == "Darwin":
            subprocess.run(["open", str(save_path)], check=False)
        else:
            subprocess.run(["xdg-open", str(save_path)], check=False)
        log.info("[%s] [ИНФО] Файл открыт.", _ts())
    except Exception as exc:
        log.warning("[%s] [ПРЕДУПРЕЖДЕНИЕ] Не удалось открыть файл автоматически: %s", _ts(), exc)

    _post_log("FILE_RECEIVED", f"Файл '{file_name}' сохранён в {MATERIALS_PATH}")


def cmd_kill_process(process_name: str):
    """
    Завершить процесс по имени исполняемого файла.
    Использует только subprocess.run (не os.system).
    """
    log.info("[%s] [КОМАНДА] Завершение процесса: %s", _ts(), process_name)

    if not process_name.strip():
        _post_log("KILL_RESULT", "Имя процесса не указано.")
        return

    # Нормализация имени
    proc = process_name.strip()
    if not proc.lower().endswith(".exe") and platform.system() == "Windows":
        proc = proc + ".exe"

    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["taskkill", "/F", "/IM", proc],
                capture_output=True,
                text=True,
                encoding="cp866",    # Windows консольная кодировка
                errors="replace",
            )
        else:
            result = subprocess.run(
                ["pkill", "-f", proc],
                capture_output=True,
                text=True,
            )

        if result.returncode == 0:
            msg = f"✅ Процесс '{proc}' успешно завершён."
            log.info("[%s] [ИНФО] %s", _ts(), msg)
        else:
            stderr_clean = (result.stderr or "").strip()
            msg = f"⚠️ Процесс '{proc}' не найден или уже завершён. ({stderr_clean})"
            log.warning("[%s] [ПРЕДУПРЕЖДЕНИЕ] %s", _ts(), msg)

    except FileNotFoundError:
        msg = f"❌ Команда завершения процессов недоступна в данной системе."
        log.error("[%s] [ОШИБКА] %s", _ts(), msg)
    except Exception as exc:
        msg = f"❌ Ошибка при завершении процесса: {exc}"
        log.error("[%s] [ОШИБКА] %s", _ts(), msg)

    _post_log("KILL_RESULT", msg)


def cmd_screenshot_single():
    """Одиночный скриншот (устаревшая команда, на всякий случай)."""
    log.info("[%s] [КОМАНДА] Разовый скриншот экрана", _ts())
    _show_notification("📸  Скриншот отправляется преподавателю", duration_ms=3000)

    try:
        img = ImageGrab.grab()
        w, h = img.size
        new_w = 800
        new_h = int(h * new_w / w)
        img = img.resize((new_w, new_h))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=40)
        b64_data = base64.b64encode(buf.getvalue()).decode("ascii")
        _post_log("FRAME", b64_data)   # отправка одного кадра как FRAME
        log.info("[%s] [ИНФО] Скриншот отправлен.", _ts())
    except Exception as exc:
        log.error("[%s] [ОШИБКА] Не удалось снять скриншот: %s", _ts(), exc)
        _send_error(f"Не удалось снять скриншот: {exc}")


# =============================================================================
#  Диспетчер команд
# =============================================================================

def _dispatch(command: str):
    """Выполнить команду, полученную от GET /get_command/<hostname>."""
    log.info("[%s] [ДИСПЕТЧЕР] Команда: %s", _ts(), command)

    # Наблюдение реального времени
    if command == "CMD_OBSERVE_START":
        threading.Thread(target=_start_observation, daemon=True).start()
    elif command == "CMD_OBSERVE_STOP":
        threading.Thread(target=_stop_observation, daemon=True).start()

    elif command == "CMD_SCREENSHOT":   # устаревшая, но оставляем совместимость
        threading.Thread(target=cmd_screenshot_single, daemon=True).start()

    elif command == "CMD_SYSINFO":
        threading.Thread(target=cmd_sysinfo, daemon=True).start()

    elif command == "CMD_COLLECT":
        threading.Thread(target=cmd_collect_works, daemon=True).start()

    elif command.startswith("CMD_KILL|"):
        proc_name = command.split("|", 1)[1] if "|" in command else ""
        threading.Thread(
            target=cmd_kill_process,
            args=(proc_name,),
            daemon=True,
        ).start()

    elif command.startswith("CMD_SEND_FILE|"):
        file_name = command.split("|", 1)[1] if "|" in command else "файл"
        threading.Thread(
            target=cmd_receive_file,
            args=(file_name,),
            daemon=True,
        ).start()

    else:
        log.warning("[%s] [ПРЕДУПРЕЖДЕНИЕ] Неизвестная команда: %s", _ts(), command)


# =============================================================================
#  Регистрация и heartbeat
# =============================================================================

def _register() -> bool:
    """Зарегистрироваться на Flask-сервере преподавателя."""
    log.info("[%s] [ИНФО] Регистрация рабочей станции '%s' на %s...",
             _ts(), HOSTNAME, _SERVER)
    try:
        resp = requests.post(
            URL_REGISTER,
            json={"hostname": HOSTNAME, "ip": LOCAL_IP},
            timeout=10,
            proxies={"http": None, "https": None}
        )
        resp.raise_for_status()
        log.info("[%s] [ИНФО] Регистрация выполнена успешно.", _ts())
        return True
    except Exception as exc:
        log.error("[%s] [ОШИБКА] Не удалось зарегистрироваться: %s", _ts(), exc)
        return False


def _heartbeat_loop():
    """Периодически отправлять heartbeat на Flask-сервер."""
    while True:
        time.sleep(AGENT_POLL_INTERVAL)
        try:
            resp = requests.post(
                URL_REGISTER,
                json={"hostname": HOSTNAME, "ip": LOCAL_IP},
                timeout=8,
                proxies={"http": None, "https": None}
            )
            resp.raise_for_status()
            log.debug("[%s] [HEARTBEAT] Отправлен.", _ts())
        except Exception as exc:
            log.warning("[%s] [ПРЕДУПРЕЖДЕНИЕ] Heartbeat не отправлен: %s", _ts(), exc)


# =============================================================================
#  Главный цикл опроса команд
# =============================================================================

def _command_poll_loop():
    """Каждые COMMAND_POLL_INTERVAL секунд опрашивает /get_command/<hostname>."""
    log.info("[%s] [ИНФО] Цикл опроса команд запущен (интервал: %ds).",
             _ts(), COMMAND_POLL_INTERVAL)

    while True:
        try:
            resp = requests.get(
                URL_GET_CMD,
                timeout=10,
                proxies={"http": None, "https": None}
            )
            resp.raise_for_status()
            payload = resp.json()
            command = payload.get("command")
            if command:
                _dispatch(command)
        except requests.exceptions.ConnectionError:
            log.warning("[%s] [ПРЕДУПРЕЖДЕНИЕ] Нет связи с %s. "
                        "Повтор через %ds...", _ts(), _SERVER, COMMAND_POLL_INTERVAL)
        except Exception as exc:
            log.error("[%s] [ОШИБКА] Ошибка при опросе команд: %s", _ts(), exc)

        time.sleep(COMMAND_POLL_INTERVAL)


# =============================================================================
#  Точка входа
# =============================================================================

def main():
    print("=" * 60)
    print("  Агент управления компьютерным классом  (v3 — без Telegram)")
    print(f"  Рабочая станция: {HOSTNAME}")
    print(f"  IP-адрес:        {LOCAL_IP}")
    print(f"  ОС:              {OS_INFO}")
    print(f"  Сервер:          {_SERVER}")
    print("=" * 60)
    print()

    log.info("[ЗАПУСК] Агент запущен на станции '%s' (IP: %s)", HOSTNAME, LOCAL_IP)
    log.info("[ИНФО]   Папка материалов: %s", MATERIALS_PATH)
    log.info("[ИНФО]   Папка проектов:   %s", PROJECT_PATH)
    log.info("[ИНФО]   Сервер команд:    %s", _SERVER)

    # Создать рабочие папки при необходимости
    _ensure_dir(MATERIALS_PATH)
    _ensure_dir(PROJECT_PATH)

    # Регистрация с повторными попытками
    attempt = 0
    while not _register():
        attempt += 1
        wait = min(5 * attempt, 30)
        log.warning("[ИНФО] Повтор через %ds (попытка %d)...", wait, attempt)
        time.sleep(wait)
        if attempt >= 10:
            log.error("[ОШИБКА] Не удалось подключиться к серверу '%s'.", _SERVER)
            log.error("Убедитесь, что teacher_gui.py запущен и TEACHER_IP в config.py верен.")
            input("Нажмите Enter для выхода...")
            sys.exit(1)

    log.info("[ИНФО] Соединение установлено. Ожидание команд от преподавателя...")

    # Запустить heartbeat в фоне
    hb_thread = threading.Thread(
        target=_heartbeat_loop,
        daemon=True,
        name="Heartbeat",
    )
    hb_thread.start()

    # Запустить главный цикл опроса команд
    try:
        log.info("[ИНФО] Режим опроса команд активен.")
        _command_poll_loop()
    except KeyboardInterrupt:
        log.info("[ИНФО] Агент остановлен пользователем.")
    except Exception as exc:
        log.error("[ОШИБКА] Критическая ошибка: %s", exc)
        input("Нажмите Enter для выхода...")
        sys.exit(1)


if __name__ == "__main__":
    main()