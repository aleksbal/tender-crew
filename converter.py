import shutil
import subprocess
import tempfile
import json
from pathlib import Path
from typing import Optional, List


def _check_tool(name: str) -> Optional[str]:
    return shutil.which(name)


def _run_cmd(cmd: list, *, check: bool = True):
    return subprocess.run(cmd, check=check)


def convert_input(path: str, *, ocr: bool = False, pandoc_ast: bool = False):
    """Convert or preprocess an input file and return a small context dict.

    Returns a dict with keys:
      - work_path: Path to file that should be consumed by downstream extractor
      - tmp_files: list of Path objects that should be cleaned up by the caller
      - pandoc_ast: parsed JSON AST (or None)

    The function intentionally only implements a minimal, well-scoped set
    of conversion helpers so it can be reused from other pipelines.
    """
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(path)

    tmp_files: List[Path] = []
    pandoc_ast = None
    work_path = src

    # Optional: produce pandoc AST for DOCX
    if pandoc_ast and src.suffix.lower() == ".docx":
        if not _check_tool("pandoc"):
            # caller can decide how to react; we simply leave pandoc_ast as None
            pandoc_ast = None
        else:
            ast_out = Path(tempfile.mkstemp(suffix=".json")[1])
            tmp_files.append(ast_out)
            _run_cmd(["pandoc", "--from", "docx", "--to", "json", "-o", str(ast_out), str(src)])
            try:
                with open(ast_out, "r", encoding="utf-8") as fh:
                    pandoc_ast = json.load(fh)
            except Exception:
                pandoc_ast = None

    # If input is PDF and --ocr requested
    if ocr and src.suffix.lower() == ".pdf":
        if not _check_tool("ocrmypdf"):
            # leave work_path as original; caller will warn
            pass
        else:
            out_pdf = Path(tempfile.mkstemp(suffix=".pdf")[1])
            tmp_files.append(out_pdf)
            _run_cmd(["ocrmypdf", str(src), str(out_pdf)])
            work_path = out_pdf

    return {"work_path": work_path, "tmp_files": tmp_files, "pandoc_ast": pandoc_ast}
