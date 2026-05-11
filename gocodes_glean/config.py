import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    glean_instance: str
    glean_indexing_token: str
    glean_client_token: str
    glean_act_as_user: str
    gocodes_datasource: str
    gocodes_base_url: str
    gocodes_api_key: str
    gocodes_asset_web_url: str

    @property
    def indexing_base(self) -> str:
        return f"https://{self.glean_instance}-be.glean.com/api/index/v1"

    @property
    def client_base(self) -> str:
        return f"https://{self.glean_instance}-be.glean.com/rest/api/v1"


def load() -> Config:
    def required(key: str) -> str:
        val = os.environ.get(key, "")
        if not val:
            raise RuntimeError(f"Missing required env var: {key}")
        return val

    return Config(
        glean_instance=required("GLEAN_INSTANCE"),
        glean_indexing_token=os.environ.get("GLEAN_INDEXING_TOKEN", ""),
        glean_client_token=os.environ.get("GLEAN_CLIENT_TOKEN", ""),
        glean_act_as_user=os.environ.get("GLEAN_ACT_AS_USER", ""),
        gocodes_datasource=os.environ.get("GOCODES_DATASOURCE", "gocodes"),
        gocodes_base_url=os.environ.get(
            "GOCODES_BASE_URL", "https://api.gocodes.com/v3"
        ),
        gocodes_api_key=os.environ.get("GOCODES_API_KEY", ""),
        gocodes_asset_web_url=os.environ.get(
            "GOCODES_ASSET_WEB_URL", "https://app.gocodes.com/assets/{id}"
        ),
    )
