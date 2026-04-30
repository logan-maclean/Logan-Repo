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
    workhub_datasource: str
    workhub_base_url: str
    workhub_api_token: str

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
        workhub_datasource=os.environ.get("WORKHUB_DATASOURCE", "workhub"),
        workhub_base_url=os.environ.get("WORKHUB_BASE_URL", ""),
        workhub_api_token=os.environ.get("WORKHUB_API_TOKEN", ""),
    )
