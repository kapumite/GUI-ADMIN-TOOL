# 🖥️ Система управления компьютерным классом

## ⚡ Адмиин панель

```bash
# Создание виртуального окружения
python -m venv venv

# Активация (Windows PowerShell)
venv\Scripts\activate

# Активация (CMD)
venv\Scripts\activate.bat

#установка зависимостей
python\python.exe get-pip.py
python\python.exe -m pip install requests psutil Pillow

# В папке проекта
python -m venv venv
# Активация (Windows PowerShell):
venv\Scripts\activate
# Активация (CMD):
venv\Scripts\activate.bat


# структура папок учителя
C:\projects\classroom\
├── teacher_gui.py         (агент преподавателя)
├── config.py              (файл конфигурации)
├── python                 (версия portable)
├── classroom.db           (создастся автоматически)
├── received_works\        (архивы от студентов)
└── venv\                  (виртуальное окружение, если создал)

# структура папок студентов
C:\agent\
├── student_agent_v2.py    (агент студента)
├── python                 (версия portable)
└── config.py              (файл конфигурации)