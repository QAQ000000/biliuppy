import importlib


def test_tars_modules_import_from_current_package() -> None:
    modules = (
        "biliup.common.tars.core",
        "biliup.common.tars.EndpointF",
        "biliup.common.tars.__async",
        "biliup.common.tars.__servantproxy",
    )

    for module in modules:
        assert importlib.import_module(module)
