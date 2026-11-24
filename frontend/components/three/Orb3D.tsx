"use client";

/**
 * AETHER's orb in 3D (three.js via @react-three/fiber): a glossy liquid sphere.
 * Simplex noise gently displaces the surface; the shader adds a Fresnel rim, a soft
 * iridescent sheen and a studio reflection. Its mood follows the agent:
 *   idle               slow breathing, sky blue
 *   thinking           livelier ripples, periwinkle
 *   using_tool         quicker, crisper waves, seafoam
 *   awaiting_approval  pulled in and still, amber, with a slow pulse
 *   speaking           the surface ripples with the voice's loudness
 * It leans toward the cursor. Loaded with next/dynamic (ssr: false): WebGL only runs in the browser.
 */
import { Canvas, useFrame } from "@react-three/fiber";
import type { MotionValue } from "framer-motion";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";

import type { AgentState } from "@/lib/types";

const NOISE = /* glsl */ `
vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g; vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857; vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.0*floor(p*ns.z*ns.z); vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_);
  vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy; vec4 h=1.0-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0; vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y); vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x; p1*=norm.y; p2*=norm.z; p3*=norm.w;
  vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0); m=m*m;
  return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}`;

const VERT = /* glsl */ `
uniform float uTime; uniform float uAmp; uniform float uFreq; uniform float uLevel;
varying vec3 vN; varying vec3 vV; varying float vD;
${NOISE}
float field(vec3 p){
  float n = snoise(p * uFreq + vec3(0.0, uTime, uTime * 0.5));
  n += 0.4 * snoise(p * uFreq * 2.2 - vec3(uTime * 0.7));
  return n * (uAmp + uLevel * 0.12);
}
vec3 displaced(vec3 p){ vec3 d = normalize(p); return d * (1.0 + field(d)); }
void main(){
  vec3 n = normalize(position);
  vec3 p = displaced(position);
  vec3 t = normalize(cross(n, abs(n.y) < 0.99 ? vec3(0.0,1.0,0.0) : vec3(1.0,0.0,0.0)));
  vec3 b = cross(n, t);
  vec3 pt = displaced(normalize(position + t * 0.012));
  vec3 pb = displaced(normalize(position + b * 0.012));
  vec3 nn = normalize(cross(pt - p, pb - p));
  if (dot(nn, n) < 0.0) nn = -nn;
  vec4 world = modelMatrix * vec4(p, 1.0);
  vN = normalize(mat3(modelMatrix) * nn);
  vV = normalize(cameraPosition - world.xyz);
  vD = field(n);
  gl_Position = projectionMatrix * viewMatrix * world;
}`;

const FRAG = /* glsl */ `
uniform float uTime; uniform vec3 uA; uniform vec3 uB; uniform vec3 uC; uniform float uPulse;
varying vec3 vN; varying vec3 vV; varying float vD;
void main(){
  vec3 n = normalize(vN); vec3 v = normalize(vV);
  float ndv = clamp(dot(n, v), 0.0, 1.0);
  float fres = pow(1.0 - ndv, 2.4);
  // soft thin-film sheen: hue drifts with view angle and the surface ripples
  float f = fract(fres * 0.8 + vD * 1.4 + n.y * 0.15 + uTime * 0.03);
  vec3 film = f < 0.5 ? mix(uA, uB, f * 2.0) : mix(uB, uC, (f - 0.5) * 2.0);
  vec3 r = reflect(-v, n);
  vec3 sky = mix(vec3(0.02, 0.05, 0.12), vec3(0.78, 0.9, 1.0), smoothstep(-0.25, 0.95, r.y));
  float key = pow(max(dot(r, normalize(vec3(-0.45, 0.8, 0.55))), 0.0), 60.0);
  float fill = pow(max(dot(r, normalize(vec3(0.7, -0.15, 0.5))), 0.0), 18.0);
  vec3 col = vec3(0.02, 0.05, 0.11) + sky * 0.2 + film * (0.32 + fres * 1.1);
  col += key * 1.05 + fill * uB * 0.45;
  col += uA * fres * uPulse * (0.5 + 0.5 * sin(uTime * 3.0));
  gl_FragColor = vec4(col, 1.0);
}`;

type Mood = { speed: number; amp: number; freq: number; scale: number; pulse: number; a: string; b: string; c: string };
// Subtle by design: small displacement, slow clocks.
const MOODS: Record<AgentState, Mood> = {
  idle:              { speed: 0.18, amp: 0.07, freq: 1.3, scale: 1.0,  pulse: 0.0, a: "#7dd3fc", b: "#38bdf8", c: "#a5b4fc" },
  thinking:          { speed: 0.5,  amp: 0.12, freq: 1.7, scale: 1.02, pulse: 0.0, a: "#a5b4fc", b: "#818cf8", c: "#7dd3fc" },
  using_tool:        { speed: 0.42, amp: 0.1,  freq: 2.2, scale: 1.0,  pulse: 0.2, a: "#5eead4", b: "#38bdf8", c: "#a5b4fc" },
  awaiting_approval: { speed: 0.1,  amp: 0.04, freq: 1.2, scale: 0.94, pulse: 1.0, a: "#fcd34d", b: "#fb923c", c: "#fde68a" },
};

function Scene({ state, level, parallax }: {
  state: AgentState;
  level?: MotionValue<number>;
  parallax?: { x: MotionValue<number>; y: MotionValue<number> };
}) {
  const mesh = useRef<THREE.Mesh>(null);
  const live = useRef({ speed: 0.18, amp: 0.07, freq: 1.3, scale: 1, pulse: 0, clock: 0 });
  const colors = useRef({ a: new THREE.Color(MOODS.idle.a), b: new THREE.Color(MOODS.idle.b), c: new THREE.Color(MOODS.idle.c) });

  const material = useMemo(() => new THREE.ShaderMaterial({
    vertexShader: VERT, fragmentShader: FRAG,
    uniforms: {
      uTime: { value: 0 }, uAmp: { value: 0.07 }, uFreq: { value: 1.3 }, uLevel: { value: 0 }, uPulse: { value: 0 },
      uA: { value: new THREE.Color(MOODS.idle.a) }, uB: { value: new THREE.Color(MOODS.idle.b) }, uC: { value: new THREE.Color(MOODS.idle.c) },
    },
  }), []);
  const geometry = useMemo(() => new THREE.IcosahedronGeometry(1, 48), []);
  useEffect(() => () => { material.dispose(); geometry.dispose(); }, [material, geometry]);

  const targetColors = useMemo(() => ({
    a: new THREE.Color(MOODS[state].a), b: new THREE.Color(MOODS[state].b), c: new THREE.Color(MOODS[state].c),
  }), [state]);

  useFrame((_, raw) => {
    const dt = Math.min(raw, 0.05);
    const m = MOODS[state], l = live.current;
    const k = 1 - Math.exp(-dt * 2.6);  // frame-rate independent easing between moods
    l.speed += (m.speed - l.speed) * k; l.amp += (m.amp - l.amp) * k; l.freq += (m.freq - l.freq) * k;
    l.scale += (m.scale - l.scale) * k; l.pulse += (m.pulse - l.pulse) * k;
    const voice = level?.get() ?? 0;
    l.clock += dt * (l.speed + voice * 0.8);
    colors.current.a.lerp(targetColors.a, k); colors.current.b.lerp(targetColors.b, k); colors.current.c.lerp(targetColors.c, k);

    const u = material.uniforms;
    u.uTime.value = l.clock; u.uAmp.value = l.amp; u.uFreq.value = l.freq; u.uPulse.value = l.pulse;
    u.uLevel.value += (voice - u.uLevel.value) * Math.min(1, dt * 12);
    (u.uA.value as THREE.Color).copy(colors.current.a);
    (u.uB.value as THREE.Color).copy(colors.current.b);
    (u.uC.value as THREE.Color).copy(colors.current.c);

    const o = mesh.current;
    if (!o) return;
    const breathe = 1 + Math.sin(l.clock * 2.2) * 0.012;
    o.scale.setScalar(l.scale * breathe);
    o.rotation.y += dt * 0.12;
    // lean toward the cursor
    const px = parallax?.x.get() ?? 0, py = parallax?.y.get() ?? 0;
    o.rotation.x += (py * 0.35 - o.rotation.x) * Math.min(1, dt * 2.5);
    o.position.x += (px * 0.06 - o.position.x) * Math.min(1, dt * 2.5);
    o.position.y += (-py * 0.06 - o.position.y) * Math.min(1, dt * 2.5);
  });

  return <mesh ref={mesh} geometry={geometry} material={material} />;
}

export default function Orb3D(props: {
  state: AgentState;
  level?: MotionValue<number>;
  parallax?: { x: MotionValue<number>; y: MotionValue<number> };
}) {
  return (
    <Canvas dpr={[1, 2]} camera={{ position: [0, 0, 5], fov: 36 }} gl={{ antialias: true, alpha: true }}
      style={{ background: "transparent" }} aria-hidden>
      <Scene {...props} />
    </Canvas>
  );
}
