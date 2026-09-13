import math
import re


SCENE_FRAGMENT_SHADER_SOURCE = r"""#version 330 core

in vec2 v_uv;
out vec4 fragColor;

uniform vec2 u_resolution;
uniform float u_time;
uniform float u_quality;
uniform float u_debug_view;
uniform vec4 u_companion;
uniform float u_companion_light;
// Orbit radius, inclination, height, and working-state tail strength.
uniform vec4 u_companion_tail;

const float PI = 3.141592653589793;
const float TAU = 6.283185307179586;
const float SCHWARZSCHILD_RADIUS = 1.0;
const float BLACK_HOLE_MASS = 0.5;
const float PHOTON_SPHERE_RADIUS = 1.5;
const float CRITICAL_IMPACT = 2.598076211;
const float DISK_INNER = 3.0;
const float DISK_OUTER = 8.5;
const float CAMERA_DISTANCE = 50.0;
const float CAMERA_HEIGHT = 3.1;
const float FOCAL_LENGTH = 6.4;
const float SCREEN_ROLL = -0.030;
const float PATTERN_SPEED = 1.30;
const float FLOW_LIFETIME = 12.0;
const float FLOW_CARRIER_RADIUS = 4.8;
const float SUPPORT_OPACITY = 0.14;
const float HIGH_ORDER_GAIN = 4.80;
const int MAX_GEODESIC_STEPS = 420;
const int MAX_DISK_CROSSINGS = 6;

float hash12(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

float hash13(vec3 p) {
    p = fract(p * 0.1031);
    p += dot(p, p.yzx + 33.33);
    return fract((p.x + p.y) * p.z);
}

float valueNoise3(vec3 p) {
    vec3 cell = floor(p);
    vec3 local = fract(p);
    vec3 blend = local * local * (3.0 - 2.0 * local);
    float n000 = hash13(cell + vec3(0.0, 0.0, 0.0));
    float n100 = hash13(cell + vec3(1.0, 0.0, 0.0));
    float n010 = hash13(cell + vec3(0.0, 1.0, 0.0));
    float n110 = hash13(cell + vec3(1.0, 1.0, 0.0));
    float n001 = hash13(cell + vec3(0.0, 0.0, 1.0));
    float n101 = hash13(cell + vec3(1.0, 0.0, 1.0));
    float n011 = hash13(cell + vec3(0.0, 1.0, 1.0));
    float n111 = hash13(cell + vec3(1.0, 1.0, 1.0));
    float z0 = mix(
        mix(n000, n100, blend.x),
        mix(n010, n110, blend.x),
        blend.y
    );
    float z1 = mix(
        mix(n001, n101, blend.x),
        mix(n011, n111, blend.x),
        blend.y
    );
    return mix(z0, z1, blend.z);
}

float fbm3(vec3 p) {
    float value = 0.0;
    float amplitude = 0.5;
    mat3 turn = mat3(
        0.80, 0.60, 0.00,
       -0.60, 0.80, 0.00,
        0.00, 0.00, 1.00
    );
    int octaveBudget = u_quality < 0.5 ? 3 : (u_quality < 1.5 ? 4 : 5);
    for (int octave = 0; octave < 5; ++octave) {
        if (octave >= octaveBudget) {
            break;
        }
        value += amplitude * valueNoise3(p);
        p = turn * p * 2.02 + vec3(2.7, 4.1, 1.9);
        amplitude *= 0.5;
    }
    return value;
}

float orbitalOmega(float arealRadius) {
    float referenceRadius = arealRadius / 6.0;
    return 0.45 / pow(max(referenceRadius, 0.50), 0.85);
}

vec4 orbitalFlowLayer(float angle, float logRadius, vec3 seed) {
    vec2 orbit = vec2(cos(angle), sin(angle));
    float parcels = fbm3(vec3(orbit * 2.5, logRadius * 4.5) + seed);
    float eddies = fbm3(vec3(orbit * 5.0, logRadius * 9.0)
        + vec3(7.3, 1.0, 2.2) + seed);
    float streams = fbm3(vec3(
        orbit * 3.2, logRadius * 28.0 + parcels * 3.0
    ) + seed);
    float fineStreams = valueNoise3(vec3(
        orbit * 5.5, logRadius * 82.0 + eddies * 4.0
    ) + seed);
    return vec4(parcels, eddies, streams, fineStreams);
}

vec4 orbitalFlow(float angle, float arealRadius, float materialTime) {
    float carrierOmega = orbitalOmega(FLOW_CARRIER_RADIUS);
    float carrierAngle = angle + mod(materialTime * carrierOmega, TAU);
    float shearOmega = orbitalOmega(arealRadius) - carrierOmega;
    // Absolute-time differential advection winds parcels into unresolved rings.
    // Overlapping finite-lifetime fields bound that shear at any application age.
    // Each field still moves at orbitalOmega(r) while it is visible.
    float cycle = materialTime / FLOW_LIFETIME;
    float ageA = (fract(cycle + 0.5) - 0.5) * FLOW_LIFETIME;
    float ageB = (fract(cycle) - 0.5) * FLOW_LIFETIME;
    float weightA = pow(cos(PI * ageA / FLOW_LIFETIME), 2.0);
    float logRadius = log(max(arealRadius, 0.1));
    vec4 fieldA = orbitalFlowLayer(carrierAngle + ageA * shearOmega,
        logRadius, vec3(0.0));
    vec4 fieldB = orbitalFlowLayer(carrierAngle + ageB * shearOmega,
        logRadius, vec3(5.7, 1.9, 8.1));
    // A resetting field has zero weight AND zero weight derivative. The other
    // field stays visible, so no global restart, hard phase seam or radial pulse.
    vec4 flow = mix(fieldB, fieldA, weightA);
    float contrast = inversesqrt(weightA * weightA
        + (1.0 - weightA) * (1.0 - weightA));
    return clamp(vec4(0.47, 0.47, 0.47, 0.50)
        + (flow - vec4(0.47, 0.47, 0.47, 0.50)) * contrast, 0.0, 1.0);
}

vec3 thermalPalette(float radialPosition) {
    float t = clamp(radialPosition, 0.0, 1.0);
    vec3 hotWhite = vec3(6.2, 4.8, 3.1);
    vec3 warmWhite = vec3(4.8, 3.2, 1.58);
    vec3 amber = vec3(2.25, 0.78, 0.10);
    vec3 ember = vec3(0.72, 0.095, 0.006);
    vec3 color = mix(hotWhite, warmWhite, smoothstep(0.18, 0.66, t));
    color = mix(color, amber, smoothstep(0.48, 0.92, t));
    return mix(color, ember, smoothstep(0.94, 1.0, t));
}

vec2 geodesicDerivative(vec2 state) {
    float inverseRadius = state.x;
    return vec2(
        state.y,
        -inverseRadius
            + 1.5 * SCHWARZSCHILD_RADIUS
            * inverseRadius * inverseRadius
    );
}

vec2 schwarzschildRK4(vec2 state, float deltaPhi) {
    vec2 k1 = geodesicDerivative(state);
    vec2 k2 = geodesicDerivative(state + 0.5 * deltaPhi * k1);
    vec2 k3 = geodesicDerivative(state + 0.5 * deltaPhi * k2);
    vec2 k4 = geodesicDerivative(state + deltaPhi * k3);
    return state + deltaPhi * (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0;
}

vec3 orbitPosition(
    float inverseRadius,
    float phi,
    vec3 initialRadial,
    vec3 planeTangent
) {
    vec3 radialBasis = cos(phi) * initialRadial + sin(phi) * planeTangent;
    return radialBasis / max(inverseRadius, 0.00001);
}

vec3 orbitDirection(
    vec2 state,
    float phi,
    vec3 initialRadial,
    vec3 planeTangent
) {
    float inverseRadius = max(state.x, 0.00001);
    vec3 radialBasis = cos(phi) * initialRadial + sin(phi) * planeTangent;
    vec3 angularBasis = -sin(phi) * initialRadial + cos(phi) * planeTangent;
    float drdPhi = -state.y / (inverseRadius * inverseRadius);
    return normalize(drdPhi * radialBasis + angularBasis / inverseRadius);
}

vec4 diskMaterial(vec3 hit, float frequencyShift) {
    float arealRadius = length(hit.xz);
    float radialPosition = clamp(
        (arealRadius - DISK_INNER) / (DISK_OUTER - DISK_INNER),
        0.0,
        1.0
    );
    float envelope = smoothstep(
        DISK_INNER,
        DISK_INNER + 0.28,
        arealRadius
    ) * (1.0 - smoothstep(
        DISK_OUTER * 0.70,
        DISK_OUTER,
        arealRadius
    ));

    float materialTime = u_time * PATTERN_SPEED;
    vec4 flow = orbitalFlow(atan(hit.z, hit.x), arealRadius, materialTime);
    float parcels = flow.x;
    float eddies = flow.y;
    float hotParcel = smoothstep(0.31, 0.68, parcels * 0.78 + eddies * 0.22);
    float streams = flow.z;
    float fineStreams = flow.w;
    float turbulence = clamp(0.62 * parcels + 0.38 * eddies, 0.0, 1.0);
    float density = envelope * mix(0.48, 0.90, turbulence);
    float innerRim = exp(-pow((arealRadius - 3.34) / 0.24, 2.0));

    float beaming = pow(frequencyShift, 3.0);

    vec3 color = thermalPalette(radialPosition);
    color = mix(
        color * vec3(1.15, 0.72, 0.42),
        color,
        smoothstep(0.52, 0.92, frequencyShift)
    );
    color = mix(
        color,
        vec3(max(max(color.r, color.g), color.b)),
        smoothstep(1.04, 1.42, frequencyShift) * 0.34
    );
    vec3 emission = color
        * (0.20 + 1.75 * hotParcel * hotParcel + 0.85 * innerRim)
        * mix(0.62, 1.40, smoothstep(0.20, 0.72, streams))
        * mix(0.88, 1.08, fineStreams)
        * beaming;
    float opacity = 1.0 - exp(-density * 7.0);
    return vec4(emission, opacity);
}

vec4 proceduralSky(vec3 direction) {
    direction = normalize(direction);
    float longitude = atan(direction.z, direction.x) / TAU + 0.5;
    float latitude = asin(clamp(direction.y, -1.0, 1.0)) / PI + 0.5;
    vec2 gridPosition = vec2(longitude, latitude) * vec2(430.0, 215.0);
    vec2 cell = floor(gridPosition);
    vec2 local = fract(gridPosition);
    vec2 starOffset = vec2(
        hash12(cell + vec2(1.7, 9.2)),
        hash12(cell + vec2(8.3, 2.8))
    );
    float starSeed = hash12(cell + vec2(4.1, 7.7));
    float starDistance = length((local - starOffset) * vec2(1.0, 1.8));
    float starCore = 1.0 - smoothstep(0.018, 0.085, starDistance);
    float starRare = smoothstep(0.988, 0.9997, starSeed);
    float star = starCore * starRare;
    float starHeat = hash12(cell + vec2(12.4, 3.6));
    vec3 starColor = mix(
        vec3(1.20, 0.72, 0.40),
        vec3(0.68, 0.82, 1.22),
        starHeat
    ) * (2.2 + 5.0 * starSeed);

    vec3 galaxyNormal = normalize(vec3(0.18, 0.91, 0.37));
    float galaxyLatitude = abs(dot(direction, galaxyNormal));
    float galaxyBand = exp(-pow(galaxyLatitude / 0.105, 2.0));
    float galaxyNoise = fbm3(direction * 4.2 + vec3(1.3, 4.7, 2.1));
    float dustNoise = fbm3(direction * 9.0 + vec3(6.1, 0.4, 3.8));
    galaxyBand *= smoothstep(0.22, 0.82, galaxyNoise)
        * mix(0.42, 1.0, dustNoise);
    float galaxyAlpha = galaxyBand * 0.032;
    vec3 galaxyColor = mix(
        vec3(0.16, 0.20, 0.32),
        vec3(0.42, 0.25, 0.16),
        dustNoise
    ) * galaxyAlpha;
    float starAlpha = star * mix(0.22, 0.78, starSeed);
    vec3 premultiplied = galaxyColor * (1.0 - starAlpha)
        + starColor * starAlpha;
    float alpha = starAlpha + galaxyAlpha * (1.0 - starAlpha);
    return vec4(premultiplied, alpha);
}

vec3 acesToneMap(vec3 color) {
    const float a = 2.51;
    const float b = 0.03;
    const float c = 2.43;
    const float d = 0.59;
    const float e = 0.14;
    return clamp(
        (color * (a * color + b))
            / (color * (c * color + d) + e),
        0.0,
        1.0
    );
}

float companionHit(vec3 origin, vec3 segment) {
    vec3 oc=origin-u_companion.xyz;
    float lengthSquared=dot(segment,segment);
    if(lengthSquared<0.0000001) return -1.0;
    float b=dot(oc,segment), c=dot(oc,oc)-u_companion.w*u_companion.w;
    float discriminant=b*b-lengthSquared*c;
    if(discriminant<0.0) return -1.0;
    float t=(-b-sqrt(discriminant))/lengthSquared;
    return t>=0.0&&t<=1.0 ? t : -1.0;
}
vec3 companionEmission(vec3 hit,vec3 direction) {
    vec3 normal=normalize(hit-u_companion.xyz);
    vec3 light=normalize(-u_companion.xyz+vec3(0.0,1.6,0.0));
    float diffuse=max(dot(normal,light),0.0);
    vec3 halfway=normalize(light-normalize(direction));
    float specular=pow(max(dot(normal,halfway),0.0),32.0);
    float rim=pow(1.0-max(dot(normal,-normalize(direction)),0.0),3.0);
    return (vec3(0.004,0.22,0.70)*(0.55+1.25*diffuse)
        +vec3(0.60,0.66,0.65)*specular*0.38
        +vec3(0.002,0.038,0.12)*rim)*u_companion_light;
}

const float COMPANION_TAIL_ARC = 0.95;
const float COMPANION_TAIL_WIDTH = 0.14;

vec3 tailLocal(vec3 p, vec2 basis) {
    p.y -= u_companion_tail.z;
    return vec3(p.x, p.y*basis.x-p.z*basis.y,
                p.y*basis.y+p.z*basis.x);
}

// A tapered emitting tube along the orbit, sampled on the actual curved ray.
// No screen-space history, repeated sphere sprites or always-on-top overlay.
vec2 companionTailHit(vec3 origin, vec3 segment, float headPhase) {
    float r = u_companion_tail.x;
    float length2 = dot(segment, segment);
    if(length2 < 1e-8) return vec2(-1.0, 0.0);
    float a = dot(segment.xz, segment.xz);
    float b = dot(origin.xz, segment.xz);
    float c = dot(origin.xz, origin.xz)-r*r;
    float t = clamp(-dot(origin, segment)/length2, 0.0, 1.0);
    if(a > 1e-8) {
        float disc = b*b-a*c;
        if(disc >= 0.0) {
            float root = sqrt(disc);
            float t0 = clamp((-b-root)/a, 0.0, 1.0);
            float t1 = clamp((-b+root)/a, 0.0, 1.0);
            vec3 p0 = origin+segment*t0, p1 = origin+segment*t1;
            float d0 = length(vec2(length(p0.xz)-r, p0.y));
            float d1 = length(vec2(length(p1.xz)-r, p1.y));
            t = d0 < d1 ? t0 : t1;
        } else t = clamp(-b/a, 0.0, 1.0);
    }
    for(int refine=0; refine<2; ++refine) {
        vec3 p = origin+segment*t;
        vec2 radial = p.xz/max(length(p.xz), 1e-6);
        vec3 center = vec3(radial.x*r, 0.0, radial.y*r);
        t = clamp(dot(center-origin, segment)/length2, 0.0, 1.0);
    }
    vec3 p = origin+segment*t;
    float distance = length(vec2(length(p.xz)-r, p.y));
    if(distance > COMPANION_TAIL_WIDTH*2.0) return vec2(-1.0, 0.0);
    // Decreasing orbital phase: older gas lies at increasing phase.
    float age = mod(atan(p.z,p.x)-headPhase+TAU, TAU);
    if(age > COMPANION_TAIL_ARC) return vec2(-1.0, 0.0);
    float remaining = 1.0-age/COMPANION_TAIL_ARC;
    float width = mix(0.012, COMPANION_TAIL_WIDTH, pow(remaining, 0.65));
    vec3 tangent = normalize(vec3(-p.z, 0.0, p.x));
    float across = length(segment-tangent*dot(segment,tangent));
    float sigma = width/max(across, 1e-5);
    float coverage = 0.5*(tanh((1.0-t)/sigma)-tanh(-t/sigma));
    float opacity = exp(-2.0*distance*distance/(width*width))
        *pow(remaining,1.35)*smoothstep(0.0,0.012,age)
        *coverage*0.65*u_companion_tail.w;
    return vec2(t, opacity);
}

void addCompanionTail(float opacity, inout vec3 radiance,
                     inout float alpha, inout float transmittance) {
    float a = clamp(opacity, 0.0, 0.70);
    radiance += transmittance*a*vec3(0.004,0.22,0.70)*1.5*u_companion_light;
    alpha += transmittance*a;
    transmittance *= 1.0-a;
}

vec4 traceScene(vec2 screenPoint) {
    vec2 resolution = max(u_resolution, vec2(1.0));

    vec3 cameraPosition = vec3(0.0, CAMERA_HEIGHT, -CAMERA_DISTANCE);
    vec3 forward = normalize(-cameraPosition);
    vec3 rightBase = normalize(cross(vec3(0.0, 1.0, 0.0), forward));
    vec3 upBase = cross(forward, rightBase);
    float rollCos = cos(SCREEN_ROLL);
    float rollSin = sin(SCREEN_ROLL);
    vec3 right = rightBase * rollCos + upBase * rollSin;
    vec3 up = -rightBase * rollSin + upBase * rollCos;
    vec3 cameraRay = normalize(
        forward * FOCAL_LENGTH
        + right * screenPoint.x
        + up * screenPoint.y
    );

    float cameraRadius = length(cameraPosition);
    float initialInverseRadius = 1.0 / cameraRadius;
    vec3 initialRadial = cameraPosition / cameraRadius;
    float radialComponent = dot(cameraRay, initialRadial);
    vec3 transverseVector = cameraRay - radialComponent * initialRadial;
    float transverseComponent = length(transverseVector);
    vec3 planeTangent = transverseComponent > 0.00001
        ? transverseVector / transverseComponent
        : rightBase;
    float metricFactor = max(
        1.0 - SCHWARZSCHILD_RADIUS / cameraRadius,
        0.001
    );
    float impactParameter = cameraRadius * transverseComponent
        / sqrt(metricFactor);
    float initialDerivativeSquared = impactParameter > 0.0001
        ? 1.0 / (impactParameter * impactParameter)
            - initialInverseRadius * initialInverseRadius
            + SCHWARZSCHILD_RADIUS
                * initialInverseRadius * initialInverseRadius
                * initialInverseRadius
        : 1.0e6;
    vec2 state = vec2(
        initialInverseRadius,
        sqrt(max(initialDerivativeSquared, 0.0))
    );

    int stepBudget = u_quality < 0.5 ? 180 : (u_quality < 1.5 ? 280 : 420);
    float deltaPhi = u_quality < 0.5 ? 0.050
        : (u_quality < 1.5 ? 0.0375 : 0.030);
    float phi = 0.0;
    // Exact equatorial roots in the geodesic plane, not straight chords.
    float nextDiskPhi = atan(-initialRadial.y, planeTangent.y);
    if (nextDiskPhi <= 0.000001) nextDiskPhi += PI;
    float angularMomentum = -cameraRadius / sqrt(metricFactor)
        * cross(initialRadial, cameraRay).y;
    vec3 previousPosition = cameraPosition;
    vec3 lastDirection = cameraRay;
    vec3 radiance = vec3(0.0);
    float alpha = 0.0;
    float transmittance = 1.0;
    float minimumRadius = cameraRadius;
    float coronaEnergy = 0.0;
    float lastFrequencyDebug = 1.0;
    int diskCrossings = 0;
    bool captured = transverseComponent < 0.00001;
    bool escaped = false;
    bool hitCompanion = false;
    bool companionCandidate = false;
    bool tailCandidate = false;
    vec2 tailBasis = vec2(1.0,0.0);
    float tailHeadPhase = 0.0;
    vec3 tailCenter = vec3(0.0);
    float tailBound = 0.0;
#ifndef OUTPUT_DESKTOP_MAP
    if(u_companion.w>0.0&&u_debug_view<0.5) {
        vec3 planeNormal=normalize(cross(initialRadial,planeTangent));
        companionCandidate=abs(dot(u_companion.xyz,planeNormal))<=u_companion.w;
        if(u_companion_tail.w > 0.001) {
            tailBasis = vec2(cos(u_companion_tail.y),sin(u_companion_tail.y));
            vec3 localHead = tailLocal(u_companion.xyz, tailBasis);
            tailHeadPhase = atan(localHead.z,localHead.x);
            float centerPhase = tailHeadPhase+COMPANION_TAIL_ARC*0.5;
            tailCenter = vec3(cos(centerPhase)*u_companion_tail.x,
                u_companion_tail.z+sin(centerPhase)*u_companion_tail.x*tailBasis.y,
                sin(centerPhase)*u_companion_tail.x*tailBasis.x);
            tailBound = 2.0*u_companion_tail.x*sin(COMPANION_TAIL_ARC*0.25)
                        +COMPANION_TAIL_WIDTH*2.0;
            tailCandidate = abs(dot(tailCenter,planeNormal)) <= tailBound;
        }
    }
#endif

    for (int stepIndex = 0; stepIndex < MAX_GEODESIC_STEPS; ++stepIndex) {
        if (stepIndex >= stepBudget || captured || escaped) {
            break;
        }

        // TEST_PROBE_GEODESIC_STEP
        // Near-radial rays otherwise leap from the camera past the thin tail
        // (or straight into the capture condition). Refine only busy-tail rays,
        // outside r=4; the accepted idle and photon-region integrator is intact.
        float stepPhi = tailCandidate && state.x < 0.25
            ? min(deltaPhi, 0.025/max(abs(state.y),0.0001)) : deltaPhi;
        vec2 nextState = schwarzschildRK4(state, stepPhi);
        float nextPhi = phi + stepPhi;
        if (nextState.x >= 1.0 / SCHWARZSCHILD_RADIUS) {
            captured = true;
            break;
        }
        if (nextState.x <= 0.0) {
            escaped = true;
            float escapePhi = phi + stepPhi
                * state.x / max(state.x - nextState.x, 0.000001);
            lastDirection = cos(escapePhi) * initialRadial
                + sin(escapePhi) * planeTangent;
            break;
        }

        vec3 currentPosition = orbitPosition(
            nextState.x,
            nextPhi,
            initialRadial,
            planeTangent
        );
        vec3 segment = currentPosition - previousPosition;
        float segmentLength = length(segment);
        if (segmentLength > 0.00001) {
            lastDirection = segment / segmentLength;
        }
        float currentRadius = 1.0 / nextState.x;
        minimumRadius = min(minimumRadius, currentRadius);
        float companionFraction=companionCandidate
            ? companionHit(previousPosition,segment) : -1.0;
        bool diskFirst=nextDiskPhi<=nextPhi
            &&(nextDiskPhi-phi)/stepPhi<companionFraction;
        float diskFraction = nextDiskPhi<=nextPhi ? (nextDiskPhi-phi)/stepPhi : 2.0;
        vec2 tailHit = vec2(-1.0,0.0);
        if(tailCandidate && length((previousPosition+currentPosition)*0.5-tailCenter)
                <= tailBound+segmentLength*0.5) {
            vec3 localOrigin = tailLocal(previousPosition,tailBasis);
            vec3 localEnd = tailLocal(currentPosition,tailBasis);
            tailHit = companionTailHit(localOrigin,localEnd-localOrigin,tailHeadPhase);
            if(companionFraction>=0.0 && tailHit.x>companionFraction) tailHit.x=-1.0;
        }
        bool tailFirst = tailHit.x>=0.0 && tailHit.x<diskFraction;
        if(tailFirst) addCompanionTail(tailHit.y,radiance,alpha,transmittance);
        if(companionFraction>=0.0&&!diskFirst) {
            radiance+=transmittance*companionEmission(
                previousPosition+segment*companionFraction,segment);
            alpha+=transmittance;
            transmittance=0.0;
            hitCompanion=true;
            break;
        }

        vec3 midpoint = 0.5 * (previousPosition + currentPosition);
        float midpointDiskRadius = length(midpoint.xz);
        if (
            midpointDiskRadius >= DISK_INNER * 0.92
            && midpointDiskRadius <= DISK_OUTER * 1.04
            && abs(midpoint.y) < 0.90
        ) {
            float diskT = clamp(
                (midpointDiskRadius - DISK_INNER)
                    / (DISK_OUTER - DISK_INNER),
                0.0,
                1.0
            );
            float radialEnvelope = smoothstep(
                DISK_INNER * 0.92,
                DISK_INNER + 0.45,
                midpointDiskRadius
            ) * (1.0 - smoothstep(
                DISK_OUTER * 0.76,
                DISK_OUTER * 1.04,
                midpointDiskRadius
            ));
            float scaleHeight = 0.018 + 0.003 * midpointDiskRadius;
            float coronaDensity = radialEnvelope
                * exp(-abs(midpoint.y) / scaleHeight);
            float glowAlpha = clamp(
                coronaDensity * min(segmentLength, 0.45) * 0.024,
                0.0,
                0.024
            );
            vec3 glowColor = mix(
                vec3(3.8, 2.15, 0.82),
                vec3(1.2, 0.24, 0.018),
                smoothstep(0.52, 1.0, diskT)
            );
            radiance += transmittance * glowColor * glowAlpha;
            alpha += transmittance * glowAlpha;
            transmittance *= 1.0 - glowAlpha;
            coronaEnergy += glowAlpha;
        }

        bool crossedDisk = nextPhi >= nextDiskPhi;
        if (crossedDisk && diskCrossings < MAX_DISK_CROSSINGS) {
            float hitPhi = nextDiskPhi;
            vec2 hitState = schwarzschildRK4(state, nextDiskPhi - phi);
            vec3 hit = orbitPosition(hitState.x, nextDiskPhi,
                initialRadial, planeTangent);
            nextDiskPhi += PI;
            float hitRadius = length(hit.xz);
            if (hitRadius >= DISK_INNER && hitRadius <= DISK_OUTER) {
                // TEST_PROBE_DISK_CROSSING
                float omegaK = sqrt(BLACK_HOLE_MASS / pow(hitRadius, 3.0));
                // Conserved energy ratio: gravity plus longitudinal/transverse Doppler.
                float frequencyShift = sqrt(1.0 - 3.0 * BLACK_HOLE_MASS / hitRadius)
                    / (sqrt(metricFactor) * (1.0 - omegaK * angularMomentum));
                vec4 material = diskMaterial(hit, frequencyShift);
                // Higher-order images retain the same physical hit/opacity.
                // Their spectral grading and local optical bloom bring out
                // the platinum subring; no screen-space ring is introduced.
                if (hitPhi > PI && minimumRadius < PHOTON_SPHERE_RADIUS * 1.40) {
                    float criticalWeight = 1.0 - smoothstep(
                        PHOTON_SPHERE_RADIUS * 1.08,
                        PHOTON_SPHERE_RADIUS * 1.40,
                        minimumRadius
                    );
                    float peak = max(material.r, max(material.g, material.b));
                    vec3 platinum = peak * vec3(1.0, 0.94, 0.78);
                    vec3 criticalEmission = mix(material.rgb, platinum, 0.48)
                        * HIGH_ORDER_GAIN;
                    material.rgb = mix(material.rgb, criticalEmission, criticalWeight);
                }
                float layerAlpha = clamp(material.a, 0.0, 0.999);
                radiance += transmittance * material.rgb * layerAlpha;
                alpha += transmittance * layerAlpha;
                transmittance *= 1.0 - layerAlpha;
                diskCrossings += 1;

                lastFrequencyDebug = frequencyShift;
            }
        }

        if(tailHit.x>=0.0 && !tailFirst)
            addCompanionTail(tailHit.y,radiance,alpha,transmittance);
        if(companionFraction>=0.0) {
            radiance+=transmittance*companionEmission(
                previousPosition+segment*companionFraction,segment);
            alpha+=transmittance;
            transmittance=0.0;
            hitCompanion=true;
            break;
        }
        previousPosition = currentPosition;
        state = nextState;
        phi = nextPhi;

    }

    bool unresolved = !captured && !escaped && !hitCompanion;
#ifdef OUTPUT_DESKTOP_MAP
    // The desktop is a bounded UI source, not an astronomical celestial sphere.
    // Use the SAME integrated outgoing ray, projected into a local angular
    // chart. Sine bounds rear-going/winding rays without clamping to a square.
    // The window-sized source chart and outer taper are deliberate UI grading.
    float criticalSine = CRITICAL_IMPACT * sqrt(metricFactor) / cameraRadius;
    float criticalRadius = FOCAL_LENGTH * criticalSine
        / sqrt(1.0 - criticalSine * criticalSine);
    float imageRadius = length(screenPoint);
    vec2 radial = screenPoint / max(imageRadius, 0.00001);
    vec3 imageTangent = right * radial.x + up * radial.y;
    float outgoingAngle = atan(dot(lastDirection, imageTangent), dot(lastDirection, forward));
    float incomingAngle = atan(imageRadius, FOCAL_LENGTH);
    float relativeRadius = imageRadius / criticalRadius;
    float taper = 1.0 - smoothstep(1.50, 2.30, relativeRadius);
    vec2 displacement = radial * (1.50 * criticalRadius)
        * sin(outgoingAngle - incomingAngle) * taper;
    float coverage = (1.0 - smoothstep(1.95, 2.65, relativeRadius))
        * (escaped ? 1.0 : 0.0);
    return vec4(displacement, coverage, 1.0);
#endif
    if (captured || unresolved) {
        // TEST_PROBE_CAPTURE
        alpha += transmittance;
        transmittance = 0.0;
    } else {
        // TEST_PROBE_SKY
        vec4 sky = proceduralSky(lastDirection);
        radiance += transmittance * sky.rgb;
        alpha += transmittance * sky.a;
        transmittance *= 1.0 - sky.a;
    }

    // Neutral black backing only: no shell highlight or wallpaper sampling.
    // The smooth feather is fully transparent well before the window border.
    float supportMetric = length(screenPoint / vec2(1.38, 0.92));
    float supportAlpha = SUPPORT_OPACITY
        * (1.0 - smoothstep(0.36, 1.0, supportMetric));
    alpha += transmittance * supportAlpha;

    float windowMetric = length(screenPoint / vec2(1.46, 1.02));
    float windowFade = 1.0 - smoothstep(0.84, 1.04, windowMetric);
    vec2 edgeDistance = min(gl_FragCoord.xy, resolution - gl_FragCoord.xy);
    windowFade *= smoothstep(2.0, 12.0, min(edgeDistance.x, edgeDistance.y));

    if (u_debug_view >= 0.5) {
        vec3 debugColor = vec3(0.0);
        if (u_debug_view < 1.5) {
            debugColor = unresolved ? vec3(1.0, 0.0, 1.0)
                : captured ? vec3(0.95, 0.08, 0.025)
                : vec3(0.03, 0.24, 0.90);
        } else if (u_debug_view < 2.5) {
            debugColor = vec3(float(diskCrossings) / 6.0, 0.12, 0.0);
        } else if (u_debug_view < 3.5) {
            debugColor = vec3(clamp(minimumRadius / 8.0, 0.0, 1.0));
        } else if (u_debug_view < 4.5) {
            debugColor = vec3(
                clamp(lastFrequencyDebug - 0.45, 0.0, 1.0),
                clamp(1.2 - abs(lastFrequencyDebug - 1.0), 0.0, 1.0),
                clamp(1.15 - lastFrequencyDebug, 0.0, 1.0)
            );
        } else if (u_debug_view < 5.5) {
            debugColor = clamp(radiance * 0.16, 0.0, 1.0);
        } else if (u_debug_view < 6.5) {
            debugColor = 0.5 + 0.5 * normalize(lastDirection);
        } else if (u_debug_view < 7.5) {
            debugColor = vec3(transmittance);
        } else if (u_debug_view < 8.5) {
            debugColor = vec3(clamp(coronaEnergy * 4.0, 0.0, 1.0));
        } else {
            debugColor = vec3(clamp(alpha, 0.0, 1.0));
        }
        return vec4(debugColor * windowFade, windowFade);
    }

    alpha = clamp(alpha, 0.0, 1.0);
#ifdef OUTPUT_HDR
    return vec4(radiance * windowFade, alpha * windowFade);
#endif
    vec3 unassociated = radiance / max(alpha, 0.001);
    vec3 mapped = pow(
        acesToneMap(unassociated * 1.08),
        vec3(1.0 / 2.2)
    );
    float vignette = 1.0 - 0.12 * smoothstep(0.30, 1.0, windowMetric);
    mapped *= vignette;
    float grain = hash12(
        gl_FragCoord.xy + floor(u_time * 24.0) * vec2(17.0, 43.0)
    ) - 0.5;
    mapped = clamp(mapped + grain * 0.006 * mapped, 0.0, 1.0);

    // TEST_PROBE_FINAL_OUTPUT
    return vec4(mapped * alpha * windowFade, alpha * windowFade);
}

void main() {
    vec2 resolution = max(u_resolution, vec2(1.0));
    vec2 point = v_uv * 2.0 - 1.0;
    point.x *= resolution.x / resolution.y;
    float observerRadius = length(vec2(CAMERA_DISTANCE, CAMERA_HEIGHT));
    float transverse = length(point) / sqrt(FOCAL_LENGTH * FOCAL_LENGTH + dot(point, point));
    float impact = observerRadius * transverse
        / sqrt(1.0 - SCHWARZSCHILD_RADIUS / observerRadius);
    float pixelSize = 2.0 / resolution.y;
    float impactFootprint = observerRadius * pixelSize / FOCAL_LENGTH;
    // This only chooses sampling density. Every sample still traces the full
    // null geodesic; no circle/mask is added to the radiance or opacity.
#ifndef OUTPUT_DESKTOP_MAP
    if (u_debug_view < 0.5 && abs(impact - CRITICAL_IMPACT) < impactFootprint * 2.5) {
        int grid = u_quality < 0.5 ? 3 : (u_quality < 1.5 ? 6 : 8);
        vec4 sum = vec4(0.0);
        for (int sampleIndex = 0; sampleIndex < 64; ++sampleIndex) {
            if (sampleIndex >= grid * grid) break;
            vec2 offset = (vec2(float(sampleIndex % grid), float(sampleIndex / grid))
                + 0.5) / float(grid) - 0.5;
            sum += traceScene(point + offset * pixelSize);
        }
        fragColor = sum / float(grid * grid);
    } else {
        fragColor = traceScene(point);
    }
#else
    fragColor = traceScene(point);
#endif
}
"""


def projected_critical_radius() -> float:
    """Project the shader's critical impact using its finite static observer.

    Keep the literal GLSL as the source of truth for tooling and the cursor's
    bounded visual approximation; there is no independent hand-tuned radius.
    """
    def constant(name):
        match = re.search(rf"const float {re.escape(name)} = ([0-9.]+);",
                          SCENE_FRAGMENT_SHADER_SOURCE)
        if match is None:
            raise ValueError(f"Missing scene projection constant: {name}")
        return float(match.group(1))

    observer = math.hypot(constant("CAMERA_DISTANCE"), constant("CAMERA_HEIGHT"))
    sine = constant("CRITICAL_IMPACT") * math.sqrt(
        1.0 - constant("SCHWARZSCHILD_RADIUS") / observer
    ) / observer
    return constant("FOCAL_LENGTH") * sine / math.sqrt(1.0 - sine * sine)
