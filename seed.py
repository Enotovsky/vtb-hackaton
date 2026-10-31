"""Добавляет учебную семью, если её ещё нет. Не удаляет существующие данные."""
from app.demo import ensure_demo

if __name__=="__main__":
    ensure_demo()
    print("Учебная семья готова. Запуск: python run.py")
