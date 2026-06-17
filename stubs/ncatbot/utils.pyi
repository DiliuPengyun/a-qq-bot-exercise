"""Type stubs for ncatbot.utils"""


class _ConfigManager:
    bot_uin: int

    def __init__(self, **kwargs: object) -> None: ...


def get_config_manager() -> _ConfigManager: ...
