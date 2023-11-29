import os
import zlib
from io import BytesIO
from pathlib import Path
from typing import Union, Any, List, BinaryIO, Tuple
from PIL import Image
import configparser
import logging
from dataclasses import dataclass


@dataclass
class PageEntry:
    tile_index: int
    offset: int
    length: int
    file_number: int


@dataclass
class Hierarchical:
    name: str
    value: str
    pages: List[List[PageEntry]] | None
    pointer: int | None


def loadpages(fp: BinaryIO, ishierarchical=True) -> List[List[PageEntry]]:
    # expects the fp to be at the start of a page
    pages = []
    nextpg = 123
    while nextpg != 0:
        n_entries = int.from_bytes(fp.read(4), byteorder="little")
        nextpg = int.from_bytes(fp.read(4), byteorder="little")
        page = []
        for _ in range(n_entries):
            if ishierarchical:
                page.append(
                    PageEntry(
                        tile_index=int.from_bytes(fp.read(4), byteorder="little"),
                        offset=int.from_bytes(fp.read(4), byteorder="little"),
                        length=int.from_bytes(fp.read(4), byteorder="little"),
                        file_number=int.from_bytes(fp.read(4), byteorder="little"),
                    )
                )
            else:
                fp.read(4)
                fp.read(4)
                page.append(
                    PageEntry(
                        tile_index=0,
                        offset=int.from_bytes(fp.read(4), byteorder="little"),
                        length=int.from_bytes(fp.read(4), byteorder="little"),
                        file_number=int.from_bytes(fp.read(4), byteorder="little"),
                    )
                )
        pages.append(page)
        fp.seek(nextpg)
    return pages


class MiraxFile:
    def __init__(self, filepath: Union[str, os.PathLike[Any]]) -> None:
        self.logger = logging.getLogger(__name__)
        self.filepath = Path(filepath)
        # .mrxs is actually just a .jpg thumbnail. The real data is in the subfolder with the same name.
        self.thumbnail = Image.open(filepath)
        # read the config file
        config = configparser.ConfigParser()
        config.read(
            str(Path(self.filepath.parent, self.filepath.stem, "Slidedat.ini")),
            encoding="utf-8-sig",
        )
        self.config = dict()
        sections = config.sections()
        for section in sections:
            items = config.items(section)
            self.config[section] = dict(items)
        # count and name the hierarchical data
        hierkeys = self.config["HIERARCHICAL"]
        self.hierarchicals = []
        for i in range(int(self.config["HIERARCHICAL"]["hier_count"])):
            for j in range(int(self.config["HIERARCHICAL"][f"hier_{i}_count"])):
                self.hierarchicals.append(
                    Hierarchical(
                        name=self.config["HIERARCHICAL"][f"hier_{i}_name"],
                        value=self.config["HIERARCHICAL"][f"hier_{i}_val_{j}"],
                        pages=None,
                        pointer=None,
                    )
                )
        self.nonhierarchicals = []
        for i in range(int(self.config["HIERARCHICAL"]["nonhier_count"])):
            for j in range(int(self.config["HIERARCHICAL"][f"nonhier_{i}_count"])):
                self.nonhierarchicals.append(
                    Hierarchical(
                        name=self.config["HIERARCHICAL"][f"nonhier_{i}_name"],
                        value=self.config["HIERARCHICAL"][f"nonhier_{i}_val_{j}"],
                        pages=None,
                        pointer=None,
                    )
                )

        # read the data in index.dat to have PageEntries
        self.__readindex()
        self.version = self.config["GENERAL"]["current_slide_version"]

    def __readindex(self):
        pth = Path(self.filepath.parent, self.filepath.stem, "Index.dat")
        with open(pth, "rb") as fp:
            # verify version
            v1 = self.config["GENERAL"]["slide_version"]
            v2 = fp.read(len(v1)).decode("utf-8")
            if v1 != v2:
                self.logger.warning(
                    f"version from slidedat.ini is {v1} but version from index.dat is {v2}"
                )
            # verify UUID
            uuid1 = self.config["GENERAL"]["slide_id"]
            uuid2 = fp.read(len(uuid1)).decode("utf-8")
            assert uuid1 == uuid2
            # get pointers
            hier_root = int.from_bytes(fp.read(4), byteorder="little")
            nonhier_root = int.from_bytes(fp.read(4), byteorder="little")
            fp.seek(hier_root)
            for h in self.hierarchicals:
                h.pointer = int.from_bytes(fp.read(4), byteorder="little")
                if h.pointer == 0:
                    h.pointer = None
            for h in self.hierarchicals:
                if h.pointer:
                    fp.seek(h.pointer)
                    h.pages = loadpages(fp, ishierarchical=True)
            fp.seek(nonhier_root)
            for h in self.nonhierarchicals:
                h.pointer = int.from_bytes(fp.read(4), byteorder="little")
                if h.pointer == 0:
                    h.pointer = None
            for h in self.nonhierarchicals:
                if h.pointer:
                    fp.seek(h.pointer)
                    h.pages = loadpages(fp, ishierarchical=False)

    def get_page_entry_as_image(self, pe: PageEntry) -> Image:
        pagebytes = self.get_page_entry(pe)
        return Image.open(BytesIO(pagebytes))

    def get_page_entry(self, pe: PageEntry) -> bytes:
        filename = self.config["DATAFILE"][f"file_{pe.file_number}"]
        with open(Path(self.filepath.parent, self.filepath.stem, filename), "rb") as fp:
            fp.seek(pe.offset)
            pagebytes = fp.read(pe.length)
        if pagebytes[0:3] == b'x\x9C\xED':  # data is using zlib compression
            return zlib.decompress(pagebytes)
        return pagebytes

    def decode_tiles(self, pagebytes: bytes) -> List[Tuple[int, int, bool]]:
        if len(pagebytes) % 9 != 0:
            self.logger.error(
                f"INCORRECT NR OF BYTES FOR DECODE_TILES {len(pagebytes)}"
            )
        coords = []
        for start in range(0, len(pagebytes), 9):
            flag = pagebytes[start] == 1
            x = int.from_bytes(
                pagebytes[start + 1 : start + 5], byteorder="little", signed=True
            )
            y = int.from_bytes(
                pagebytes[start + 5 : start + 9], byteorder="little", signed=True
            )
            coords.append((x, y, flag))
        return coords

    def save_all_tiles(self, pth: Path = Path.cwd(), level=0):
        if self.hierarchicals[level].name != "Slide zoom level":
            self.logger.error("Not a zoomlevel")
            return
        for page in self.hierarchicals[level].pages:
            for pe in page:
                xy = self.get_tile_xy(pe)
                im = self.get_page_entry_as_image(pe)
                im.save(Path(pth, f"x{xy['x']}_y{xy['y']}.jpg"))

    def get_tile_xy(self, pe: PageEntry):
        across = int(self.config["GENERAL"]["imagenumber_x"])
        return {"x": pe.tile_index % across, "y": pe.tile_index // across}
