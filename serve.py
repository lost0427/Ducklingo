import argparse

import uvicorn

from backend.logging_config import configure_logging

parser = argparse.ArgumentParser(description="Ducklingo server")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=8000)
parser.add_argument(
    "--reload",
    action="store_true",
    help="backend hot reload (Windows: local transcription unavailable, exit may be unclean)",
)
args = parser.parse_args()

if args.reload:
    print("warning: --reload uses the selector event loop; local transcription cannot spawn subprocesses")

configure_logging()
uvicorn.run("backend.app:app", host=args.host, port=args.port, reload=args.reload, log_level="info")
