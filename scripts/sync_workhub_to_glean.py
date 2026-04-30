"""Sync Workhub documents into Glean as a custom datasource."""
from workhub_glean import config, workhub
from workhub_glean.glean import GleanIndexer


def main() -> None:
    cfg = config.load()
    indexer = GleanIndexer(cfg)
    indexer.ensure_datasource()
    count = indexer.index(workhub.fetch_documents(cfg))
    print(f"Indexed {count} Workhub documents into Glean datasource "
          f"'{cfg.workhub_datasource}'.")


if __name__ == "__main__":
    main()
