import io, json, os, re
from typing import List, Optional

import fitz  # PyMuPDF
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

app = FastAPI()

# CORS: allow your GitHub Pages origin (and fallback *)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://rishvan102.github.io", "*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health():
    return {"ok": True}

# ---- helpers ---------------------------------------------------------------

def parse_deletions(s: str, total_pages: int) -> List[int]:
    """Return a descending, de-duplicated list of valid page indexes to delete."""
    try:
        arr = json.loads(s)
        nums = sorted({int(x) for x in arr if 0 <= int(x) < total_pages}, reverse=True)
        return nums
    except Exception:
        return []

def index_map_after_deletes(total_pages: int, deletions_desc: List[int]):
    """Map original index -> new index after deletions."""
    deleted = set(deletions_desc)
    keep = [i for i in range(total_pages) if i not in deleted]
    return {orig: new for new, orig in enumerate(keep)}

_overlay_rx = re.compile(r"overlay_(\d+)\.", re.IGNORECASE)

# ---- main endpoint ---------------------------------------------------------

@app.post("/api/pdf/export")
async def export_pdf(
    pdf: UploadFile = File(...),
    deletions: str = Form("[]"),
    overlays: Optional[List[UploadFile]] = File(None),
):
    # Read source PDF
    src_bytes = await pdf.read()
    doc = fitz.open(stream=src_bytes, filetype="pdf")
    original_page_count = doc.page_count

    # 1) Delete pages (indexes are from the ORIGINAL PDF)
    del_idxs_desc = parse_deletions(deletions, original_page_count)
    for i in del_idxs_desc:  # delete from highest to lowest
        if 0 <= i < doc.page_count:
            doc.delete_page(i)

    # Build map: original index -> new index (after deletions)
    idx_map = index_map_after_deletes(original_page_count, del_idxs_desc)

    # 2) Burn overlays (files named overlay_<ORIGINAL_INDEX>.png)
    if overlays:
        for f in overlays:
            # extract original index from filename
            m = _overlay_rx.search(f.filename or "")
            if not m:
                continue
            orig_idx = int(m.group(1))
            if orig_idx not in idx_map:
                continue  # this page was deleted or invalid

            new_idx = idx_map[orig_idx]
            if not (0 <= new_idx < doc.page_count):
                continue

            img_bytes = await f.read()
            page = doc.load_page(new_idx)
            rect = page.rect  # full-page rect
            # place image to cover the full page (stretched—OK because aspect matches)
            page.insert_image(rect, stream=img_bytes, overlay=True, keep_proportion=False)

    # 3) Save and return
    out = io.BytesIO()
    doc.save(out, garbage=3, deflate=True)
    doc.close()
    out.seek(0)
    return Response(
        content=out.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=edited.pdf"},
    )

# Optional compatibility alias if your old front-end called /api/burn
@app.post("/api/burn")
async def burn_alias(
    pdf: UploadFile = File(...),
    deletions: str = Form("[]"),
    overlays: Optional[List[UploadFile]] = File(None),
):
    return await export_pdf(pdf=pdf, deletions=deletions, overlays=overlays)

# Local run (Render uses your start command, this helps local testing)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
