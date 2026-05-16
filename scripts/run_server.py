"""Convenience script to start the NeuRecall HTTP service."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neurecall.http_service import run_server

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="NeuRecall board service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9147)
    parser.add_argument("--data-root", default=None)
    args = parser.parse_args()
    if args.data_root:
        os.environ["NEURECALL_DATA_ROOT"] = args.data_root
    run_server(host=args.host, port=args.port)
