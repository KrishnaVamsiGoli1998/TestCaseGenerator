"""
zip_handler.py — ZIP Archive Extraction Utility
Extracts Python or JavaScript source files from uploaded zip archives.
"""
import os
import zipfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

MAX_ZIP_SIZE_MB = float(os.getenv("MAX_ZIP_SIZE_MB", "10"))

_EXTENSIONS = {
    "python": {".py"},
    "javascript": {".js", ".mjs"},
}
_IGNORE = {"__pycache__", ".git", "node_modules", ".DS_Store"}


def validate_zip(zip_path: str, language: str = "python") -> bool:
    """Return True if the zip is valid and contains at least one source file."""
    size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    if size_mb > MAX_ZIP_SIZE_MB:
        raise ValueError(f"Zip file exceeds maximum size of {MAX_ZIP_SIZE_MB} MB")

    exts = _EXTENSIONS.get(language, {".py"})
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                p = Path(name)
                if p.suffix in exts and not any(part in _IGNORE for part in p.parts):
                    return True
    except zipfile.BadZipFile:
        raise ValueError("Uploaded file is not a valid zip archive")

    return False


def extract_zip(zip_path: str, output_dir: str, language: str = "python") -> list:
    """
    Extract all source files of the given language from a zip archive.

    Returns:
        List of absolute paths to extracted source files.
    """
    os.makedirs(output_dir, exist_ok=True)
    exts = _EXTENSIONS.get(language, {".py"})
    extracted = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            p = Path(member.filename)
            # Skip directories, ignored folders, wrong extensions
            if member.is_dir():
                continue
            if any(part in _IGNORE for part in p.parts):
                continue
            if p.suffix not in exts:
                continue
            # Flatten to output_dir (strip subdirectory structure)
            dest_name = p.name
            dest_path = os.path.join(output_dir, dest_name)
            with zf.open(member) as src, open(dest_path, "wb") as dst:
                dst.write(src.read())
            extracted.append(dest_path)

    return extracted
