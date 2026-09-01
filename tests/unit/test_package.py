import hyperset


def test_package_importable():
    assert hyperset.__version__


def test_v0_foundation_packages_importable():
    import hyperset.connectors  # noqa: F401
    import hyperset.db  # noqa: F401
    import hyperset.repositories  # noqa: F401
