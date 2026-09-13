"""Small continuously orbiting status companion. No UI or capture dependencies."""
from dataclasses import dataclass
import math

ORBIT_DIRECTION = -1.0
SPHERE_RADIUS = 0.1785  # Keep the accepted sphere size and shader color.
ORBIT_INCLINATION = 0.10
OUTER_SAFE_RADIUS = 9.2
BUSY_ORBIT_RADIUS = 7.3
MIN_ORBIT_RADIUS = BUSY_ORBIT_RADIUS
BUSY_HEIGHT = 0.62
BUSY_INCLINATION = 0.02
IDLE_ORBIT_RADIUS = 9.8
TRANSITION_RATE = 1.5

# Radius, seconds per orbit, light, breathing amplitude, breathing period.
# Non-working modes retain their accepted outer orbits. Busy lifts above the
# disk before approaching; the same staged path is reversed when work ends.
# Speed is an activity cue, not a claim of a freely falling Kepler orbit.
_PROFILES = {
    "idle": (IDLE_ORBIT_RADIUS, 22.0, 1.00, 0.030, 5.2),
    "busy": (BUSY_ORBIT_RADIUS, 5.0, 1.22, 0.100, 1.8),
    "output": (OUTER_SAFE_RADIUS, 6.8, 1.24, 0.120, 1.5),
    "attention": (9.5, 15.0, 0.94, 0.120, 2.8),
    "unknown": (IDLE_ORBIT_RADIUS, 22.0, 0.55, 0.025, 5.2),
}

@dataclass
class CompanionOrbit:
    phase: float = 0.45
    radius: float = IDLE_ORBIT_RADIUS
    activity: str = "unknown"
    clock: float = 0.0
    brightness: float = 0.55
    angular_speed: float = math.tau / 22.0
    breath_phase: float = 0.0
    breath_rate: float = math.tau / 5.2
    breath_amount: float = 0.025
    hover: float = 0.0
    height: float = 0.0
    inclination: float = ORBIT_INCLINATION
    rest_radius: float | None = None

    def __post_init__(self):
        if self.rest_radius is None:
            self.rest_radius = max(OUTER_SAFE_RADIUS, self.radius)

    def tail_uniform(self) -> tuple[float, float, float, float]:
        return (self.radius, self.inclination, self.height,
                self.hover if self.hover > 0.001 else 0.0)

    def advance(self, seconds: float) -> tuple[float, float, float, float, float]:
        dt = min(max(float(seconds), 0.0), 0.10)
        radius, period, light, breath, breath_period = _PROFILES.get(
            self.activity, _PROFILES["unknown"])
        blend = -math.expm1(-dt * TRANSITION_RATE)
        rest_target = IDLE_ORBIT_RADIUS if self.activity == "busy" else radius
        self.rest_radius += (rest_target-self.rest_radius)*blend
        self.hover += (float(self.activity == "busy")-self.hover)*blend
        lift = min(1.0, max(0.0, self.hover/0.45))
        lift = lift*lift*(3.0-2.0*lift)
        approach = min(1.0, max(0.0, (self.hover-0.45)/0.55))
        approach = approach*approach*(3.0-2.0*approach)
        self.radius = self.rest_radius + (BUSY_ORBIT_RADIUS-self.rest_radius)*approach
        self.height = BUSY_HEIGHT*lift
        self.inclination = ORBIT_INCLINATION + (BUSY_INCLINATION-ORBIT_INCLINATION)*lift
        self.brightness += (light-self.brightness) * blend
        self.breath_amount += (breath-self.breath_amount) * blend
        # Integrate the easing exactly: no phase reset or sudden speed jump when
        # a hook changes state, and no need to move through the disk to speed up.
        speed = math.tau / period
        travel = speed*dt + (self.angular_speed-speed)*blend/TRANSITION_RATE
        self.angular_speed += (speed-self.angular_speed) * blend
        self.phase = (self.phase + ORBIT_DIRECTION * travel) % math.tau
        breath_rate = math.tau / breath_period
        self.breath_phase = (self.breath_phase + breath_rate*dt
                             + (self.breath_rate-breath_rate)*blend/TRANSITION_RATE) % math.tau
        self.breath_rate += (breath_rate-self.breath_rate) * blend
        self.clock += dt
        sine = math.sin(self.phase)
        # Nearly equatorial: front arc crosses the view, rear arc is genuinely
        # occluded/lensed by the existing scene, instead of circling its outline.
        return (self.radius*math.cos(self.phase),
                self.height+self.radius*sine*math.sin(self.inclination),
                self.radius*sine*math.cos(self.inclination),
                SPHERE_RADIUS, self.brightness*(1.0+self.breath_amount*math.sin(self.breath_phase)))
