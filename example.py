# %load_ext autoreload
# %autoreload 2
from mirax import MiraxFile
from pathlib import Path

# save all tiles to some directory (warning 35.648 jpg files will be created at level 0)
file = Path(Path.cwd(), "tests", "testdata", "CMU-1.mrxs")
mxf = MiraxFile(file)
outpth = Path(Path.cwd(), "tests", "testdata", "CMU-1_as_jpg2")
outpth.mkdir(parents=True, exist_ok=True)
mxf.save_all_tiles(outpth, level=2)
