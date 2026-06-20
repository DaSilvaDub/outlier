import io
from urllib.error import HTTPError

from outlier_scrapers.api import OutlierApiClient


def test_auth_required_error_writes_no_success_artifact(tmp_path, monkeypatch):
    from outlier_scrapers import paths as paths_mod
    from outlier_scrapers.props import export_props_for_league

    monkeypatch.setattr(paths_mod, "DATA_DIR", tmp_path / "data")

    def opener(request, timeout):
        raise HTTPError(
            request.full_url,
            403,
            "forbidden",
            hdrs=None,
            fp=io.BytesIO(b'{"message":"denied"}'),
        )

    client = OutlierApiClient(storage_state={"origins": [], "cookies": []}, opener=opener)
    try:
        export_props_for_league(client, "MLB")
    except Exception:
        pass
    else:
        raise AssertionError("Expected export failure")

    assert not list((tmp_path / "data").glob("**/*props_latest.json"))

