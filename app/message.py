import msgspec
from typing import Type, Any


class Struct(msgspec.Struct, forbid_unknown_fields=True): ...


def _decode_hook(type: Type, obj: Any):
  raise NotImplementedError(f"Objects of type {type} are not supported")


def _encode_hook(obj: Any):
  raise NotImplementedError(f"Objects of type {type(obj)} are not supported")


def decode_message(data: Any, type: Type):
  return msgspec.convert(data, type=type, dec_hook=_decode_hook, strict=True)


def encode_message(obj: Any):
  return msgspec.json.encode(obj, enc_hook=_encode_hook)
