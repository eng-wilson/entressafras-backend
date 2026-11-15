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
