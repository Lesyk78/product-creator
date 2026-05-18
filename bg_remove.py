"""Background removal via rembg + u2net + edge smoothing.

Session is reused across requests (loading u2net is ~1s, inference is ~2-5s on CPU).
"""
import base64
import io
from PIL import Image, ImageFilter
from rembg import remove, new_session
from threading import Lock

_session = None
_session_lock = Lock()


# Lazy-init the rembg u2net session, reused across requests.
# Out: shared onnxruntime InferenceSession; thread-safe via _session_lock.
def _get_session():
    global _session
    with _session_lock:
        if _session is None:
            _session = new_session("u2net")
        return _session


# Strip background via u2net + smooth alpha (blur + threshold) + composite on white.
# In: base64 jpeg/png. Out: base64 jpeg, white background, quality=95.
def remove_background_to_white(image_b64: str) -> str:
    img_bytes = base64.b64decode(image_b64)
    result_bytes = remove(img_bytes, session=_get_session())
    img = Image.open(io.BytesIO(result_bytes)).convert("RGBA")

    r, g, b, a = img.split()
    a = a.filter(ImageFilter.GaussianBlur(radius=1))
    a = a.point(lambda x: 0 if x < 30 else 255 if x > 220 else x)
    img = Image.merge("RGBA", (r, g, b, a))

    white_bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    white_bg.paste(img, mask=img.split()[3])
    final = white_bg.convert("RGB")

    buf = io.BytesIO()
    final.save(buf, format="JPEG", quality=95)
    return base64.b64encode(buf.getvalue()).decode("utf-8")
