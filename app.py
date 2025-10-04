# app.py
import io, json, re
from typing import List, Optional

import fitz  # PyMuPDF
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

app = FastAPI()

# Lock down to your site(s)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://rishvan102.github.io",     # GitHub Pages
        "https://rishvan102.github.io"      # (same)
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health():
    return {"ok": True}

# ---------- helpers ----------
def html_color_to_rgb(color: Optional[str]):
    if not color:
        return (0, 0, 0)
    s = color.strip()
    if s.startswith("#"):
        s = s[1:]
        if len(s) == 3:
            s = "".join(ch * 2 for ch in s)
        r = int(s[0:2], 16) / 255.0
        g = int(s[2:4], 16) / 255.0
        b = int(s[4:6], 16) / 255.0
        return (r, g, b)
    return (0, 0, 0)

# ---------- 1) Inspect: get text boxes for a page ----------
@app.post("/api/pdf/inspect")
async def inspect_pdf(
    pdf: UploadFile = File(...),
    page: int = Form(...),  # 1-based
):
    data = await pdf.read()
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        raise HTTPException(400, f"Cannot open PDF: {e}")

    if page < 1 or page > len(doc):
        raise HTTPException(400, "Invalid page number")

    p = doc[page - 1]
    info = p.get_text("dict")  # blocks -> lines -> spans
    out_spans = []
    for b in info.get("blocks", []):
        for l in b.get("lines", []):
            for s in l.get("spans", []):
                # Keep only visible text (ignore empty/space-only)
                if s.get("text", "").strip():
                    # bbox in PDF points (PyMuPDF uses top-left origin)
                    x0, y0, x1, y1 = s["bbox"]
                    out_spans.append({
                        "text": s["text"],
                        "bbox": [x0, y0, x1, y1],
                        "size": s.get("size", 12),
                        "font": s.get("font", "helv"),
                    })

    return {
        "width": p.rect.width,
        "height": p.rect.height,
        "spans": out_spans
    }

# ---------- 2) Export: burn overlays + replacements + deletions ----------
@app.post("/api/pdf/export")
async def export_pdf(
    pdf: UploadFile = File(...),
    overlays: List[UploadFile] = File(default=[]),   # files named overlay_{ORIG_INDEX}.png
    deletions: str = Form(default="[]"),             # JSON list of original page indices
    replacements: str = Form(default="[]"),          # JSON list of {page, bbox, text, color?, size?, align?}
):
    data = await pdf.read()
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        raise HTTPException(400, f"Cannot open PDF: {e}")

    # Parse inputs
    try:
        deleted_pages = set(json.loads(deletions or "[]"))
        repl_list = json.loads(replacements or "[]")
    except Exception:
        raise HTTPException(400, "Bad JSON in deletions/replacements")

    # --- A) Apply text replacements on ORIGINAL indices ---
    # First redact (paint over) each bbox, then write new text inside it.
    # Using white fill for erase; adjust if you use dark pages.
    for r in repl_list:
        try:
            page_idx = int(r["page"])
            rect = fitz.Rect(*r["bbox"])
            txt = str(r["text"])
            color = html_color_to_rgb(r.get("color", "#000"))
            size = float(r.get("size", 12))
            align_map = {"left": 0, "center": 1, "right": 2, "justify": 3}
            align = align_map.get(str(r.get("align", "left")).lower(), 0)
        except Exception:
            continue

        if page_idx < 0 or page_idx >= len(doc):
            continue
        p = doc[page_idx]

        # Redact (erase) the original content in that rect
        p.add_redact_annot(rect, fill=(1, 1, 1))
        p.apply_redactions()

        # Insert new text within that rectangle
        # (font "helv" is built-in; change if you need another)
        p.insert_textbox(
            rect,
            txt,
            fontname="helv",
            fontsize=size,
            color=color,
            align=align
        )

    # --- B) Burn overlay images named by ORIGINAL page index ---
    overlay_map = {}
    index_rx = re.compile(r"overlay_(\d+)\.png$", re.IGNORECASE)
    for f in overlays:
        m = index_rx.search(f.filename or "")
        if not m:
            continue
        idx = int(m.group(1))
        overlay_map[idx] = await f.read()

    for page_idx, img_bytes in overlay_map.items():
        if page_idx < 0 or page_idx >= len(doc):
            continue
        if page_idx in deleted_pages:
            continue
        p = doc[page_idx]
        # stretch overlay to full page
        p.insert_image(p.rect, stream=img_bytes, keep_proportion=False, overlay=True)

    # --- C) Delete pages (use original indices; delete high->low) ---
    for i in sorted(deleted_pages, reverse=True):
        if 0 <= i < len(doc):
            doc.delete_page(i)

    # --- Done ---
    out = doc.tobytes()  # bytes in memory
    return StreamingResponse(
        io.BytesIO(out),
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=edited.pdf"}
    )

# Render runs with: uvicorn app:app --host 0.0.0.0 --port $PORT
