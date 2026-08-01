from pathlib import Path

DOUYIN_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.159 Safari/537.36"
)


def load_webmssdk(js_file: Path) -> str:
    return js_file.read_text(encoding="utf-8")


def get_signature(x_ms_stub: str) -> str:
    import jsengine

    context = jsengine.jsengine()
    javascript_dom = f"""
document = {{}}
window = {{}}
navigator = {{
'userAgent': '{DOUYIN_USER_AGENT}'
}}
""".strip()
    project_root = Path(__file__).resolve().parents[2]
    sdk_path = project_root / "biliup" / "Danmaku" / "douyin_util" / "webmssdk.js"
    context.eval(javascript_dom + load_webmssdk(sdk_path))
    return str(context.eval(f"get_sign('{x_ms_stub}')"))


def main() -> None:
    print("signature:", get_signature("69a78110dbe05a916c750237d701907e"))


if __name__ == "__main__":
    main()
