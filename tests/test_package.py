import privacyfence_cockpit


def test_version_is_a_string() -> None:
    assert isinstance(privacyfence_cockpit.__version__, str)
    assert privacyfence_cockpit.__version__
