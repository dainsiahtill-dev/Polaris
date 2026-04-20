"""Server fixture file."""

HOST = "localhost"
PORT = 8080


def run() -> str:
    return f"server-running on {HOST}:{PORT}"
