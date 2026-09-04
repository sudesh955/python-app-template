class AppError(RuntimeError):
  def __init__(self, code: str, message: str = "") -> None:
    super().__init__(code, message)
    self.code = code
    self.message = message
