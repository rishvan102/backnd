# app.py
import io, os, json
import fitz  # PyMuPDF
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

app = FastAPI()

# --- CORS (lock down if you want: set CORS_ORIGINS="https://your-site.com,https://other.com")
origins_env = os.getenv("CORS_ORIGINS", "*")
allow_origins = [o.strip() for o in origins_env.split(",")] if origins_env != "*" else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins, allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"]
)

@app.get("/api/health")
def health():
    return {"ok": True}

@app.post("/api/burn", response_class=Response)
async def burn(
    pdf: UploadFile = File(...),
    overlays: list[UploadFile] = File(default=[]),   # overlay_0.png, overlay_1.png ...
    keep: str | None = Form(default=None)            # JSON array of original page indexes to keep
):
    """
    Takes:
      - pdf: original PDF
      - overlays[]: PNG overlays (after deletion): overlay_0.png -> first kept page, etc.
      - keep: JSON array of original page indexes to keep (e.g., [0,1,3])
    Returns:
      - edited PDF (application/pdf)
    """
    base_bytes = await pdf.read()
    src = fitz.open(stream=base_bytes, filetype="pdf")

    # Which pages to keep?
    if keep:
        keep_idx = json.loads(keep)
    else:
        keep_idx = list(range(src.page_count))

    # Collect overlays by new page position j (0..len(keep)-1)
    overlay_map: dict[int, bytes] = {}
    for f in overlays:
        name = (f.filename or "").lower()
        # Expect "overlay_{j}.png"
        try:
            j = int(name.split("_")[-1].split(".")[0])
        except Exception:
            continue
        overlay_map[j] = await f.read()

    out = fitz.open()
    for j, orig in enumerate(keep_idx):
        # Append the original page
        out.insert_pdf(src, from_page=orig, to_page=orig)
        # If an overlay PNG exists for this kept page, burn it
        if j in overlay_map:
            pg = out[-1]  # last appended page
            # Place full-page; PNG alpha preserved
            pg.insert_image(pg.rect, stream=overlay_map[j], keep_proportion=False, overlay=True)

    data = out.tobytes()
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="edited.pdf"'}
    )

# Local dev: uvicorn app:app --reload
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=9000, reload=True)
