"""Run the Detailing Operations Dashboard locally.

Usage:
    python run.py
Then open http://127.0.0.1:5000
"""
import os
import socket
from app import create_app

app = create_app()


def _local_ip():
    """Best-effort LAN IP that other devices can reach."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    ip = _local_ip()
    print(f"\n  Local:   http://127.0.0.1:{port}")
    if ip:
        print(f"  Network: http://{ip}:{port}")
    print()
    app.run(host="0.0.0.0", port=port, debug=True)
