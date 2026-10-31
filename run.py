"""Один сервер для интерфейса и API. Запуск: python run.py."""
import argparse
import os
from pathlib import Path
import socket


def main():
    parser=argparse.ArgumentParser(description="Локальный учебный MVP ВТБ Семья")
    parser.add_argument("--host",default="127.0.0.1")
    parser.add_argument("--port",type=int,default=8000)
    args=parser.parse_args()
    os.chdir(Path(__file__).resolve().parent)
    os.environ.setdefault("VTB_DEMO_MODE","1")
    try:
        import uvicorn
        from app.demo import ensure_demo
    except ImportError as exc:
        raise SystemExit("Не установлены зависимости. Выполните: python3 -m pip install -r requirements.txt\n"+str(exc))
    with socket.socket() as probe:
        try:probe.bind((args.host,args.port))
        except OSError:
            raise SystemExit(f"Порт {args.port} занят. Запустите: python run.py --port {args.port+1}")
    ensure_demo()
    print(f"\nОткройте http://{'127.0.0.1' if args.host=='0.0.0.0' else args.host}:{args.port}/?mobile=1")
    print("Учебные профили: Александр, Мария, Макс. Ctrl+C — остановка.\n",flush=True)
    uvicorn.run("app.main:app",host=args.host,port=args.port)


if __name__=="__main__":main()
