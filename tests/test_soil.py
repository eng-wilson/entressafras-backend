import pytest

from app.core.config import get_settings
from app.services.cropwat import water_by_code
from app.services.soil import SoilType, calculate_available_water_capacity


@pytest.mark.parametrize(
    ("soil_type", "root_depth_m", "expected"),
    [
        ("sandy", 1.0, 80),
        ("medium", 0.5, 75),
        (SoilType.CLAYEY, 1.2, 192),
        ("medium", 0.0, 0),
    ],
)
def test_capacity_is_available_water_times_root_depth(
    soil_type: SoilType | str, root_depth_m: float, expected: float
) -> None:
    assert calculate_available_water_capacity(soil_type, root_depth_m) == pytest.approx(expected)


def test_unknown_soil_type_lists_valid_types() -> None:
    with pytest.raises(ValueError, match="sandy, medium, clayey"):
        calculate_available_water_capacity("peat", 1.0)


def test_negative_root_depth_is_rejected() -> None:
    with pytest.raises(ValueError, match="root_depth_m"):
        calculate_available_water_capacity("sandy", -0.1)


def test_works_with_cropwat_root_depth() -> None:
    maize = water_by_code(get_settings().cropwat_tsv_path)[2175]
    capacity = calculate_available_water_capacity("medium", maize.root_depth_m.max)
    assert capacity == pytest.approx(150 * maize.root_depth_m.max)


# --- Texture-based soil water (SoilGrids + pedotransfer functions) ----------------------------


def test_saxton_rawls_loam_matches_hand_calculation() -> None:
    from app.services.soil import SoilTexture, saxton_rawls_2006

    # Loam, S = 0.40, C = 0.20, OM = 2.5 % (hand-computed with Saxton & Rawls 2006, Table 1).
    limits = saxton_rawls_2006(
        SoilTexture(sand_pct=40, silt_pct=40, clay_pct=20, organic_matter_pct=2.5)
    )
    assert limits.wilting_point == pytest.approx(0.1367, abs=5e-4)
    assert limits.field_capacity == pytest.approx(0.2796, abs=5e-4)
    assert limits.available_water_mm_per_m == pytest.approx(1000 * (0.2796 - 0.1367), abs=0.5)


def test_saxton_rawls_holds_more_water_in_finer_soils() -> None:
    from app.services.soil import SoilTexture, saxton_rawls_2006

    sand = saxton_rawls_2006(SoilTexture(90, 5, 5, 1.0)).available_water_mm_per_m
    loam = saxton_rawls_2006(SoilTexture(40, 40, 20, 1.0)).available_water_mm_per_m
    assert sand < loam


def test_zarc_pedotransfer_and_class() -> None:
    from app.services.soil import zarc_ad_class, zarc_available_water_mm_per_cm

    # Sorriso farmland, SoilGrids 0–40 cm: sand 53.0, silt 20.0, clay 27.1 → about 0.99 mm/cm.
    ad = zarc_available_water_mm_per_cm(53.0, 20.0, 27.1)
    assert ad == pytest.approx(0.99, abs=0.01)
    assert zarc_ad_class(ad) == "AD4"
    assert zarc_ad_class(0.30) is None
    assert zarc_ad_class(0.34) == "AD1"
    assert zarc_ad_class(1.40) == "AD6"


@pytest.mark.parametrize(
    ("sand", "silt", "clay", "expected"),
    [(88, 7, 5, SoilType.SANDY), (55, 18, 27, SoilType.MEDIUM), (20, 25, 55, SoilType.CLAYEY)],
)
def test_texture_class_for_surface_evaporation(sand, silt, clay, expected) -> None:
    from app.services.soil import SoilTexture, texture_class

    assert texture_class(SoilTexture(sand, silt, clay, 1.0)) == expected


def test_soil_profile_from_class_matches_legacy_capacity() -> None:
    from app.services.soil import SoilProfile

    profile = SoilProfile.from_texture_class(SoilType.MEDIUM)
    assert profile.available_water_mm(0.5) == calculate_available_water_capacity("medium", 0.5)
    assert profile.surface_evaporation.total_evaporable_water_mm == 19.0


def test_soil_profile_from_texture_uses_saxton_rawls() -> None:
    from app.services.soil import SoilProfile, SoilTexture, saxton_rawls_2006

    texture = SoilTexture(53.0, 20.0, 27.1, 1.3)
    profile = SoilProfile.from_texture(texture)
    expected = saxton_rawls_2006(texture).available_water_mm_per_m
    assert profile.available_water_mm_per_m == pytest.approx(expected)
    assert profile.texture_class == SoilType.MEDIUM
    assert profile.available_water_mm(1.0) == pytest.approx(expected)
