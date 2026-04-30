"""Ask Glean a question, scoped to the Workhub datasource."""
import sys

from workhub_glean import config
from workhub_glean.glean import GleanClient


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/ask_glean.py \"<question>\"")
        sys.exit(1)
    question = " ".join(sys.argv[1:])

    cfg = config.load()
    client = GleanClient(cfg)
    response = client.ask(question)
    print(client.extract_answer(response))


if __name__ == "__main__":
    main()
