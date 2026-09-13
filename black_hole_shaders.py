from gargantua_scene_shader import SCENE_FRAGMENT_SHADER_SOURCE, projected_critical_radius


PROJECTED_LENS_RADIUS = projected_critical_radius()
PROJECTED_PHOTON_RADIUS = PROJECTED_LENS_RADIUS
# Keep the proxy through the core; the outer handoff extends beyond deformation
# so capture/restore occurs where the cursor has already returned to native.
CURSOR_INTERACTION_INNER_RATIO = 0.0
CURSOR_INTERACTION_OUTER_RATIO = 2.65
CURSOR_DISTORTION_HALF_WIDTH_RATIO = 0.50


FULLSCREEN_VERTEX_SHADER_SOURCE = r"""#version 330 core

out vec2 v_uv;

const vec2 POSITIONS[3] = vec2[3](
    vec2(-1.0, -1.0),
    vec2( 3.0, -1.0),
    vec2(-1.0,  3.0)
);

void main() {
    vec2 position = POSITIONS[gl_VertexID];
    v_uv = position * 0.5 + 0.5;
    gl_Position = vec4(position, 0.0, 1.0);
}
"""



CURSOR_FRAGMENT_SHADER_SOURCE = r"""#version 330 core
in vec2 v_uv;
out vec4 fragColor;

uniform vec2 u_resolution;
uniform sampler2D u_cursor_texture;
uniform vec2 u_cursor_top_left_px;
uniform vec2 u_cursor_size_px;
uniform vec2 u_pointer_px;
uniform vec2 u_lens_center_px;
uniform float u_lens_radius_px;
uniform float u_lens_half_width_px;
uniform float u_max_displacement_px;
uniform float u_max_tangent_scale;
uniform float u_pixel_ratio;
uniform vec2 u_cursor_body_axis;
uniform float u_cursor_body_extent;
uniform float u_cursor_time;

float pullWeight() {
    float halfWidth = max(u_lens_half_width_px, 0.001);
    return 1.0 - smoothstep(max(0.0, u_lens_radius_px - 0.30 * halfWidth),
        u_lens_radius_px + 2.40 * halfWidth, length(u_pointer_px-u_lens_center_px));
}
vec2 bodyAxis() {
    return length(u_cursor_body_axis)>0.001
        ? normalize(u_cursor_body_axis) : normalize(vec2(0.35,-1.0));
}
vec4 nativeSample(vec2 p) {
    vec2 local = vec2(p.x-u_cursor_top_left_px.x,u_cursor_top_left_px.y-p.y);
    if(any(lessThan(local,vec2(0)))||any(greaterThanEqual(local,u_cursor_size_px)))
        return vec4(0);
    return texture(u_cursor_texture,local/u_cursor_size_px);
}
float extensionProfile(float u,float transition) {
    return u-transition*(1.0-exp(-u/transition));
}
vec4 cursorSample(vec2 p) {
    float dpr=max(u_pixel_ratio,1.0),head=2.0*dpr;
    vec2 axis=bodyAxis(),across=vec2(-axis.y,axis.x),delta=p-u_pointer_px;
    float destinationLong=dot(delta,axis),sourceLong=destinationLong;
    float strain=(clamp(u_max_tangent_scale,1.0,2.2)-1.0)*pullWeight()
        *smoothstep(0.0,4.0*dpr,u_max_displacement_px);
    strain=min(strain,clamp(u_max_displacement_px,0.0,64.0*dpr)
        /max(extensionProfile(max(u_cursor_body_extent-head,0.0),5.0*dpr),0.001));
    if(destinationLong>head&&strain>0.00001) {
        sourceLong=head+(destinationLong-head)/(1.0+strain);
        for(int i=0;i<5;i++) {
            float u=max(0.0,sourceLong-head);
            float error=sourceLong+strain*extensionProfile(u,5.0*dpr)-destinationLong;
            sourceLong-=error/(1.0+strain*(1.0-exp(-u/(5.0*dpr))));
        }
    }
    float tail=smoothstep(head,max(head+1.0,u_cursor_body_extent),sourceLong);
    vec2 toward=u_lens_center_px-u_pointer_px;
    toward/=sqrt(dot(toward,toward)+pow(max(0.20*u_lens_radius_px,dpr),2.0));
    // Soften even subpixel changes of direction at the exact center.
    toward*=smoothstep(0.0,max(2.0*dpr,0.15*u_lens_radius_px),
        length(u_lens_center_px-u_pointer_px));
    float bend=dot(toward,across)*min(0.48*u_cursor_body_extent,24.0*dpr)
        *tail*tail*pullWeight()*smoothstep(0.0,4.0*dpr,u_max_displacement_px);
    vec4 ink=nativeSample(u_pointer_px+axis*sourceLong+across*(dot(delta,across)-bend));
    return ink*(1.0-0.32*tail*pullWeight()*smoothstep(0.0,4.0*dpr,u_max_displacement_px));
}
// A tilted, compressed orbit chart aligns the UI flow with the disk rather
// than stretching the arrow along its own shaft. This is a cursor effect,
// not a claim of tracing physical cursor-source geodesics.
vec2 chart(vec2 p) {
    return vec2(0.999550*p.x-0.029996*p.y,
                (0.029996*p.x+0.999550*p.y)/0.70);
}
vec2 unchart(vec2 p) {
    p.y*=0.70;
    return vec2(0.999550*p.x+0.029996*p.y,-0.029996*p.x+0.999550*p.y);
}
vec2 inwardPoint(float t) {
    vec2 q=chart(u_pointer_px-u_lens_center_px);
    float radius=length(q),dpr=max(u_pixel_ratio,1.0);
    float strength=pullWeight()*smoothstep(0.0,4.0*dpr,u_max_displacement_px);
    float reach=min(clamp(u_max_displacement_px,0.0,64.0*dpr),u_cursor_body_extent*1.65);
    float contraction=min(0.52,reach/max(radius,0.001))*strength;
    float turn=min(0.72,1.15*reach/max(radius,0.001))*strength
        *smoothstep(2.0*dpr,max(3.0*dpr,0.45*u_lens_radius_px),radius);
    float angle=turn*t,cs=cos(angle),sn=sin(angle);
    return u_lens_center_px+unchart(
        vec2(cs*q.x-sn*q.y,sn*q.x+cs*q.y)*(1.0-contraction*t));
}
vec4 inwardFlow(vec2 p) {
    float dpr=max(u_pixel_ratio,1.0);
    float strength=pullWeight()*smoothstep(0.0,4.0*dpr,u_max_displacement_px);
    strength*=smoothstep(2.0*dpr,max(3.0*dpr,0.25*u_lens_radius_px),
        length(u_pointer_px-u_lens_center_px));
    if(strength<0.0001||length(p-u_pointer_px)<=2.5*dpr) return vec4(0);
    float bounds=max(u_cursor_size_px.x,u_cursor_size_px.y)*2.8+64.0*dpr;
    if(length(p-u_pointer_px)>bounds) return vec4(0);
    vec4 echoes=vec4(0);
    // Three overlapping native silhouettes, softly integrated along an inward
    // path. No pointer history, separate arrow sprites or procedural light cord.
    for(int i=0;i<3;i++) {
        float age=fract(u_cursor_time*0.28+float(i)/3.0);
        float t=0.06+0.54*age;
        vec4 impression=vec4(0);
        for(int j=0;j<3;j++) {
            float s=t+(float(j)-1.0)*0.035;
            vec2 center=inwardPoint(s);
            vec2 tangent=inwardPoint(s+0.01)-inwardPoint(s-0.01);
            tangent=length(tangent)>0.001 ? normalize(tangent) : bodyAxis();
            vec2 normal=vec2(-tangent.y,tangent.x),delta=p-center;
            float elongation=1.0+strength*(0.20+1.20*age);
            float narrow=1.0-strength*(0.08+0.42*age);
            vec2 source=tangent*dot(delta,tangent)/elongation
                +normal*dot(delta,normal)/narrow;
            vec4 ink=cursorSample(u_pointer_px+source);
            ink.rgb=mix(ink.rgb,vec3(0.90,0.92,0.95)*ink.a,0.30);
            impression+=ink/3.0;
        }
        // Each impression travels inward, then renews only at zero opacity.
        // The path and real pointer do not wobble or jump at the cycle boundary.
        float fade=smoothstep(0.0,0.16,age)*(1.0-smoothstep(0.48,1.0,age));
        float opacity=0.60*strength*fade;
        impression*=opacity;
        echoes+=impression*(1.0-echoes.a);
    }
    return echoes*smoothstep(2.5*dpr,6.5*dpr,length(p-u_pointer_px));
}
void main() {
    vec2 p=v_uv*max(u_resolution,vec2(1));
    if(length(p-u_pointer_px)<=2.0*max(u_pixel_ratio,1.0)) {
        fragColor=nativeSample(p);
        return;
    }
    vec4 arrow=vec4(0);
    for(int i=0;i<4;i++) {
        vec2 offset=(vec2(float(i%2),float(i/2))-.5)*.5;
        arrow+=cursorSample(p+offset)*.25;
    }
    // Exact native sampling at the outer handoff.
    arrow=mix(nativeSample(p),arrow,smoothstep(0.0,.35,pullWeight()));
    vec4 flow=inwardFlow(p);
    fragColor=arrow+flow*(1.0-arrow.a);
}
"""
