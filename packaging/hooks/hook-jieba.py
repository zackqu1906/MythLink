"""The voice editor uses jieba's dictionary and Python HMM, both offline."""
from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("jieba", includes=["dict.txt"])
excludedimports = ["paddle", "jieba.lac_small"]
