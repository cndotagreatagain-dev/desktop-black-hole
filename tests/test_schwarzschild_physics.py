from __future__ import annotations

import math

import pytest


SCHWARZSCHILD_RADIUS = 1.0


def _null_effective_potential(inverse_radius: float) -> float:
    """Return u^2 - r_s u^3 for an equatorial Schwarzschild null ray."""
    return (
        inverse_radius * inverse_radius
        - SCHWARZSCHILD_RADIUS * inverse_radius**3
    )


def _geodesic_derivative(state: tuple[float, float]) -> tuple[float, float]:
    inverse_radius, radial_slope = state
    return (
        radial_slope,
        -inverse_radius
        + 1.5 * SCHWARZSCHILD_RADIUS * inverse_radius**2,
    )


def _rk4_step(
    state: tuple[float, float],
    delta_phi: float,
) -> tuple[float, float]:
    def add_scaled(
        base: tuple[float, float],
        slope: tuple[float, float],
        scale: float,
    ) -> tuple[float, float]:
        return (base[0] + scale * slope[0], base[1] + scale * slope[1])

    k1 = _geodesic_derivative(state)
    k2 = _geodesic_derivative(add_scaled(state, k1, 0.5 * delta_phi))
    k3 = _geodesic_derivative(add_scaled(state, k2, 0.5 * delta_phi))
    k4 = _geodesic_derivative(add_scaled(state, k3, delta_phi))
    return (
        state[0]
        + delta_phi * (k1[0] + 2.0 * k2[0] + 2.0 * k3[0] + k4[0]) / 6.0,
        state[1]
        + delta_phi * (k1[1] + 2.0 * k2[1] + 2.0 * k3[1] + k4[1]) / 6.0,
    )


def _trace_from_far_field(impact_parameter: float) -> tuple[str, float]:
    initial_radius = 50.0
    inverse_radius = 1.0 / initial_radius
    radial_slope = math.sqrt(
        1.0 / impact_parameter**2
        - _null_effective_potential(inverse_radius)
    )
    state = (inverse_radius, radial_slope)
    minimum_radius = initial_radius

    for _ in range(20_000):
        state = _rk4_step(state, 0.002)
        inverse_radius = state[0]
        if inverse_radius >= 1.0 / SCHWARZSCHILD_RADIUS:
            return "captured", minimum_radius
        if inverse_radius <= 0.0:
            return "escaped", minimum_radius
        minimum_radius = min(minimum_radius, 1.0 / inverse_radius)

    raise AssertionError("reference geodesic did not terminate")


def test_photon_sphere_is_stationary_null_orbit() -> None:
    photon_sphere_radius = 1.5 * SCHWARZSCHILD_RADIUS
    inverse_radius = 1.0 / photon_sphere_radius

    assert photon_sphere_radius == pytest.approx(1.5)
    assert _geodesic_derivative((inverse_radius, 0.0))[1] == pytest.approx(
        0.0,
        abs=1.0e-12,
    )


def test_critical_impact_parameter_matches_effective_potential_peak() -> None:
    inverse_photon_radius = 1.0 / (1.5 * SCHWARZSCHILD_RADIUS)
    critical_impact = 1.0 / math.sqrt(
        _null_effective_potential(inverse_photon_radius)
    )

    assert critical_impact == pytest.approx(3.0 * math.sqrt(3.0) / 2.0)


def test_capture_and_escape_bracket_the_critical_impact_parameter() -> None:
    critical_impact = 3.0 * math.sqrt(3.0) / 2.0

    captured, captured_minimum = _trace_from_far_field(0.98 * critical_impact)
    escaped, escaped_minimum = _trace_from_far_field(1.02 * critical_impact)

    assert captured == "captured"
    assert escaped == "escaped"
    assert captured_minimum < 1.5
    assert escaped_minimum > 1.5
