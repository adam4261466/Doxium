import pyautogui
import time

PROJECT_DIR = r"C:\Users\kabli\Desktop\codes\doc-hub"

def run_command_as_keyboard(command: str, cwd: str = None):
    # Open a new PowerShell window
    pyautogui.hotkey('win', 'r')
    time.sleep(0.5)
    pyautogui.write('cmd')
    pyautogui.press('enter')
    time.sleep(1)  # Wait for PowerShell to open

    if cwd:
        pyautogui.write(f'cd "{cwd}"')
        pyautogui.press('enter')
        time.sleep(0.5)

    pyautogui.write(command)
    pyautogui.press('enter')


# Commands to run
commands = [
    r'C:\Redis\redis-server.exe',
    r'python -m celery -A app.celery_app.celery worker --loglevel=INFO -P solo',
    r'python run.py'
]

for cmd in commands:
    run_command_as_keyboard(cmd, cwd=PROJECT_DIR)
    time.sleep(2)  # Wait a bit before starting the next one
