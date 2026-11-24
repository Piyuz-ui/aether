"use client";

/**
 * A deep, slow field of soft light motes behind the glass UI (three.js points + a tiny shader).
 * Each mote twinkles on its own clock, far ones are smaller and dimmer, and the whole field
 * turns very slowly and shifts with the cursor for depth. Kept faint so text stays easy to read.
 */
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import type { MotionValue } from "framer-motion";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";

const VERT = /* glsl */ `
uniform float uTime; uniform float uPixel;
attribute float aSize; attribute float aPhase; attribute vec3 aColor;
varying vec3 vColor; varying float vAlpha;
void main(){
  vec3 p = position;
  p.y += sin(uTime * 0.15 + aPhase) * 0.12;
  p.x += cos(uTime * 0.11 + aPhase * 1.3) * 0.12;
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  float depth = clamp((-mv.z - 2.0) / 14.0, 0.0, 1.0);          // 0 near → 1 far
  float twinkle = 0.55 + 0.45 * sin(uTime * (0.6 + aPhase * 0.25) + aPhase * 6.28);
  vAlpha = twinkle * mix(0.9, 0.25, depth);
  vColor = aColor;
  gl_PointSize = aSize * uPixel * (13.0 / -mv.z);
  gl_Position = projectionMatrix * mv;
}`;

const FRAG = /* glsl */ `
varying vec3 vColor; varying float vAlpha;
void main(){
  vec2 c = gl_PointCoord - 0.5;
  float d = length(c);
  float a = smoothstep(0.5, 0.0, d);          // soft round mote
  a *= a;
  gl_FragColor = vec4(vColor, a * vAlpha * 0.8);
}`;

const PALETTE = ["#7dd3fc", "#38bdf8", "#a5b4fc", "#5eead4", "#e0f2fe"];

function Field({ parallax, count }: { parallax?: { x: MotionValue<number>; y: MotionValue<number> }; count: number }) {
  const points = useRef<THREE.Points>(null);
  const { camera, gl } = useThree();

  const geometry = useMemo(() => {
    const pos = new Float32Array(count * 3), col = new Float32Array(count * 3);
    const size = new Float32Array(count), phase = new Float32Array(count);
    const c = new THREE.Color();
    for (let i = 0; i < count; i++) {
      pos.set([(Math.random() - 0.5) * 22, (Math.random() - 0.5) * 13, -Math.random() * 16 + 2], i * 3);
      c.set(PALETTE[Math.floor(Math.random() * PALETTE.length)]);
      col.set([c.r, c.g, c.b], i * 3);
      size[i] = 1.2 + Math.pow(Math.random(), 3) * 4.5;      // mostly fine dust, a few larger motes
      phase[i] = Math.random() * 10;
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    g.setAttribute("aColor", new THREE.BufferAttribute(col, 3));
    g.setAttribute("aSize", new THREE.BufferAttribute(size, 1));
    g.setAttribute("aPhase", new THREE.BufferAttribute(phase, 1));
    return g;
  }, [count]);

  const material = useMemo(() => new THREE.ShaderMaterial({
    vertexShader: VERT, fragmentShader: FRAG, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { uTime: { value: 0 }, uPixel: { value: 1 } },
  }), []);
  useEffect(() => () => { geometry.dispose(); material.dispose(); }, [geometry, material]);

  useFrame((_, raw) => {
    const dt = Math.min(raw, 0.05);
    material.uniforms.uTime.value += dt;
    material.uniforms.uPixel.value = gl.getPixelRatio();
    if (points.current) points.current.rotation.y += dt * 0.008;
    // parallax: the camera drifts a little against the cursor, so near motes move more than far ones
    const px = parallax?.x.get() ?? 0, py = parallax?.y.get() ?? 0;
    camera.position.x += (px * 0.6 - camera.position.x) * Math.min(1, dt * 1.5);
    camera.position.y += (-py * 0.4 - camera.position.y) * Math.min(1, dt * 1.5);
    camera.lookAt(0, 0, -6);
  });

  return <points ref={points} geometry={geometry} material={material} />;
}

export default function Particles3D({ parallax }: { parallax?: { x: MotionValue<number>; y: MotionValue<number> } }) {
  const count = typeof window !== "undefined" && window.innerWidth < 768 ? 700 : 1400;
  return (
    <Canvas dpr={[1, 1.5]} camera={{ position: [0, 0, 5], fov: 55 }} gl={{ antialias: false, alpha: true, powerPreference: "low-power" }}
      style={{ background: "transparent" }} aria-hidden>
      <Field parallax={parallax} count={count} />
    </Canvas>
  );
}
