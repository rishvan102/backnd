# app.py
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
import json, io, re
import fitz  # PyMuPDF

app = FastAPI(title="NiceDay PDF API")

# CORS: allow your static site to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # tighten later to your domain(s)
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health():
    return {"ok": True}

def _new_index_after_deletions(orig_idx: int, deleted_sorted: List[int]) -> Optional[int]:
    """Map original page index -> new index after deletions.
       Returns None if the page was deleted.
    """
    if orig_idx in set(deleted_sorted):
        return None
    # how many deleted pages are <= this original index?
    shift = sum(1 for d in deleted_sorted if d <= orig_idx)
    return orig_idx - shift

@app.post("/api/pdf/export")
async def export_pdf(
    pdf: UploadFile = File(..., description="Original PDF"),
    deletions: str = Form("[]", description="JSON array of original indices to delete"),
    overlays: List[UploadFile] = File(default=None, description="PNG overlays named overlay_<origIndex>.png"),
):
    # 1) Read inputs
    try:
        pdf_bytes = await pdf.read()
    except Exception as e:
        raise HTTPException(400, f"Failed to read PDF: {e}")

    try:
        del_list = json.loads(deletions or "[]")
        if not isinstance(del_list, list):
            raise ValueError("deletions must be a JSON list")
        # normalize: unique, ints, in-range handled later
        deleted_sorted = sorted({int(x) for x in del_list if int(x) >= 0})
    except Exception as e:
        raise HTTPException(400, f"Invalid 'deletions': {e}")

    # Build overlay map: originalIndex -> bytes
    overlay_map = {}
    if overlays:
        for up in overlays:
            name = (up.filename or "").lower()
            m = re.search(r"overlay_(\d+)", name)
            if not m:
                # ignore files with unexpected names
                continue
            idx = int(m.group(1))
            overlay_map[idx] = await up.read()

    # 2) Open PDF
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        raise HTTPException(400, f"Could not open PDF: {e}")

    # 3) Delete pages (reverse so indices remain valid)
    for d in sorted(deleted_sorted, reverse=True):
        if 0 <= d < doc.page_count:
            doc.delete_page(d)

    # 4) Place overlays (stretch each PNG to full page)
    #    overlay filenames use ORIGINAL indices; map them to NEW indices
    for orig_idx, png_bytes in overlay_map.items():
        new_idx = _new_index_after_deletions(orig_idx, deleted_sorted)
        if new_idx is None:
            continue  # that original page was deleted
        if not (0 <= new_idx < doc.page_count):
            continue
        try:
            page = doc.load_page(new_idx)
            rect = page.rect  # full page
            # overlay=True draws on top; keep_proportion=False stretches to full page
            page.insert_image(rect, stream=png_bytes, keep_proportion=False, overlay=True)
        except Exception as e:
            # skip bad overlay but keep processing others
            print(f"Overlay failed on page {new_idx}: {e}")

    # 5) Return PDF
    try:
        out = doc.tobytes()
    except Exception as e:
        raise HTTPException(500, f"Failed to serialize PDF: {e}")
    finally:
        doc.close()

    return Response(
        content=out,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="edited.pdf"'},
    )

# Optional: simple root clarifier (avoids confusing 404 on "/")
@app.get("/")
def root():
    return {"message": "NiceDay PDF API. Use POST /api/pdf/export"}
