"""Entry point for running the Flask server with SocketIO support"""
from assai.server.run import ASSAI

if __name__ == "__main__":
    server = ASSAI()
    # Use SocketIO's run method which supports WebSocket connections
    # This allows WebSocket connections and concurrent requests
    server.socketio.run(
        server.app,
        host="0.0.0.0",
        port=5001,
        debug=True,
        allow_unsafe_werkzeug=True
    )
