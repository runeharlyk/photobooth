import io

import segno


def share_url(base: str, capture_id: str) -> str:
    return base.rstrip("/") + "/" + capture_id


def qr_svg(url: str) -> str:
    out = io.BytesIO()
    segno.make(url, error="m").save(out, kind="svg", dark="black", light="white", border=2, xmldecl=False)
    return out.getvalue().decode("utf-8")
