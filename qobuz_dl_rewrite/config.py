import logging
import os
from pprint import pformat
from typing import Optional

import click
from ruamel.yaml import YAML

from .constants import CONFIG_PATH, FOLDER_FORMAT, TRACK_FORMAT
from .exceptions import InvalidSourceError

yaml = YAML()


logger = logging.getLogger(__name__)


class Config:
    """Config class that handles command line args and config files.

    Usage:
    >>> config = Config()

    Now config contains the default settings. Let's load a config file.

    >>> config.load(CONFIG_PATH)

    Now, it has been updated. If we want to merge these with command line
    args, we pass arg keys in.

    >>> config.update(**args)
    """

    def __init__(self, config_path: Optional[str] = None):

        # DEFAULTS
        folder = "Downloads"
        quality = 6
        folder_format = FOLDER_FORMAT
        track_format = TRACK_FORMAT

        self.qobuz = {
            "enabled": True,
            "email": None,
            "password": None,
            "app_id": "",  # Avoid NoneType error
            "secrets": "",
        }
        self.tidal = {"enabled": True, "email": None, "password": None}
        self.deezer = {"enabled": True}
        self.downloads_database = None
        self.filters = {"smart_discography": False, "albums_only": False}
        self.downloads = {"folder": folder, "quality": quality}
        self.metadata = {
            "embed_cover": False,
            "large_cover": False,
            "default_comment": None,
            "remove_extra_tags": False,
        }
        self.path_format = {"folder": folder_format, "track": track_format}

        self.__path = config_path or CONFIG_PATH
        self.__loaded = False

    def save(self):
        if self.__loaded:
            info = dict()
            for k, v in self.__dict__.items():
                logger.debug("Adding value %s to %s key to config", k, v)
                if not k.startswith("_"):
                    info[k] = v

            with open(self.__path, "w") as cfg:
                logger.debug("Config saved: %s", self.__path)
                yaml.dump(info, cfg)

    def load(self):
        if not os.path.isfile(self.__path):
            logger.debug("File not found. Creating one: %s", self.__path)
            self.__loaded = True
            self.save()

            click.secho(
                "A config file has been created. Please update it "
                f"with your credentials: {self.__path}",
                fg="yellow",
            )
        else:
            logger.debug("Config file found: %s", self.__path)

        with open(self.__path) as cfg:
            self.__dict__.update(yaml.load(cfg))

        logger.debug("Config loaded")
        self.__loaded = True

    def update_from_cli(self, **kwargs):
        for category in (self.downloads, self.metadata, self.filters):
            for key in category.keys():
                if kwargs[key] is None:
                    continue

                # For debugging's sake
                og_value = category[key]
                new_value = kwargs[key] or og_value
                category[key] = new_value

                if og_value != new_value:
                    logger.debug("Updated %s config key from args: %s", key, new_value)

    @property
    def tidal_creds(self):
        return {
            "email": self.tidal["email"],
            "pwd": self.tidal["password"],
        }

    @property
    def qobuz_creds(self):
        return {
            "email": self.qobuz["email"],
            "pwd": self.qobuz["password"],
            "app_id": self.qobuz["app_id"],
            "secrets": self.qobuz["secrets"].split(","),
        }

    def creds(self, source: str):
        if source == "qobuz":
            return self.qobuz_creds
        elif source == "tidal":
            return self.tidal_creds
        elif source == "deezer":
            return dict()
        else:
            raise InvalidSourceError(source)

    def __getitem__(self, key):
        return getattr(self, key)

    def __setitem__(self, key, val):
        setattr(self, key, val)

    def __repr__(self):
        return f"Config({pformat(self.__dict__)})"
