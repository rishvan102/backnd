# app.py
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, RedirectResponse, JSONResponse, Response
from typing import List, Iterable
import os, io, re, json, base64
import fitz  # PyMuPDF

APP_TITLE = "NiceDay PDF API"
APP_DESC = "Backend helpers for thumbnails, page render, and PDF export with overlays."

# -----------------------------------------------------------------------------
# App & CORS
# -----------------------------------------------------------------------------
app = FastAPI(title=APP_TITLE, description=APP_DESC)

# Comma-separated env var is easiest to manage on Render
# e.g. FRONTEND_ORIGINS=https://niceday.example.com,https://www.niceday.example.com
_env = os.getenv("FRONTEND_ORIGINS", "*").strip()
ALLOWED_ORIGINS = ["*"] if _env == "*" else [o.strip() for o in _env.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------
def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def _stream_bytes(b: bytes, chunk: int = 1 << 16) -> Iterable[bytes]:
    """Yield bytes in chunks for StreamingResponse."""
    mv = memoryview(b)
    for i in range(0, len(b), chunk):
        yield mv[i : i + chunk]

def _parse_deletions(raw: str, page_count: int) -> List[int]:
    try:
        arr = json.loads(raw or "[]")
        keep = []
        for x in arr:
            try:
                i = int(x)
                if 0 <= i < page_count:
                    keep.append(i)
            except Exception:
                continue
        return list(sorted(set(keep)))
    except Exception:
        return []

def _page_idx_from_filename(name: str) -> int | None:
    """
    Accepts names like 'overlay_0.png', 'overlay-12.png', '12.png', etc.
    Returns the last integer found or None.
    """
    m = re.findall(r"(\d+)", name or "")
    return int(m[-1]) if m else None

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.get("/")
def root() -> RedirectResponse:
    # Avoid 404s on Render root probes; HEAD will also be handled automatically.
    return RedirectResponse(url="/docs")

@app.get("/favicon.ico")
def favicon() -> Response:
    # Silence browser favicon 404 noise.
    return Response(status_code=204)

@app.get("/api/health")
def health():
    return {"ok": True}

# --------- Thumbnails as data URLs (front-end friendly) ----------------------
@app.post("/api/pdf/thumbs")
async def thumbs(
    pdf: UploadFile = File(...),
    scale: float = Form(0.3),  # 0.05–1.0 is typical for thumbs
):
    try:
        scale = _clamp(scale, 0.05, 2.0)
        data = await pdf.read()
        doc = fitz.open(stream=data, filetype="pdf")
        try:
            out: List[str] = []
            for i in range(len(doc)):
                page = doc[i]
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                b = pix.tobytes("png")
                out.append("data:image/png;base64," + base64.b64encode(b).decode())
            return {"count": len(out), "thumbs": out}
        finally:
            doc.close()
    except Exception as e:
        return JSONResponse({"error": f"thumbs_failed: {e}"}, status_code=400)

# --------- High-res single page PNG (for preview if needed) ------------------
@app.post("/api/pdf/page")
async def render_page(
    pdf: UploadFile = File(...),
    page: int = Form(...),
    scale: float = Form(1.5),  # 1.25–2.0 is a good preview range
):
    try:
        scale = _clamp(scale, 0.3, 4.0)
        data = await pdf.read()
        doc = fitz.open(stream=data, filetype="pdf")
        try:
            page = max(0, min(page, len(doc) - 1))
            pix = doc[page].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            buf = io.BytesIO(pix.tobytes("png"))
            buf.seek(0)
            return StreamingResponse(buf, media_type="image/png")
        finally:
            doc.close()
    except Exception as e:
        return JSONResponse({"error": f"page_render_failed: {e}"}, status_code=400)

# --------- Export: apply overlays (full-page) + delete pages -----------------
@app.post("/api/pdf/export")
async def export_pdf(
    pdf: UploadFile = File(...),
    overlays: List[UploadFile] = File(default=[]),
    deletions: str = Form(default="[]"),  # JSON array of page indices
):
    """
    Combine original PDF + N overlay PNGs (stretched to full page) and delete
    any pages requested. Returns the edited PDF as a download.
    Overlay files should be named like overlay_0.png, overlay-3.png, 5.png etc.
    """
    try:
        pdf_bytes = await pdf.read()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            # Parse deletions (validated to existing range)
            delete_idx = set(_parse_deletions(deletions, len(doc)))

            # Apply overlays
            for ov in overlays or []:
                page_idx = _page_idx_from_filename(ov.filename or "")
                if page_idx is None:
                    continue
                if page_idx in delete_idx or not (0 <= page_idx < len(doc)):
                    continue
                img_bytes = await ov.read()
                page = doc[page_idx]
                # Stretch full overlay to page rect — this assumes front-end overlay canvas
                # matches the page aspect ratio (your editor does this).
                page.insert_image(page.rect, stream=img_bytes, keep_proportion=False)

            # Delete pages (descending order)
            for idx in sorted(delete_idx, reverse=True):
                if 0 <= idx < len(doc):
                    doc.delete_page(idx)

            # Save with standard deflation; garbage=4 cleans xref
            out = io.BytesIO()
            doc.save(out, deflate=True, garbage=4)
            out.seek(0)

            headers = {
                "Content-Disposition": 'attachment; filename="edited.pdf"',
                "Cache-Control": "no-store",
            }
            return StreamingResponse(_stream_bytes(out.getvalue()), media_type="application/pdf", headers=headers)
        finally:
            doc.close()
    except Exception as e:
        return JSONResponse({"error": f"export_failed: {e}"}, status_code=400)

# -----------------------------------------------------------------------------
# Local run (optional). On Render you use:
# uvicorn app:app --host 0.0.0.0 --port $PORT
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
