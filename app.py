"""Local development entry point: `python app.py`.

Production uses wsgi.py (see its docstring for the gunicorn command).
"""
from heaven_angel.app import app
from heaven_angel.extensions import socketio

if __name__ == "__main__":
    socketio.run(app, debug=app.config["DEBUG"], port=5000)
