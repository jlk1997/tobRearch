import os
import shutil
import tempfile
from datetime import datetime


def test_copytree_resume_style():
    with tempfile.TemporaryDirectory() as src:
        marker = os.path.join(src, "hello.txt")
        with open(marker, "w", encoding="utf-8") as f:
            f.write("ok")
        new_save_dir = src + "_resume_" + datetime.now().strftime("%Y%m%d%H%M%S")
        shutil.copytree(src, new_save_dir)
        assert os.path.exists(os.path.join(new_save_dir, "hello.txt"))
        shutil.rmtree(new_save_dir)
