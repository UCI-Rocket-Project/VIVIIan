import tomllib
from dataclasses import dataclass


@dataclass
class LiveTomlConfig:
    def __init__(self, config_path: str) -> None:
        self._config_path = config_path
        self._config = None

    def load_config(self) -> None:
        try:
            with open(self._config_path, "rb") as toml:
                self._config = tomllib.load(toml)
        except Exception as E:
            print(f"Raised Uncaught Exception During TOML Load {E}")

    def return_loaded_config(self, system_name) -> dict:
        if self._config == None or self._config.get(system_name) == None:
            raise Exception("No config loading base config")
        else:
            return self._config.get(system_name)
