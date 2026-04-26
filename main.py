from __future__ import annotations

import argparse
import gzip
import re
import shutil
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


ARXIV_HOSTS = {"arxiv.org", "www.arxiv.org"}
WORKDIR = Path("workdir")
ARXIV_ID_RE = re.compile(
    r"^(?P<id>(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?)"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a folder, then populate it from metadata.txt with arXiv "
            "sources and symlinks to local paths."
        )
    )
    parser.add_argument("name", help="Folder name to create/populate under workdir/")
    parser.add_argument(
        "--metadata",
        default="metadata.txt",
        help="Metadata file to read line by line (default: metadata.txt)",
    )
    return parser.parse_args()


def unique_path(path: Path) -> Path:
    if not path.exists() and not path.is_symlink():
        return path

    for index in range(2, 10_000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists() and not candidate.is_symlink():
            return candidate

    raise RuntimeError(f"Could not find an unused path near {path}")


def sanitize_name(name: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
    return sanitized.strip("._") or "item"


def arxiv_id_from_url(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.lower() not in ARXIV_HOSTS:
        return None

    path = parsed.path.strip("/")
    for prefix in ("abs/", "pdf/", "e-print/"):
        if path.startswith(prefix):
            path = path[len(prefix) :]
            break

    if path.endswith(".pdf"):
        path = path[:-4]

    match = ARXIV_ID_RE.match(path)
    if not match:
        return None

    return match.group("id")


def download_arxiv_source(url: str, output_dir: Path) -> None:
    arxiv_id = arxiv_id_from_url(url)
    if arxiv_id is None:
        raise ValueError(f"Not an arXiv URL: {url}")

    paper_dir = unique_path(output_dir / sanitize_name(arxiv_id))

    with tempfile.TemporaryDirectory(prefix="arxiv-source-") as temp_root:
        raw_dir = Path(temp_root) / "source"
        raw_dir.mkdir()

        source_url = f"https://arxiv.org/e-print/{arxiv_id}"
        print(f"Downloading {source_url} -> {paper_dir}")

        request = urllib.request.Request(
            source_url,
            headers={"User-Agent": "metadata-source-downloader/1.0"},
        )
        with tempfile.NamedTemporaryFile(prefix="arxiv-source-") as download:
            with urllib.request.urlopen(request) as response:
                shutil.copyfileobj(response, download)
            download.flush()

            extract_source(Path(download.name), raw_dir, arxiv_id)

        cleaned_dir = clean_arxiv_source(raw_dir)
        shutil.move(str(cleaned_dir), paper_dir)


def extract_source(source_file: Path, destination: Path, arxiv_id: str) -> None:
    if tarfile.is_tarfile(source_file):
        with tarfile.open(source_file) as archive:
            archive.extractall(destination, filter="data")
        return

    if zipfile.is_zipfile(source_file):
        with zipfile.ZipFile(source_file) as archive:
            archive.extractall(destination)
        return

    try:
        with gzip.open(source_file, "rb") as compressed:
            first_bytes = compressed.read(2)
            compressed.seek(0)
            target = destination / f"{sanitize_name(arxiv_id)}.tex"
            if first_bytes:
                with target.open("wb") as output:
                    shutil.copyfileobj(compressed, output)
                return
    except (EOFError, gzip.BadGzipFile, OSError):
        pass

    shutil.copy2(source_file, destination / f"{sanitize_name(arxiv_id)}.source")


def clean_arxiv_source(source_dir: Path) -> Path:
    from arxiv_latex_cleaner.arxiv_latex_cleaner import run_arxiv_cleaner

    print(f"Cleaning arXiv source in {source_dir}")
    parameters = {
        "input_folder": str(source_dir),
        "resize_images": False,
        "im_size": 500,
        "compress_pdf": False,
        "pdf_im_resolution": 500,
        "images_allowlist": {},
        "keep_bib": False,
        "commands_to_delete": [],
        "commands_only_to_delete": [],
        "environments_to_delete": [],
        "if_exceptions": [],
        "use_external_tikz": None,
        "svg_inkscape": None,
        "convert_png_to_jpg": False,
        "png_quality": 50,
        "png_size_threshold": 0.5,
        "verbose": False,
    }
    run_arxiv_cleaner(parameters)

    cleaned_dir = source_dir.with_name(f"{source_dir.name}_arXiv")
    if not cleaned_dir.is_dir():
        raise RuntimeError(f"arxiv-latex-cleaner did not create {cleaned_dir}")

    return cleaned_dir


def create_symlink(line: str, output_dir: Path) -> None:
    target = Path(line).expanduser()
    link = unique_path(output_dir / target.name)
    print(f"Linking {link} -> {target}")
    link.symlink_to(target)


def process_metadata(metadata_file: Path, output_dir: Path) -> None:
    lines = metadata_file.read_text().splitlines()
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if arxiv_id_from_url(line):
            download_arxiv_source(line, output_dir)
        else:
            try:
                create_symlink(line, output_dir)
            except OSError as exc:
                raise RuntimeError(
                    f"Failed to create symlink for line {line_number}: {line}"
                ) from exc


def main() -> None:
    args = parse_args()
    output_name = Path(args.name)
    metadata_file = Path(args.metadata)

    if not metadata_file.is_file():
        raise FileNotFoundError(f"Metadata file does not exist: {metadata_file}")
    if output_name.is_absolute():
        raise ValueError("name must be relative so the output stays under workdir/")

    output_dir = WORKDIR / output_name
    output_dir.mkdir(parents=True, exist_ok=True)
    process_metadata(metadata_file, output_dir)


if __name__ == "__main__":
    main()
