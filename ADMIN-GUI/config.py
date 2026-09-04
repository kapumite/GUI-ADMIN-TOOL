# config.py
TEACHER_IP = "192.168.1.100"      # IP‑адрес компьютера учителя
FLASK_PORT = 5000                 # порт Flask‑сервера (должен совпадать в обоих файлах)

# Папки на студенческих ПК (можно оставить как было)
MATERIALS_FOLDER = "Учебные_материалы"
PROJECT_FOLDER   = "Project"

# Интервалы (секунды)
AGENT_POLL_INTERVAL    = 30      # heartbeat
COMMAND_POLL_INTERVAL  = 3       # как часто агент проверяет команды