"""Ask Glean about GoCodes assets (who has it, where it is, etc.).

Usage:
  python scripts/ask_glean_assets.py "Who has the Dewalt drill?"
  python scripts/ask_glean_assets.py "Where is asset GC-00421 located?"
"""
import sys

from gocodes_glean import config
from gocodes_glean.glean import GleanClient


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: python scripts/ask_glean_assets.py "<question>"')
        sys.exit(1)
    question = " ".join(sys.argv[1:])

    cfg = config.load()
    client = GleanClient(cfg)
    response = client.ask(question)
    print(client.extract_answer(response))


if __name__ == "__main__":
    main()
