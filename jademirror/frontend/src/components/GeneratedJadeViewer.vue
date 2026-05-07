<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as THREE from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'

const props = defineProps({
  imageSrc: {
    type: String,
    default: '',
  },
  jade: {
    type: Object,
    default: null,
  },
  modelUrl: {
    type: String,
    default: '',
  },
  multiViews: {
    type: Array,
    default: () => [],
  },
})

const emit = defineEmits(['trigger-sound'])

const containerRef = ref(null)

let renderer = null
let scene = null
let camera = null
let group = null
let jadeMesh = null
let glbModel = null
let shadowMesh = null
let frameId = null
let dragging3d = false
let pointerId = null
let pressTimer = null
let pressTriggeredHold = false
let startX = 0
let startY = 0
let lastMoveX = 0
let activeTexture = null
let orderedTextures = []
let lastBlendIdxA = -1
let autoRotateSpeed = 0.004
let rotationVelocity = 0
let multiViewMaterial = null
let defaultTexture = null
let clock = null

const MV_VERT = `
varying vec2 vUv;
varying vec3 vWorldNormal;
varying vec3 vViewDir;
varying vec3 vWorldPos;

void main() {
  vUv = uv;
  vec4 worldPos = modelMatrix * vec4(position, 1.0);
  vWorldPos = worldPos.xyz;
  vWorldNormal = normalize(mat3(modelMatrix) * normal);
  vViewDir = normalize(cameraPosition - worldPos.xyz);
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`

const MV_FRAG = `
uniform sampler2D uTexA;
uniform sampler2D uTexB;
uniform float uBlend;
uniform float uTime;

varying vec2 vUv;
varying vec3 vWorldNormal;
varying vec3 vViewDir;
varying vec3 vWorldPos;

vec3 ACESFilm(vec3 x) {
  float a = 2.51; float b = 0.03; float c = 2.43; float d = 0.59; float e = 0.14;
  return clamp((x*(a*x+b))/(x*(c*x+d)+e), 0.0, 1.0);
}

void main() {
  vec3 N = normalize(vWorldNormal);
  vec3 V = normalize(vViewDir);

  float sb = smoothstep(0.0, 1.0, uBlend);
  vec4 tA = texture2D(uTexA, vUv);
  vec4 tB = texture2D(uTexB, vUv);
  vec3 base = mix(tA.rgb, tB.rgb, sb);

  vec3 L1 = normalize(vec3(3.0, 4.0, 5.0));
  vec3 L2 = normalize(vec3(-3.0, 1.0, 3.0));
  vec3 L3 = normalize(vec3(0.0, -1.0, -3.0));
  vec3 L4 = normalize(vec3(0.0, 4.0, 0.5));

  float d1 = max(dot(N, L1), 0.0);
  float d2 = max(dot(N, L2), 0.0);
  float d3 = max(dot(N, L3), 0.0);
  float d4 = max(dot(N, L4), 0.0);

  vec3 H1 = normalize(L1 + V);
  float sp = pow(max(dot(N, H1), 0.0), 128.0);
  float spB = pow(max(dot(N, H1), 0.0), 32.0);

  float fresnel = pow(1.0 - max(dot(N, V), 0.0), 4.0);

  float sss = pow(max(dot(-V, L1), 0.0), 4.0) * 0.45;
  vec3 sssC = vec3(0.62, 0.82, 0.72) * sss;

  float sss2 = pow(max(dot(-V, L2), 0.0), 3.0) * 0.2;
  vec3 sssC2 = vec3(0.55, 0.78, 0.68) * sss2;

  vec3 amb = base * 0.22;
  vec3 dif = base * (d1*0.58 + d2*0.14 + d3*0.04 + d4*0.08);
  vec3 spe = vec3(1.0,0.98,0.95) * (sp*0.75 + spB*0.12);
  vec3 cc  = vec3(0.95,0.97,0.96) * sp * 0.35;
  vec3 rim = vec3(0.80,0.93,0.86) * fresnel * 0.55;
  vec3 sssF = sssC + sssC2;

  vec3 col = amb + dif + spe + cc + rim + sssF;
  col = ACESFilm(col * 1.12);
  col = pow(col, vec3(1.0/2.2));

  gl_FragColor = vec4(col, 0.97);
}
`

function ensureDefaultTexture() {
  if (defaultTexture) return defaultTexture
  const c = document.createElement('canvas')
  c.width = 2; c.height = 2
  const ctx = c.getContext('2d')
  ctx.fillStyle = '#e8f0ec'
  ctx.fillRect(0, 0, 2, 2)
  defaultTexture = new THREE.CanvasTexture(c)
  defaultTexture.colorSpace = THREE.SRGBColorSpace
  return defaultTexture
}

function getJadeType(jade) {
  const name = String(jade?.name || '')
  if (name.includes('琮')) return 'cong'
  if (name.includes('璧')) return 'bi'
  if (name.includes('环')) return 'huan'
  if (name.includes('璜')) return 'huang'
  if (name.includes('勒')) return 'le'
  if (name.includes('牌')) return 'pai'
  if (name.includes('佩') || name.includes('珮')) return 'pei'
  return 'pei'
}

function normalizeCapUVs(geometry) {
  const pos = geometry.attributes.position
  const uv = geometry.attributes.uv
  if (!pos || !uv) return
  const depth = (() => {
    let dMin = Infinity, dMax = -Infinity
    for (let i = 0; i < pos.count; i++) { const z = pos.getZ(i); if (z < dMin) dMin = z; if (z > dMax) dMax = z }
    return { min: dMin, max: dMax, half: (dMax - dMin) / 2 }
  })()
  const topZ = depth.max - depth.half * 0.25
  const bottomZ = depth.min + depth.half * 0.25
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
  for (let i = 0; i < pos.count; i++) {
    const z = pos.getZ(i)
    if (z < bottomZ || z > topZ) {
      const x = pos.getX(i), y = pos.getY(i)
      if (x < minX) minX = x; if (x > maxX) maxX = x
      if (y < minY) minY = y; if (y > maxY) maxY = y
    }
  }
  const rangeX = maxX - minX || 1, rangeY = maxY - minY || 1
  const scale = Math.max(rangeX, rangeY)
  const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2
  const pad = 0.03
  for (let i = 0; i < uv.count; i++) {
    const z = pos.getZ(i)
    if (z > topZ || z < bottomZ) {
      uv.setX(i, pad + (1 - 2 * pad) * ((pos.getX(i) - cx) / scale + 0.5))
      uv.setY(i, pad + (1 - 2 * pad) * ((pos.getY(i) - cy) / scale + 0.5))
    } else {
      uv.setX(i, 0)
      uv.setY(i, 0)
    }
  }
  uv.needsUpdate = true
}

function clearMeshes() {
  if (!group) return
  if (jadeMesh) {
    group.remove(jadeMesh)
    if (jadeMesh.geometry) jadeMesh.geometry.dispose()
    if (jadeMesh.material) {
      if (Array.isArray(jadeMesh.material)) jadeMesh.material.forEach((m) => m.dispose())
      else jadeMesh.material.dispose()
    }
    jadeMesh = null
  }
  if (glbModel) {
    group.remove(glbModel)
    glbModel.traverse((child) => {
      if (child.isMesh) {
        if (child.geometry) child.geometry.dispose()
        if (child.material) {
          if (Array.isArray(child.material)) child.material.forEach((m) => m.dispose())
          else child.material.dispose()
        }
      }
    })
    glbModel = null
  }
  if (shadowMesh) {
    if (shadowMesh.parent) shadowMesh.parent.remove(shadowMesh)
    if (shadowMesh.geometry) shadowMesh.geometry.dispose()
    if (shadowMesh.material) shadowMesh.material.dispose()
    shadowMesh = null
  }
  if (activeTexture) { activeTexture.dispose(); activeTexture = null }
  if (multiViewMaterial) { multiViewMaterial.dispose(); multiViewMaterial = null }
  orderedTextures.forEach((t) => { if (t) t.dispose() })
  orderedTextures = []
  lastBlendIdxA = -1
  rotationVelocity = 0
}

function buildJadeGeometry(type) {
  let geometry
  switch (type) {
    case 'bi': {
      const s = new THREE.Shape(); s.absarc(0, 0, 1.5, 0, Math.PI * 2, false)
      const hole = new THREE.Path(); hole.absarc(0, 0, 0.28, 0, Math.PI * 2, true); s.holes.push(hole)
      geometry = new THREE.ExtrudeGeometry(s, { depth: 0.12, bevelEnabled: true, bevelThickness: 0.03, bevelSize: 0.03, bevelSegments: 3, curveSegments: 64 }); break
    }
    case 'huan': {
      const s = new THREE.Shape(); s.absarc(0, 0, 1.2, 0, Math.PI * 2, false)
      const hole = new THREE.Path(); hole.absarc(0, 0, 0.68, 0, Math.PI * 2, true); s.holes.push(hole)
      geometry = new THREE.ExtrudeGeometry(s, { depth: 0.1, bevelEnabled: true, bevelThickness: 0.03, bevelSize: 0.03, bevelSegments: 3, curveSegments: 64 }); break
    }
    case 'cong': {
      const s = new THREE.Shape(), cw = 0.82, cr = 0.06
      s.moveTo(-cw + cr, -cw); s.lineTo(cw - cr, -cw); s.quadraticCurveTo(cw, -cw, cw, -cw + cr)
      s.lineTo(cw, cw - cr); s.quadraticCurveTo(cw, cw, cw - cr, cw); s.lineTo(-cw + cr, cw)
      s.quadraticCurveTo(-cw, cw, -cw, cw - cr); s.lineTo(-cw, -cw + cr); s.quadraticCurveTo(-cw, -cw, -cw + cr, -cw)
      const hole = new THREE.Path(); hole.absarc(0, 0, 0.28, 0, Math.PI * 2, true); s.holes.push(hole)
      geometry = new THREE.ExtrudeGeometry(s, { depth: 2.0, bevelEnabled: true, bevelThickness: 0.03, bevelSize: 0.03, bevelSegments: 3, curveSegments: 24 }); break
    }
    case 'huang': {
      const s = new THREE.Shape(); s.absarc(0, 0, 1.35, Math.PI * 0.18, Math.PI * 0.82, false)
      const hole = new THREE.Path(); hole.absarc(0, 0, 0.82, Math.PI * 0.82, Math.PI * 0.18, true); s.holes.push(hole)
      geometry = new THREE.ExtrudeGeometry(s, { depth: 0.16, bevelEnabled: true, bevelThickness: 0.04, bevelSize: 0.04, bevelSegments: 4, curveSegments: 48 }); break
    }
    case 'le': {
      const s = new THREE.Shape(); s.moveTo(0, -1.3); s.quadraticCurveTo(0.52, -1.3, 0.52, -0.85)
      s.lineTo(0.52, 0.85); s.quadraticCurveTo(0.52, 1.3, 0, 1.3); s.quadraticCurveTo(-0.52, 1.3, -0.52, 0.85)
      s.lineTo(-0.52, -0.85); s.quadraticCurveTo(-0.52, -1.3, 0, -1.3)
      geometry = new THREE.ExtrudeGeometry(s, { depth: 0.2, bevelEnabled: true, bevelThickness: 0.04, bevelSize: 0.04, bevelSegments: 3, curveSegments: 32 }); break
    }
    case 'pai': {
      const s = new THREE.Shape(), pw = 0.9, ph = 1.4, pr = 0.08
      s.moveTo(-pw + pr, -ph); s.lineTo(pw - pr, -ph); s.quadraticCurveTo(pw, -ph, pw, -ph + pr)
      s.lineTo(pw, ph - pr); s.quadraticCurveTo(pw, ph, pw - pr, ph); s.lineTo(-pw + pr, ph)
      s.quadraticCurveTo(-pw, ph, -pw, ph - pr); s.lineTo(-pw, -ph + pr); s.quadraticCurveTo(-pw, -ph, -pw + pr, -ph)
      geometry = new THREE.ExtrudeGeometry(s, { depth: 0.12, bevelEnabled: true, bevelThickness: 0.03, bevelSize: 0.03, bevelSegments: 3, curveSegments: 16 }); break
    }
    default: {
      const s = new THREE.Shape(); s.moveTo(0, -1.35)
      s.bezierCurveTo(0.65, -1.35, 1.15, -0.78, 1.15, 0); s.bezierCurveTo(1.15, 0.78, 0.65, 1.35, 0, 1.35)
      s.bezierCurveTo(-0.65, 1.35, -1.15, 0.78, -1.15, 0); s.bezierCurveTo(-1.15, -0.78, -0.65, -1.35, 0, -1.35)
      const hole = new THREE.Path(); hole.absarc(0, 1.0, 0.09, 0, Math.PI * 2, false); s.holes.push(hole)
      geometry = new THREE.ExtrudeGeometry(s, { depth: 0.14, bevelEnabled: true, bevelThickness: 0.04, bevelSize: 0.04, bevelSegments: 4, curveSegments: 48 }); break
    }
  }
  normalizeCapUVs(geometry)
  return geometry
}

function createMultiViewShaderMaterial() {
  const mat = new THREE.ShaderMaterial({
    uniforms: {
      uTexA: { value: ensureDefaultTexture() },
      uTexB: { value: ensureDefaultTexture() },
      uBlend: { value: 0.0 },
      uTime: { value: 0.0 },
    },
    vertexShader: MV_VERT,
    fragmentShader: MV_FRAG,
    transparent: true,
    side: THREE.DoubleSide,
  })
  return mat
}

function createJadeSideMaterial() {
  return new THREE.MeshPhysicalMaterial({
    color: 0xc8dcc8, roughness: 0.14, metalness: 0.02,
    transmission: 0.78, thickness: 1.6,
    clearcoat: 0.88, clearcoatRoughness: 0.1,
    ior: 1.54, attenuationColor: new THREE.Color(0x98b8a4),
    attenuationDistance: 0.7, sheen: 0.28, sheenRoughness: 0.45,
    sheenColor: new THREE.Color(0xd0e8d8),
    envMapIntensity: 1.2, side: THREE.DoubleSide,
  })
}

function createJadeCapMaterial() {
  return new THREE.MeshPhysicalMaterial({
    color: 0xf5faf5, roughness: 0.2, metalness: 0.02,
    transmission: 0.55, thickness: 0.8,
    clearcoat: 0.8, clearcoatRoughness: 0.16,
    ior: 1.52, attenuationColor: new THREE.Color(0xb0d0c0),
    attenuationDistance: 1.0, sheen: 0.35, sheenRoughness: 0.5,
    sheenColor: new THREE.Color(0xddf0e4),
    envMapIntensity: 1.0, side: THREE.DoubleSide,
  })
}

function buildJadeMesh() {
  if (jadeMesh) {
    group.remove(jadeMesh)
    if (jadeMesh.geometry) jadeMesh.geometry.dispose()
    if (jadeMesh.material) {
      if (Array.isArray(jadeMesh.material)) jadeMesh.material.forEach((m) => m.dispose())
      else jadeMesh.material.dispose()
    }
    jadeMesh = null
  }
  if (activeTexture) { activeTexture.dispose(); activeTexture = null }
  if (multiViewMaterial) { multiViewMaterial.dispose(); multiViewMaterial = null }

  const type = getJadeType(props.jade)
  const geometry = buildJadeGeometry(type)

  const sideMaterial = createJadeSideMaterial()

  if (props.multiViews.length > 0) {
    multiViewMaterial = createMultiViewShaderMaterial()
    jadeMesh = new THREE.Mesh(geometry, [sideMaterial, multiViewMaterial])
  } else {
    const capMaterial = createJadeCapMaterial()
    jadeMesh = new THREE.Mesh(geometry, [sideMaterial, capMaterial])
  }

  if (type === 'huang') { jadeMesh.rotation.x = Math.PI / 2; jadeMesh.position.z = -0.12 }
  else if (type === 'pai' || type === 'pei' || type === 'le') { jadeMesh.rotation.y = Math.PI * 0.06 }
  else if (type === 'cong') { jadeMesh.rotation.x = Math.PI * 0.05; jadeMesh.rotation.y = Math.PI * 0.12 }
  group.add(jadeMesh)
}

function addShadowPlane() {
  if (shadowMesh) {
    if (shadowMesh.parent) shadowMesh.parent.remove(shadowMesh)
    shadowMesh.geometry.dispose()
    shadowMesh.material.dispose()
    shadowMesh = null
  }
  const shadowGeo = new THREE.CircleGeometry(1.6, 48)
  const shadowMat = new THREE.MeshBasicMaterial({
    color: 0x000000,
    transparent: true,
    opacity: 0.08,
    depthWrite: false,
  })
  shadowMesh = new THREE.Mesh(shadowGeo, shadowMat)
  shadowMesh.rotation.x = -Math.PI / 2
  shadowMesh.position.y = -1.6
  scene.add(shadowMesh)
}

function loadGLBModel(url) {
  clearMeshes()
  const loader = new GLTFLoader()
  loader.load(url, (gltf) => {
    const model = gltf.scene
    const box = new THREE.Box3().setFromObject(model)
    const center = box.getCenter(new THREE.Vector3())
    const size = box.getSize(new THREE.Vector3())
    const maxDim = Math.max(size.x, size.y, size.z)
    const scale = 2.8 / (maxDim || 1)
    model.position.sub(center); model.scale.multiplyScalar(scale); model.position.y += 0.1
    model.traverse((child) => {
      if (child.isMesh) {
        const origMat = child.material
        child.material = new THREE.MeshPhysicalMaterial({
          map: origMat.map || null, normalMap: origMat.normalMap || null,
          color: origMat.color ? origMat.color.clone() : new THREE.Color(0xffffff),
          roughness: 0.22, metalness: 0.02, clearcoat: 0.65, clearcoatRoughness: 0.18,
          ior: 1.52, transmission: 0.12, thickness: 0.6, attenuationColor: new THREE.Color(0xa8c8b4),
          attenuationDistance: 1.5, side: THREE.DoubleSide,
        })
        origMat.dispose()
      }
    })
    glbModel = model; group.add(model)
  }, undefined, () => { buildJadeMesh(); applyTexture(props.imageSrc) })
}

function applyTexture(src) {
  if (!jadeMesh || !src) return
  if (activeTexture) { activeTexture.dispose(); activeTexture = null }

  const materials = Array.isArray(jadeMesh.material) ? jadeMesh.material : [jadeMesh.material]
  const capMat = materials.length > 1 ? materials[1] : materials[0]
  if (!capMat) return

  const loader = new THREE.TextureLoader()
  loader.load(src, (texture) => {
    texture.colorSpace = THREE.SRGBColorSpace
    texture.wrapS = THREE.ClampToEdgeWrapping
    texture.wrapT = THREE.ClampToEdgeWrapping
    texture.anisotropy = 8
    activeTexture = texture
    capMat.map = texture
    capMat.color = new THREE.Color(0xffffff)
    capMat.needsUpdate = true
  }, undefined, () => {
    capMat.map = null
    capMat.needsUpdate = true
  })
}

function preloadMultiViewTextures() {
  orderedTextures.forEach((t) => { if (t) t.dispose() })
  orderedTextures = new Array(props.multiViews.length).fill(null)
  lastBlendIdxA = -1

  if (!props.multiViews || props.multiViews.length === 0) return

  const loader = new THREE.TextureLoader()
  props.multiViews.forEach((view, index) => {
    if (!view.imageUrl) return
    loader.load(view.imageUrl, (texture) => {
      texture.colorSpace = THREE.SRGBColorSpace
      texture.wrapS = THREE.ClampToEdgeWrapping
      texture.wrapT = THREE.ClampToEdgeWrapping
      texture.anisotropy = 8
      orderedTextures[index] = texture
    })
  })
}

function updateMultiViewBlend() {
  if (!multiViewMaterial || !group) return

  const total = orderedTextures.length
  if (total === 0) return

  const angle = ((group.rotation.y % (Math.PI * 2)) + Math.PI * 2) % (Math.PI * 2)
  const segment = (Math.PI * 2) / total
  const continuousIndex = angle / segment
  const idxA = Math.floor(continuousIndex) % total
  const idxB = (idxA + 1) % total
  const blend = continuousIndex - Math.floor(continuousIndex)

  const texA = orderedTextures[idxA]
  const texB = orderedTextures[idxB]

  if (texA) {
    multiViewMaterial.uniforms.uTexA.value = texA
  }
  if (texB) {
    multiViewMaterial.uniforms.uTexB.value = texB
  }

  if (texA && texB) {
    multiViewMaterial.uniforms.uBlend.value = blend
  } else if (texA) {
    multiViewMaterial.uniforms.uTexA.value = texA
    multiViewMaterial.uniforms.uTexB.value = texA
    multiViewMaterial.uniforms.uBlend.value = 0.0
  }

  if (clock) {
    multiViewMaterial.uniforms.uTime.value = clock.getElapsedTime()
  }
}

function buildScene() {
  const container = containerRef.value
  if (!container) return
  const width = container.clientWidth, height = container.clientHeight

  clock = new THREE.Clock()
  scene = new THREE.Scene()
  camera = new THREE.PerspectiveCamera(38, width / Math.max(height, 1), 0.1, 100)
  camera.position.set(0, 0.2, 4.8)
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.setSize(width, height)
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.15
  container.appendChild(renderer.domElement)
  group = new THREE.Group(); scene.add(group)

  if (props.modelUrl) { loadGLBModel(props.modelUrl) }
  else { buildJadeMesh() }

  addShadowPlane()

  scene.add(new THREE.AmbientLight(0xf5fbf7, 0.55))
  const hemiLight = new THREE.HemisphereLight(0xeef7f0, 0x8aaa98, 0.5); scene.add(hemiLight)
  const keyLight = new THREE.DirectionalLight(0xfff8e8, 1.3); keyLight.position.set(3, 4, 5); scene.add(keyLight)
  const fillLight = new THREE.DirectionalLight(0xd8eef0, 0.5); fillLight.position.set(-3, 1, 3); scene.add(fillLight)
  const backLight = new THREE.PointLight(0xbee7d2, 0.7, 10); backLight.position.set(0, -2, -3); scene.add(backLight)
  const topLight = new THREE.PointLight(0xfff5e0, 0.5, 8); topLight.position.set(0, 4, 0); scene.add(topLight)

  if (!props.modelUrl && props.multiViews.length === 0) { applyTexture(props.imageSrc) }
  if (props.multiViews.length > 0) { preloadMultiViewTextures() }

  if (renderer.domElement) {
    renderer.domElement.addEventListener('pointerdown', handlePointerDown)
    renderer.domElement.addEventListener('pointermove', handlePointerMove)
    renderer.domElement.addEventListener('pointerup', handlePointerUp)
    renderer.domElement.addEventListener('pointercancel', handlePointerUp)
    renderer.domElement.addEventListener('pointerleave', handlePointerUp)
  }
}

function animate() {
  if (!renderer || !scene || !camera || !group) return

  if (!dragging3d) {
    if (Math.abs(rotationVelocity) > 0.0001) {
      group.rotation.y += rotationVelocity
      rotationVelocity *= 0.94
      if (Math.abs(rotationVelocity) < 0.0001) rotationVelocity = 0
    } else {
      group.rotation.y += autoRotateSpeed
    }
  }

  if (props.multiViews.length > 0 && multiViewMaterial) {
    updateMultiViewBlend()
  }

  if (shadowMesh && group) {
    const shadowScale = 1.0 + Math.abs(Math.sin(group.rotation.y)) * 0.15
    shadowMesh.scale.set(shadowScale, shadowScale, 1)
    shadowMesh.material.opacity = 0.06 + Math.abs(Math.sin(group.rotation.y)) * 0.04
  }

  renderer.render(scene, camera)
  frameId = requestAnimationFrame(animate)
}

function handlePointerDown(event) {
  if (!renderer?.domElement || pointerId !== null) return
  pointerId = event.pointerId; dragging3d = false; pressTriggeredHold = false
  startX = event.clientX; startY = event.clientY; lastMoveX = event.clientX
  rotationVelocity = 0
  renderer.domElement.setPointerCapture(pointerId)
  clearTimeout(pressTimer)
  pressTimer = window.setTimeout(() => { if (!dragging3d) { pressTriggeredHold = true; emit('trigger-sound', 'hold') } }, 800)
}

function handlePointerMove(event) {
  if (pointerId === null || event.pointerId !== pointerId || !group) return
  const deltaX = event.clientX - startX, deltaY = event.clientY - startY
  if (Math.abs(deltaX) + Math.abs(deltaY) > 6) dragging3d = true
  if (dragging3d) {
    const rotDelta = deltaX * 0.008
    group.rotation.y += rotDelta
    group.rotation.x += deltaY * 0.003
    group.rotation.x = Math.max(-0.6, Math.min(0.6, group.rotation.x))
    rotationVelocity = (event.clientX - lastMoveX) * 0.006
    lastMoveX = event.clientX
    startX = event.clientX; startY = event.clientY
  }
}

function handlePointerUp(event) {
  if (pointerId === null || event.pointerId !== pointerId) return
  clearTimeout(pressTimer)
  if (!dragging3d && !pressTriggeredHold) { emit('trigger-sound', 'touch') }
  if (renderer?.domElement && renderer.domElement.hasPointerCapture(pointerId)) { renderer.domElement.releasePointerCapture(pointerId) }
  pointerId = null; dragging3d = false; pressTriggeredHold = false
}

function handleResize() {
  const container = containerRef.value
  if (!container || !renderer || !camera) return
  const width = container.clientWidth, height = container.clientHeight
  camera.aspect = width / Math.max(height, 1); camera.updateProjectionMatrix(); renderer.setSize(width, height)
}

function disposeScene() {
  clearTimeout(pressTimer)
  if (frameId) { cancelAnimationFrame(frameId); frameId = null }
  window.removeEventListener('resize', handleResize)
  if (renderer?.domElement) {
    renderer.domElement.removeEventListener('pointerdown', handlePointerDown)
    renderer.domElement.removeEventListener('pointermove', handlePointerMove)
    renderer.domElement.removeEventListener('pointerup', handlePointerUp)
    renderer.domElement.removeEventListener('pointercancel', handlePointerUp)
    renderer.domElement.removeEventListener('pointerleave', handlePointerUp)
  }
  clearMeshes()
  if (defaultTexture) { defaultTexture.dispose(); defaultTexture = null }
  if (renderer) { renderer.dispose(); if (renderer.domElement?.parentNode) renderer.domElement.parentNode.removeChild(renderer.domElement) }
  scene = null; camera = null; renderer = null; group = null; jadeMesh = null; glbModel = null; pointerId = null; clock = null
}

watch(() => props.modelUrl, (url) => {
  if (!group) return
  if (url) { loadGLBModel(url) } else { clearMeshes(); buildJadeMesh(); applyTexture(props.imageSrc) }
})

watch(() => props.imageSrc, (value) => {
  if (props.modelUrl || props.multiViews.length > 0) return
  applyTexture(value)
})

watch(() => props.jade, () => {
  if (!group || props.modelUrl) return
  buildJadeMesh()
  if (props.multiViews.length > 0) { preloadMultiViewTextures() }
  else { applyTexture(props.imageSrc) }
}, { deep: true })

watch(() => props.multiViews, (views) => {
  if (views && views.length > 0) {
    if (jadeMesh && !multiViewMaterial) {
      buildJadeMesh()
    }
    preloadMultiViewTextures()
    autoRotateSpeed = 0.005
  } else {
    orderedTextures.forEach((t) => { if (t) t.dispose() })
    orderedTextures = []
    lastBlendIdxA = -1
    rotationVelocity = 0
    autoRotateSpeed = 0.004
    if (jadeMesh) {
      buildJadeMesh()
      if (props.imageSrc) applyTexture(props.imageSrc)
    }
  }
}, { deep: true })

onMounted(() => { buildScene(); animate(); window.addEventListener('resize', handleResize) })
onBeforeUnmount(() => { disposeScene() })
</script>

<template>
  <div ref="containerRef" class="generated-viewer" />
</template>

<style scoped>
.generated-viewer {
  width: 100%;
  height: min(480px, 58vw);
  min-height: 280px;
  border-radius: var(--radius-md);
  border: 1px dashed rgba(54, 89, 76, 0.36);
  background:
    radial-gradient(circle at 54% 36%, rgba(236, 248, 241, 0.86), rgba(217, 233, 222, 0.36) 62%),
    linear-gradient(145deg, rgba(250, 253, 251, 0.9), rgba(237, 247, 241, 0.72));
  box-shadow: inset 0 0 24px rgba(255, 255, 255, 0.68);
  touch-action: none;
}
</style>
