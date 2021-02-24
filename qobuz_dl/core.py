import logging
import os
import re
import string
import sys
import time

import requests
from bs4 import BeautifulSoup as bso
from mutagen.flac import FLAC
from mutagen.mp3 import EasyMP3
from pathvalidate import sanitize_filename

import qobuz_dl.spoofbuz as spoofbuz
from qobuz_dl import downloader, qopy
from qobuz_dl.color import CYAN, OFF, RED, YELLOW, DF, RESET
from qobuz_dl.exceptions import NonStreamable
from qobuz_dl.db import create_db, handle_download_id

WEB_URL = "https://play.qobuz.com/"
ARTISTS_SELECTOR = "td.chartlist-artist > a"
TITLE_SELECTOR = "td.chartlist-name > a"
EXTENSIONS = (".mp3", ".flac")
QUALITIES = {5: "5 - MP3", 6: "6 - FLAC",
             7: "7 - 24B<96kHz", 27: "27 - 24B>96kHz"}

logger = logging.getLogger(__name__)


class PartialFormatter(string.Formatter):
    def __init__(self, missing="n/a", bad_fmt="n/a"):
        self.missing, self.bad_fmt = missing, bad_fmt

    def get_field(self, field_name, args, kwargs):
        try:
            val = super(PartialFormatter, self).get_field(field_name,
                                                          args, kwargs)
        except (KeyError, AttributeError):
            val = None, field_name
        return val

    def format_field(self, value, spec):
        if not value:
            return self.missing
        try:
            return super(PartialFormatter, self).format_field(value, spec)
        except ValueError:
            if self.bad_fmt:
                return self.bad_fmt
            raise


class QobuzDL:
    def __init__(
        self,
        directory="Qobuz Downloads",
        quality=6,
        embed_art=False,
        lucky_limit=1,
        lucky_type="album",
        interactive_limit=20,
        ignore_singles_eps=False,
        no_m3u_for_playlists=False,
        quality_fallback=True,
        cover_og_quality=False,
        no_cover=False,
        downloads_db=None,
    ):
        self.directory = self.create_dir(directory)
        self.quality = quality
        self.embed_art = embed_art
        self.lucky_limit = lucky_limit
        self.lucky_type = lucky_type
        self.interactive_limit = interactive_limit
        self.ignore_singles_eps = ignore_singles_eps
        self.no_m3u_for_playlists = no_m3u_for_playlists
        self.quality_fallback = quality_fallback
        self.cover_og_quality = cover_og_quality
        self.no_cover = no_cover
        self.downloads_db = create_db(downloads_db) if downloads_db else None

    def initialize_client(self, email, pwd, app_id, secrets):
        self.client = qopy.Client(email, pwd, app_id, secrets)
        logger.info(f"{YELLOW}Set quality: {QUALITIES[int(self.quality)]}\n")

    def get_tokens(self):
        spoofer = spoofbuz.Spoofer()
        self.app_id = spoofer.getAppId()
        self.secrets = [
            secret for secret in spoofer.getSecrets().values() if secret
        ]  # avoid empty fields

    def create_dir(self, directory=None):
        fix = os.path.normpath(directory)
        os.makedirs(fix, exist_ok=True)
        return fix

    def get_id(self, url):
        return re.match(
            r"https?://(?:w{0,3}|play|open)\.qobuz\.com/(?:(?:album|track"
            r"|artist|playlist|label)/|[a-z]{2}-[a-z]{2}/album/-?\w+(?:-\w+)*"
            r"-?/|user/library/favorites/)(\w+)",
            url,
        ).group(1)

    def get_type(self, url):
        if re.match(r'https?', url) is not None:
            url_type = url.split('/')[3]
            if url_type not in ['album', 'artist', 'playlist',
                                'track', 'label']:
                if url_type == "user":
                    url_type = url.split('/')[-1]
                else:
                    # url is from Qobuz store
                    # e.g. "https://www.qobuz.com/us-en/album/..."
                    url_type = url.split('/')[4]
        else:
            # url missing base
            # e.g. "/us-en/album/{artist}/{id}"
            url_type = url.split('/')[2]
        return url_type

    def download_from_id(self, item_id, album=True, alt_path=None):
        if handle_download_id(self.downloads_db, item_id, add_id=False):
            logger.info(
                f"{OFF}This release ID ({item_id}) was already downloaded "
                "according to the local database.\nUse the '--no-db' flag "
                "to bypass this."
            )
            return
        try:
            downloader.download_id_by_type(
                self.client,
                item_id,
                alt_path or self.directory,
                str(self.quality),
                album,
                self.embed_art,
                self.ignore_singles_eps,
                self.quality_fallback,
                self.cover_og_quality,
                self.no_cover,
            )
            handle_download_id(self.downloads_db, item_id, add_id=True)
        except (requests.exceptions.RequestException, NonStreamable) as e:
            logger.error(f"{RED}Error getting release: {e}. Skipping...")

    def handle_url(self, url):
        possibles = {
            "playlist": {
                "func": self.client.get_plist_meta,
                "iterable_key": "tracks",
            },
            "artist": {
                "func": self.client.get_artist_meta,
                "iterable_key": "albums",
            },
            "label": {
                "func": self.client.get_label_meta,
                "iterable_key": "albums",
            },
            "album": {"album": True, "func": None, "iterable_key": None},
            "track": {"album": False, "func": None, "iterable_key": None},
        }
        try:
            url_type = self.get_type(url)
            type_dict = possibles[url_type]
            item_id = self.get_id(url)
        except (KeyError, IndexError):
            logger.info(
                f'{RED}Invalid url: "{url}". Use urls from '
                'https://play.qobuz.com!'
            )
            return
        if type_dict["func"]:
            content = [item for item in type_dict["func"](item_id)]
            content_name = content[0]["name"]
            logger.info(
                f"{YELLOW}Downloading all the music from {content_name} "
                f"({url_type})!"
            )
            new_path = self.create_dir(
                os.path.join(self.directory, sanitize_filename(content_name))
            )
            items = [item[type_dict["iterable_key"]]["items"]
                     for item in content][0]
            logger.info(f"{YELLOW}{len(items)} downloads in queue")
            for item in items:
                self.download_from_id(
                    item["id"],
                    True if type_dict["iterable_key"] == "albums" else False,
                    new_path,
                )
            if url_type == "playlist":
                self.make_m3u(new_path)
        else:
            self.download_from_id(item_id, type_dict["album"])

    def download_list_of_urls(self, urls):
        if not urls or not isinstance(urls, list):
            logger.info(f"{OFF}Nothing to download")
            return
        for url in urls:
            if "last.fm" in url:
                self.download_lastfm_pl(url)
            elif os.path.isfile(url):
                self.download_from_txt_file(url)
            else:
                self.handle_url(url)

    def download_from_txt_file(self, txt_file):
        with open(txt_file, "r") as txt:
            try:
                urls = [
                    line.replace("\n", "")
                    for line in txt.readlines()
                    if not line.strip().startswith("#")
                ]
            except Exception as e:
                logger.error(f"{RED}Invalid text file: {e}")
                return
            logger.info(
                f"{YELLOW}qobuz-dl will download {len(urls)}"
                f" urls from file: {txt_file}"
            )
            self.download_list_of_urls(urls)

    def lucky_mode(self, query, download=True):
        if len(query) < 3:
            logger.info(f"{RED}Your search query is too short or invalid")
            return

        logger.info(
            f'{YELLOW}Searching {self.lucky_type}s for "{query}".\n'
            f"{YELLOW}qobuz-dl will attempt to download the first "
            f"{self.lucky_limit} results."
        )
        results = self.search_by_type(query, self.lucky_type,
                                      self.lucky_limit, True)

        if download:
            self.download_list_of_urls(results)

        return results

    def format_duration(self, duration):
        return time.strftime("%H:%M:%S", time.gmtime(duration))

    def search_by_type(self, query, item_type, limit=10, lucky=False):
        if len(query) < 3:
            logger.info("{RED}Your search query is too short or invalid")
            return

        possibles = {
            "album": {
                "func": self.client.search_albums,
                "album": True,
                "key": "albums",
                "format": "{artist[name]} - {title}",
                "requires_extra": True,
            },
            "artist": {
                "func": self.client.search_artists,
                "album": True,
                "key": "artists",
                "format": "{name} - ({albums_count} releases)",
                "requires_extra": False,
            },
            "track": {
                "func": self.client.search_tracks,
                "album": False,
                "key": "tracks",
                "format": "{performer[name]} - {title}",
                "requires_extra": True,
            },
            "playlist": {
                "func": self.client.search_playlists,
                "album": False,
                "key": "playlists",
                "format": "{name} - ({tracks_count} releases)",
                "requires_extra": False,
            },
        }

        try:
            mode_dict = possibles[item_type]
            results = mode_dict["func"](query, limit)
            iterable = results[mode_dict["key"]]["items"]
            item_list = []
            for i in iterable:
                fmt = PartialFormatter()
                text = fmt.format(mode_dict["format"], **i)
                if mode_dict["requires_extra"]:

                    text = "{} - {} [{}]".format(
                        text,
                        self.format_duration(i["duration"]),
                        "HI-RES" if i["hires_streamable"] else "LOSSLESS",
                    )

                url = "{}{}/{}".format(WEB_URL, item_type, i.get("id", ""))
                item_list.append({"text": text, "url": url} if not lucky
                                 else url)
            return item_list
        except (KeyError, IndexError):
            logger.info(f"{RED}Invalid type: {item_type}")
            return

    def interactive(self, download=True):
        try:
            from pick import pick
        except (ImportError, ModuleNotFoundError):
            if os.name == "nt":
                sys.exit(
                    'Please install curses with '
                    '"pip3 install windows-curses" to continue'
                )
            raise

        qualities = [
            {"q_string": "320", "q": 5},
            {"q_string": "Lossless", "q": 6},
            {"q_string": "Hi-res =< 96kHz", "q": 7},
            {"q_string": "Hi-Res > 96 kHz", "q": 27},
        ]

        def get_title_text(option):
            return option.get("text")

        def get_quality_text(option):
            return option.get("q_string")

        try:
            item_types = ["Albums", "Tracks", "Artists", "Playlists"]
            selected_type = pick(item_types,
                                 "I'll search for:\n[press Intro]"
                                 )[0][:-1].lower()
            logger.info(f"{YELLOW}Ok, we'll search for "
                        f"{selected_type}s{RESET}")
            final_url_list = []
            while True:
                query = input(f"{CYAN}Enter your search: [Ctrl + c to quit]\n"
                              f"-{DF} ")
                logger.info(f"{YELLOW}Searching...{RESET}")
                options = self.search_by_type(
                    query, selected_type, self.interactive_limit
                )
                if not options:
                    logger.info(f"{OFF}Nothing found{RESET}")
                    continue
                title = (
                    f'*** RESULTS FOR "{query.title()}" ***\n\n'
                    "Select [space] the item(s) you want to download "
                    "(one or more)\nPress Ctrl + c to quit\n"
                    "Don't select anything to try another search"
                )
                selected_items = pick(
                    options,
                    title,
                    multiselect=True,
                    min_selection_count=0,
                    options_map_func=get_title_text,
                )
                if len(selected_items) > 0:
                    [final_url_list.append(i[0]["url"])
                     for i in selected_items]
                    y_n = pick(
                        ["Yes", "No"],
                        "Items were added to queue to be downloaded. "
                        "Keep searching?",
                    )
                    if y_n[0][0] == "N":
                        break
                else:
                    logger.info(f"{YELLOW}Ok, try again...{RESET}")
                    continue
            if final_url_list:
                desc = (
                    "Select [intro] the quality (the quality will "
                    "be automatically\ndowngraded if the selected "
                    "is not found)"
                )
                self.quality = pick(
                    qualities,
                    desc,
                    default_index=1,
                    options_map_func=get_quality_text,
                )[0]["q"]

                if download:
                    self.download_list_of_urls(final_url_list)

                return final_url_list
        except KeyboardInterrupt:
            logger.info(f"{YELLOW}Bye")
            return

    def download_lastfm_pl(self, playlist_url):
        # Apparently, last fm API doesn't have a playlist endpoint. If you
        # find out that it has, please fix this!
        try:
            r = requests.get(playlist_url, timeout=10)
        except requests.exceptions.RequestException as e:
            logger.error(f"{RED}Playlist download failed: {e}")
            return
        soup = bso(r.content, "html.parser")
        artists = [artist.text for artist in soup.select(ARTISTS_SELECTOR)]
        titles = [title.text for title in soup.select(TITLE_SELECTOR)]

        track_list = []
        if len(artists) == len(titles) and artists:
            track_list = [
                artist + " " + title for artist, title in zip(artists, titles)
            ]

        if not track_list:
            logger.info(f"{OFF}Nothing found")
            return

        pl_title = sanitize_filename(soup.select_one("h1").text)
        pl_directory = os.path.join(self.directory, pl_title)
        logger.info(
            f"{YELLOW}Downloading playlist: {pl_title} "
            f"({len(track_list)} tracks)"
        )

        for i in track_list:
            track_id = self.get_id(self.search_by_type(i, "track", 1,
                                                       lucky=True)[0])
            if track_id:
                self.download_from_id(track_id, False, pl_directory)

        self.make_m3u(pl_directory)

    def make_m3u(self, pl_directory):
        if self.no_m3u_for_playlists:
            return

        track_list = ["#EXTM3U"]
        rel_folder = os.path.basename(os.path.normpath(pl_directory))
        pl_name = rel_folder + ".m3u"
        for local, dirs, files in os.walk(pl_directory):
            dirs.sort()
            audio_rel_files = [
                # os.path.abspath(os.path.join(local, file_))
                # os.path.join(rel_folder,
                #              os.path.basename(os.path.normpath(local)),
                #              file_)
                os.path.join(os.path.basename(os.path.normpath(local)), file_)
                for file_ in files
                if os.path.splitext(file_)[-1] in EXTENSIONS
            ]
            audio_files = [
                os.path.abspath(os.path.join(local, file_))
                for file_ in files
                if os.path.splitext(file_)[-1] in EXTENSIONS
            ]
            if not audio_files or len(audio_files) != len(audio_rel_files):
                continue

            for audio_rel_file, audio_file in zip(audio_rel_files,
                                                  audio_files):
                try:
                    pl_item = (
                        EasyMP3(audio_file)
                        if ".mp3" in audio_file
                        else FLAC(audio_file)
                    )
                    title = pl_item["TITLE"][0]
                    artist = pl_item["ARTIST"][0]
                    length = int(pl_item.info.length)
                    index = "#EXTINF:{}, {} - {}\n{}".format(
                        length, artist, title, audio_rel_file
                    )
                except:  # noqa
                    continue
                track_list.append(index)

        if len(track_list) > 1:
            with open(os.path.join(pl_directory, pl_name), "w") as pl:
                pl.write("\n\n".join(track_list))
