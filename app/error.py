class AppError(RuntimeError):
  def __init__(self, code: str, message: str = "") -> None:
    self.code = code
    self.message = message
