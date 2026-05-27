# -*- coding: utf-8 -*-
"""Safe archive extraction with path traversal protection."""

from __future__ import annotations

import os
import sys
import tarfile
import zipfile


def safe_extract_tar(tar: tarfile.TarFile, dest_dir: str) -> None:
    dest_dir = os.path.realpath(dest_dir)
    use_filter = sys.version_info >= (3, 12)
    for member in tar.getmembers():
        if member.issym() or member.islnk():
            continue
        member_path = os.path.realpath(os.path.join(dest_dir, member.name))
        if not member_path.startswith(dest_dir + os.sep) and member_path != dest_dir:
            raise ValueError(f"Unsafe path in archive: {member.name}")
        if use_filter:
            try:
                tar.extract(member, dest_dir, filter="data")
            except (AttributeError, TypeError):
                tar.extract(member, dest_dir)
        else:
            tar.extract(member, dest_dir)


def safe_extract_zip(zip_file: zipfile.ZipFile, dest_dir: str) -> None:
    dest_dir = os.path.realpath(dest_dir)
    for member in zip_file.namelist():
        member_path = os.path.realpath(os.path.join(dest_dir, member))
        if not member_path.startswith(dest_dir + os.sep) and member_path != dest_dir:
            raise ValueError(f"Unsafe path in archive: {member}")
        zip_file.extract(member, dest_dir)
