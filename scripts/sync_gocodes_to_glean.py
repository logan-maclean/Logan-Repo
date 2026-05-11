"""Pull every GoCodes asset and index it into Glean as a custom datasource.

After this runs you can ask Glean questions like:
  - "Who has the MacBook Pro M3?"
  - "Where is asset GC-00421?"
  - "List every projector assigned to alice@example.com"
"""
from gocodes_glean import config
from gocodes_glean.gocodes import GoCodesClient
from gocodes_glean.glean import GleanIndexer


def main() -> None:
    cfg = config.load()
    gc = GoCodesClient(cfg)
    indexer = GleanIndexer(cfg)
    indexer.ensure_datasource()
    count = indexer.index(gc.list_assets())
    print(
        f"Indexed {count} GoCodes assets into Glean datasource "
        f"'{cfg.gocodes_datasource}'."
    )


if __name__ == "__main__":
    main()
