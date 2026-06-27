import os
from outlier_scrapers.environment import load_environment


def test_load_environment(monkeypatch, tmp_path):
    monkeypatch.setattr("outlier_scrapers.environment.PROJECT_ROOT", tmp_path)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "TEST_VAR1=val1\n"
        "TEST_VAR2='val2'\n"
        'TEST_VAR3="val3"\n'
        "# This is a comment\n"
        "TEST_VAR4= val4 \n"
        "TEST_VAR5=val=5\n"
        'TEST_VAR6=""\n'
        "TEST_VAR7='' \n"
    )

    # Precedence test: var already set
    monkeypatch.setenv("TEST_VAR1", "existing_val")

    # Prevent test pollution
    for k in ["TEST_VAR2", "TEST_VAR3", "TEST_VAR4", "TEST_VAR5", "TEST_VAR6", "TEST_VAR7"]:
        monkeypatch.delenv(k, raising=False)

    load_environment()

    assert os.environ["TEST_VAR1"] == "existing_val"
    assert os.environ["TEST_VAR2"] == "val2"
    assert os.environ["TEST_VAR3"] == "val3"
    assert os.environ["TEST_VAR4"] == "val4"
    assert os.environ["TEST_VAR5"] == "val=5"
    assert os.environ["TEST_VAR6"] == ""
    assert os.environ["TEST_VAR7"] == ""
    assert "This" not in os.environ


def test_missing_env_file(monkeypatch, tmp_path):
    monkeypatch.setattr("outlier_scrapers.environment.PROJECT_ROOT", tmp_path)
    # Should not raise error
    load_environment()
