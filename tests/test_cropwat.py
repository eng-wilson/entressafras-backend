import pytest

from app.core.config import get_settings
from app.services.cropwat import ECOCROP_TO_CROPWAT, load_profiles, water_by_code
from app.services.ecocrop import get_repository

TSV = get_settings().cropwat_tsv_path


def test_only_fao_profiles_are_loaded() -> None:
    profiles = load_profiles(TSV)
    assert len(profiles) == 37
    assert all(name.startswith("FAO-") for name in profiles)


def test_every_mapped_profile_exists() -> None:
    assert set(ECOCROP_TO_CROPWAT.values()) <= load_profiles(TSV).keys()


def test_every_mapped_code_exists_in_ecocrop() -> None:
    repo = get_repository()
    missing = [code for code in ECOCROP_TO_CROPWAT if repo.get(code) is None]
    assert missing == []


def test_unknown_profile_in_mapping_fails_fast() -> None:
    with pytest.raises(ValueError, match="FAO-NOPE"):
        water_by_code(TSV, {1: "FAO-NOPE"})
