import tomli

from app.message import Struct, decode_message


class AppConfig(Struct): ...


def load_config(filename: str) -> AppConfig:
  with open(filename, "rb") as f:
    data = tomli.load(f)
  return decode_message(data, AppConfig)
