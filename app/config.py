import os

import msgspec
import tomli


class Struct(msgspec.Struct, forbid_unknown_fields=True): ...


def load_env():
  with open("etc/env") as f:
    lines = f.readlines()
  lines = [line.strip() for line in lines]
  lines = [line for line in lines if not line.startswith("#")]
  lines = [line.strip() for line in lines]
  lines = [line for line in lines if line]
  for line in lines:
    parts = line.split("=")
    assert len(parts) == 2
    k, v = parts[0].strip(), parts[1].strip()
    os.environ[k] = v


class AppConfig(Struct): ...


def load_config(filename: str) -> AppConfig:
  try:
    load_env()
  except Exception:
    pass
  with open(filename, "rb") as f:
    data = tomli.load(f)
  return msgspec.convert(data, type=AppConfig, strict=True)
