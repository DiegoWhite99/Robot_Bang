# Prueba las 5 caras sin microfono: manda /cara <guia> por la terminal del
# dashboard (socket.io) y muestra lo que contesta la App.
#   docker exec robot-bang-3-main-1 /app/.cache/.venv/bin/python /app/tools/test_faces.py
import time

import socketio

GUIAS = ["carmel", "cesia", "cori", "cristal", "crispi"]
sio = socketio.Client(ssl_verify=False)


@sio.on("*")
def any_event(event, data):
    if event == "terminal_response":
        print("  <-", data)


sio.connect("https://localhost:7000", transports=["websocket"])
for g in GUIAS:
    print("->", "/cara", g)
    sio.emit("terminal", {"line": f"/cara {g}"})
    time.sleep(8)
sio.disconnect()
