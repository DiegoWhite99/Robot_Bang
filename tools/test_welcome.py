# Prueba la bienvenida sin microfono: pide /bienvenida por la terminal del
# dashboard (socket.io) e imprime lo que la App va avisando mientras habla.
#   docker exec robot-bang-3-main-1 /app/.cache/.venv/bin/python /app/tools/test_welcome.py
import time

import socketio

sio = socketio.Client(ssl_verify=False)


@sio.on("*")
def any_event(event, data):
    if event in ("terminal_response", "status", "reply", "debug", "heard"):
        print(f"{time.strftime('%H:%M:%S')} {event}: {data}")


sio.connect("https://localhost:7000", transports=["websocket"])
sio.emit("terminal", {"line": "/menu"})
time.sleep(35)
sio.disconnect()
