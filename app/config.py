import tomli

from app.message import Struct, convert_message


class AppConfig(Struct): ...


def load_config(filename: str) -> AppConfig:
  with open(filename, "rb") as f:
    data = tomli.load(f)
  return convert_message(data, AppConfig)
