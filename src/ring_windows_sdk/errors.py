class RingError(Exception):
    """SDK failure; reason is stable, device_code/response preserve device rejection."""

    def __init__(self, reason: str, message: str = "", *, device_code=None, response=None, phase=None):
        super().__init__(message or reason)
        self.reason = reason
        self.device_code = device_code
        self.response = response
        self.phase = phase


class ProtocolError(RingError):
    def __init__(self, message: str):
        super().__init__("protocol_error", message)
