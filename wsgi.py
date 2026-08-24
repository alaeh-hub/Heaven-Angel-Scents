"""WSGI entry point for production servers.

Exposes `app` for ordinary WSGI servers. Because Socket.IO needs
long-lived connections, a production deployment also needs an async
worker instead of plain sync workers, e.g.:

    pip install gevent gevent-websocket
    gunicorn -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker -w 1 wsgi:app

(gevent rather than eventlet — eventlet's own maintainers are steering
new projects away from it, see their migration guide. gevent isn't
deprecated and Flask-SocketIO has supported it for just as long.)

With that worker, Flask-SocketIO serves its own routes through this
same WSGI app automatically — no separate process or port needed.

Local development (`py app.py`) doesn't need any of this — it uses
Flask-SocketIO's built-in threading mode via socketio.run() in app.py,
which needs no extra async library at all. `pip install
simple-websocket` there gets you real WebSocket upgrades instead of
long-polling.
"""

from app import create_app

app = create_app()
